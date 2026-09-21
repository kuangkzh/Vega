"""内置基础插件（合并为单文件，均用 @register_plugin 注册）。"""
from __future__ import annotations

import os
import shutil
import subprocess

import requests

from plugins.base import PluginResult, register_plugin, shell_permission


def _abs(cwd: str, path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(cwd, path)


def _shell_argv(command: str) -> list:
    if os.name == "nt":
        if shutil.which("bash"):
            return ["bash", "-c", command]
        return ["cmd", "/c", command]
    return ["bash", "-c", command]


@register_plugin
def wait_for_new_instruction(ctx):
    """暂时没有更多任务可做时调用，暂停 Agent Loop 等待新指令"""
    return PluginResult({"waiting": True}, "已暂停，等待新指令。")


@register_plugin
def get_last_overflow_result(ctx, keyword: str = "", start_line: int = 0, max_lines: int = 50):
    """获取上一次超长结果（可按行号或关键词筛选，限制返回行数）

    Args:
        keyword (str): 只返回包含该关键词的行
        start_line (int): 起始行号（从1开始）
        max_lines (int): 最多返回行数（默认50）
    """
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


@register_plugin(permission_points=(), resolve_permission=shell_permission)
def run_shell(ctx, command: str):
    """通过 bash 执行 shell 命令

    Args:
        command (str): 要执行的 shell 命令
    """
    try:
        proc = subprocess.run(_shell_argv(command), cwd=ctx.cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    except subprocess.TimeoutExpired:
        return PluginResult({}, "命令执行超时（300s）")
    except Exception as exc:  # noqa: BLE001
        return PluginResult({}, f"命令执行失败: {exc}")
    result = {"stdout": proc.stdout or "", "stderr": proc.stderr or "", "returncode": proc.returncode}
    msg = f"返回码 {proc.returncode}"
    if proc.returncode != 0 and proc.stderr:
        msg += f"，stderr: {proc.stderr[:500]}"
    return PluginResult(result, msg)


@register_plugin(permission_points=("global", "cwd", "external"))
def read_file(ctx, path: str):
    """读入一个文件的全部内容（附行号）

    Args:
        path (str): 文件路径（相对cwd或绝对）
    """
    p = _abs(ctx.cwd, str(path or ""))
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception as exc:  # noqa: BLE001
        return PluginResult({}, f"读取失败: {exc}")
    numbered = "\n".join(f"{i}: {ln.rstrip(chr(10))}" for i, ln in enumerate(lines, 1))
    return PluginResult(numbered, f"共 {len(lines)} 行")


@register_plugin(permission_points=("global", "cwd", "external"))
def edit_file(ctx, path: str, line: int = 0, content: str = "", search: str = "", replace: str = ""):
    """修改文件：按行号替换，或 search/replace 块替换

    Args:
        path (str): 文件路径（相对cwd或绝对）
        line (int): 要替换的行号
        content (str): 整行替换时的新内容
        search (str): search/replace 模式的查找串
        replace (str): search/replace 模式的替换串
    """
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


@register_plugin
def web_fetch(ctx, url: str):
    """获取指定 URL 的内容

    Args:
        url (str): 要获取的 URL
    """
    try:
        resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    except Exception as exc:  # noqa: BLE001
        return PluginResult({}, f"请求失败: {exc}")
    return PluginResult({"status_code": resp.status_code, "content": resp.text}, f"HTTP {resp.status_code}")


@register_plugin(permission_points=(), resolve_permission=shell_permission)
def run_shell_async(ctx, task_name: str, command: str):
    """异步执行 shell：给定任务名和命令，后台执行，完成后自动追加结果

    Args:
        task_name (str): 任务名，用于唯一标识
        command (str): 要执行的 shell 命令
    """
    tm = ctx.runtime.task_manager
    if tm is None:
        return PluginResult({}, "后台任务管理器不可用")

    def target():
        proc = subprocess.Popen(_shell_argv(command), cwd=ctx.cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        tm.track_subprocess(proc)
        out, err = proc.communicate()
        return PluginResult({"stdout": out or "", "stderr": err or "", "returncode": proc.returncode}, f"异步命令返回码 {proc.returncode}")

    try:
        task_id = tm.submit(task_name, ctx.session_id, target, params={"command": command}, tool_name="run_shell_async")
    except ValueError as exc:
        return PluginResult({}, str(exc))
    return PluginResult({"task_id": task_id}, f"后台任务提交成功，任务标识={task_id}。完成后结果会自动追加到对话。")


@register_plugin
def request_approval(ctx, uuid: str, task_name: str = "", reason: str = ""):
    """发起权限审批：填入审批 uuid、任务名、理由，审批通过后自动执行

    Args:
        uuid (str): 权限审批 uuid
        task_name (str): 任务名
        reason (str): 申请理由
    """
    return ctx.runtime.request_approval(
        str(uuid or ""),
        str(task_name or ""),
        str(reason or ""),
        ctx.session_id,
    )


@register_plugin
def send_feishu(ctx, receive_id: str, content: str):
    """通过飞书发送一条消息

    Args:
        receive_id (str): 接收者 open_id/chat_id/email
        content (str): 消息内容
    """
    ms = ctx.runtime.message_source
    if ms is None:
        return PluginResult({}, "消息源未启用")
    try:
        ms.send(str(receive_id or ""), str(content or ""))
        return PluginResult({"sent": True}, "已发送")
    except Exception as exc:  # noqa: BLE001
        return PluginResult({}, f"发送失败: {exc}")
