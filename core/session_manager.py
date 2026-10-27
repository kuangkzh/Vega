"""会话管理：project 与会话、IM 监听、fork、队列调度、压缩重启、异步结果回填。"""
from __future__ import annotations

import datetime
import os
import queue
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from core.agent import AgentSession
from im_link.base import build_im_link
from plugins.runtime import format_tool_content
from storage.schemas import Project


class SessionManager:
    def __init__(self, store, runtime, config: Dict[str, Any], cwd: str, model_provider):
        self.store = store
        self.runtime = runtime
        self.config = config
        self.cwd = cwd  # fallback：正常场景会话 cwd 取自所属 project.path
        self.model_provider = model_provider
        self.sessions: Dict[str, AgentSession] = {}
        self.projects: Dict[str, Project] = {}
        self._im_session_by_project: Dict[str, str] = {}
        self._im_links: Dict[str, Any] = {}
        self._global_subscribers: set = set()
        self._lock = threading.RLock()
        self.runtime.project_resolver = self._project_context

    def load(self) -> "SessionManager":
        """启动时恢复项目与会话（默认暂停），并启动各项目的 IM 监听。"""
        for p in self.store.list_projects():
            self.projects[p.id] = p
        for s in self.store.list_sessions():
            project = self.projects.get(s.project_id)
            # 进程重启后不存在真正在跑的会话：持久化的 running 不可信，统一重置为 paused，
            # 否则会卡在 running 却没有线程在推理，且 resume 会因 status==running 直接返回。
            status = "paused" if s.status == "running" else s.status
            sess = AgentSession(
                session_id=s.id, cwd=project.path if project else self.cwd, config=self.config,
                runtime=self.runtime, store=self.store, model_provider=self.model_provider,
                manager=self, token_usage=s.token_usage, created_at=s.created_at, updated_at=s.updated_at,
                status=status, project_id=s.project_id,
            )
            self.sessions[s.id] = sess
            if s.status == "running":
                sess._set_status("paused")
            sess.start()
        for pid in list(self.projects):
            self.start_project_im(pid)
        if not self.sessions and self.projects:
            self.create(start=True)
        return self

    def _make_session(self, project_id: str) -> AgentSession:
        project = self.projects.get(project_id)
        sid = uuid.uuid4().hex[:8]
        sess = AgentSession(
            session_id=sid, cwd=project.path if project else self.cwd, config=self.config,
            runtime=self.runtime, store=self.store, model_provider=self.model_provider,
            manager=self, project_id=project_id,
        )
        with self._lock:
            self.sessions[sid] = sess
        sess._persist()
        return sess

    def create(self, project_id: Optional[str] = None, start: bool = True) -> Optional[AgentSession]:
        if project_id is None and self.projects:
            project_id = next(iter(self.projects))
        if project_id is None or project_id not in self.projects:
            return None
        sess = self._make_session(project_id)
        sess._insert_system_message()
        if start:
            sess.start()
        return sess

    def get(self, sid: str) -> Optional[AgentSession]:
        with self._lock:
            return self.sessions.get(sid)

    def list(self, project_id: Optional[str] = None) -> List[AgentSession]:
        with self._lock:
            sessions = list(self.sessions.values())
        if project_id is not None:
            sessions = [s for s in sessions if s.project_id == project_id]
        return sessions

    # ---------- project ----------
    def list_projects(self) -> List[Project]:
        with self._lock:
            projects = list(self.projects.values())
        return sorted(projects, key=lambda p: p.updated_at, reverse=True)

    def get_project(self, project_id: str) -> Optional[Project]:
        with self._lock:
            return self.projects.get(project_id)

    def create_project(self, path: str, im_link: Optional[Dict[str, Any]] = None) -> Project:
        path = os.path.abspath(path or ".")
        os.makedirs(path, exist_ok=True)
        now = time.time()
        p = Project(id=uuid.uuid4().hex[:8], path=path, im_link=dict(im_link or {}), created_at=now, updated_at=now)
        self.store.save_project(p)
        with self._lock:
            self.projects[p.id] = p
        self.start_project_im(p.id)
        self.emit_project_change(p)
        return p

    def update_project(self, project_id: str, path: Optional[str] = None, im_link: Optional[Dict[str, Any]] = None) -> Optional[Project]:
        p = self.get_project(project_id)
        if p is None:
            return None
        if path is not None:
            p.path = os.path.abspath(path or ".")
            os.makedirs(p.path, exist_ok=True)
        if im_link is not None:
            p.im_link = dict(im_link)
        p.updated_at = time.time()
        self.store.save_project(p)
        self.restart_project_im(project_id)
        for s in self.list(project_id):
            s.cwd = p.path
        self.emit_project_change(p)
        return p

    def delete_project(self, project_id: str) -> None:
        if self.get_project(project_id) is None:
            return
        self.stop_project_im(project_id)
        with self._lock:
            self._im_session_by_project.pop(project_id, None)
        for s in list(self.list(project_id)):
            self.delete(s.id)
        self.store.delete_project(project_id)
        with self._lock:
            self.projects.pop(project_id, None)
        self.emit_project_change({"id": project_id, "deleted": True})

    def emit_project_change(self, project) -> None:
        d = project if isinstance(project, dict) else project.to_dict()
        self.emit_global({"type": "project", "project": d})

    # ---------- 全局事件总线（所有会话状态/元数据实时广播） ----------
    def subscribe_global(self) -> "queue.Queue":
        q: "queue.Queue" = queue.Queue()
        with self._lock:
            self._global_subscribers.add(q)
        return q

    def unsubscribe_global(self, q) -> None:
        with self._lock:
            self._global_subscribers.discard(q)

    def emit_global(self, event: Dict[str, Any]) -> None:
        with self._lock:
            subs = list(self._global_subscribers)
        for q in subs:
            try:
                q.put(event)
            except Exception:  # noqa: BLE001
                pass

    def notify_session(self, sess: AgentSession) -> None:
        """会话元数据（status/title/message_count/updated_at/token_usage）变化时广播给所有前端。"""
        d = sess.to_dict()
        with self._lock:
            d["im_bound"] = self._im_session_by_project.get(sess.project_id) == sess.id
        self.emit_global({"type": "session", "session": d})

    def fork(self, sid: str, message_index: Optional[int] = None) -> Optional[AgentSession]:
        src = self.get(sid)
        if src is None:
            return None
        msgs = self.store.load_messages(sid)
        up_to_id = None
        if message_index is not None and 0 <= message_index < len(msgs):
            up_to_id = msgs[message_index]["id"]
        dst = self._make_session(src.project_id)
        self.store.copy_messages(sid, dst.id, up_to_id)
        dst.start()
        return dst

    def delete(self, sid: str) -> None:
        with self._lock:
            sess = self.sessions.pop(sid, None)
            if sess is not None and self._im_session_by_project.get(sess.project_id) == sid:
                self._im_session_by_project.pop(sess.project_id, None)
        if sess is not None:
            sess.stop()
            self.store.delete_session(sid)
            self.store.delete_messages(sid)
            self.store.delete_tasks_by_session(sid)

    def send(self, sid: str, text: str) -> Optional[AgentSession]:
        sess = self.get(sid)
        if sess is None:
            return None
        sess.send(text)
        return sess

    def restart_after_compress(self, old: AgentSession, summary: str) -> None:
        new = self._make_session(old.project_id)
        self.store.append_message(new.id, {"role": "system", "content": f"[对话历史摘要]\n{summary}"})
        old.emit({"type": "restart", "old_id": old.id, "new_id": new.id})
        old.drain_pending()  # 压缩重启不迁移队列：old 的待处理输入作废（视同 fork 不拷贝）
        old.stop()
        new.nudge()

    def shutdown(self) -> None:
        with self._lock:
            sessions = list(self.sessions.values())
        for s in sessions:
            s.stop()

    # ---------- IM 监听（每个 project 独立 im_link，一个 project 内仅一个会话监听） ----------
    def start_project_im(self, project_id: str) -> None:
        p = self.get_project(project_id)
        if p is None:
            return
        self.stop_project_im(project_id)
        link = None
        if (p.im_link or {}).get("type"):
            try:
                link = build_im_link(p.im_link)
            except Exception as exc:  # noqa: BLE001
                print(f"[im] project {project_id} 构建 im_link 失败: {exc}")
                link = None
        with self._lock:
            self._im_links[project_id] = link
        if link is not None:
            link.start(lambda text, _sender_id, pid=project_id: self.im_recv(pid, text))

    def stop_project_im(self, project_id: str) -> None:
        link = self._im_links.pop(project_id, None)
        if link is not None and getattr(link, "stop", None):
            try:
                link.stop()
            except Exception:  # noqa: BLE001
                pass

    def restart_project_im(self, project_id: str) -> None:
        self.start_project_im(project_id)

    def get_im_session_id(self, project_id: str) -> Optional[str]:
        with self._lock:
            return self._im_session_by_project.get(project_id)

    def toggle_im(self, project_id: str, sid: str) -> bool:
        """切换某 project 内的 IM 监听会话：绑定 sid；若已绑定则该次取消。返回切换后是否绑定。"""
        with self._lock:
            sess = self.sessions.get(sid)
            if sess is None or sess.project_id != project_id:
                return False
            if self._im_session_by_project.get(project_id) == sid:
                self._im_session_by_project.pop(project_id, None)
                return False
            self._im_session_by_project[project_id] = sid
            return True

    def im_recv(self, project_id: str, text: str) -> None:
        """IM 入站消息：投递到该 project 当前绑定的会话，末尾标注来源。未绑定则丢弃。"""
        sid = self.get_im_session_id(project_id)
        if not sid:
            return
        sess = self.get(sid)
        if sess is None:
            return
        sess.send(f"{text}（本消息来自 IM）")

    def im_notify(self, sess: AgentSession, text: str) -> bool:
        """会话绑定 project 的 IM 时，把 text 推送到该 IM 聊天；未绑定/未配置则跳过。"""
        if not text or not str(text).strip():
            return False
        with self._lock:
            bound = self._im_session_by_project.get(sess.project_id) == sess.id
            link = self._im_links.get(sess.project_id)
        if not bound or link is None:
            return False
        p = self.get_project(sess.project_id)
        chat_id = (p.im_link or {}).get("chat_id") if p else ""
        if not chat_id:
            return False
        try:
            link.send(chat_id, str(text))
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[im] 发送失败: {exc}")
            return False

    def _project_context(self, session_id: Optional[str]) -> Dict[str, Any]:
        """插件运行时解析：会话 → project 的 cwd / im_link 实例 / im 配置。"""
        sess = self.get(session_id) if session_id else None
        if sess is None:
            return {
                "cwd": self.cwd,
                "im_link": None,
                "im_config": {},
            }
        project = self.get_project(sess.project_id)
        im = self._im_links.get(sess.project_id)
        if im is None and project and (project.im_link or {}).get("type"):
            try:
                im = build_im_link(project.im_link)
            except Exception:  # noqa: BLE001
                im = None
        return {
            "project_id": sess.project_id,
            "cwd": project.path if project else self.cwd,
            "im_link": im,
            "im_config": dict(project.im_link) if project else {},
        }

    # 异步任务完成回调（task_manager.on_done）
    def on_task_done(self, session_id: Optional[str], tool_name: str, task) -> None:
        if not session_id:
            return
        sess = self.get(session_id)
        if sess is None:
            return
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        result = task.result.get("result")
        msg = task.result.get("msg") or ""
        payload = {
            "入参": task.params,
            "执行时间": task.start_time,
            "完成时间": task.end_time or now,
            "result": result,
            "msg": msg,
        }
        content = format_tool_content(tool_name, payload, msg)
        # 异步回填没有可配对的 assistant.tool_calls，用独立 role 存储，模型请求时再按
        # 模型配置的 tool_compat_mode 投影成 tool 或 user（见 AgentSession._to_model_messages）。
        tool_msg = {"role": "async-tool", "tool_call_id": f"async-{task.id}", "name": tool_name, "content": content, "task_id": task.id}
        sess.receive_tool_result(tool_msg)