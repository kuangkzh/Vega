"""上下文：模板渲染、token 估算。"""
from __future__ import annotations

import datetime
from typing import Any, Dict

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