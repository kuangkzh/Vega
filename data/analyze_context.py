"""分析近 N 天对话的 context 字符占比，按 role 与 tool_name 分别统计。

统计口径：
- “近 N 天” = 会话 updated_at 落在最近 N 天内（即有新消息活动的会话）；
- 只统计进入模型 context 的消息（排除 role=error 的展示型消息）；
- 单条消息字符数 = content + reasoning_content + tool_calls 的长度之和。

用法：
    python data/analyze_context.py [db_path] [days]
默认：db_path=./data/vega_v2.db，days=7。
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from collections import defaultdict


def _ln(s):
    return len(s) if s else 0


def _num(n):
    return f"{n:,}"


def _pct(part, total):
    return f"{part / total * 100:.1f}%" if total else "-"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("db_path", nargs="?", default=None, help="SQLite 数据库路径（默认 ./data/vega_v2.db）")
    ap.add_argument("days", nargs="?", type=int, default=7, help="统计最近 N 天（默认 7）")
    args = ap.parse_args()

    db_path = args.db_path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "vega_v2.db")
    if not os.path.exists(db_path):
        raise SystemExit(f"[错误] 数据库不存在: {db_path}")

    days = max(1, args.days)
    threshold = time.time() - days * 86400

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        session_count = conn.execute(
            "SELECT COUNT(*) c FROM sessions WHERE updated_at >= ?", (threshold,)
        ).fetchone()["c"]
        rows = conn.execute(
            """
            SELECT m.role, m.name, m.content, m.reasoning_content, m.tool_calls
            FROM messages m
            JOIN sessions s ON m.session_id = s.id
            WHERE s.updated_at >= ? AND m.role != 'error'
            ORDER BY m.id
            """,
            (threshold,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        print(f"近 {days} 天内没有活跃会话。")
        return

    role_order = ["system", "user", "assistant", "tool"]
    role_stat = defaultdict(lambda: {"count": 0, "content": 0, "reasoning": 0, "tool_calls": 0, "total": 0})
    tool_stat = defaultdict(lambda: {"count": 0, "chars": 0})

    for r in rows:
        role = r["role"] or ""
        name = r["name"] or ""
        c = _ln(r["content"])
        re = _ln(r["reasoning_content"])
        tc = _ln(r["tool_calls"])
        total = c + re + tc

        st = role_stat[role]
        st["count"] += 1
        st["content"] += c
        st["reasoning"] += re
        st["tool_calls"] += tc
        st["total"] += total

        if role == "tool" and name:
            ts = tool_stat[name]
            ts["count"] += 1
            ts["chars"] += c

    grand_total = sum(s["total"] for s in role_stat.values())
    tool_total = role_stat["tool"]["content"] if "tool" in role_stat else 0

    print("=" * 78)
    print(f"上下文字符占用分析（近 {days} 天）")
    print(f"DB: {db_path}")
    print(f"活跃会话: {_num(session_count)} | 统计消息: {_num(len(rows))} | 总字符: {_num(grand_total)}")
    print("=" * 78)

    print("\n[按 role × 字段 打平（按字符数降序）]")
    print(f"{'role':<10}{'字段':<12}{'字符':>14}{'占总context比':>14}")
    flat = []
    for role in sorted(role_stat, key=lambda k: (role_order.index(k) if k in role_order else len(role_order), k)):
        for f in ("content", "reasoning", "tool_calls"):
            chars = role_stat[role][f]
            if chars:
                flat.append((role, f, chars))
    flat.sort(key=lambda x: -x[2])
    for role, f, chars in flat:
        print(f"{role:<10}{f:<12}{_num(chars):>14}{_pct(chars, grand_total):>14}")

    print("\n[按 tool_name 统计（role=tool 结果，按结果字符降序）]")
    print(f"{'tool_name':<28}{'条数':>8}{'结果字符':>14}{'占结果比':>10}{'占总context比':>14}")
    for name in sorted(tool_stat, key=lambda n: -tool_stat[n]["chars"]):
        ts = tool_stat[name]
        print(
            f"{name:<28}{_num(ts['count']):>8}{_num(ts['chars']):>14}"
            f"{_pct(ts['chars'], tool_total):>10}{_pct(ts['chars'], grand_total):>14}"
        )

    

if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
