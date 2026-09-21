"""Flask 控制台：JSON API + SSE + 页面渲染。"""
from __future__ import annotations

import json
import os
import queue
import subprocess
from typing import Any, Dict, List

from flask import Flask, Response, jsonify, render_template, request

from storage.schemas import ModelConfig
from web.auth import Auth, login_required


def _safe_path(cwd: str, path: str) -> str:
    """拼接并限制在 cwd 内（防目录穿越）。"""
    cwd_abs = os.path.abspath(cwd)
    full = path if os.path.isabs(path) else os.path.join(cwd_abs, path)
    full = os.path.abspath(full)
    if os.path.commonpath([cwd_abs, full]) != cwd_abs:
        raise ValueError("路径越界")
    return full


def create_app(config, store, manager, runtime, task_manager, registry, cwd, feishu=None, config_path=None):
    app = Flask(__name__)
    auth = Auth((config.get("web") or {}).get("token"))
    auth.init_app(app)

    # ---------- 页面 ----------
    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/login")
    def login_page():
        return render_template("login.html")

    # ---------- 鉴权 ----------
    @app.post("/api/login")
    def api_login():
        data = request.get_json(force=True, silent=True) or {}
        if auth.login(data.get("token", "")):
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": "token 错误"}), 401

    @app.post("/api/logout")
    def api_logout():
        auth.logout()
        return jsonify({"ok": True})

    @app.get("/api/authed")
    def api_authed():
        return jsonify({"authed": auth.is_authed()})

    # ---------- 会话 ----------
    @app.get("/api/sessions")
    @login_required
    def api_sessions():
        return jsonify([s.to_dict() for s in manager.list()])

    @app.post("/api/sessions")
    @login_required
    def api_create_session():
        data = request.get_json(force=True, silent=True) or {}
        s = manager.create(name=data.get("name") or "session")
        return jsonify(s.to_dict())

    @app.post("/api/sessions/<sid>/fork")
    @login_required
    def api_fork(sid):
        s = manager.fork(sid)
        if s is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(s.to_dict())

    @app.delete("/api/sessions/<sid>")
    @login_required
    def api_delete_session(sid):
        manager.delete(sid)
        return jsonify({"ok": True})

    @app.get("/api/sessions/<sid>/messages")
    @login_required
    def api_messages(sid):
        s = manager.get(sid)
        if s is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(s.messages)

    @app.post("/api/sessions/<sid>/message")
    @login_required
    def api_send(sid):
        s = manager.get(sid)
        if s is None:
            return jsonify({"error": "not found"}), 404
        data = request.get_json(force=True, silent=True) or {}
        text = data.get("content") or data.get("text") or ""
        if not text:
            return jsonify({"error": "empty content"}), 400
        s.send(text)
        return jsonify({"ok": True})

    @app.post("/api/sessions/<sid>/resume")
    @login_required
    def api_resume(sid):
        s = manager.get(sid)
        if s is None:
            return jsonify({"error": "not found"}), 404
        s.resume()
        return jsonify({"ok": True})

    @app.post("/api/sessions/<sid>/pause")
    @login_required
    def api_pause(sid):
        s = manager.get(sid)
        if s is None:
            return jsonify({"error": "not found"}), 404
        s.pause()
        return jsonify({"ok": True})

    @app.get("/api/sessions/<sid>/stream")
    def api_stream(sid):
        if not auth.is_authed():
            return jsonify({"error": "unauthorized"}), 401
        s = manager.get(sid)
        if s is None:
            return jsonify({"error": "not found"}), 404
        return _sse_stream(s)

    # ---------- 后台任务 ----------
    @app.get("/api/tasks")
    @login_required
    def api_tasks():
        status = request.args.get("status")
        name = request.args.get("name")
        tasks = [t.to_dict() for t in task_manager.store.list_tasks(status)]
        if name:
            tasks = [t for t in tasks if name in (t.get("name") or "")]
        return jsonify(tasks)

    @app.post("/api/tasks/<tid>/approve")
    @login_required
    def api_task_approve(tid):
        data = request.get_json(force=True, silent=True) or {}
        approved = bool(data.get("approved"))
        reason = data.get("reason") or ""
        task = task_manager.store.get_task(tid)
        if task is None:
            return jsonify({"error": "not found"}), 404
        uuid_ = (task.params or {}).get("uuid") or data.get("uuid")
        if not uuid_:
            return jsonify({"error": "缺少 uuid"}), 400
        res = runtime.approve(uuid_, approved, reason)
        return jsonify(res.to_dict())

    @app.get("/api/tasks/<tid>/logs")
    @login_required
    def api_task_logs(tid):
        task = task_manager.store.get_task(tid)
        if task is None:
            return jsonify({"error": "not found"}), 404
        return jsonify({
            "logs": task.logs, "result": task.result, "params": task.params,
            "status": task.status, "start_time": task.start_time, "end_time": task.end_time,
        })

    # ---------- 模型配置 ----------
    @app.get("/api/models")
    @login_required
    def api_models():
        models = registry.list()
        active = registry.active()
        return jsonify({"models": [m.to_dict() for m in models], "active": active.id if active else None})

    @app.post("/api/models")
    @login_required
    def api_add_model():
        data = request.get_json(force=True, silent=True) or {}
        mc = ModelConfig.from_dict(data)
        if not mc.id:
            return jsonify({"error": "缺少 id"}), 400
        registry.add(mc)
        return jsonify(mc.to_dict())

    @app.put("/api/models/<mid>")
    @login_required
    def api_update_model(mid):
        data = request.get_json(force=True, silent=True) or {}
        data["id"] = mid
        mc = ModelConfig.from_dict(data)
        registry.add(mc)
        return jsonify(mc.to_dict())

    @app.delete("/api/models/<mid>")
    @login_required
    def api_delete_model(mid):
        registry.remove(mid)
        return jsonify({"ok": True})

    @app.post("/api/models/<mid>/activate")
    @login_required
    def api_activate_model(mid):
        registry.set_active(mid)
        return jsonify({"ok": True})

    # ---------- 设置 ----------
    @app.get("/api/config")
    @login_required
    def api_config():
        cfg = json.loads(json.dumps(config, ensure_ascii=False))
        m = cfg.get("model") or {}
        if m.get("api_key"):
            m["api_key"] = "******"
        f = cfg.get("feishu") or {}
        if f.get("app_secret"):
            f["app_secret"] = "******"
        return jsonify(cfg)

    @app.post("/api/config")
    @login_required
    def api_save_config():
        data = request.get_json(force=True, silent=True) or {}
        if isinstance(data.get("model"), dict) and data["model"].get("api_key") == "******":
            data["model"].pop("api_key", None)
        if isinstance(data.get("feishu"), dict) and data["feishu"].get("app_secret") == "******":
            data["feishu"].pop("app_secret", None)
        allowed = ("system_prompt", "permissions", "approver", "retry", "feishu")

        def merge(a, b):
            for k, v in b.items():
                if k not in allowed:
                    continue
                if isinstance(v, dict) and isinstance(a.get(k), dict):
                    merge(a[k], v)
                else:
                    a[k] = v

        merge(runtime.config, data)
        merge(config, data)
        if config_path:
            try:
                with open(config_path, "w", encoding="utf-8") as fh:
                    json.dump(config, fh, ensure_ascii=False, indent=2)
            except Exception as exc:  # noqa: BLE001
                return jsonify({"error": f"写回失败: {exc}"}), 500
        return jsonify({"ok": True})

    # ---------- Code：文件浏览 ----------
    @app.get("/api/browse")
    @login_required
    def api_browse():
        rel = request.args.get("path") or "."
        try:
            target = _safe_path(cwd, rel)
        except ValueError:
            return jsonify({"error": "路径越界"}), 400
        if not os.path.isdir(target):
            return jsonify({"error": "不是目录"}), 400
        try:
            entries = [{"name": n, "is_dir": os.path.isdir(os.path.join(target, n))}
                       for n in sorted(os.listdir(target))]
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": str(exc)}), 400
        return jsonify({"path": os.path.relpath(target, cwd), "entries": entries})

    @app.get("/api/file")
    @login_required
    def api_read_file():
        rel = request.args.get("path") or ""
        try:
            target = _safe_path(cwd, rel)
        except ValueError:
            return jsonify({"error": "路径越界"}), 400
        try:
            with open(target, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": str(exc)}), 400
        return jsonify({"path": os.path.relpath(target, cwd), "content": content})

    @app.post("/api/file")
    @login_required
    def api_write_file():
        data = request.get_json(force=True, silent=True) or {}
        rel = data.get("path") or ""
        try:
            target = _safe_path(cwd, rel)
        except ValueError:
            return jsonify({"error": "路径越界"}), 400
        try:
            with open(target, "w", encoding="utf-8") as f:
                f.write(data.get("content") or "")
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": str(exc)}), 400
        return jsonify({"ok": True})

    # ---------- Git ----------
    def _git(args):
        try:
            r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}
        return {"out": r.stdout, "err": r.stderr, "code": r.returncode}

    @app.get("/api/git/branches")
    @login_required
    def api_git_branches():
        cur = _git(["rev-parse", "--abbrev-ref", "HEAD"])
        brs = _git(["branch", "-a"])
        return jsonify({"current": (cur.get("out") or "").strip(), "branches": brs.get("out") or ""})

    @app.get("/api/git/log")
    @login_required
    def api_git_log():
        return jsonify(_git(["log", "--graph", "--oneline", "--all", "-30"]))

    @app.get("/api/git/status")
    @login_required
    def api_git_status():
        return jsonify(_git(["status", "--short"]))

    return app


def _sse_stream(sess) -> Response:
    q = sess.subscribe()

    def gen():
        try:
            yield "data: " + json.dumps({"type": "hello"}, ensure_ascii=False) + "\n\n"
            while True:
                try:
                    ev = q.get(timeout=15)
                except queue.Empty:
                    yield ": keepalive\n\n"
                    continue
                yield "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"
        finally:
            sess.unsubscribe(q)

    return Response(gen(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache"})