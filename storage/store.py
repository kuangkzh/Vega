"""SQLite 持久化（会话 / 消息 / 后台任务）。线程安全。"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import List, Optional

from storage.schemas import BackgroundTask, ChatSession, Project


class Store:
    def __init__(self, db_path: str):
        self.db_path = db_path
        parent = os.path.dirname(os.path.abspath(db_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS projects (
                        id TEXT PRIMARY KEY,
                        path TEXT,
                        im_link TEXT,
                        created_at REAL,
                        updated_at REAL
                    );
                    CREATE TABLE IF NOT EXISTS sessions (
                        id TEXT PRIMARY KEY,
                        project_id TEXT,
                        created_at REAL,
                        updated_at REAL,
                        status TEXT,
                        token_usage TEXT
                    );
                    CREATE TABLE IF NOT EXISTS tasks (
                        id TEXT PRIMARY KEY,
                        name TEXT,
                        session_id TEXT,
                        project_id TEXT,
                        start_time TEXT,
                        end_time TEXT,
                        status TEXT,
                        pending_approval INTEGER DEFAULT 0,
                        params TEXT,
                        result TEXT,
                        logs TEXT
                    );
                    CREATE TABLE IF NOT EXISTS messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL DEFAULT '',
                        reasoning_content TEXT,
                        tool_calls TEXT,
                        tool_call_id TEXT,
                        name TEXT,
                        task_id TEXT,
                        created_at REAL
                    );
                    CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
                    """
                )
                conn.commit()
            finally:
                conn.close()

    def _insert_msg(self, conn: sqlite3.Connection, session_id: str, role: str, m: dict) -> None:
        conn.execute(
            "INSERT INTO messages(session_id, role, content, reasoning_content, tool_calls, tool_call_id, name, task_id, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                session_id,
                role,
                m.get("content") or "",
                m.get("reasoning_content") or "",
                json.dumps(m["tool_calls"], ensure_ascii=False) if m.get("tool_calls") else None,
                m.get("tool_call_id") or "",
                m.get("name") or "",
                m.get("task_id") or "",
                time.time(),
            ),
        )

    # ---------- 会话 ----------
    def save_session(self, s: ChatSession) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO sessions(id, project_id, created_at, updated_at, status, token_usage) "
                    "VALUES (?,?,?,?,?,?)",
                    (s.id, s.project_id, s.created_at, s.updated_at, s.status, json.dumps(s.token_usage)),
                )
                conn.commit()
            finally:
                conn.close()

    def load_session(self, session_id: str) -> Optional[ChatSession]:
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
                return self._row_to_session(row) if row else None
            finally:
                conn.close()

    def list_sessions(self, project_id: Optional[str] = None) -> List[ChatSession]:
        with self._lock:
            conn = self._conn()
            try:
                if project_id:
                    rows = conn.execute(
                        "SELECT id, project_id, created_at, updated_at, status, token_usage "
                        "FROM sessions WHERE project_id=? ORDER BY updated_at DESC",
                        (project_id,),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT id, project_id, created_at, updated_at, status, token_usage "
                        "FROM sessions ORDER BY updated_at DESC"
                    ).fetchall()
                return [self._row_to_session(r) for r in rows]
            finally:
                conn.close()

    def delete_session(self, session_id: str) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
                conn.commit()
            finally:
                conn.close()

    def delete_tasks_by_session(self, session_id: str) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.execute("DELETE FROM tasks WHERE session_id=?", (session_id,))
                conn.commit()
            finally:
                conn.close()

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> ChatSession:
        return ChatSession(
            id=row["id"],
            created_at=float(row["created_at"] or 0.0),
            updated_at=float(row["updated_at"] or 0.0),
            status=str(row["status"] or "paused"),
            token_usage=json.loads(row["token_usage"] or "{}"),
            project_id=str(row["project_id"] or ""),
        )

    # ---------- 项目 ----------
    def save_project(self, p: Project) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO projects(id, path, im_link, created_at, updated_at) "
                    "VALUES (?,?,?,?,?)",
                    (p.id, p.path, json.dumps(p.im_link, ensure_ascii=False), p.created_at, p.updated_at),
                )
                conn.commit()
            finally:
                conn.close()

    def load_project(self, project_id: str) -> Optional[Project]:
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
                return self._row_to_project(row) if row else None
            finally:
                conn.close()

    def list_projects(self) -> List[Project]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute("SELECT * FROM projects ORDER BY updated_at DESC").fetchall()
                return [self._row_to_project(r) for r in rows]
            finally:
                conn.close()

    def delete_project(self, project_id: str) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
                conn.commit()
            finally:
                conn.close()

    @staticmethod
    def _row_to_project(row: sqlite3.Row) -> Project:
        return Project(
            id=row["id"],
            path=str(row["path"] or ""),
            im_link=json.loads(row["im_link"] or "{}"),
            created_at=float(row["created_at"] or 0.0),
            updated_at=float(row["updated_at"] or 0.0),
        )

    # ---------- 消息 ----------
    def append_message(self, session_id: str, msg: dict) -> None:
        with self._lock:
            conn = self._conn()
            try:
                self._insert_msg(conn, session_id, msg.get("role") or "system", msg)
                conn.commit()
            finally:
                conn.close()

    def load_messages(self, session_id: str) -> List[dict]:
        """按写入顺序返回该会话全部消息（含 system / error，用于展示与 fork）。"""
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM messages WHERE session_id=? ORDER BY id", (session_id,)
                ).fetchall()
                return [self._row_to_msg(r) for r in rows]
            finally:
                conn.close()

    def load_context(self, session_id: str) -> List[dict]:
        """模型上下文：排除 role=error 的只展示型消息。"""
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM messages WHERE session_id=? AND role != 'error' ORDER BY id",
                    (session_id,),
                ).fetchall()
                return [self._row_to_msg(r) for r in rows]
            finally:
                conn.close()

    def first_user_message(self, session_id: str) -> Optional[str]:
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute(
                    "SELECT content FROM messages WHERE session_id=? AND role='user' ORDER BY id LIMIT 1",
                    (session_id,),
                ).fetchone()
                return str(row["content"]) if row else None
            finally:
                conn.close()

    def count_messages(self, session_id: str) -> int:
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute(
                    "SELECT COUNT(*) AS c FROM messages WHERE session_id=?", (session_id,)
                ).fetchone()
                return int(row["c"] or 0)
            finally:
                conn.close()

    def copy_messages(self, src_session_id: str, dst_session_id: str, up_to_id: Optional[int] = None) -> None:
        """fork：把源会话消息（可截断到某条消息 id）复制到目标会话。"""
        cols = "session_id, role, content, reasoning_content, tool_calls, tool_call_id, name, task_id, created_at"
        sel = "role, content, reasoning_content, tool_calls, tool_call_id, name, task_id, created_at"
        with self._lock:
            conn = self._conn()
            try:
                if up_to_id is not None:
                    conn.execute(
                        f"INSERT INTO messages({cols}) SELECT ?, {sel} FROM messages WHERE session_id=? AND id<=? ORDER BY id",
                        (dst_session_id, src_session_id, up_to_id),
                    )
                else:
                    conn.execute(
                        f"INSERT INTO messages({cols}) SELECT ?, {sel} FROM messages WHERE session_id=? ORDER BY id",
                        (dst_session_id, src_session_id),
                    )
                conn.commit()
            finally:
                conn.close()

    def delete_messages(self, session_id: str) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
                conn.commit()
            finally:
                conn.close()

    @staticmethod
    def _row_to_msg(row: sqlite3.Row) -> dict:
        m = {"id": row["id"], "role": row["role"], "content": row["content"] or ""}
        if row["reasoning_content"]:
            m["reasoning_content"] = row["reasoning_content"]
        if row["tool_calls"]:
            m["tool_calls"] = json.loads(row["tool_calls"])
        if row["tool_call_id"]:
            m["tool_call_id"] = row["tool_call_id"]
        if row["name"]:
            m["name"] = row["name"]
        if row["task_id"]:
            m["task_id"] = row["task_id"]
        return m

    # ---------- 后台任务 ----------
    def cancel_pending_approvals(self) -> int:
        """启动时清理：待审批任务依赖内存中的等待线程，重启后已失效，统一标记为 cancelled。"""
        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute(
                    "UPDATE tasks SET status='cancelled', pending_approval=0, end_time=? "
                    "WHERE status='pending_approval'",
                    (time.strftime("%Y-%m-%d %H:%M:%S"),),
                )
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()

    def save_task(self, t: BackgroundTask) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO tasks(id, name, session_id, project_id, start_time, end_time, status, "
                    "pending_approval, params, result, logs) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (t.id, t.name, t.session_id, t.project_id, t.start_time, t.end_time, t.status,
                     1 if t.pending_approval else 0,
                     json.dumps(t.params, ensure_ascii=False),
                     json.dumps(t.result, ensure_ascii=False),
                     t.logs),
                )
                conn.commit()
            finally:
                conn.close()

    def get_task(self, task_id: str) -> Optional[BackgroundTask]:
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
                return self._row_to_task(row) if row else None
            finally:
                conn.close()

    def list_tasks(self, status: Optional[str] = None) -> List[BackgroundTask]:
        with self._lock:
            conn = self._conn()
            try:
                if status:
                    rows = conn.execute(
                        "SELECT * FROM tasks WHERE status=? ORDER BY start_time DESC", (status,)
                    ).fetchall()
                else:
                    rows = conn.execute("SELECT * FROM tasks ORDER BY start_time DESC").fetchall()
                return [self._row_to_task(r) for r in rows]
            finally:
                conn.close()

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> BackgroundTask:
        return BackgroundTask(
            id=row["id"],
            name=str(row["name"] or row["id"]),
            session_id=row["session_id"],
            project_id=row["project_id"],
            start_time=str(row["start_time"] or ""),
            end_time=str(row["end_time"] or ""),
            status=str(row["status"] or "running"),
            pending_approval=bool(row["pending_approval"]),
            params=json.loads(row["params"] or "{}"),
            result=json.loads(row["result"] or "{}"),
            logs=str(row["logs"] or ""),
        )