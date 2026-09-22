"""飞书消息源：发送消息 + 长连接监听事件。"""
from __future__ import annotations

import json
import threading
from typing import Any, Dict

import lark_oapi as lark


class FeishuSource:
    def __init__(self, config: Dict[str, Any]):
        config = config or {}
        self.app_id = config.get("app_id") or ""
        self.app_secret = config.get("app_secret") or ""
        self.admin_open_id = config.get("admin_open_id") or ""
        self._client = None
        self._ws = None

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
        request = lark.im.v1.CreateMessageRequest.builder().receive_id_type("open_id").request_body(body).build()
        resp = client.im.v1.message.create(request)
        if not resp.success():
            raise RuntimeError(f"飞书发送失败: {resp.msg}")

    def notify_approval(self, pa) -> None:
        if not self.admin_open_id:
            return
        text = (
            f"[CodeAgent 审批请求]\n插件: {pa.plugin_name}\n理由: {pa.reason}\n审批 uuid: {pa.uuid}\n"
            "请在控制台「后台任务」页面批准或拒绝。"
        )
        self.send(self.admin_open_id, text)

    def start(self, on_message) -> None:
        if not self.app_id:
            return

        def _on_message(data: lark.im.v1.P2ImMessageReceiveV1):
            try:
                msg = data.event.message
                if msg.message_type != "text":
                    return
                text = (json.loads(msg.content or "{}") or {}).get("text") or ""
                sender_id = ""
                try:
                    sender_id = data.event.sender.sender_id.open_id or ""
                except Exception:  # noqa: BLE001
                    sender_id = ""
            except Exception as exc:  # noqa: BLE001
                print(f"[feishu] 解析消息失败: {exc}")
                return
            if text:
                on_message(text, sender_id)

        handler = lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(_on_message).build()
        self._ws = lark.ws.Client(self.app_id, self.app_secret, event_handler=handler, log_level=lark.LogLevel.ERROR)
        threading.Thread(target=self._ws.start, daemon=True).start()