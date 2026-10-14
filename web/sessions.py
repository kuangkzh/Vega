"""会话相关：JSON API + SSE 流。"""
from __future__ import annotations

import json
import queue

from flask import Blueprint, Response, jsonify, request

from web.auth import login_required


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


def _global_sse_stream(manager) -> Response:
    q = manager.subscribe_global()

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
            manager.unsubscribe_global(q)

    return Response(gen(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache"})


def build_bp(manager, auth) -> Blueprint:
    bp = Blueprint("sessions", __name__)

    @bp.get("/api/sessions")
    @login_required
    def api_sessions():
        bound = manager.get_im_session_id()
        return jsonify([{**s.to_dict(), "im_bound": s.id == bound} for s in manager.list()])

    @bp.post("/api/sessions")
    @login_required
    def api_create_session():
        s = manager.create()
        return jsonify(s.to_dict())

    @bp.post("/api/sessions/<sid>/fork")
    @login_required
    def api_fork(sid):
        data = request.get_json(force=True, silent=True) or {}
        message_index = data.get("message_index")
        s = manager.fork(sid, message_index)
        if s is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(s.to_dict())

    @bp.delete("/api/sessions/<sid>")
    @login_required
    def api_delete_session(sid):
        manager.delete(sid)
        return jsonify({"ok": True})

    @bp.get("/api/sessions/<sid>/messages")
    @login_required
    def api_messages(sid):
        s = manager.get(sid)
        if s is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(s.store.load_messages(s.id))

    @bp.get("/api/sessions/<sid>/pending")
    @login_required
    def api_pending(sid):
        s = manager.get(sid)
        if s is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(s.pending_items())

    @bp.post("/api/sessions/<sid>/message")
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

    @bp.post("/api/sessions/<sid>/resume")
    @login_required
    def api_resume(sid):
        s = manager.get(sid)
        if s is None:
            return jsonify({"error": "not found"}), 404
        s.resume()
        return jsonify({"ok": True})

    @bp.post("/api/sessions/<sid>/pause")
    @login_required
    def api_pause(sid):
        s = manager.get(sid)
        if s is None:
            return jsonify({"error": "not found"}), 404
        s.pause()
        return jsonify({"ok": True})

    @bp.post("/api/sessions/<sid>/im/toggle")
    @login_required
    def api_im_toggle(sid):
        if manager.get(sid) is None:
            return jsonify({"error": "not found"}), 404
        bound = manager.toggle_im(sid)
        return jsonify({"im_bound": bound, "im_session_id": manager.get_im_session_id()})

    @bp.get("/api/sessions/<sid>/stream")
    def api_stream(sid):
        if not auth.is_authed():
            return jsonify({"error": "unauthorized"}), 401
        s = manager.get(sid)
        if s is None:
            return jsonify({"error": "not found"}), 404
        return _sse_stream(s)

    @bp.get("/api/events")
    def api_events():
        if not auth.is_authed():
            return jsonify({"error": "unauthorized"}), 401
        return _global_sse_stream(manager)

    return bp
