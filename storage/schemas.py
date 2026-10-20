"""数据模型（dataclass）：项目、会话、后台任务、token 用量。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional


@dataclass
class Project:
    id: str
    path: str = ""
    im_link: Dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["im_link"] = dict(self.im_link)
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Project":
        d = d or {}
        return cls(
            id=str(d["id"]),
            path=str(d.get("path") or ""),
            im_link=dict(d.get("im_link") or {}),
            created_at=float(d.get("created_at") or 0.0),
            updated_at=float(d.get("updated_at") or 0.0),
        )


@dataclass
class TokenUsage:
    input: int = 0
    output: int = 0
    cache: int = 0

    def total(self) -> int:
        return self.input + self.output + self.cache

    def to_dict(self) -> Dict[str, int]:
        return {"input": self.input, "output": self.output, "cache": self.cache}

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "TokenUsage":
        d = d or {}
        return cls(
            input=int(d.get("input", 0) or 0),
            output=int(d.get("output", 0) or 0),
            cache=int(d.get("cache", 0) or 0),
        )


@dataclass
class ChatSession:
    id: str
    created_at: float = 0.0
    updated_at: float = 0.0
    status: str = "paused"  # paused | running | complete | error
    token_usage: Dict[str, int] = field(default_factory=lambda: {"input": 0, "output": 0, "cache": 0})
    project_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["token_usage"] = dict(self.token_usage)
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ChatSession":
        d = d or {}
        return cls(
            id=str(d["id"]),
            created_at=float(d.get("created_at") or 0.0),
            updated_at=float(d.get("updated_at") or 0.0),
            status=str(d.get("status") or "paused"),
            token_usage=dict(d.get("token_usage") or {"input": 0, "output": 0, "cache": 0}),
            project_id=str(d.get("project_id") or ""),
        )


@dataclass
class BackgroundTask:
    id: str  # 唯一标识 = 任务名-执行时间
    name: str
    session_id: Optional[str] = None
    project_id: Optional[str] = None
    start_time: str = ""
    end_time: str = ""
    status: str = "running"  # running | done | error | pending_approval | cancelled
    pending_approval: bool = False
    params: Dict[str, Any] = field(default_factory=dict)
    result: Dict[str, Any] = field(default_factory=dict)
    logs: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "BackgroundTask":
        d = d or {}
        return cls(
            id=str(d["id"]),
            name=str(d.get("name") or d["id"]),
            session_id=d.get("session_id"),
            project_id=d.get("project_id"),
            start_time=str(d.get("start_time") or ""),
            end_time=str(d.get("end_time") or ""),
            status=str(d.get("status") or "running"),
            pending_approval=bool(d.get("pending_approval", False)),
            params=dict(d.get("params") or {}),
            result=dict(d.get("result") or {}),
            logs=str(d.get("logs") or ""),
        )