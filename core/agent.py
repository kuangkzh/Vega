"""Agent 会话与 Agent Loop。"""
from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from core.config import build_model, resolve_prompt
from core.context import estimate_tokens, render_slots, summarize
from plugins.base import format_tool_content
from storage.schemas import ChatSession, TokenUsage

_CONTEXT_KEYWORDS = ("context", "maximum context", "context length", "too long")
_NUDGE = "__async_continue__"


class AgentSession:
    def __init__(
        self,
        session_id: str,
        cwd: str,
        config: Dict[str, Any],
        runtime,
        store,
        model_provider,
        manager=None,
        messages: Optional[List[Dict[str, Any]]] = None,
        token_usage: Optional[Dict[str, int]] = None,
    ):
        self.id = session_id
        self.cwd = cwd
        self.config = config
        self.runtime = runtime
        self.store = store
        self.model_provider = model_provider
        self.manager = manager
        self.created_at = time.time()
        self.status = "paused"
        self.token_usage = dict(token_usage or {"input": 0, "output": 0, "cache": 0})
        self.messages = self._build_messages(messages)
        self.queue: "queue.Queue[Any]" = queue.Queue()
        self._subscribers: set = set()
        self._lock = threading.RLock()
        self._stop = False
        self._pause_requested = False
        self._thread: Optional[threading.Thread] = None

    # ---------- 初始化 ----------
    def _system_content(self) -> str:
        return render_slots(resolve_prompt(self.config.get("system_prompt"), self.cwd), self.cwd)

    def _build_messages(self, messages: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        if messages:
            if messages and messages[0].get("role") == "system":
                return list(messages)
            return [{"role": "system", "content": self._system_content()}] + list(messages)
        return [{"role": "system", "content": self._system_content()}]

    # ---------- 生命周期 ----------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._worker, daemon=True, name=f"agent-{self.id}")
        self._thread.start()

    def send(self, text: str, mid: Optional[str] = None) -> None:
        mid = mid or uuid.uuid4().hex
        self.emit({"type": "queued", "id": mid, "content": text})
        self.queue.put((mid, text))
        if not self._thread or not self._thread.is_alive():
            self.start()

    def resume(self) -> None:
        if not self._thread or not self._thread.is_alive():
            self.start()
        self.status = "running"
        self._persist()
        self.emit({"type": "status", "status": "running"})

    def pause(self) -> None:
        self._pause_requested = True

    def stop(self) -> None:
        self._stop = True
        try:
            self.queue.put(None)
        except Exception:  # noqa: BLE001
            pass

    def is_alive(self) -> bool:
        return bool(self._thread) and self._thread.is_alive()

    # ---------- 事件订阅（SSE） ----------
    def subscribe(self) -> "queue.Queue":
        q: "queue.Queue" = queue.Queue()
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q) -> None:
        with self._lock:
            self._subscribers.discard(q)

    def emit(self, event: Dict[str, Any]) -> None:
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put(event)
            except Exception:  # noqa: BLE001
                pass

    def to_dict(self) -> Dict[str, Any]:
        entry = build_model(self.config)
        max_ctx = int((entry or {}).get("max_context") or 0)
        return {
            "id": self.id,
            "title": self._title(),
            "created_at": self.created_at,
            "status": self.status,
            "token_usage": dict(self.token_usage),
            "max_context": max_ctx,
            "message_count": len(self.messages),
        }

    def _title(self) -> str:
        for m in self.messages or []:
            if isinstance(m, dict) and m.get("role") == "user":
                return str(m.get("content") or "").strip()
        return ""

    def _persist(self) -> None:
        try:
            s = ChatSession(
                id=self.id, created_at=self.created_at,
                status=self.status, messages=self.messages, token_usage=self.token_usage,
            )
            self.store.save_session(s)
        except Exception as exc:  # noqa: BLE001
            print(f"[agent] 持久化失败: {exc}")

    # ---------- 主循环 ----------
    def nudge(self) -> None:
        """异步结果已追加后，唤醒模型继续处理。"""
        self.queue.put(_NUDGE)
        if not self.is_alive():
            self.start()

    def _worker(self) -> None:
        while not self._stop:
            item = self.queue.get()
            if item is None:
                break
            self.status = "running"
            self._persist()
            self.emit({"type": "status", "status": "running"})
            try:
                if item == _NUDGE:
                    self._model_loop()
                else:
                    mid, text = item
                    self._run(mid, text)
            except Exception as exc:  # noqa: BLE001
                self._append_assistant(f"[运行出错] {type(exc).__name__}: {exc}")
            while not self._stop and not self.queue.empty():
                nxt = self.queue.get()
                if nxt is None:
                    break
                if nxt == _NUDGE:
                    self._model_loop()
                else:
                    mid, text = nxt
                    self._run(mid, text)
            self.status = "paused"
            self._persist()
            self.emit({"type": "status", "status": "paused"})

    def _run(self, mid: str, text: str) -> None:
        self.messages.append({"role": "user", "content": text})
        self._persist()
        self.emit({"type": "message", "id": mid, "message": {"role": "user", "content": text}})
        if self._maybe_compress():
            return  # 已触发压缩重启，本轮结束
        self._model_loop()

    def _model_loop(self) -> None:
        self._pause_requested = False
        rounds = 0
        while not self._stop and not self._pause_requested and rounds < 20:
            rounds += 1
            content, tool_calls = self._call_model()
            if self._stop or self._pause_requested:
                break
            if not tool_calls:
                break
            for tc in tool_calls:
                if self._stop or self._pause_requested:
                    break
                self._execute_tool(tc)

    def _call_model(self):
        model = self.model_provider(None)
        tools = self.runtime.visible_tools()
        content_parts: List[str] = []
        tool_calls: List[Dict[str, Any]] = []
        if model is None:
            msg = "[错误] 未配置可用模型"
            self._append_assistant(msg)
            return "", []
        try:
            for ev in model.chat_stream(self.messages, tools=tools):
                if ev.kind == "text":
                    content_parts.append(ev.text)
                    self.emit({"type": "text", "content": ev.text})
                elif ev.kind == "tool_calls":
                    tool_calls = ev.tool_calls
                elif ev.kind == "usage" and ev.usage:
                    self._add_usage(ev.usage)
        except Exception as exc:  # noqa: BLE001
            if self._is_context_error(exc):
                self._compress_and_restart()
                self._pause_requested = True
                return "", []
            msg = f"[模型调用失败] {type(exc).__name__}: {exc}"
            content_parts.append(msg)
            self.emit({"type": "text", "content": msg})
        content = "".join(content_parts)
        assistant_msg = {"role": "assistant", "content": content}
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        self.messages.append(assistant_msg)
        self._persist()
        self.emit({"type": "message", "message": dict(assistant_msg)})
        return content, tool_calls

    def _execute_tool(self, tc: Dict[str, Any]) -> None:
        fn = tc.get("function") or {}
        name = fn.get("name") or ""
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        res = self.runtime.execute(name, args, self.id)
        content = format_tool_content(name, res.result, res.msg)
        tool_msg = {"role": "tool", "tool_call_id": tc.get("id", ""), "name": name, "content": content}
        self.messages.append(tool_msg)
        self.emit({"type": "message", "message": tool_msg})
        self._persist()
        if name == "wait_for_new_instruction":
            self._pause_requested = True

    def _add_usage(self, usage: TokenUsage) -> None:
        self.token_usage["input"] += usage.input
        self.token_usage["output"] += usage.output
        self.token_usage["cache"] += usage.cache
        self.emit({"type": "usage", "usage": dict(self.token_usage)})
        self._persist()

    def _append_assistant(self, text: str) -> None:
        msg = {"role": "assistant", "content": text}
        self.messages.append(msg)
        self.emit({"type": "message", "message": msg})
        self._persist()

    # ---------- 上下文压缩 ----------
    def _maybe_compress(self) -> bool:
        model = self.model_provider(None)
        max_ctx = (model.config.get("max_context") if model else None) or 128000
        ratio = (model.config.get("compress_ratio") if model else None) or 0.8
        if estimate_tokens(self.messages) >= int(max_ctx * ratio):
            self._compress_and_restart()
            return True
        return False

    def _compress_and_restart(self) -> None:
        model = self.model_provider(None)
        new_messages = summarize(self.messages, model, keep_n=4) if model else list(self.messages[-4:])
        if self.manager is not None and hasattr(self.manager, "restart_after_compress"):
            self.manager.restart_after_compress(self, new_messages)

    @staticmethod
    def _is_context_error(exc: Exception) -> bool:
        return any(k in str(exc).lower() for k in _CONTEXT_KEYWORDS)