"""会话内容搜索：关键词 + 内容范围 + 项目过滤。"""
from __future__ import annotations

import os

from flask import Blueprint, jsonify, request

from web.auth import login_required

SCOPES = ("user", "reasoning", "tool_call", "tool_result", "reply")
LIMIT_MAX = 200
_PAD = 60


def _snippet(text: str, keyword: str) -> str:
    """压缩空白，截取关键词前后各 _PAD 字的片段。"""
    flat = " ".join(text.split())
    i = flat.lower().find(keyword.lower())
    if i < 0:
        return flat[:_PAD * 2]
    start = max(0, i - _PAD)
    end = min(len(flat), i + len(keyword) + _PAD)
    return ("…" if start else "") + flat[start:end] + ("…" if end < len(flat) else "")


def _hit(row: dict, keyword: str, scopes: list) -> tuple:
    """确定命中范围与摘要文本；同一消息命中多个范围时按展示顺序取第一个。"""
    kw = keyword.lower()
    content = row["content"] or ""
    reasoning = row["reasoning_content"] or ""
    tool_calls = row["tool_calls"] or ""
    role = row["role"]
    if "user" in scopes and role == "user" and kw in content.lower():
        return "user", content
    if "reasoning" in scopes and kw in reasoning.lower():
        return "reasoning", reasoning
    if "tool_call" in scopes and kw in tool_calls.lower():
        return "tool_call", tool_calls
    if "tool_result" in scopes and role in ("tool", "async-tool") and kw in content.lower():
        return "tool_result", content
    if "reply" in scopes and role == "assistant" and kw in content.lower():
        return "reply", content
    return None, ""


def build_bp(manager) -> Blueprint:
    bp = Blueprint("search", __name__)

    @bp.get("/api/search")
    @login_required
    def api_search():
        keyword = (request.args.get("q") or "").strip()
        scopes = [s for s in (request.args.get("scopes") or "").split(",") if s in SCOPES]
        if not keyword or not scopes:
            return jsonify({"results": [], "truncated": False})
        limit = min(int(request.args.get("limit") or 100), LIMIT_MAX)
        rows = manager.store.search_messages(keyword, scopes, request.args.get("project_id") or None, limit)
        results = []
        for row in rows:
            kind, text = _hit(row, keyword, scopes)
            if kind is None:
                continue
            path = row["project_path"] or ""
            results.append({
                "id": row["id"],
                "session_id": row["session_id"],
                "project_id": row["project_id"] or "",
                "project_name": os.path.basename(path.rstrip("/\\")) or path,
                "title": (row["title"] or "").strip(),
                "kind": kind,
                "snippet": _snippet(text, keyword),
                "created_at": row["created_at"],
            })
        return jsonify({"results": results, "truncated": len(rows) >= limit})

    return bp