"""git worktree 插件：创建隔离工作区并把当前会话切过去；合并回主分支并把会话切回来。"""
from __future__ import annotations

import os
import subprocess

from plugins.runtime import PluginResult, register_plugin


def _abs(cwd: str, path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(cwd, path)


def _same(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _run(cwd: str, *args: str):
    """执行 git 命令，返回 (返回码, stdout+stderr)。"""
    proc = subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip("\n")


def _step(steps: list, args, out: str) -> None:
    steps.append(f"> git {' '.join(args)}\n{out}")


@register_plugin(
    description="新建 git worktree 分支，并切换 cwd 到新目录",
    parameters={
        "type": "object",
        "properties": {
            "branch": {"type": "string", "description": "新建的分支名"},
            "path": {"type": "string", "description": "worktree 目录（默认为 `../{project_name}-{branch}`）"},
            "base": {"type": "string", "description": "分支起点（默认 HEAD）"},
        },
        "required": ["branch"],
    },
)
def git_create_worktree(ctx, branch: str = "", path: str = "", base: str = ""):
    manager = ctx.manager
    if manager is None or not ctx.session_id:
        return PluginResult({}, "缺少会话上下文，无法切换 cwd")
    if not str(branch).strip():
        return PluginResult({}, "branch 不能为空")
    if not str(path).strip():
        path = f"../{os.path.basename(os.path.abspath(ctx.cwd))}-{branch}"

    steps: list = []
    rc, out = _run(ctx.cwd, "rev-parse", "--git-dir")
    _step(steps, ("rev-parse", "--git-dir"), out)
    if rc != 0:
        return PluginResult("\n\n".join(steps), "当前目录不是 git 仓库")
    git_dir = _abs(ctx.cwd, out)

    rc, out = _run(ctx.cwd, "rev-parse", "--git-common-dir")
    _step(steps, ("rev-parse", "--git-common-dir"), out)
    if rc != 0:
        return PluginResult("\n\n".join(steps), "当前目录不是 git 仓库")
    if not _same(git_dir, _abs(ctx.cwd, out)):
        return PluginResult("\n\n".join(steps), "当前会话已在 worktree 中，不能重复创建")

    worktree_path = os.path.abspath(_abs(ctx.cwd, path))
    args = ["worktree", "add", "-b", branch, worktree_path] + ([base] if str(base).strip() else [])
    rc, out = _run(ctx.cwd, *args)
    _step(steps, args, out)
    if rc != 0:
        return PluginResult("\n\n".join(steps), f"创建 worktree 失败（返回码 {rc}）")

    origin = manager.get_project(ctx.project_id) if ctx.project_id else None
    project = manager.create_project(worktree_path, im_link=(origin.im_link if origin else None))
    if manager.move_session(ctx.session_id, project.id) is None:
        return PluginResult("\n\n".join(steps), f"worktree 已创建（{worktree_path}），但会话迁移到新 project 失败")
    return PluginResult("\n\n".join(steps), f"worktree 已创建：{worktree_path}（分支 {branch}），当前工作路径 cwd 已切换")


@register_plugin(
    description="合并当前 worktree 分支回主工作树，并随后清理 worktree 与分支",
    parameters={
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "要合入的分支（留空取主工作树当前分支）"},
        },
    },
)
def git_merge_worktree(ctx, target: str = ""):
    manager = ctx.manager
    if manager is None or not ctx.session_id:
        return PluginResult({}, "缺少会话上下文，无法合并")

    steps: list = []
    rc, out = _run(ctx.cwd, "rev-parse", "--show-toplevel")
    _step(steps, ("rev-parse", "--show-toplevel"), out)
    if rc != 0:
        return PluginResult("\n\n".join(steps), "当前目录不是 git 仓库")
    worktree_root = out.strip()

    rc, out = _run(ctx.cwd, "worktree", "list", "--porcelain")
    _step(steps, ("worktree", "list", "--porcelain"), out)
    main_root = next((ln[len("worktree "):].strip() for ln in out.splitlines() if ln.startswith("worktree ")), "")
    if not main_root:
        return PluginResult("\n\n".join(steps), "无法从 git worktree list 解析主工作树路径")
    if _same(worktree_root, main_root):
        return PluginResult("\n\n".join(steps), "当前会话不在 worktree 中")

    rc, out = _run(ctx.cwd, "rev-parse", "--abbrev-ref", "HEAD")
    _step(steps, ("rev-parse", "--abbrev-ref", "HEAD"), out)
    if rc != 0 or not out.strip():
        return PluginResult("\n\n".join(steps), "无法确定当前 worktree 的分支")
    branch = out.strip()

    if not str(target).strip():
        rc, out = _run(main_root, "-C", main_root, "rev-parse", "--abbrev-ref", "HEAD")
        _step(steps, ("-C", main_root, "rev-parse", "--abbrev-ref", "HEAD"), out)
        if rc != 0 or not out.strip():
            return PluginResult("\n\n".join(steps), "无法确定主工作树的分支")
        target = out.strip()

    origin = next((p for p in manager.list_projects() if _same(p.path, main_root)), None)
    if origin is None:
        return PluginResult("\n\n".join(steps), f"找不到路径为 {main_root} 的 project，无法回迁会话")

    rc, out = _run(ctx.cwd, "status", "--porcelain")
    _step(steps, ("status", "--porcelain"), out)
    if rc != 0:
        return PluginResult("\n\n".join(steps), f"git status 失败（返回码 {rc}）")
    if out.strip():
        return PluginResult("\n\n".join(steps), "worktree 有未提交改动，请先 commit 后再合并")

    rc, out = _run(ctx.cwd, "merge", target)
    _step(steps, ("merge", target), out)
    if rc != 0:
        return PluginResult("\n\n".join(steps), f"合并 {target} 到 {branch} 失败（返回码 {rc}），冲突在 worktree 内，处理完重新调用")

    rc, out = _run(main_root, "-C", main_root, "merge", "--ff-only", branch)
    _step(steps, ("-C", main_root, "merge", "--ff-only", branch), out)
    if rc != 0:
        return PluginResult("\n\n".join(steps), f"主工作树快进合并 {branch} 失败（返回码 {rc}），请同步 {target} 后重试")

    if manager.move_session(ctx.session_id, origin.id) is None:
        return PluginResult("\n\n".join(steps), f"已合并，但会话回迁到 project {origin.id} 失败")
    if ctx.project_id and ctx.project_id != origin.id:
        # 临时 project 下可能还有别的会话（新建/fork），一并迁回，避免 delete_project 连带删除
        for s in manager.list(ctx.project_id):
            manager.move_session(s.id, origin.id)
        manager.delete_project(ctx.project_id)

    rc, out = _run(main_root, "worktree", "remove", worktree_root)
    _step(steps, ("worktree", "remove", worktree_root), out)
    if rc != 0:
        return PluginResult("\n\n".join(steps), f"已合并并迁回，但 worktree 清理失败（返回码 {rc}），残留目录 {worktree_root}")

    rc, out = _run(main_root, "-C", main_root, "branch", "-d", branch)
    _step(steps, ("-C", main_root, "branch", "-d", branch), out)
    if rc != 0:
        return PluginResult("\n\n".join(steps), f"已合并并迁回，但分支 {branch} 删除失败（返回码 {rc}）")

    return PluginResult("\n\n".join(steps), f"已合并 {branch} 到 {target}，worktree 与分支已清理，当前工作路径 cwd 已切回：{origin.path}")