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


def _git(args, cwd):
    try:
        r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
    return {"out": r.stdout, "err": r.stderr, "code": r.returncode}


def _parse_name_status(text: str) -> list:
    """解析 `git diff-tree --name-status` 输出为变化文件列表。

    每项：status(首字母 A/M/D/R/C/T)、path(展示路径)、old_path(重命名/删除时的旧路径)。
    """
    files = []
    for raw in text.split("\n"):
        if not raw.strip("\n\r"):
            continue
        parts = raw.rstrip("\n\r").split("\t")
        if len(parts) < 2:
            continue
        code = parts[0].strip()
        status = code[0] if code else ""
        if status in ("R", "C") and len(parts) >= 3:
            old_path, new_path = parts[1], parts[2]
        elif status == "D":
            old_path, new_path = parts[1], ""
        else:
            old_path, new_path = "", parts[1]
        files.append({"status": status, "path": new_path or old_path, "old_path": old_path})
    return files


def _show_content(cwd: str, ref: str, path: str) -> str:
    """取 ref 版本中 path 的文件内容；不存在（新增/删除/根提交）时返回空串。"""
    res = _git(["show", f"{ref}:{path}"], cwd)
    return res.get("out") if res.get("code") == 0 else ""


def _worktree_content(cwd: str, path: str) -> str:
    """读取工作区中 path 的当前内容；文件不存在返回空串。"""
    try:
        target = _safe_path(cwd, path)
        if os.path.isfile(target):
            with open(target, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
    except Exception:  # noqa: BLE001
        pass
    return ""


def build_bp(manager) -> Blueprint:
    bp = Blueprint("code", __name__)

    def _cwd():
        """从 query(project) 或 body(project_id) 解析当前 project 的工作路径。"""
        pid = request.args.get("project") or ""
        if not pid:
            data = request.get_json(force=True, silent=True) or {}
            pid = data.get("project_id") or ""
        project = manager.get_project(pid) if pid else None
        if project is None:
            raise ValueError("project not found")
        return project.path

    @bp.get("/api/browse")
    @login_required
    def api_browse():
        try:
            cwd = _cwd()
        except ValueError:
            return jsonify({"error": "project not found"}), 404
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
        try:
            cwd = _cwd()
        except ValueError:
            return jsonify({"error": "project not found"}), 404
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
        try:
            cwd = _cwd()
        except ValueError:
            return jsonify({"error": "project not found"}), 404
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
        try:
            cwd = _cwd()
        except ValueError:
            return jsonify({"error": "project not found"}), 404
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
        head_res = _git(["show", f"HEAD:{git_path}"], cwd)
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
        try:
            cwd = _cwd()
        except ValueError:
            return jsonify({"error": "project not found"}), 404
        cur = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
        brs = _git(["branch", "-a"], cwd)
        return jsonify({"current": (cur.get("out") or "").strip(), "branches": brs.get("out") or ""})

    @bp.get("/api/git/log")
    @login_required
    def api_git_log():
        try:
            cwd = _cwd()
        except ValueError:
            return jsonify({"error": "project not found"}), 404
        return jsonify(_git(["log", "--graph", "--oneline", "--all", "-30"], cwd))

    @bp.get("/api/git/status")
    @login_required
    def api_git_status():
        try:
            cwd = _cwd()
        except ValueError:
            return jsonify({"error": "project not found"}), 404
        return jsonify(_git(["status", "--short"], cwd))

    @bp.get("/api/git/commit")
    @login_required
    def api_git_commit():
        try:
            cwd = _cwd()
        except ValueError:
            return jsonify({"error": "project not found"}), 404
        h = (request.args.get("hash") or "").strip()
        if not h:
            return jsonify({"error": "missing hash"}), 400
        meta_res = _git(["show", "--no-patch", "--format=%H%x09%h%x09%s%x09%an%x09%ad",
                         "--date=format:%Y-%m-%d %H:%M", h], cwd)
        meta = None
        first = (meta_res.get("out") or "").split("\n", 1)[0]
        if first:
            parts = first.split("\t")
            if len(parts) >= 5:
                meta = {"hash": parts[0], "short": parts[1], "subject": parts[2],
                        "author": parts[3], "date": parts[4]}
        ns = _git(["diff-tree", "--no-commit-id", "--name-status", "-r", h], cwd)
        files = _parse_name_status(ns.get("out") or "")
        return jsonify({"commit": meta, "files": files,
                        "err": ns.get("err") or meta_res.get("err") or ""})

    @bp.get("/api/git/commits")
    @login_required
    def api_git_commits():
        try:
            cwd = _cwd()
        except ValueError:
            return jsonify({"error": "project not found"}), 404
        log_res = _git(["log", "--all", "--format=%H%x09%h%x09%s", "-200"], cwd)
        head_res = _git(["rev-parse", "HEAD"], cwd)
        head = (head_res.get("out") or "").strip() if head_res.get("code") == 0 else ""
        return jsonify({"out": log_res.get("out") or "", "head": head})

    @bp.get("/api/git/parent")
    @login_required
    def api_git_parent():
        try:
            cwd = _cwd()
        except ValueError:
            return jsonify({"error": "project not found"}), 404
        h = (request.args.get("hash") or "").strip()
        if not h:
            return jsonify({"parent": ""})
        res = _git(["rev-parse", f"{h}^"], cwd)
        return jsonify({"parent": (res.get("out") or "").strip() if res.get("code") == 0 else ""})

    @bp.get("/api/git/diff")
    @login_required
    def api_git_diff():
        """统一对比两侧：left/right 可为 commit hash、'working'(工作区) 或 ''(空)。"""
        try:
            cwd = _cwd()
        except ValueError:
            return jsonify({"error": "project not found"}), 404
        left_ref = (request.args.get("left") or "").strip()
        right_ref = (request.args.get("right") or "").strip()
        path = (request.args.get("path") or "").strip()
        if not path:
            return jsonify({"error": "missing params"}), 400

        def content(ref):
            if ref == "working":
                return _worktree_content(cwd, path)
            if not ref:
                return ""
            return _show_content(cwd, ref, path)

        left, right = content(left_ref), content(right_ref)
        binary = "\0" in left or "\0" in right
        return jsonify({"path": path, "binary": binary,
                        "left": left if not binary else "", "right": right if not binary else ""})

    return bp
