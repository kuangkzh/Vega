"""Code 页：文件浏览 / 读写 / git。"""
from __future__ import annotations

import difflib
import os
import subprocess

from flask import Blueprint, jsonify, request

from web.auth import login_required


_MAX_FILE_SIZE = 1024 * 1024  # 编辑器打开文件大小上限：1MB


def _safe_path(cwd: str, path: str) -> str:
    """拼接并限制在 cwd 内（防目录穿越）。"""
    cwd_abs = os.path.abspath(cwd)
    full = path if os.path.isabs(path) else os.path.join(cwd_abs, path)
    full = os.path.abspath(full)
    if os.path.commonpath([cwd_abs, full]) != cwd_abs:
        raise ValueError("路径越界")
    return full


def _diff_lines(head_content: str, work_content: str) -> dict:
    """对比 HEAD 版本与工作区版本，返回逐行标记、删除行与差异段信息。

    lines: 与工作区每行一一对应（0-based），值为 None / 'added' / 'modified'。
    deleted: 工作区已不存在的删除行，anchorNewNo 指向删除处的「新文件行号」。
    hunks: 差异段区间（自动跳过 equal），anchor 为段落在新文件中可点击定位的行号。
    """
    head_lines = head_content.split("\n") if head_content else []
    work_lines = work_content.split("\n") if work_content else []
    sm = difflib.SequenceMatcher(None, head_lines, work_lines)

    lines = [None] * len(work_lines)
    deleted = []
    hunks = []
    new_idx = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            new_idx = j2
            continue
        hunks.append({
            "type": tag,
            "headStart": i1, "headEnd": i2,
            "newStart": j1, "newEnd": j2,
            "anchor": new_idx if tag == "delete" else j1,
        })
        if tag == "insert":
            lines[j1:j2] = ["added"] * (j2 - j1)
        elif tag == "delete":
            for i in range(i1, i2):
                deleted.append({"headNo": i, "anchorNewNo": new_idx, "text": head_lines[i]})
        elif tag == "replace":
            for i in range(i1, i2):
                deleted.append({"headNo": i, "anchorNewNo": new_idx, "text": head_lines[i]})
            lines[j1:j2] = ["modified"] * (j2 - j1)
        new_idx = j2
    return {"lines": lines, "deleted": deleted, "hunks": hunks}


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
        if os.path.isfile(target) and os.path.getsize(target) > _MAX_FILE_SIZE:
            return jsonify({"error": "文件过大（>1MB），暂不支持在编辑器打开"}), 413
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

    @bp.get("/api/file/diff")
    @login_required
    def api_file_diff():
        rel = request.args.get("path") or ""
        try:
            target = _safe_path(cwd, rel)
        except ValueError:
            return jsonify({"error": "路径越界"}), 400
        if not os.path.isfile(target):
            return jsonify({"error": "不是文件"}), 400
        try:
            with open(target, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": str(exc)}), 400

        # 二进制文件无「行」，不做行级 diff
        if "\0" in content:
            return jsonify({"path": os.path.relpath(target, cwd), "binary": True, "lines": [], "deleted": [], "hunks": [], "headLines": []})

        git_path = rel.replace(os.sep, "/")
        head_res = _git(["show", f"HEAD:{git_path}"])
        tracked = head_res.get("code") == 0
        head = head_res.get("out") if tracked else ""

        diff = _diff_lines(head, content)
        return jsonify({
            "path": os.path.relpath(target, cwd),
            "tracked": tracked,
            "lines": diff["lines"],
            "deleted": diff["deleted"],
            "hunks": diff["hunks"],
            "headLines": head.split("\n") if head else [],
        })

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
