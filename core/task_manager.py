"""后台任务管理：唯一标识、线程执行、状态持久化、退出清理。"""
from __future__ import annotations

import atexit
import datetime
import threading
from typing import Any, Callable, Dict, Optional

from storage.schemas import BackgroundTask


def _now_str() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class TaskManager:
    def __init__(self, store):
        self.store = store
        self.on_done: Optional[Callable[..., None]] = None
        self._lock = threading.RLock()
        self._subprocs: list = []
        self._registered = False

    def _now_compact(self) -> str:
        return datetime.datetime.now().strftime("%Y%m%d%H%M%S")

    def _make_id(self, name: str) -> str:
        base = f"{name}-{self._now_compact()}"
        if self.store.get_task(base):
            raise ValueError(f"任务标识不唯一: {base}")
        return base

    # ---------- 子进程跟踪（退出清理） ----------
    def track_subprocess(self, proc) -> None:
        with self._lock:
            self._subprocs.append(proc)
        self.register_cleanup()

    def register_cleanup(self) -> None:
        if not self._registered:
            atexit.register(self.shutdown)
            self._registered = True

    def shutdown(self) -> None:
        with self._lock:
            procs = list(self._subprocs)
            self._subprocs = []
        for p in procs:
            try:
                p.terminate()
            except Exception:  # noqa: BLE001
                pass
        for p in procs:
            try:
                p.wait(timeout=2)
            except Exception:  # noqa: BLE001
                try:
                    p.kill()
                except Exception:  # noqa: BLE001
                    pass

    # ---------- 任务 ----------
    def submit(self, name: str, session_id: Optional[str], target: Callable, params: Optional[Dict[str, Any]] = None, tool_name: Optional[str] = None) -> str:
        task_id = self._make_id(name)
        t = BackgroundTask(id=task_id, name=name, session_id=session_id, start_time=_now_str(), status="running", params=params or {})
        self.store.save_task(t)
        self.register_cleanup()
        threading.Thread(target=self._execute, args=(task_id, session_id, target, tool_name), daemon=True).start()
        return task_id

    def submit_pending(self, name: str, session_id: Optional[str], params: Optional[Dict[str, Any]] = None) -> str:
        task_id = self._make_id(name)
        t = BackgroundTask(id=task_id, name=name, session_id=session_id, start_time=_now_str(), status="pending_approval", pending_approval=True, params=params or {})
        self.store.save_task(t)
        self.register_cleanup()
        return task_id

    def run_pending(self, task_id: str, session_id: Optional[str], target: Callable, tool_name: Optional[str] = None) -> None:
        if task_id:
            t = self.store.get_task(task_id)
            if t:
                t.status = "running"
                t.pending_approval = False
                t.end_time = ""
                self.store.save_task(t)
        else:
            task_id = self._make_id(tool_name or "task")
        threading.Thread(target=self._execute, args=(task_id, session_id, target, tool_name), daemon=True).start()

    def finish_task(self, task_id: Optional[str], status: str, result_dict: Dict[str, Any]) -> None:
        if not task_id:
            return
        t = self.store.get_task(task_id)
        if t:
            t.status = status
            t.end_time = _now_str()
            t.result = result_dict
            self.store.save_task(t)

    def _execute(self, task_id: str, session_id: Optional[str], target: Callable, tool_name: Optional[str]) -> None:
        try:
            out = target()
            result, msg = self._normalize(out)
            t = self.store.get_task(task_id) or BackgroundTask(id=task_id, name=tool_name or "task", session_id=session_id)
            t.status = "done"
            t.end_time = _now_str()
            t.result = {"result": result, "msg": msg}
            self.store.save_task(t)
            if self.on_done and session_id and tool_name:
                try:
                    self.on_done(session_id, tool_name, self.store.get_task(task_id) or t)
                except Exception as exc:  # noqa: BLE001
                    print(f"[task] on_done 失败: {exc}")
        except Exception as exc:  # noqa: BLE001
            t = self.store.get_task(task_id) or BackgroundTask(id=task_id, name=tool_name or "task", session_id=session_id)
            t.status = "error"
            t.end_time = _now_str()
            t.logs = f"{type(exc).__name__}: {exc}"
            self.store.save_task(t)
            if self.on_done and session_id and tool_name:
                try:
                    t.result = t.result or {"result": {}, "msg": ""}
                    t.result["msg"] = t.result.get("msg") or f"后台任务执行失败: {exc}"
                    self.on_done(session_id, tool_name, t)
                except Exception:  # noqa: BLE001
                    pass

    @staticmethod
    def _normalize(out: Any):
        if hasattr(out, "result") and hasattr(out, "msg"):
            return out.result, out.msg
        if isinstance(out, tuple) and len(out) == 2:
            return out[0], out[1]
        return out, ""