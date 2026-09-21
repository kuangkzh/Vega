"""上下文：slot 渲染、token 估算、压缩摘要。"""
from __future__ import annotations

import datetime
from typing import Any, Dict, List

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def render_slots(text: str, cwd: str) -> str:
    if not text:
        return text
    now = datetime.datetime.now().strftime(TIME_FORMAT)
    return text.replace("{{time}}", now).replace("{{cwd}}", cwd)


def estimate_tokens(messages: Any) -> int:
    """轻量 token 概算（字符数/4），仅用于压缩阈值触发；精确口径以模型返回 usage 为准。"""
    if isinstance(messages, (list, tuple)):
        total = 0
        for m in messages:
            if isinstance(m, dict):
                total += len(str(m.get("content") or ""))
            else:
                total += len(str(m))
        return max(1, total // 4)
    return max(1, len(str(messages)) // 4)


def summarize(messages: List[Dict[str, Any]], model, keep_n: int = 4) -> List[Dict[str, Any]]:
    """用模型把较早的历史压缩成摘要，保留最近 keep_n 条。失败则原样返回。"""
    if not messages or len(messages) <= keep_n:
        return messages
    older = messages[:-keep_n]
    recent = messages[-keep_n:]
    text = "\n".join(f"{m.get('role', '?')}: {str(m.get('content', ''))[:2000]}" for m in older)
    try:
        resp = model.chat([
            {"role": "system", "content": "请把以下对话历史压缩成简洁摘要，保留关键任务、结论与未完成事项。"},
            {"role": "user", "content": text},
        ])
        summary = (resp.content or "").strip()
    except Exception as exc:  # noqa: BLE001
        summary = f"(摘要失败: {exc})"
    return [{"role": "system", "content": f"[对话历史摘要]\n{summary}"}] + recent