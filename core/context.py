"""上下文：模板渲染、token 估算。"""
from __future__ import annotations

import datetime
from typing import Any, Dict, Optional

from jinja2 import ChainableUndefined, Environment

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
_env = Environment(undefined=ChainableUndefined, keep_trailing_newline=True)


def render_prompt(template: str, config: Dict[str, Any], cwd: str, extra: Optional[Dict[str, Any]] = None) -> str:
    """用 Jinja2 渲染 prompt 模板，上下文为整个配置 + {{ time }} / {{ cwd }}。

    配置顶层键直接平铺（如 {{ plugins.run_shell.shell_path }}），
    也提供 {{ config.* }} 全量访问；访问不存在的键渲染为空串而非报错。
    extra 为额外注入的模板槽位（覆盖与 config 顶层键同名的键）。
    """
    ctx = dict(config or {})
    ctx["config"] = config
    ctx["time"] = datetime.datetime.now().strftime(TIME_FORMAT)
    ctx["cwd"] = cwd
    if extra:
        ctx.update(extra)
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