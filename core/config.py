"""配置加载与解析。"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional


def load_config(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_model(config: Dict[str, Any], model_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """从 config 的 models 列表 + active_model 选出模型（返回 JSON dict）。

    会把顶层全局配置项（如 `retry`）实时合并进模型配置，供运行时每次调用读取；
    模型自身字段优先（不覆盖已存在的同名字段）。
    """
    models = config.get("models") or []
    if model_id:
        entry = next((m for m in models if m.get("id") == model_id), None)
    else:
        entry = next((m for m in models if m.get("id") == config.get("active_model")), None)
        if entry is None and models:
            entry = models[0]
    if entry is None:
        return None
    merged = dict(entry)
    for key in ("retry",):
        if key not in merged and key in config:
            merged[key] = config[key]
    return merged