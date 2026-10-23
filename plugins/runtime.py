"""插件框架：@register_plugin 函数式注册、权限解析、运行时统一包装、审批流程。"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import jinja2

from core.context import render_prompt

DEFAULT_LEVEL = "Denied"
MAX_RESULT_LEN = 8000
MAX_SUMMARY_CHARS = (2000, 100)


@dataclass
class PluginResult:
    result: Any = None
    msg: str = ""
    line_numbers: Optional[List[int]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"result": self.result, "msg": self.msg}


@dataclass
class ApprovalStep:
    """单级审批结论：level=agent|human；decision=approve|reject|escalate（escalate 仅 agent 级）。"""

    level: str
    decision: str
    comment: str = ""

    def to_dict(self) -> Dict[str, str]:
        return {"level": self.level, "decision": self.decision, "comment": self.comment}


@dataclass
class PendingApproval:
    uuid: str
    plugin_name: str
    args: Dict[str, Any]
    level: str
    session_id: Optional[str] = None
    request_reason: str = ""
    task_name: str = ""
    task_id: Optional[str] = None
    event: Any = field(default_factory=threading.Event)
    steps: List["ApprovalStep"] = field(default_factory=list)
    status: str = "pending"  # pending_agent | pending_human | approved | rejected


_REGISTRY: Dict[str, "Plugin"] = {}

PermissionResolver = Callable[[Dict[str, Any], Dict[str, Any], str], str]


def default_permission(config: Dict[str, Any], _args: Dict[str, Any], _cwd: str) -> str:
    """通用默认权限解析：读插件配置 permission.default，缺省 Denied。"""
    return str((config.get("permission") or {}).get("default") or DEFAULT_LEVEL)


@dataclass
class Plugin:
    """已注册插件：函数 + 元数据 + 配置表单 schema + 权限解析 + 输出处理。"""

    name: str
    fn: Callable[..., Any]
    description: str
    parameters: Dict[str, Any]
    config: Dict[str, Any]
    resolve_permission: PermissionResolver

    def tool_spec(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }

    def __str__(self) -> str:
        """审批等场景的可读摘要：name：description（无描述时仅 name）。"""
        return f"{self.name}：{self.description}" if self.description else self.name


DEFAULT_CONFIG: Dict[str, Any] = {"permission": {"type": "permission-level", "keys": ("default",)}}


def register_plugin(
    *,
    description: str,
    parameters: Dict[str, Any],
    config: Optional[Dict[str, Any]] = None,
    resolve_permission: PermissionResolver = default_permission,
):
    """函数式插件注册。

    - name 自动取 func.__name__，无需传 name。
    - description / parameters：函数说明与工具参数 JSON Schema（显式，不再从 docstring 推导）。
    - config：插件所需配置的「表单 schema」（字段 → {type, keys}），前端据此生成配置表单。
      可省略；缺省为 {"permission": {"type": "permission-level", "keys": ("default",)}}（单权限点 default）。
    - resolve_permission：校验逻辑 (config, args, cwd) -> level；默认 default_permission（读 permission.default）；
      文件/shell 等特殊权限点由各插件显式传入（定义在 plugins/builtin.py，与插件绑定）。
    """
    if config is None:
        config = DEFAULT_CONFIG

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        final_name = fn.__name__
        _REGISTRY[final_name] = Plugin(
            name=final_name,
            fn=fn,
            description=description,
            parameters=parameters,
            config=config,
            resolve_permission=resolve_permission,
        )
        return fn

    return deco


def load_plugins(plugins_dir: str) -> Dict[str, "Plugin"]:
    """导入内置插件与 plugins/*.py 用户插件；@register_plugin 已自注册进 _REGISTRY。"""
    import plugins.builtin  # noqa: F401 —— 触发 @register_plugin 注册内置插件

    if plugins_dir and os.path.isdir(plugins_dir):
        for fn in sorted(os.listdir(plugins_dir)):
            if not fn.endswith(".py") or fn.startswith("_") or fn in ("runtime.py", "builtin.py"):
                continue
            path = os.path.join(plugins_dir, fn)
            mod_name = f"ca_plugin_{fn[:-3]}"
            spec = importlib.util.spec_from_file_location(mod_name, path)
            if not spec or not spec.loader:
                continue
            mod = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = mod
            try:
                spec.loader.exec_module(mod)
            except Exception as exc:  # noqa: BLE001
                sys.modules.pop(mod_name, None)
                print(f"[plugins] 加载 {fn} 失败: {exc}")
    return _REGISTRY


def _finalize(value: Any) -> str:
    """None / Undefined 留空，其余 str()，避免模板整体报错。"""
    if value is None or isinstance(value, jinja2.Undefined):
        return ""
    return str(value)


_MSG_ENV = jinja2.Environment(undefined=jinja2.ChainableUndefined, finalize=_finalize)


def render_msg(template: str, result: Any) -> str:
    """msg 模板渲染：支持 {{result}} 与 {{result.<key>}}，缺失值留空。"""
    if not template or "{{" not in template:
        return template
    return _MSG_ENV.from_string(template).render(result=result)


def format_tool_content(plugin_name: str, result: Any, msg: str = "", line_numbers: Optional[List[int]] = None) -> str:
    """工具结果以 XML 展开，追加给模型上下文；纯文本统一在此加行号。"""
    if isinstance(result, str):
        lines = result.split("\n")
        if line_numbers is not None:
            text = "\n".join(f"{n}: {ln}" for n, ln in zip(line_numbers, lines))
        else:
            text = "\n".join(f"{i}: {ln}" for i, ln in enumerate(lines))
    else:
        text = json.dumps(result, ensure_ascii=False, indent=2)
    parts = [f'<tool_result name="{plugin_name}">', str(text), "</tool_result>"]
    if msg:
        parts.append(str(msg))
    return "\n".join(parts)


def render_approver_prompt(
    template: str,
    plugin: Optional["Plugin"],
    args: Dict[str, Any],
    reason: str,
    cwd: str,
    config: Dict[str, Any],
) -> str:
    """渲染审批 prompt 槽位（Jinja2，复用 render_prompt）。

    基础上下文与 system_prompt 一致（整个 config 平铺 + {{ config.* }} + {{ time }} / {{ cwd }}），
    额外提供 {{ plugin }}（可读摘要，等同 plugin.name：plugin.description）、{{ plugin.name }}、
    {{ plugin.description }}、{{ args }}（执行参数 JSON）、{{ reason }}（申请理由）。
    """
    return render_prompt(
        template,
        config,
        cwd,
        extra={
            "plugin": plugin or "",
            "args": json.dumps(args or {}, ensure_ascii=False),
            "reason": reason or "",
        },
    )


@dataclass
class PluginContext:
    """插件运行时上下文。config 为该插件自己的配置（schema 默认值 + JSON 覆盖值解析而来）。"""

    cwd: str
    config: Dict[str, Any]
    runtime: "PluginRuntime"
    session_id: Optional[str] = None
    project_id: Optional[str] = None
    im_link: Any = None
    im_config: Dict[str, Any] = field(default_factory=dict)


class PluginRuntime:
    """插件运行时：权限解析 + 统一包装 + 审批编排。"""

    def __init__(
        self,
        plugins_dir: str,
        config: Dict[str, Any],
        cwd: str,
        task_manager=None,
        model_provider: Optional[Callable[[Optional[str]], Any]] = None,
    ):
        self.plugins = load_plugins(plugins_dir)
        self.config = config or {}
        self.cwd = cwd
        self.task_manager = task_manager
        self.model_provider = model_provider or (lambda _mid=None: None)
        self.project_resolver: Optional[Callable[[Optional[str]], Dict[str, Any]]] = None
        self.pending: Dict[str, PendingApproval] = {}
        self.overflow_results: Dict[str, PluginResult] = {}
        self._lock = threading.RLock()

    def plugin_config(self, name: str) -> Dict[str, Any]:
        """该插件自己的配置：直接取 config.plugins.<name>，缺省空 dict。"""
        return (self.config.get("plugins") or {}).get(name) or {}

    def visible_tools(self, cwd: Optional[str] = None) -> List[Dict[str, Any]]:
        tools: List[Dict[str, Any]] = []
        for name, p in self.plugins.items():
            if p.resolve_permission(self.plugin_config(name), {}, cwd or self.cwd) != "Denied":
                tools.append(p.tool_spec())
        return tools

    def _resolve_project(self, session_id: Optional[str]) -> Dict[str, Any]:
        """按会话解析所属 project 的运行时上下文（cwd / im_link 实例 / im 配置）。"""
        if self.project_resolver and session_id:
            try:
                return self.project_resolver(session_id) or {}
            except Exception as exc:  # noqa: BLE001
                print(f"[plugin] 解析 project 上下文失败: {exc}")
        return {"cwd": self.cwd, "im_link": None, "im_config": {}}

    def execute(self, plugin_name: str, args: Dict[str, Any], session_id: Optional[str] = None) -> PluginResult:
        plugin = self.plugins.get(plugin_name)
        if plugin is None:
            return PluginResult({}, f"未知插件: {plugin_name}")
        args = args or {}
        project_ctx = self._resolve_project(session_id)
        cwd = project_ctx.get("cwd") or self.cwd
        level = plugin.resolve_permission(self.plugin_config(plugin_name), args, cwd)
        if level == "Denied":
            return PluginResult({}, f"插件 {plugin_name} 被禁止使用 (Denied)")
        if level == "Trusted":
            return self._invoke_plugin(plugin, args, session_id, project_ctx)
        uid = uuid.uuid4().hex
        status = "pending_agent" if level == "AgentReview" else "pending_human"
        pa = PendingApproval(uuid=uid, plugin_name=plugin_name, args=args, level=level, session_id=session_id, status=status)
        with self._lock:
            self.pending[uid] = pa
        return PluginResult(
            {},
            f'该插件的执行被拦截，需审批后执行。请调用 request_approval 插件发起审批，审批通过后会自动执行：'
            f'uuid="{uid}"、task_name=任务名、reason=申请理由。',
        )

    def _invoke_plugin(self, plugin: Plugin, args: Dict[str, Any], session_id: Optional[str] = None, project_ctx: Optional[Dict[str, Any]] = None) -> PluginResult:
        project_ctx = project_ctx or {}
        ctx = PluginContext(
            cwd=project_ctx.get("cwd") or self.cwd,
            config=self.plugin_config(plugin.name),
            runtime=self,
            session_id=session_id,
            project_id=project_ctx.get("project_id"),
            im_link=project_ctx.get("im_link"),
            im_config=project_ctx.get("im_config") or {},
        )
        try:
            kwargs = {k: v for k, v in args.items() if k in plugin.parameters.get("properties", {})}
            res = plugin.fn(ctx, **kwargs)
        except Exception as exc:  # noqa: BLE001
            return PluginResult({}, f"插件执行出错: {type(exc).__name__}: {exc}")
        if not isinstance(res, PluginResult):
            res = PluginResult(res, "")
        res.msg = render_msg(res.msg, res.result)
        self._handle_overflow(res)
        return res

    def _handle_overflow(self, res: PluginResult) -> None:
        text = res.result if isinstance(res.result, str) else json.dumps(res.result, ensure_ascii=False)
        if len(text) <= MAX_RESULT_LEN + MAX_SUMMARY_CHARS[0]:
            return
        oid = "overflow_" + uuid.uuid4().hex[:8]
        self.overflow_results[oid] = PluginResult(text, line_numbers=res.line_numbers)
        res.result = text[:MAX_RESULT_LEN]
        hint = f"结果过长已截断，完整结果已暂存（overflow_id={oid}），可使用 get_overflow_result 查询本次超长结果。"
        if isinstance(res.result, str):
            hint += "\n\n截断部分摘要：\n" + self._overflow_summary(text, MAX_RESULT_LEN, res.line_numbers)
        res.msg = hint if not res.msg else f"{res.msg}\n{hint}"

    def _overflow_summary(self, text: str, limit: int, line_numbers=None) -> str:
        start_line = text[:limit].count("\n")
        lines = re.findall("(\n*)([^\n]*)", text[limit:])[:-1]

        candidates = []
        for blanks, ln in lines:
            start_line += len(blanks)
            line_no = line_numbers[start_line] if line_numbers is not None else start_line
            candidates.append((-len(blanks), line_no, ln))
        candidates.sort()

        picked = []
        for _, i, ln in candidates:
            snippet = ln if len(ln) <= MAX_SUMMARY_CHARS[1] else ln[:MAX_SUMMARY_CHARS[1]] + "……"
            if sum(len(x[1]) for x in picked) + len(snippet) > MAX_SUMMARY_CHARS[0]:
                break
            picked.append((i, snippet))
        return "\n".join(f"{i}: {snippet}" for i, snippet in sorted(picked))

    # ---------- 审批 ----------
    @staticmethod
    def _approval_snapshot(pa: PendingApproval, status: str = "") -> Dict[str, Any]:
        """审批进度的结构化快照，供回传给模型。"""
        return {
            "status": status or pa.status,
            "steps": [s.to_dict() for s in pa.steps],
            "task_id": pa.task_id,
        }

    def request_approval(self, uuid_: str, task_name: str, reason: str, session_id: Optional[str] = None) -> PluginResult:
        with self._lock:
            pa = self.pending.get(uuid_)
        if pa is None:
            return PluginResult({}, f"找不到审批申请 uuid={uuid_}，可能已过期或被处理")
        pa.request_reason = reason or ""
        pa.task_name = task_name or pa.plugin_name
        if pa.session_id is None:
            pa.session_id = session_id

        if pa.level == "AgentReview":
            decision, advice = self._run_agent_approval(pa)
            pa.steps.append(ApprovalStep("agent", decision, advice))
            if decision == "approve":
                with self._lock:
                    self.pending.pop(uuid_, None)
                pa.status = "approved"
                plugin = self.plugins.get(pa.plugin_name)
                return self._invoke_plugin(plugin, pa.args, pa.session_id)
            if decision == "reject":
                with self._lock:
                    self.pending.pop(uuid_, None)
                pa.status = "rejected"
                return PluginResult(self._approval_snapshot(pa), f"审批被拒绝：{advice or '未说明理由'}")
            pa.status = "pending_human"
            return self._start_human_approval(pa)

        pa.status = "pending_human"
        return self._start_human_approval(pa)

    def approve(self, uuid_: str, approved: bool, reason: str = "") -> PluginResult:
        with self._lock:
            pa = self.pending.pop(uuid_, None)
        if pa is None:
            return PluginResult({}, f"找不到审批申请 uuid={uuid_}")
        pa.steps.append(ApprovalStep("human", "approve" if approved else "reject", reason or ""))
        pa.status = "approved" if approved else "rejected"
        pa.event.set()
        # 通过则把任务从 pending_approval 标记为 running，让 UI 及时过渡
        tm = self.task_manager
        if approved and tm is not None and pa.task_id:
            t = tm.store.get_task(pa.task_id)
            if t:
                t.status = "running"
                t.pending_approval = False
                t.end_time = ""
                tm.store.save_task(t)
        return PluginResult({}, "审批通过，后台自动执行。" if approved else f"审批被拒绝：{reason or '未说明理由'}")

    def _start_human_approval(self, pa: PendingApproval) -> PluginResult:
        tm = self.task_manager
        ctx = self._resolve_project(pa.session_id)
        project_id = ctx.get("project_id")
        params = {"uuid": pa.uuid, "plugin": pa.plugin_name, "args": pa.args, "reason": pa.request_reason, "task_name": pa.task_name}
        if tm is not None and getattr(tm, "submit", None):
            task_id = tm.submit(
                pa.task_name or pa.plugin_name, pa.session_id,
                target=lambda: self._human_approval_target(pa),
                params=params, tool_name=pa.plugin_name, pending=True, project_id=project_id,
            )
            pa.task_id = task_id
        else:
            task_id = pa.uuid
        im = ctx.get("im_link")
        if im is not None and getattr(im, "notify_approval", None):
            try:
                im.notify_approval(pa)
            except Exception as exc:  # noqa: BLE001
                print(f"[plugin] 审批通知失败: {exc}")
        return PluginResult({}, f"已提交后台人工审批（级别=HumanReview），任务标识={task_id}，等待人工批准后自动执行。")

    def _human_approval_target(self, pa: PendingApproval) -> PluginResult:
        """人工审批的目标函数体：阻塞等人工信号，通过则执行插件，否则返回拒绝。"""
        pa.event.wait()
        if pa.status == "approved":
            plugin = self.plugins.get(pa.plugin_name)
            if plugin is None:
                return PluginResult({}, f"未知插件: {pa.plugin_name}")
            return self._invoke_plugin(plugin, pa.args, pa.session_id)
        human_step = next((s for s in pa.steps if s.level == "human"), None)
        advice = human_step.comment if human_step else ""
        return PluginResult(self._approval_snapshot(pa), f"审批被拒绝：{advice or '未说明理由'}")

    def _run_agent_approval(self, pa: PendingApproval) -> tuple:
        approver = self.config.get("approver") or {}
        model = self.model_provider(approver.get("model"))
        if model is None:
            return "escalate", ""
        project_ctx = self._resolve_project(pa.session_id)
        cwd = project_ctx.get("cwd") or self.cwd
        system = render_approver_prompt(
            approver.get("prompt"),
            self.plugins.get(pa.plugin_name),
            pa.args,
            pa.request_reason,
            cwd=cwd,
            config=self.config,
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": "请根据以上信息完成审批。"},
        ]
        try:
            resp = model.chat(messages)
            text = resp.content or ""
            lo, hi = text.find("{"), text.rfind("}")
            data = json.loads(text[lo:hi + 1]) if (lo != -1 and hi != -1) else {}
            decision = str(data.get("decision") or "escalate").lower()
            if decision not in ("approve", "reject", "escalate"):
                decision = "escalate"
            reason = str(data.get("reason") or "")
            return decision, reason
        except Exception as exc:  # noqa: BLE001
            print(f"[plugin] 审批 Agent 调用失败，转人类审批: {exc}")
            return "escalate", ""
