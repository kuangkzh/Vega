"""单 token 鉴权（登录 -> session cookie）。"""
from __future__ import annotations

from functools import wraps

from flask import jsonify, session


class Auth:
    def __init__(self, token: str):
        self.token = token

    def init_app(self, app) -> None:
        app.secret_key = self.token or "vega-secret"

    def check(self, token: str) -> bool:
        return bool(self.token) and token == self.token

    def login(self, token: str) -> bool:
        if self.check(token):
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