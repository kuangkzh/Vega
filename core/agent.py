"""Agent 会话与 Agent Loop。"""
from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from core.config import build_model, resolve_prompt
from core.context import estimate_tokens, render_prompt
from plugins.base import format_tool_content
from storage.schemas import ChatSession, TokenUsage

_CONTEXT_KEYWORDS = ("context", "maximum context", "context length", "too long")


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
        token_usage: Optional[Dict[str, int]] = None,
        created_at: Optional[float] = None,
        updated_at: Optional[float] = None,
    ):
        self.id = session_id
        self.cwd = cwd
        self.config = config
        self.runtime = runtime
        self.store = store
        self.model_provider = model_provider
        self.manager = manager
        self.created_at = created_at if created_at is not None else time.time()
        self.updated_at = updated_at if updated_at is not None else self.created_at
        self.status = "paused"
        self.token_usage = dict(token_usage or {"input": 0, "output": 0, "cache": 0})
        self._wake = threading.Event()
        self._pending: List[Any] = []
        self._subscribers: set = set()
        self._lock = threading.RLock()
        self._stop = False
        self._pause_requested = False
        self._thread: Optional[threading.Thread] = None

    # ---------- 初始化 ----------
    def _system_content(self) -> str:
        return render_prompt(resolve_prompt(self.config.get("system_prompt")), self.config, self.cwd)

    def _insert_system_message(self) -> None:
        self.store.append_message(self.id, {"role": "system", "content": self._system_content()})

    # ---------- 生命周期 ----------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._worker, daemon=True, name=f"agent-{self.id}")
        self._thread.start()

    def _ensure_started(self) -> None:
        if not self.is_alive():
            self.start()

    def _enqueue(self, item) -> None:
        self._pending.append(item)
        self._wake.set()

    def drain_pending(self) -> List[Any]:
        """一次性取出队列全部项，按入队顺序（FIFO）返回并清空队列。"""
        items = self._pending
        self._pending = []
        return items

    def send(self, text: str) -> None:
        item = {"id": uuid.uuid4().hex, "kind": "user", "text": text}
        self._enqueue(item)
        self.emit({"type": "pending_add", "item": item})
        self._ensure_started()

    def receive_tool_result(self, msg: Dict[str, Any]) -> None:
        """后台异步任务完成后的 tool 结果，交回统一队列处理。"""
        item = {"id": uuid.uuid4().hex, "kind": "tool", "message": msg}
        self._enqueue(item)
        self.emit({"type": "pending_add", "item": item})
        self._ensure_started()

    def pending_items(self) -> List[Dict[str, Any]]:
        """当前排队中、可展示的输入项（user + tool），按 FIFO 顺序，不含 continue 哨兵。"""
        return [it for it in self._pending if it.get("kind") in ("user", "tool")]

    def resume(self) -> None:
        self._ensure_started()
        self._pause_requested = False
        if self.status == "running":
            return
        self._set_status("running")
        self.nudge()

    def pause(self) -> None:
        self._pause_requested = True

    def stop(self) -> None:
        self._stop = True
        self._wake.set()

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
            "updated_at": self.updated_at,
            "status": self.status,
            "token_usage": dict(self.token_usage),
            "max_context": max_ctx,
            "message_count": self.store.count_messages(self.id),
        }

    def _title(self) -> str:
        content = self.store.first_user_message(self.id)
        return (content or "").strip()

    def _persist(self) -> None:
        self.updated_at = time.time()
        try:
            s = ChatSession(
                id=self.id, created_at=self.created_at, updated_at=self.updated_at,
                status=self.status, token_usage=self.token_usage,
            )
            self.store.save_session(s)
        except Exception as exc:  # noqa: BLE001
            print(f"[agent] 持久化失败: {exc}")
        if self.manager is not None:
            try:
                self.manager.notify_session(self)
            except Exception as exc:  # noqa: BLE001
                print(f"[agent] 广播失败: {exc}")

    def _set_status(self, status: str) -> None:
        self.status = status
        self._persist()
        self.emit({"type": "status", "status": status})

    # ---------- 主循环 ----------
    def nudge(self) -> None:
        """Resume / 异步结果已回填：唤醒 loop 继续推理（不追加新消息）。"""
        self._enqueue({"kind": "continue"})
        self._ensure_started()

    def _worker(self) -> None:
        while not self._stop:
            self._wake.wait()
            self._wake.clear()
            if self._stop:
                break
            items = self.drain_pending()
            if not items:
                continue
            self._set_status("running")
            try:
                for item in items:
                    self._append(item)
                if not self._maybe_compress():
                    self._model_loop()
            except Exception as exc:  # noqa: BLE001
                self._append_error(f"[运行出错] {type(exc).__name__}: {exc}")
            if self.status == "running":
                self._set_status("paused")

    def _append(self, item) -> None:
        """把队列项对应的消息落库进上下文并推送；continue 哨兵不追加。

        落库 = 唯一事实来源：所有可展示消息统一「先落库、后 emit message」，
        队列项在进入上下文后同步 emit pending_remove，让前端从排队区移除。
        """
        kind = item.get("kind")
        if kind == "user":
            text = item.get("text") or ""
            message = {"role": "user", "content": text}
            self.store.append_message(self.id, message)
            self._persist()
            self.emit({"type": "message", "message": message})
            self.emit({"type": "pending_remove", "id": item["id"]})
        elif kind == "tool":
            msg = item["message"]
            self.store.append_message(self.id, msg)
            self._persist()
            self.emit({"type": "message", "message": msg})
            self.emit({"type": "pending_remove", "id": item["id"]})

    def _model_loop(self) -> None:
        self._pause_requested = False
        rounds = 0
        max_rounds = int(self.config.get("max_rounds", 20) or 0)
        while not self._stop and not self._pause_requested:
            if max_rounds and rounds >= max_rounds:
                break
            rounds += 1
            content, tool_calls = self._call_model()
            if self._stop or self._pause_requested:
                break
            if not tool_calls:
                if self.status == "running":
                    self._set_status("complete")
                break
            for tc in tool_calls:
                if self._stop or self._pause_requested:
                    break
                self._execute_tool(tc)

    def _stream_round(self, tools: Optional[List[Dict[str, Any]]]) -> tuple:
        """执行一次流式模型调用：逐段 emit、追加并 emit assistant 消息，返回 (content, tool_calls)。"""
        model = self.model_provider(None)
        content_parts: List[str] = []
        reasoning_parts: List[str] = []
        tool_calls: List[Dict[str, Any]] = []
        messages = self._to_model_messages(self.store.load_context(self.id))
        for ev in model.chat_stream(messages, tools=tools):
            if ev.kind == "text":
                content_parts.append(ev.text)
                self.emit({"type": "text", "content": ev.text})
            elif ev.kind == "reasoning":
                reasoning_parts.append(ev.text)
                self.emit({"type": "reasoning", "content": ev.text})
            elif ev.kind == "tool_calls":
                tool_calls = ev.tool_calls
            elif ev.kind == "usage" and ev.usage:
                self._add_usage(ev.usage)
        content = "".join(content_parts)
        reasoning = "".join(reasoning_parts)
        assistant_msg = {"role": "assistant", "content": content}
        if reasoning:
            assistant_msg["reasoning_content"] = reasoning
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        self.store.append_message(self.id, assistant_msg)
        self._persist()
        self.emit({"type": "message", "message": dict(assistant_msg)})
        return content, tool_calls

    def _to_model_messages(self, msgs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """从 DB 消息行构造干净的模型请求体：剔除 id/task_id 等内部字段，保证 role 合法。

        include_reasoning_content 为 False 时不把 reasoning_content 回传入上下文，以节约 token。
        """
        out: List[Dict[str, Any]] = []
        include_reasoning = bool(self.config.get("include_reasoning_content", True))
        for m in msgs:
            clean: Dict[str, Any] = {"role": m["role"], "content": m.get("content") or ""}
            if include_reasoning and m.get("reasoning_content"):
                clean["reasoning_content"] = m["reasoning_content"]
            if m.get("tool_calls"):
                clean["tool_calls"] = m["tool_calls"]
            if m.get("tool_call_id"):
                clean["tool_call_id"] = m["tool_call_id"]
            if m.get("name"):
                clean["name"] = m["name"]
            out.append(clean)
        return out

    def _call_model(self, tools: Optional[List[Dict[str, Any]]] = None):
        model = self.model_provider(None)
        if model is None:
            self._append_error("[错误] 未配置可用模型")
            return "", []
        if tools is None:
            tools = self.runtime.visible_tools()
        try:
            return self._stream_round(tools)
        except Exception as exc:  # noqa: BLE001
            if self._is_context_error(exc):
                self._compress_and_restart()
                self._pause_requested = True
                return "", []
            msg = f"[模型调用失败] {type(exc).__name__}: {exc}"
            self._append_error(msg)
            return msg, []

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
        if isinstance(res.result, dict) and res.result.get("task_id"):
            tool_msg["task_id"] = res.result["task_id"]
        self.store.append_message(self.id, tool_msg)
        self.emit({"type": "message", "message": tool_msg})
        self._persist()

    def _add_usage(self, usage: TokenUsage) -> None:
        # 记录最近一次调用的用量：input 即当前完整上下文规模，供前端进度条展示；
        # 不可累加，否则会随工具调用轮次成倍膨胀，远超真实上下文占用。
        self.token_usage["input"] = usage.input
        self.token_usage["output"] = usage.output
        self.token_usage["cache"] = usage.cache
        self.emit({"type": "usage", "usage": dict(self.token_usage)})
        self._persist()

    def _append_error(self, text: str) -> None:
        msg = {"role": "error", "content": text}
        self.store.append_message(self.id, msg)
        self.emit({"type": "message", "message": msg})
        self._set_status("error")

    # ---------- 上下文压缩 ----------
    def _maybe_compress(self) -> bool:
        model = self.model_provider(None)
        max_ctx = (model.config.get("max_context") if model else None) or 128000
        ratio = (model.config.get("compress_ratio") if model else None) or 0.8
        if estimate_tokens(self.store.load_context(self.id)) >= int(max_ctx * ratio):
            self._compress_and_restart()
            return True
        return False

    def _compress_and_restart(self) -> None:
        """上下文压缩：追加摘要 system prompt → 跑一轮无工具回复（正常流式展示）→ 重启到新会话。"""
        prompt = self.config["summarizer"]["prompt"]
        self.store.append_message(self.id, {"role": "system", "content": prompt})
        self._persist()
        self.emit({"type": "message", "message": {"role": "system", "content": prompt}})
        summary = ""
        if self.model_provider(None) is not None:
            try:
                summary, _ = self._stream_round(tools=[])
            except Exception as exc:  # noqa: BLE001
                print(f"[agent] 摘要失败: {exc}")
        summary = (summary or "").strip()
        if not summary:
            msgs = self.store.load_messages(self.id)
            summary = "\n".join(str(m.get("content") or "") for m in msgs[-4:] if isinstance(m, dict))
        if self.manager is not None and hasattr(self.manager, "restart_after_compress"):
            self.manager.restart_after_compress(self, summary)

    @staticmethod
    def _is_context_error(exc: Exception) -> bool:
        return any(k in str(exc).lower() for k in _CONTEXT_KEYWORDS)