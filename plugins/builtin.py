"""内置基础插件（合并为单文件，均用 @register_plugin 注册）。"""
from __future__ import annotations

import os
import re
import shutil
import subprocess

import requests

from plugins.base import PluginResult, register_plugin


def _abs(cwd: str, path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(cwd, path)


def _shell_argv(command: str, shell_path: str = "") -> list:
    if shell_path:
        base = os.path.basename(shell_path).lower()
        if base in ("cmd", "cmd.exe"):
            return [shell_path, "/c", command]
        if base in ("powershell", "powershell.exe", "pwsh", "pwsh.exe"):
            return [shell_path, "-Command", command]
        return [shell_path, "-c", command]
    if os.name == "nt":
        if shutil.which("bash"):
            return ["bash", "-c", command]
        return ["cmd", "/c", command]
    return ["bash", "-c", command]


_SHELL_LEVEL = {"denied": "Denied", "human_review": "HumanReview", "agent_review": "AgentReview", "trusted": "Trusted"}


def _is_inside(cwd, path):
    if not cwd:
        return True
    try:
        return os.path.commonpath([os.path.abspath(cwd), os.path.abspath(path)]) == os.path.abspath(cwd)
    except Exception:  # noqa: BLE001
        return False


def file_permission(config, args, cwd):
    """文件权限校验：路径在 cwd 内取 permission.cwd，外取 permission.external。"""
    perm = config.get("permission") or {}
    if _is_inside(cwd, str(args.get("path") or "")):
        return str(perm.get("cwd") or perm.get("default") or "Denied")
    return str(perm.get("external") or perm.get("default") or "Denied")


def shell_permission(config, args, _cwd):
    """shell 权限校验：命令匹配 regex_list 四组正则，未命中回退 permission.default。"""
    regex_list = config.get("regex_list") or {}
    for key in ("denied", "human_review", "agent_review", "trusted"):
        for pat in regex_list.get(key) or []:
            try:
                if re.search(str(pat), str(args.get("command") or "")):
                    return _SHELL_LEVEL[key]
            except re.error:
                continue
    return str((config.get("permission") or {}).get("default") or "Denied")


@register_plugin(
    description="暂时没有更多任务可做时调用，暂停 Agent Loop 等待新指令",
    parameters={"type": "object", "properties": {}},
)
def wait_for_new_instruction(ctx):
    return PluginResult({"waiting": True}, "已暂停，等待新指令。")


@register_plugin(
    description="获取上一次超长结果（可按行号或关键词筛选，限制返回行数）",
    parameters={
        "type": "object",
        "properties": {
            "keyword": {"type": "string", "description": "只返回包含该关键词的行"},
            "start_line": {"type": "integer", "description": "起始行号（从1开始）"},
            "max_lines": {"type": "integer", "description": "最多返回行数（默认50）"},
        },
    },
)
def get_last_overflow_result(ctx, keyword: str = "", start_line: int = 0, max_lines: int = 50):
    text = ctx.runtime.last_overflow_result
    if not text:
        return PluginResult({}, "当前没有「上一次超长结果」可查。")
    lines = text.split("\n")
    picked = []
    for i, ln in enumerate(lines, 1):
        if keyword and keyword not in ln:
            continue
        if start_line and i < int(start_line):
            continue
        picked.append(f"{i}\t{ln}")
        if len(picked) >= int(max_lines):
            break
    return PluginResult("\n".join(picked), f"匹配 {len(picked)} 行（原始共 {len(lines)} 行）")


@register_plugin(
    description="通过 bash 执行 shell 命令（is_async=true 时后台执行，完成后自动追加结果）",
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的 shell 命令"},
            "is_async": {"type": "boolean", "description": "是否异步后台执行（默认 false）"},
            "task_name": {"type": "string", "description": "异步任务名，用于唯一标识（默认 shell）"},
        },
        "required": ["command"],
    },
    config={
        "permission": {"type": "permission-level", "keys": ("default",)},
        "regex_list": {"type": "textlist", "keys": ("denied", "human_review", "agent_review", "trusted")},
        "shell_path": {"type": "text", "label": "Shell 路径（留空自动探测）"},
    },
    resolve_permission=shell_permission,
)
def run_shell(ctx, command: str, is_async: bool = False, task_name: str = ""):
    shell_path = str(ctx.config.get("shell_path") or "")
    if is_async:
        tm = ctx.runtime.task_manager
        if tm is None:
            return PluginResult({}, "后台任务管理器不可用")

        def target():
            proc = subprocess.Popen(_shell_argv(command, shell_path), cwd=ctx.cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
            tm.track_subprocess(proc)
            out, err = proc.communicate()
            return PluginResult({"stdout": out or "", "stderr": err or "", "returncode": proc.returncode}, f"异步命令返回码 {proc.returncode}")

        try:
            task_id = tm.submit(task_name or "shell", ctx.session_id, target, params={"command": command}, tool_name="run_shell")
        except ValueError as exc:
            return PluginResult({}, str(exc))
        return PluginResult({"task_id": task_id}, f"后台任务提交成功，任务标识={task_id}。完成后结果会自动追加到对话。")

    try:
        proc = subprocess.run(_shell_argv(command, shell_path), cwd=ctx.cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    except subprocess.TimeoutExpired:
        return PluginResult({}, "命令执行超时（300s）")
    except Exception as exc:  # noqa: BLE001
        return PluginResult({}, f"命令执行失败: {exc}")
    result = {"stdout": proc.stdout or "", "stderr": proc.stderr or "", "returncode": proc.returncode}
    msg = f"返回码 {proc.returncode}"
    if proc.returncode != 0 and proc.stderr:
        msg += f"，stderr: {proc.stderr[:500]}"
    return PluginResult(result, msg)


@register_plugin(
    description="读入一个文件的全部内容（附行号）",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "文件路径（相对cwd或绝对）"}},
        "required": ["path"],
    },
    config={"permission": {"type": "permission-level", "keys": ("cwd", "external")}},
    resolve_permission=file_permission,
)
def read_file(ctx, path: str):
    p = _abs(ctx.cwd, str(path or ""))
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception as exc:  # noqa: BLE001
        return PluginResult({}, f"读取失败: {exc}")
    numbered = "\n".join(f"{i}: {ln.rstrip(chr(10))}" for i, ln in enumerate(lines, 1))
    return PluginResult(numbered, f"共 {len(lines)} 行")


@register_plugin(
    description="修改文件：按行号替换，或 search/replace 块替换",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径（相对cwd或绝对）"},
            "line": {"type": "integer", "description": "要替换的行号"},
            "content": {"type": "string", "description": "整行替换时的新内容"},
            "search": {"type": "string", "description": "search/replace 模式的查找串"},
            "replace": {"type": "string", "description": "search/replace 模式的替换串"},
        },
        "required": ["path"],
    },
    config={"permission": {"type": "permission-level", "keys": ("cwd", "external")}},
    resolve_permission=file_permission,
)
def edit_file(ctx, path: str, line: int = 0, content: str = "", search: str = "", replace: str = ""):
    p = _abs(ctx.cwd, str(path or ""))
    try:
        with open(p, "r", encoding="utf-8") as f:
            original = f.read()
    except FileNotFoundError:
        return PluginResult({}, f"文件不存在: {p}")
    except Exception as exc:  # noqa: BLE001
        return PluginResult({}, f"读取失败: {exc}")

    if search:
        if search not in original:
            return PluginResult({}, f"未找到: {search}")
        new_content = original.replace(search, replace)
    elif line:
        lines = original.split("\n")
        ln = int(line)
        if ln < 1 or ln > len(lines):
            return PluginResult({}, f"行号越界: {ln}（共{len(lines)}行）")
        lines[ln - 1] = content
        new_content = "\n".join(lines)
    else:
        return PluginResult({}, "需提供 line 或 search 参数")

    try:
        with open(p, "w", encoding="utf-8") as f:
            f.write(new_content)
    except Exception as exc:  # noqa: BLE001
        return PluginResult({}, f"写入失败: {exc}")
    return PluginResult({"changed": True, "path": p}, "已保存修改")


@register_plugin(
    description="获取指定 URL 的内容",
    parameters={
        "type": "object",
        "properties": {"url": {"type": "string", "description": "要获取的 URL"}},
        "required": ["url"],
    },
)
def web_fetch(ctx, url: str):
    try:
        resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    except Exception as exc:  # noqa: BLE001
        return PluginResult({}, f"请求失败: {exc}")
    return PluginResult({"status_code": resp.status_code, "content": resp.text}, f"HTTP {resp.status_code}")


@register_plugin(
    description="发起权限审批：填入审批 uuid、任务名、理由，审批通过后自动执行",
    parameters={
        "type": "object",
        "properties": {
            "uuid": {"type": "string", "description": "权限审批 uuid"},
            "task_name": {"type": "string", "description": "任务名"},
            "reason": {"type": "string", "description": "申请理由"},
        },
        "required": ["uuid"],
    },
)
def request_approval(ctx, uuid: str, task_name: str = "", reason: str = ""):
    return ctx.runtime.request_approval(
        str(uuid or ""),
        str(task_name or ""),
        str(reason or ""),
        ctx.session_id,
    )


@register_plugin(
    description="通过飞书发送一条消息",
    parameters={
        "type": "object",
        "properties": {
            "receive_id": {"type": "string", "description": "接收者 open_id/chat_id/email"},
            "content": {"type": "string", "description": "消息内容"},
        },
        "required": ["receive_id", "content"],
    },
)
def send_feishu(ctx, receive_id: str, content: str):
    ms = ctx.runtime.message_source
    if ms is None:
        return PluginResult({}, "消息源未启用")
    try:
        ms.send(str(receive_id or ""), str(content or ""))
        return PluginResult({"sent": True}, "已发送")
    except Exception as exc:  # noqa: BLE001
        return PluginResult({}, f"发送失败: {exc}")
