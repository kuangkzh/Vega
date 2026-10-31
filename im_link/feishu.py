"""飞书 IM 实现：发送消息 + 长连接监听事件。

飞书 SDK 的 ws.Client 共用模块级 event loop 且 start() 会永久占用，进程内只能有一条连接；
因此按 app_id 复用一条长连接，收到的事件按 chat_id 分发给各 project。
"""
from __future__ import annotations

import json
import threading
from typing import Any, Callable, Dict, List

import lark_oapi as lark

from im_link.base import ImLink


_conns: Dict[str, "_FeishuConn"] = {}
_conns_lock = threading.Lock()


def _get_conn(app_id: str, app_secret: str) -> "_FeishuConn":
    with _conns_lock:
        conn = _conns.get(app_id)
        if conn is None:
            conn = _FeishuConn(app_id, app_secret)
            _conns[app_id] = conn
        return conn


class _FeishuConn:
    """一个 app_id 对应一条长连接，事件按 chat_id 分发给订阅者。"""

    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self._routes: Dict[str, List[Callable[[str, str], None]]] = {}
        self._lock = threading.Lock()
        self._ws = None

    def subscribe(self, chat_id: str, on_message) -> None:
        with self._lock:
            self._routes.setdefault(chat_id, []).append(on_message)
            if self._ws is not None:
                return
            handler = (
                lark.EventDispatcherHandler.builder("", "")
                .register_p2_im_message_receive_v1(self._on_message)
                .build()
            )
            self._ws = lark.ws.Client(
                self.app_id, self.app_secret, event_handler=handler, log_level=lark.LogLevel.ERROR
            )
        threading.Thread(target=self._ws.start, daemon=True).start()

    def unsubscribe(self, chat_id: str, on_message) -> None:
        with self._lock:
            callbacks = self._routes.get(chat_id) or []
            if on_message in callbacks:
                callbacks.remove(on_message)
            if not callbacks:
                self._routes.pop(chat_id, None)

    def _on_message(self, data: lark.im.v1.P2ImMessageReceiveV1) -> None:
        try:
            msg = data.event.message
            if msg.message_type != "text":
                return
            text = (json.loads(msg.content or "{}") or {}).get("text") or ""
            chat_id = msg.chat_id or ""
            sender_id = ""
            try:
                sender_id = data.event.sender.sender_id.open_id or ""
            except Exception:  # noqa: BLE001
                sender_id = ""
        except Exception as exc:  # noqa: BLE001
            print(f"[feishu] 解析消息失败: {exc}")
            return
        if not text:
            return
        with self._lock:
            callbacks = list(self._routes.get(chat_id) or [])
        for cb in callbacks:
            cb(text, sender_id)


class FeishuImLink(ImLink):
    def __init__(self, config: Dict[str, Any]):
        config = config or {}
        self.app_id = config.get("app_id") or ""
        self.app_secret = config.get("app_secret") or ""
        self.chat_id = config.get("chat_id") or ""
        self._client = None
        self._conn = None
        self._on_message = None

    def _get_client(self):
        if self._client is None:
            self._client = lark.Client.builder().app_id(self.app_id).app_secret(self.app_secret).build()
        return self._client

    def send(self, target: str, content: str) -> None:
        client = self._get_client()
        body = (
            lark.im.v1.CreateMessageRequestBody.builder()
            .receive_id(target)
            .msg_type("text")
            .content(json.dumps({"text": content}, ensure_ascii=False))
            .build()
        )
        request = lark.im.v1.CreateMessageRequest.builder().receive_id_type("chat_id").request_body(body).build()
        resp = client.im.v1.message.create(request)
        if not resp.success():
            raise RuntimeError(f"飞书发送失败: {resp.msg}")

    def notify_approval(self, pa) -> None:
        if not self.chat_id:
            return
        text = (
            f"[Vega 审批请求]\n插件: {pa.plugin_name}\n理由: {pa.request_reason}\n审批 uuid: {pa.uuid}\n"
            "请在控制台「后台任务」页面批准或拒绝。"
        )
        self.send(self.chat_id, text)

    def start(self, on_message) -> None:
        if not self.app_id or not self.chat_id:
            return
        self._on_message = on_message
        self._conn = _get_conn(self.app_id, self.app_secret)
        self._conn.subscribe(self.chat_id, on_message)

    def stop(self) -> None:
        if self._conn is not None and self._on_message is not None:
            self._conn.unsubscribe(self.chat_id, self._on_message)
        self._conn = None
        self._on_message = None