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


def seed_model_config(store, config: Dict[str, Any]) -> Optional[ModelConfig]:
    """把 config 里的初始模型写入 SQLite，若已有活跃模型则保留。"""
    model_cfg = config.get("model") or {}
    if not model_cfg:
        return None
    mc = ModelConfig.from_dict(model_cfg)
    existing = store.get_model_config(mc.id)
    if existing is None:
        store.upsert_model_config(mc)
        if store.get_active_model() is None:
            store.set_active_model(mc.id)
    elif store.get_active_model() is None:
        store.set_active_model(mc.id)
    return store.get_model_config(mc.id)