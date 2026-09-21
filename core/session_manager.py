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
        self._lock = threading.RLock()

    def load(self) -> "SessionManager":
        """启动时恢复持久化会话（默认暂停）。"""
        for s in self.store.list_sessions():
            sess = AgentSession(
                session_id=s.id, name=s.name, cwd=self.cwd, config=self.config,
                runtime=self.runtime, store=self.store, model_provider=self.model_provider,
                manager=self, parent_id=s.parent_id, messages=s.messages, token_usage=s.token_usage,
            )
            self.sessions[s.id] = sess
            sess.start()
        if not self.sessions:
            self.create("default")
        return self

    def create(
        self,
        name: str = "session",
        parent_id: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
        token_usage: Optional[Dict[str, int]] = None,
        start: bool = True,
    ) -> AgentSession:
        sid = uuid.uuid4().hex
        sess = AgentSession(
            session_id=sid, name=name, cwd=self.cwd, config=self.config,
            runtime=self.runtime, store=self.store, model_provider=self.model_provider,
            manager=self, parent_id=parent_id, messages=messages, token_usage=token_usage,
        )
        with self._lock:
            self.sessions[sid] = sess
        sess._persist()
        if start:
            sess.start()
        return sess

    def get(self, sid: str) -> Optional[AgentSession]:
        with self._lock:
            return self.sessions.get(sid)

    def list(self) -> List[AgentSession]:
        with self._lock:
            return list(self.sessions.values())

    def fork(self, sid: str) -> Optional[AgentSession]:
        sess = self.get(sid)
        if sess is None:
            return None
        return self.create(
            name=f"{sess.name}-fork", parent_id=sid,
            messages=list(sess.messages), token_usage=dict(sess.token_usage),
        )

    def delete(self, sid: str) -> None:
        with self._lock:
            sess = self.sessions.pop(sid, None)
        if sess is not None:
            sess.stop()
            self.store.delete_session(sid)

    def send(self, sid: str, text: str) -> Optional[AgentSession]:
        sess = self.get(sid)
        if sess is None:
            return None
        sess.send(text)
        return sess

    def restart_after_compress(self, old: AgentSession, new_messages: List[Dict[str, Any]]) -> None:
        new = self.create(
            name=f"{old.name}-压缩", parent_id=old.id,
            messages=new_messages, token_usage=dict(old.token_usage),
        )
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
        sess.messages.append({"role": "tool", "tool_call_id": f"async-{task.id}", "name": tool_name, "content": content})
        sess._persist()
        sess.emit({"type": "async_result", "plugin": tool_name, "task_id": task.id, "result": result, "msg": msg})
        sess.nudge()