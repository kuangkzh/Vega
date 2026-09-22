"""上下文：模板渲染、token 估算、压缩摘要。"""
from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from jinja2 import ChainableUndefined, Environment

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
_env = Environment(undefined=ChainableUndefined, keep_trailing_newline=True)


def render_prompt(template: str, config: Dict[str, Any], cwd: str) -> str:
    """用 Jinja2 渲染 prompt 模板，上下文为整个配置 + {{ time }} / {{ cwd }}。

    配置顶层键直接平铺（如 {{ plugins.run_shell.shell_path }}），
    也提供 {{ config.* }} 全量访问；访问不存在的键渲染为空串而非报错。
    """
    if not template:
        return ""
    ctx = dict(config or {})
    ctx["config"] = config
    ctx["time"] = datetime.datetime.now().strftime(TIME_FORMAT)
    ctx["cwd"] = cwd
    return _env.from_string(template).render(**ctx)


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


SUMMARIZE_PROMPT = "请把以下对话历史压缩成简洁摘要，保留关键任务、结论与未完成事项。"


def summarize(messages: List[Dict[str, Any]], model, config: Optional[Dict[str, Any]] = None, keep_n: int = 4) -> List[Dict[str, Any]]:
    """用模型把较早的历史压缩成摘要，保留最近 keep_n 条。失败则原样返回。

    prompt 可从 config.summarize.prompt 配置化（缺省用 SUMMARIZE_PROMPT）。
    """
    if not messages or len(messages) <= keep_n:
        return messages
    older = messages[:-keep_n]
    recent = messages[-keep_n:]
    text = "\n".join(f"{m.get('role', '?')}: {str(m.get('content', ''))[:2000]}" for m in older)
    prompt = (((config or {}).get("summarize") or {}).get("prompt") or SUMMARIZE_PROMPT)
    if not isinstance(prompt, str):
        prompt = SUMMARIZE_PROMPT
    try:
        resp = model.chat([
            {"role": "system", "content": prompt},
            {"role": "user", "content": text},
        ])
        summary = (resp.content or "").strip()
    except Exception as exc:  # noqa: BLE001
        summary = f"(摘要失败: {exc})"
    return [{"role": "system", "content": f"[对话历史摘要]\n{summary}"}] + recent