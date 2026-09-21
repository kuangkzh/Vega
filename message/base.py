"""消息源接口：发送消息 / 接收事件。"""
from __future__ import annotations

from abc import ABC, abstractmethod


class MessageSource(ABC):
    @abstractmethod
    def send(self, target: str, content: str) -> None:
        """发送一条文本消息。"""

    def start(self, on_message) -> None:
        """启动监听（长连接/回调）。on_message(text, sender_id)。"""

    def notify_approval(self, pa) -> None:
        """审批通知（可选实现）。"""