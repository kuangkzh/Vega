"""会话管理：多会话、fork、队列调度、压缩重启、异步结果回填。"""
from __future__ import annotations

import datetime
import threading
import uuid
from typing import Any, Dict, List, Optional

from core.agent import AgentSession
from plugins.base import format_tool_content


class SessionManager:
    def __init__(self, store, runtime, config: Dict[str, Any], cwd: str, model_provider):
        self.store = store
        self.runtime = runtime
        self.config = config
        self.cwd = cwd
        self.model_provider = model_provider
        self.sessions: Dict[str, AgentSession] = {}
        self._feishu_session_id: Optional[str] = None
        self._lock = threading.RLock()

    def load(self) -> "SessionManager":
        """启动时恢复持久化会话（默认暂停）。"""
        for s in self.store.list_sessions():
            sess = AgentSession(
                session_id=s.id, cwd=self.cwd, config=self.config,
                runtime=self.runtime, store=self.store, model_provider=self.model_provider,
                manager=self, token_usage=s.token_usage, created_at=s.created_at, updated_at=s.updated_at,
            )
            self.sessions[s.id] = sess
            sess.start()
        if not self.sessions:
            self.create()
        return self

    def _make_session(self) -> AgentSession:
        sid = uuid.uuid4().hex
        sess = AgentSession(
            session_id=sid, cwd=self.cwd, config=self.config,
            runtime=self.runtime, store=self.store, model_provider=self.model_provider,
            manager=self,
        )
        with self._lock:
            self.sessions[sid] = sess
        sess._persist()
        return sess

    def create(self, start: bool = True) -> AgentSession:
        sess = self._make_session()
        sess._insert_system_message()
        if start:
            sess.start()
        return sess

    def get(self, sid: str) -> Optional[AgentSession]:
        with self._lock:
            return self.sessions.get(sid)

    def list(self) -> List[AgentSession]:
        with self._lock:
            return list(self.sessions.values())

    def fork(self, sid: str, message_index: Optional[int] = None) -> Optional[AgentSession]:
        if self.get(sid) is None:
            return None
        msgs = self.store.load_messages(sid)
        up_to_id = None
        if message_index is not None and 0 <= message_index < len(msgs):
            up_to_id = msgs[message_index]["id"]
        dst = self._make_session()
        self.store.copy_messages(sid, dst.id, up_to_id)
        dst.start()
        return dst

    def delete(self, sid: str) -> None:
        with self._lock:
            sess = self.sessions.pop(sid, None)
            if self._feishu_session_id == sid:
                self._feishu_session_id = None
        if sess is not None:
            sess.stop()
            self.store.delete_session(sid)
            self.store.delete_messages(sid)
            self.store.delete_tasks_by_session(sid)

    def send(self, sid: str, text: str, mid: Optional[str] = None) -> Optional[AgentSession]:
        sess = self.get(sid)
        if sess is None:
            return None
        sess.send(text, mid)
        return sess

    def restart_after_compress(self, old: AgentSession, summary: str) -> None:
        new = self._make_session()
        self.store.append_message(new.id, {"role": "system", "content": f"[对话历史摘要]\n{summary}"})
        old.emit({"type": "restart", "old_id": old.id, "new_id": new.id})
        while not old.queue.empty():
            try:
                new.queue.put(old.queue.get_nowait())
            except Exception:  # noqa: BLE001
                break
        old.stop()
        new.nudge()

    def shutdown(self) -> None:
        with self._lock:
            sessions = list(self.sessions.values())
        for s in sessions:
            s.stop()

    # ---------- 飞书监听会话绑定（同一时间仅一个会话监听） ----------
    def get_feishu_session_id(self) -> Optional[str]:
        with self._lock:
            return self._feishu_session_id

    def toggle_feishu(self, sid: str) -> bool:
        """切换飞书监听：绑定 sid；若已绑定则该次取消。返回切换后是否处于绑定状态。"""
        with self._lock:
            if sid not in self.sessions:
                return False
            if self._feishu_session_id == sid:
                self._feishu_session_id = None
                return False
            self._feishu_session_id = sid
            return True

    def feishu_recv(self, text: str) -> None:
        """飞书入站消息：投递到当前绑定会话，末尾标注来源。未绑定则丢弃。"""
        sid = self.get_feishu_session_id()
        if not sid:
            return
        sess = self.get(sid)
        if sess is None:
            return
        sess.send(f"{text}（本消息来自飞书）")

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
        tool_msg = {"role": "tool", "tool_call_id": f"async-{task.id}", "name": tool_name, "content": content, "task_id": task.id}
        self.store.append_message(session_id, tool_msg)
        sess._persist()
        sess.emit({"type": "message", "message": tool_msg})
        sess.nudge()