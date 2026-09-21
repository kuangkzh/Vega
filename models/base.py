"""模型抽象接口 + 模型配置注册/管理（registry 并入）。"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

from storage.schemas import ModelConfig, TokenUsage


@dataclass
class ModelResponse:
    content: str = ""
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)


@dataclass
class StreamEvent:
    kind: str  # 'text' | 'tool_calls' | 'usage' | 'done'
    text: str = ""
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    usage: Optional[TokenUsage] = None


class BaseModel(ABC):
    def __init__(self, config: ModelConfig):
        self.config = config

    @property
    def model_id(self) -> str:
        return self.config.id

    @abstractmethod
    def chat(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None) -> ModelResponse: ...

    @abstractmethod
    def chat_stream(
        self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None
    ) -> Iterator[StreamEvent]: ...

    def get_usage(self, response: Dict[str, Any]) -> TokenUsage:
        """从返回结果的 usage 取 input/output/cache，缺失计 0（默认 OpenAI 协议口径）。"""
        u = response.get("usage") or {}
        cache = 0
        details = u.get("prompt_tokens_details") or {}
        if isinstance(details, dict):
            cache = int(details.get("cached_tokens", 0) or 0)
        if not cache:
            for key in ("prompt_cache_hit_tokens", "cache_read_input_tokens", "cached_input_tokens"):
                val = details.get(key, 0) if isinstance(details, dict) else 0
                if val:
                    cache = int(val)
                    break
        return TokenUsage(
            input=int(u.get("prompt_tokens", 0) or 0),
            output=int(u.get("completion_tokens", 0) or 0),
            cache=cache,
        )


class ModelRegistry:
    """模型配置的增删改查/切换（绑定到一个 Store）。"""

    def __init__(self, store):
        self._store = store

    def list(self) -> List[ModelConfig]:
        return self._store.list_model_configs()

    def get(self, model_id: str) -> Optional[ModelConfig]:
        return self._store.get_model_config(model_id)

    def add(self, config: ModelConfig) -> None:
        self._store.upsert_model_config(config)

    def remove(self, model_id: str) -> None:
        self._store.delete_model_config(model_id)

    def set_active(self, model_id: str) -> None:
        self._store.set_active_model(model_id)

    def active(self) -> Optional[ModelConfig]:
        return self._store.get_active_model()


def retry_call(fn, attempts: int = 3, base_delay: float = 1.0):
    """连接模型失败默认重试 attempts 次（指数退避）。"""
    last_exc: Optional[Exception] = None
    for i in range(max(1, attempts)):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if i < max(1, attempts) - 1:
                time.sleep(base_delay * (2 ** i))
    raise last_exc  # type: ignore[misc]