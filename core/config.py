"""配置加载与解析。"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from storage.schemas import ModelConfig


def load_config(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def resolve_system_prompt(raw: Any, cwd: str) -> str:
    """system_prompt 允许直接给文本或 {"file": "...md"}。"""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        fp = raw.get("file")
        if fp:
            p = fp if os.path.isabs(fp) else os.path.join(cwd, fp)
            with open(p, encoding="utf-8") as f:
                return f.read()
    return ""


def build_model(config: Dict[str, Any], model_id: Optional[str] = None) -> Optional[ModelConfig]:
    """从 config 的 models 列表 + active_model 选出模型。"""
    models = config.get("models") or []
    if model_id:
        entry = next((m for m in models if m.get("id") == model_id), None)
    else:
        entry = next((m for m in models if m.get("id") == config.get("active_model")), None)
        if entry is None and models:
            entry = models[0]
    return ModelConfig.from_dict(entry) if entry else None