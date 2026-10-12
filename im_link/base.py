"""IM 连接层接口：发送消息 / 接收事件 / 审批通知，以及按配置构建实现的工厂。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class ImLink(ABC):
    @abstractmethod
    def send(self, target: str, content: str) -> None:
        """发送一条文本消息。"""

    def start(self, on_message) -> None:
        """启动监听（长连接/回调）。on_message(text, sender_id)。"""

    def notify_approval(self, pa) -> None:
        """审批通知（可选实现）。"""


def build_im_link(config: Optional[Dict[str, Any]]) -> Optional["ImLink"]:
    """按配置中的 type 构建具体 IM 实现；未配置或 type 为空返回 None（表示未启用）。"""
    config = config or {}
    kind = str(config.get("type") or "").strip()
    if not kind:
        return None
    if kind == "feishu":
        from im_link.feishu import FeishuImLink

        return FeishuImLink(config)
    raise ValueError(f"未知的 im_link.type: {kind}")