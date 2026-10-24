"""Flask 控制台：装配层 + 页面渲染 + 任务/设置的 JSON API。"""
from __future__ import annotations

import gzip
import json
import os

from flask import Flask, jsonify, render_template, request

from web.auth import Auth, build_bp as build_auth_bp, login_required
from web.code import build_bp as build_code_bp
from web.sessions import build_bp as build_sessions_bp


_GZIP_MIN_SIZE = 10 * 1024


def create_app(config, manager, runtime, task_manager, config_path=None):
    app = Flask(__name__)
    # 全局 JSON 输出不转义非 ASCII，保证中文、git 输出等内容可读
    app.json.ensure_ascii = False

    @app.after_request
    def _gzip_response(response):
        # 大响应体（如会话历史）启用 gzip；SSE 流与已编码响应跳过
        if response.direct_passthrough or response.mimetype == "text/event-stream":
            return response
        if "gzip" not in (request.headers.get("Accept-Encoding") or "").lower():
            return response
        if response.headers.get("Content-Encoding") or response.status_code in (204, 304):
            return response
        data = response.get_data()
        if len(data) < _GZIP_MIN_SIZE:
            return response
        compressed = gzip.compress(data)
        response.set_data(compressed)
        response.headers["Content-Encoding"] = "gzip"
        response.headers["Content-Length"] = str(len(compressed))
        response.headers.add("Vary", "Accept-Encoding")
        return response

    auth = Auth((config.get("web") or {}).get("token"))
    auth.init_app(app)

    # ---------- 页面 ----------
    @app.get("/")
    def index():
        return render_template("index.html")

    def _project_name(project_id):
        p = manager.get_project(project_id or "")
        if p is None:
            return project_id or ""
        return os.path.basename(p.path.rstrip("/\\")) or p.path

    @app.get("/code")
    def code_page():
        return render_template("code_window.html", project_name=_project_name(request.args.get("project")))

    @app.get("/git")
    def git_page():
        return render_template("git_window.html", project_name=_project_name(request.args.get("project")))

    @app.get("/git/diff")
    def git_diff_page():
        return render_template(
            "git_diff_window.html",
            project_name=_project_name(request.args.get("project")),
            file_path=request.args.get("path") or "",
        )

    # ---------- 蓝图分区 ----------
    app.register_blueprint(build_auth_bp(auth))
    app.register_blueprint(build_sessions_bp(manager, auth))
    app.register_blueprint(build_code_bp(manager))

    # ---------- 项目 ----------
    def _project_payload(p):
        sessions = []
        for s in manager.list(p.id):
            d = {**s.to_dict(), "im_bound": s.id == manager.get_im_session_id(p.id)}
            sessions.append(d)
        return {**p.to_dict(), "name": os.path.basename(p.path.rstrip("/\\")) or p.path, "sessions": sessions}

    @app.get("/api/projects")
    @login_required
    def api_projects():
        return jsonify([_project_payload(p) for p in manager.list_projects()])

    @app.post("/api/projects")
    @login_required
    def api_create_project():
        data = request.get_json(force=True, silent=True) or {}
        path = (data.get("path") or "").strip()
        if not path:
            return jsonify({"error": "缺少 path"}), 400
        p = manager.create_project(path, data.get("im_link"))
        return jsonify(_project_payload(p))

    @app.put("/api/projects/<pid>")
    @login_required
    def api_update_project(pid):
        data = request.get_json(force=True, silent=True) or {}
        p = manager.update_project(pid, path=data.get("path"), im_link=data.get("im_link"))
        if p is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(_project_payload(p))

    @app.delete("/api/projects/<pid>")
    @login_required
    def api_delete_project(pid):
        manager.delete_project(pid)
        return jsonify({"ok": True})

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
            "id": task.id, "name": task.name,
            "logs": task.logs, "result": task.result, "params": task.params,
            "status": task.status, "start_time": task.start_time, "end_time": task.end_time,
        })

    # ---------- 模型配置（存于 config JSON）----------
    def _persist_config():
        if config_path:
            with open(config_path, "w", encoding="utf-8") as fh:
                json.dump(config, fh, ensure_ascii=False, indent=2)

    @app.get("/api/models")
    @login_required
    def api_models():
        return jsonify({"models": config.get("models") or [], "active": config.get("active_model")})

    @app.post("/api/models")
    @login_required
    def api_add_model():
        data = request.get_json(force=True, silent=True) or {}
        mid = data.get("id")
        if not mid:
            return jsonify({"error": "缺少 id"}), 400
        models = config.setdefault("models", [])
        for i, m in enumerate(models):
            if m.get("id") == mid:
                models[i] = data
                break
        else:
            models.append(data)
        if not config.get("active_model"):
            config["active_model"] = mid
        _persist_config()
        return jsonify(data)

    @app.put("/api/models/<mid>")
    @login_required
    def api_update_model(mid):
        data = request.get_json(force=True, silent=True) or {}
        data["id"] = mid
        models = config.setdefault("models", [])
        for i, m in enumerate(models):
            if m.get("id") == mid:
                models[i] = data
                break
        else:
            models.append(data)
        _persist_config()
        return jsonify(data)

    @app.delete("/api/models/<mid>")
    @login_required
    def api_delete_model(mid):
        config["models"] = [m for m in (config.get("models") or []) if m.get("id") != mid]
        if config.get("active_model") == mid:
            config["active_model"] = config["models"][0]["id"] if config["models"] else None
        _persist_config()
        return jsonify({"ok": True})

    @app.post("/api/models/<mid>/activate")
    @login_required
    def api_activate_model(mid):
        if not any(m.get("id") == mid for m in (config.get("models") or [])):
            return jsonify({"error": "not found"}), 404
        config["active_model"] = mid
        _persist_config()
        return jsonify({"ok": True})

    @app.get("/api/plugins")
    @login_required
    def api_plugins():
        plugins = [
            {
                "name": p.name,
                "description": p.description,
                "config": p.config,
                "value": runtime.plugin_config(p.name),
            }
            for p in runtime.plugins.values()
        ]
        return jsonify(plugins)

    # ---------- 设置 ----------
    @app.get("/api/config")
    @login_required
    def api_config():
        cfg = json.loads(json.dumps(config, ensure_ascii=False))
        for m in cfg.get("models") or []:
            if isinstance(m, dict) and m.get("api_key"):
                m["api_key"] = "******"
        return jsonify(cfg)

    @app.post("/api/config")
    @login_required
    def api_save_config():
        data = request.get_json(force=True, silent=True) or {}
        for m in data.get("models") or []:
            if isinstance(m, dict) and m.get("api_key") == "******":
                m.pop("api_key", None)
        allowed = ("system_prompt", "plugins", "approver", "retry", "max_rounds", "summarizer", "include_reasoning_content")

        def deep_merge(a, b):
            for k, v in b.items():
                if isinstance(v, dict) and isinstance(a.get(k), dict):
                    deep_merge(a[k], v)
                else:
                    a[k] = v

        # 只允许白名单内的顶层字段，嵌套层级做纯深度合并
        filtered = {k: data[k] for k in allowed if k in data}
        deep_merge(runtime.config, filtered)
        deep_merge(config, filtered)
        if config_path:
            try:
                with open(config_path, "w", encoding="utf-8") as fh:
                    json.dump(config, fh, ensure_ascii=False, indent=2)
            except Exception as exc:  # noqa: BLE001
                return jsonify({"error": f"写回失败: {exc}"}), 500
        return jsonify({"ok": True})

    return app
