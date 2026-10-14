"""Code 页：文件浏览 / 读写 / git。"""
from __future__ import annotations

import os
import subprocess

from flask import Blueprint, jsonify, request

from web.auth import login_required


def _safe_path(cwd: str, path: str) -> str:
    """拼接并限制在 cwd 内（防目录穿越）。"""
    cwd_abs = os.path.abspath(cwd)
    full = path if os.path.isabs(path) else os.path.join(cwd_abs, path)
    full = os.path.abspath(full)
    if os.path.commonpath([cwd_abs, full]) != cwd_abs:
        raise ValueError("路径越界")
    return full


def build_bp(cwd: str) -> Blueprint:
    bp = Blueprint("code", __name__)

    def _git(args):
        try:
            r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}
        return {"out": r.stdout, "err": r.stderr, "code": r.returncode}

    @bp.get("/api/browse")
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

    @bp.get("/api/file")
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

    @bp.post("/api/file")
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

    @bp.get("/api/git/branches")
    @login_required
    def api_git_branches():
        cur = _git(["rev-parse", "--abbrev-ref", "HEAD"])
        brs = _git(["branch", "-a"])
        return jsonify({"current": (cur.get("out") or "").strip(), "branches": brs.get("out") or ""})

    @bp.get("/api/git/log")
    @login_required
    def api_git_log():
        return jsonify(_git(["log", "--graph", "--oneline", "--all", "-30"]))

    @bp.get("/api/git/status")
    @login_required
    def api_git_status():
        return jsonify(_git(["status", "--short"]))

    return bp
