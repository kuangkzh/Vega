"""内置基础插件（合并为单文件，均用 @register_plugin 注册）。"""
from __future__ import annotations

import os
import re
import shutil
import subprocess

import requests
import html2text

from plugins.runtime import PluginResult, register_plugin


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
        base = os.path.abspath(cwd)
        target = os.path.abspath(_abs(cwd, path))
        return os.path.commonpath([base, target]) == base
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


def _parse_lines_spec(spec: str) -> set:
    """解析行号索引："0-4,6,8,11-20"（闭区间、0 起算）→ 命中的行号集合。"""
    indexes: set = set()
    for part in str(spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            left, _, right = part.partition("-")
            try:
                lo, hi = int(left.strip()), int(right.strip())
            except ValueError:
                raise ValueError(f"非法行号区间: {part}")
            if lo > hi:
                lo, hi = hi, lo
            indexes.update(range(lo, hi + 1))
        else:
            try:
                indexes.add(int(part))
            except ValueError:
                raise ValueError(f"非法行号: {part}")
    return indexes


def filter_text_lines(text: str, lines_spec: str = "", regexp: str = "", base_numbers=None):
    """统一的文本行筛选：按行号区间 + 正则表达式（与关系）筛选，返回 (文本, 命中行数, 总行数, 行号列表)。

    - lines_spec：形如 "0-4,6,8,11-20"（闭区间、0 起算），可空表示全量。
    - regexp：正则表达式；同时提供行号与正则时按与关系过滤。
    - base_numbers：text 每行对应的原始行号（缺省即 0 起算）。
    - 返回的行号列表为命中行的原始行号；全量且无基准时返回 None（展示层默认 0 起算）。
    """
    lines = (text or "").split("\n")
    total = len(lines)
    indexes = _parse_lines_spec(lines_spec) if str(lines_spec or "").strip() else None
    pattern = re.compile(str(regexp or "")) if str(regexp or "").strip() else None
    out: list = []
    orig_numbers: list = []
    matched = 0
    for i, ln in enumerate(lines):
        if indexes is not None and i not in indexes:
            continue
        if pattern is not None and not pattern.search(ln):
            continue
        matched += 1
        out.append(ln)
        orig_numbers.append(i if base_numbers is None else base_numbers[i])
    if indexes is None and pattern is None and base_numbers is None:
        return "\n".join(out), matched, total, None
    return "\n".join(out), matched, total, orig_numbers


def filter_regexp_matches(text: str, regexp: str, lines_spec: str = "", base_numbers=None):
    """字符级匹配（regexp_mode=find）：只返回 regexp 匹配到的片段。

    - 逐行匹配，不跨行。
    - lines_spec 按当前 text 的行索引限定匹配范围（与 regexp 为与关系）。
    - base_numbers：text 每行对应的原始行号（缺省即 0 起算），用于片段前缀展示。
    - 每个片段前缀格式 "[L行:C列] 片段"：行号按 base_numbers 还原，列偏移按当前 text 计算。
    - 零宽匹配跳过；返回 (结果文本, 匹配处数, 原始行数, 行号列表)，行号列表为各片段对应的原始行号。
    """
    lines = (text or "").split("\n")
    indexes = _parse_lines_spec(lines_spec) if str(lines_spec or "").strip() else None
    pattern = re.compile(str(regexp))

    out: list = []
    orig_numbers: list = []
    matched = 0
    for i, ln in enumerate(lines):
        if indexes is not None and i not in indexes:
            continue
        for m in pattern.finditer(ln):
            if m.end() == m.start():
                continue
            matched += 1
            orig_no = i if base_numbers is None else base_numbers[i]
            out.append(f"[L{orig_no}:C{m.start()}] {m.group(0)}")
            orig_numbers.append(orig_no)
    return "\n".join(out), matched, len(lines), orig_numbers


@register_plugin(
    description="根据 overflow_id 再次获取插件返回的超长结果（可按行号区间或正则筛选）",
    parameters={
        "type": "object",
        "properties": {
            "overflow_id": {"type": "string", "description": "超长结果的唯一标识（超长截断时返回）"},
            "lines": {"type": "string", "description": "行号索引，如 \"0-4,6,8,11-20\"（闭合区间、0 起算）"},
            "regexp": {"type": "string", "description": "只返回匹配该正则的行；同时给行号时按与关系再过滤"},
            "regexp_mode": {"type": "string", "description": "regexp 模式：filter（默认，按行筛选）或 find（仅返回正则匹配的片段，在单行超长时也很有用）"},
        },
        "required": ["overflow_id"],
    },
)
def get_overflow_result(ctx, overflow_id: str = "", lines: str = "", regexp: str = "", regexp_mode: str = "filter"):
    cached = ctx.runtime.overflow_results.get(overflow_id)
    if cached is None:
        return PluginResult({}, f"overflow_id={overflow_id}不存在或可能被清理")
    if str(regexp_mode) == "find":
        if not str(regexp or "").strip():
            return PluginResult({}, "regexp_mode=find 需要提供 regexp")
        try:
            out, matched, total, _ = filter_regexp_matches(cached.result, regexp, lines, cached.line_numbers)
        except (ValueError, re.error) as exc:
            return PluginResult({}, str(exc))
        return PluginResult(out, f"匹配 {matched} 处（原始共 {total} 行）")
    try:
        out, matched, total, orig_numbers = filter_text_lines(cached.result, lines, regexp, cached.line_numbers)
    except ValueError as exc:
        return PluginResult({}, str(exc))
    return PluginResult(out, f"匹配 {matched} 行（原始共 {total} 行）", line_numbers=orig_numbers)


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
            task_id = tm.submit(task_name or "shell", ctx.session_id, target, params={"command": command}, tool_name="run_shell", project_id=ctx.project_id)
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
    description="读入一个文件的内容（可按行号区间或正则筛选，返回附行号；regexp_mode=find 时返回正则匹配到的片段）",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径（相对cwd或绝对）"},
            "lines": {"type": "string", "description": "行号索引，如 \"0-4,6,8,11-20\"（闭合区间、0 起算）"},
            "regexp": {"type": "string", "description": "只返回匹配该正则的行；同时给行号时按与关系再过滤"},
            "regexp_mode": {"type": "string", "description": "regexp 模式：filter（默认，按行筛选）或 find（仅返回正则匹配的片段，在单行超长时也很有用）"},
        },
        "required": ["path"],
    },
    config={"permission": {"type": "permission-level", "keys": ("cwd", "external")}},
    resolve_permission=file_permission,
)
def read_file(ctx, path: str, lines: str = "", regexp: str = "", regexp_mode: str = "filter"):
    p = _abs(ctx.cwd, str(path or ""))
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception as exc:  # noqa: BLE001
        return PluginResult({}, f"读取失败: {exc}")
    if str(regexp_mode) == "find":
        if not str(regexp or "").strip():
            return PluginResult({}, "regexp_mode=find 需要提供 regexp")
        try:
            out, matched, total, _ = filter_regexp_matches(text, regexp, lines)
        except (ValueError, re.error) as exc:
            return PluginResult({}, str(exc))
        return PluginResult(out, f"匹配 {matched} 处（原始共 {total} 行）")
    try:
        out, matched, total, orig_numbers = filter_text_lines(text, lines, regexp)
    except ValueError as exc:
        return PluginResult({}, str(exc))
    return PluginResult(out, f"共 {total} 行，匹配 {matched} 行", line_numbers=orig_numbers)


@register_plugin(
    description="修改文件：search/replace（text 或 regexp 模式，可指定起始搜索行）；文件不存在时自动创建",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径（相对cwd或绝对）"},
            "search": {"type": "string", "description": "查找串（text 模式为字面文本，regexp 模式为正则）"},
            "replace": {"type": "string", "description": "替换串（字面文本）"},
            "start_line": {"type": "integer", "description": "起始搜索行（0 起算，默认 0）"},
            "mode": {"type": "string", "description": "匹配模式：text（默认）或 regexp"},
        },
        "required": ["path", "search"],
    },
    config={"permission": {"type": "permission-level", "keys": ("cwd", "external")}},
    resolve_permission=file_permission,
)
def edit_file(ctx, path: str, search: str = "", replace: str = "", start_line: int = 0, mode: str = "text"):
    p = _abs(ctx.cwd, str(path or ""))
    is_new_file = not os.path.exists(p)
    try:
        with open(p, "r", encoding="utf-8") as f:
            original = f.read()
    except FileNotFoundError:
        original = ""
    except Exception as exc:  # noqa: BLE001
        return PluginResult({}, f"读取失败: {exc}")

    pattern_src = re.escape(search) if str(mode) != "regexp" else search
    try:
        pattern = re.compile(pattern_src)
    except re.error as exc:
        return PluginResult({}, f"正则无效: {exc}")

    start = int(start_line or 0)
    offset = sum(len(ln) + 1 for ln in original.split("\n")[:start])
    matches = list(pattern.finditer(original, offset))
    if not matches:
        if is_new_file:
            return PluginResult({}, f"文件不存在: {p}")
        return PluginResult({}, f"未找到: {search}")

    first = matches[0]
    new_content = original[:first.start()] + replace + original[first.end():]

    try:
        with open(p, "w", encoding="utf-8") as f:
            f.write(new_content)
    except Exception as exc:  # noqa: BLE001
        return PluginResult({}, f"写入失败: {exc}")
    created_note = "（文件不存在，已创建）" if is_new_file else ""
    return PluginResult({"changed": True, "is_new_file": is_new_file, "path": p}, f"已修改第一处（共找到 {len(matches)} 处）{created_note}")


_TREE_BRANCH = "├── "
_TREE_LAST = "└── "
_TREE_PIPE = "│   "
_TREE_BLANK = "    "


def _count_items(path: str) -> int:
    with os.scandir(path) as it:
        return sum(1 for _ in it)


def _render_tree(root: str, depth: int, max_entries: int, exclude: "re.Pattern") -> str:
    """渲染目录树：命中 exclude（匹配相对路径）或超出 depth 的目录只标注不展开。

    max_entries 为 0 表示按层取默认值（首层 30，其余层 10）。
    """
    lines = ["./"]

    def walk(path: str, level: int, prefix: str, rel: str) -> None:
        limit = max_entries or (30 if level == 0 else 10)
        with os.scandir(path) as it:
            entries = sorted(it, key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()))
        shown = entries[:limit]
        for i, entry in enumerate(shown):
            last = i == len(shown) - 1 and len(shown) == len(entries)
            connector = _TREE_LAST if last else _TREE_BRANCH
            name = entry.name
            if not entry.is_dir(follow_symlinks=False):
                lines.append(prefix + connector + name)
                continue
            child_rel = f"{rel}/{name}" if rel else name
            if exclude.search(child_rel) or level + 1 >= depth:
                lines.append(f"{prefix}{connector}{name}/  (未展开，包含 {_count_items(entry.path)} 项)")
            else:
                lines.append(prefix + connector + name + "/")
                walk(entry.path, level + 1, prefix + (_TREE_BLANK if last else _TREE_PIPE), child_rel)
        if len(entries) > len(shown):
            lines.append(f"{prefix}{_TREE_LAST}(共 {len(entries)} 项，{len(entries) - len(shown)} 项已折叠)")

    walk(root, 0, "", "")
    return "\n".join(lines)


def _git_output(cwd: str, *args: str) -> str:
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    except FileNotFoundError:
        return "git 不可用"
    out = (proc.stdout or "").rstrip("\n")
    if out.strip():
        return out
    return (proc.stderr or "").strip() or "(无输出)"


@register_plugin(
    description="获取项目状态，一次性返回目录树和`git status --short` & `git diff --stat` & `git log --oneline`的结果",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "目录树起始路径（相对或绝对路径，默认 .）"},
            "tree_depth": {"type": "integer", "description": "目录树最大展开层级（默认 3）"},
            "tree_max_entries": {"type": "integer", "description": "目录树子项展开上限（不指定时首层 30、其余层 10）"},
            "exclude_regexp": {"type": "string", "description": "目录树中不展开的目录路径正则（相对起始路径匹配，默认 \\..*|__pycache__|node_modules|build|dist）"},
            "git_log_lines": {"type": "integer", "description": "git log 展示条数（默认 5）"},
        },
    },
    config={"permission": {"type": "permission-level", "keys": ("cwd", "external")}},
    resolve_permission=file_permission,
)
def get_project_status(
    ctx,
    path: str = ".",
    tree_depth: int = 3,
    tree_max_entries: int = 0,
    exclude_regexp: str = r"\..*|__pycache__|node_modules|build|dist",
    git_log_lines: int = 5,
):
    root = _abs(ctx.cwd, str(path or "."))
    depth = int(tree_depth)
    max_entries = int(tree_max_entries or 0)
    log_lines = int(git_log_lines)
    exclude = re.compile(str(exclude_regexp))
    sections = [
        "[目录树]\n" + _render_tree(root, depth, max_entries, exclude),
        "[git status --short]\n" + _git_output(ctx.cwd, "status", "--short"),
        "[git diff --stat]\n" + _git_output(ctx.cwd, "diff", "--stat"),
        f"[git log --oneline -{log_lines}]\n" + _git_output(ctx.cwd, "log", "--oneline", f"-{log_lines}"),
    ]
    return PluginResult("\n\n".join(sections), "")


@register_plugin(
    description="获取指定 URL 的内容",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "要获取的 URL"},
            "mode": {"type": "string", "description": "返回格式：markdown（默认，转为Markdown）或 raw（原始HTML）"},
        },
        "required": ["url"],
    },
)
def web_fetch(ctx, url: str, mode: str = "markdown"):
    try:
        resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    except Exception as exc:  # noqa: BLE001
        return PluginResult({}, f"请求失败: {exc}")

    if str(mode).lower() == "markdown":
        converter = html2text.HTML2Text()
        converter.body_width = 0
        content = converter.handle(resp.text)
    else:
        content = resp.text

    return PluginResult(content, f"HTTP {resp.status_code}")


@register_plugin(
    description="发起权限审批：填入审批 uuid、任务名、理由，审批通过后自动执行",
    parameters={
        "type": "object",
        "properties": {
            "uuid": {"type": "string", "description": "权限审批 uuid"},
            "task_name": {"type": "string", "description": "任务名"},
            "reason": {"type": "string", "description": "申请理由，请写明必要性"},
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
    description="向配置的 IM 接收目标发送一条消息",
    parameters={
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "消息内容"},
        },
        "required": ["content"],
    },
)
def send_im(ctx, content: str):
    im = ctx.im_config or {}
    target = im.get("chat_id") or ""
    if not target:
        return PluginResult({}, "未配置 IM 接收目标，无法发送")
    link = ctx.im_link
    if link is None:
        return PluginResult({}, "IM 连接未启用")
    try:
        link.send(str(target), str(content or ""))
        return PluginResult({"sent": True, "chat_id": target}, "已发送")
    except Exception as exc:  # noqa: BLE001
        return PluginResult({}, f"发送失败: {exc}")
