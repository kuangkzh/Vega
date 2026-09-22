"""配置加载与解析。"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional


def load_config(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def resolve_prompt(raw: Any) -> str:
    """取 prompt 文案：仅支持 JSON 中直接书写的纯文本；缺省/非文本一律返回空串。

    system_prompt 与 approver.prompt 共用。
    """
    return raw if isinstance(raw, str) else ""


def build_model(config: Dict[str, Any], model_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """从 config 的 models 列表 + active_model 选出模型（返回 JSON dict）。"""
    models = config.get("models") or []
    if model_id:
        entry = next((m for m in models if m.get("id") == model_id), None)
    else:
        entry = next((m for m in models if m.get("id") == config.get("active_model")), None)
        if entry is None and models:
            entry = models[0]
    return entry