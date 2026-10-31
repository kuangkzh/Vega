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

    def stop(self) -> None:
        """停止监听（可选实现）。"""

    def notify_approval(self, pa) -> None:
        """审批通知（可选实现）。"""


def build_im_link(
    project_cfg: Optional[Dict[str, Any]], global_cfg: Optional[Dict[str, Any]]
) -> Optional["ImLink"]:
    """按 project 的 type 从全局配置取该渠道凭据，构建具体 IM 实现。

    project_cfg: `{"type": "feishu", "chat_id": "..."}`；
    global_cfg: `{"feishu": {"app_id": "...", "app_secret": "..."}}`。
    未配置 type / chat_id / 该渠道凭据时返回 None（表示未启用）。
    """
    project_cfg = project_cfg or {}
    kind = str(project_cfg.get("type") or "").strip()
    chat_id = str(project_cfg.get("chat_id") or "").strip()
    creds = dict((global_cfg or {}).get(kind) or {}) if kind else {}
    if not kind or not chat_id or not creds.get("app_id"):
        return None
    if kind == "feishu":
        from im_link.feishu import FeishuImLink

        return FeishuImLink({**creds, "type": kind, "chat_id": chat_id})
    raise ValueError(f"未知的 im_link.type: {kind}")