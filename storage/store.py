"""SQLite 持久化（会话 / 后台任务 / 模型配置）。线程安全。"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import List, Optional

from storage.schemas import BackgroundTask, ChatSession, ModelConfig


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
                    CREATE TABLE IF NOT EXISTS sessions (
                        id TEXT PRIMARY KEY,
                        name TEXT,
                        parent_id TEXT,
                        created_at REAL,
                        status TEXT,
                        messages TEXT,
                        token_usage TEXT
                    );
                    CREATE TABLE IF NOT EXISTS tasks (
                        id TEXT PRIMARY KEY,
                        name TEXT,
                        session_id TEXT,
                        start_time TEXT,
                        end_time TEXT,
                        status TEXT,
                        pending_approval INTEGER DEFAULT 0,
                        params TEXT,
                        result TEXT,
                        logs TEXT
                    );
                    CREATE TABLE IF NOT EXISTS model_configs (
                        id TEXT PRIMARY KEY,
                        name TEXT,
                        base_url TEXT,
                        api_key TEXT,
                        model TEXT,
                        max_context INTEGER,
                        compress_ratio REAL,
                        input_price REAL,
                        output_price REAL,
                        cache_price REAL,
                        active INTEGER DEFAULT 0
                    );
                    """
                )
                conn.commit()
            finally:
                conn.close()

    # ---------- 会话 ----------
    def save_session(self, s: ChatSession) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO sessions(id, name, parent_id, created_at, status, messages, token_usage) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (s.id, s.name, s.parent_id, s.created_at, s.status,
                     json.dumps(s.messages, ensure_ascii=False),
                     json.dumps(s.token_usage)),
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

    def list_sessions(self) -> List[ChatSession]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute("SELECT * FROM sessions ORDER BY created_at DESC").fetchall()
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

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> ChatSession:
        return ChatSession(
            id=row["id"],
            name=str(row["name"] or "session"),
            parent_id=row["parent_id"],
            created_at=float(row["created_at"] or 0.0),
            status=str(row["status"] or "paused"),
            messages=json.loads(row["messages"] or "[]"),
            token_usage=json.loads(row["token_usage"] or "{}"),
        )

    # ---------- 后台任务 ----------
    def save_task(self, t: BackgroundTask) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO tasks(id, name, session_id, start_time, end_time, status, "
                    "pending_approval, params, result, logs) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (t.id, t.name, t.session_id, t.start_time, t.end_time, t.status,
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
            start_time=str(row["start_time"] or ""),
            end_time=str(row["end_time"] or ""),
            status=str(row["status"] or "running"),
            pending_approval=bool(row["pending_approval"]),
            params=json.loads(row["params"] or "{}"),
            result=json.loads(row["result"] or "{}"),
            logs=str(row["logs"] or ""),
        )

    # ---------- 模型配置 ----------
    def upsert_model_config(self, c: ModelConfig) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO model_configs(id, name, base_url, api_key, model, max_context, "
                    "compress_ratio, input_price, output_price, cache_price, active) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (c.id, c.name, c.base_url, c.api_key, c.model, c.max_context, c.compress_ratio,
                     c.input_price, c.output_price, c.cache_price, 1 if c.active else 0),
                )
                conn.commit()
            finally:
                conn.close()

    def get_model_config(self, model_id: str) -> Optional[ModelConfig]:
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute("SELECT * FROM model_configs WHERE id=?", (model_id,)).fetchone()
                return self._row_to_model(row) if row else None
            finally:
                conn.close()

    def list_model_configs(self) -> List[ModelConfig]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute("SELECT * FROM model_configs ORDER BY id").fetchall()
                return [self._row_to_model(r) for r in rows]
            finally:
                conn.close()

    def delete_model_config(self, model_id: str) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.execute("DELETE FROM model_configs WHERE id=?", (model_id,))
                conn.commit()
            finally:
                conn.close()

    def set_active_model(self, model_id: str) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.execute("UPDATE model_configs SET active=0")
                conn.execute("UPDATE model_configs SET active=1 WHERE id=?", (model_id,))
                conn.commit()
            finally:
                conn.close()

    def get_active_model(self) -> Optional[ModelConfig]:
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute("SELECT * FROM model_configs WHERE active=1 LIMIT 1").fetchone()
                return self._row_to_model(row) if row else None
            finally:
                conn.close()

    @staticmethod
    def _row_to_model(row: sqlite3.Row) -> ModelConfig:
        return ModelConfig(
            id=row["id"],
            name=str(row["name"] or row["id"]),
            base_url=str(row["base_url"] or ""),
            api_key=str(row["api_key"] or ""),
            model=str(row["model"] or ""),
            max_context=int(row["max_context"] or 128000),
            compress_ratio=float(row["compress_ratio"] or 0.8),
            input_price=float(row["input_price"] or 0.0),
            output_price=float(row["output_price"] or 0.0),
            cache_price=float(row["cache_price"] or 0.0),
            active=bool(row["active"]),
        )