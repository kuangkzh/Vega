"""单 token 鉴权（登录 -> session cookie）。"""
from __future__ import annotations

from datetime import timedelta
from functools import wraps

from flask import Blueprint, jsonify, render_template, request, session


class Auth:
    def __init__(self, token: str):
        self.token = token

    def init_app(self, app) -> None:
        app.secret_key = self.token or "vega-secret"
        app.permanent_session_lifetime = timedelta(days=7)

    def check(self, token: str) -> bool:
        return bool(self.token) and token == self.token

    def login(self, token: str) -> bool:
        if self.check(token):
            session.permanent = True
            session["authed"] = True
            return True
        return False

    def logout(self) -> None:
        session.pop("authed", None)

    @staticmethod
    def is_authed() -> bool:
        return session.get("authed") is True


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("authed"):
            return jsonify({"error": "unauthorized"}), 401
        return fn(*args, **kwargs)

    return wrapper


def build_bp(auth: Auth) -> Blueprint:
    """页面 + 鉴权相关路由。"""
    bp = Blueprint("auth", __name__)

    @bp.get("/login")
    def login_page():
        return render_template("login.html")

    @bp.post("/api/login")
    def api_login():
        data = request.get_json(force=True, silent=True) or {}
        if auth.login(data.get("token", "")):
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": "token 错误"}), 401

    @bp.post("/api/logout")
    def api_logout():
        auth.logout()
        return jsonify({"ok": True})

    @bp.get("/api/authed")
    def api_authed():
        return jsonify({"authed": auth.is_authed()})

    return bp