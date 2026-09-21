"""插件框架：@register_plugin 函数式注册、权限解析、运行时统一包装、审批流程。"""
from __future__ import annotations

import importlib.util
import inspect
import json
import os
import re
import sys
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.config import resolve_prompt

DEFAULT_LEVEL = "Denied"
MAX_RESULT_LEN = 8000


@dataclass
class PluginResult:
    result: Any = None
    msg: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"result": self.result, "msg": self.msg}


@dataclass
class PendingApproval:
    uuid: str
    plugin_name: str
    args: Dict[str, Any]
    level: str
    session_id: Optional[str] = None
    reason: str = ""
    task_name: str = ""
    task_id: Optional[str] = None
    event: Any = field(default_factory=threading.Event)
    approved: bool = False
    decision_reason: str = ""


_REGISTRY: Dict[str, "Plugin"] = {}

PermissionResolver = Callable[[Dict[str, Any], Dict[str, Any], str], str]


def default_permission(config: Dict[str, Any], _args: Dict[str, Any], _cwd: str) -> str:
    """通用默认权限解析：读插件配置 permission.default，缺省 Denied。"""
    return str((config.get("permission") or {}).get("default") or DEFAULT_LEVEL)


def _arg_names(fn: Callable[..., Any]) -> Tuple[str, ...]:
    return tuple(
        p.name for p in list(inspect.signature(fn).parameters.values())[1:]
        if p.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    )


@dataclass
class Plugin:
    """已注册插件：函数 + 元数据 + 配置表单 schema + 权限解析 + 输出处理。"""

    name: str
    fn: Callable[..., Any]
    description: str
    parameters: Dict[str, Any]
    config: Dict[str, Any]
    resolve_permission: PermissionResolver
    arg_names: Tuple[str, ...] = ()
    msg_template: str = ""
    truncate: bool = True

    def tool_spec(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }


DEFAULT_CONFIG: Dict[str, Any] = {"permission": {"type": "permission-level", "keys": ("default",)}}


def register_plugin(
    *,
    description: str,
    parameters: Dict[str, Any],
    config: Optional[Dict[str, Any]] = None,
    resolve_permission: PermissionResolver = default_permission,
    msg_template: Optional[str] = None,
    truncate: bool = True,
):
    """函数式插件注册。

    - name 自动取 func.__name__，无需传 name。
    - description / parameters：函数说明与工具参数 JSON Schema（显式，不再从 docstring 推导）。
    - config：插件所需配置的「表单 schema」（字段 → {type, keys}），前端据此生成配置表单。
      可省略；缺省为 {"permission": {"type": "permission-level", "keys": ("default",)}}（单权限点 default）。
    - resolve_permission：校验逻辑 (config, args, cwd) -> level；默认 default_permission（读 permission.default）；
      文件/shell 等特殊权限点由各插件显式传入（定义在 plugins/builtin.py，与插件绑定）。
    - msg_template / truncate：注入输出处理。
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
            arg_names=_arg_names(fn),
            msg_template=msg_template or "",
            truncate=truncate,
        )
        return fn

    return deco


def load_plugins(plugins_dir: str) -> Dict[str, "Plugin"]:
    """导入内置插件与 plugins/*.py 用户插件；@register_plugin 已自注册进 _REGISTRY。"""
    import plugins.builtin  # noqa: F401 —— 触发 @register_plugin 注册内置插件

    if plugins_dir and os.path.isdir(plugins_dir):
        for fn in sorted(os.listdir(plugins_dir)):
            if not fn.endswith(".py") or fn.startswith("_") or fn in ("base.py", "builtin.py"):
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


def render_msg(template: str, result: Any) -> str:
    """msg 模板渲染：支持 {{result}} 与 {{result.<key>}}。"""
    if not template or "{{" not in template:
        return template

    def _rep(m: "re.Match[str]") -> str:
        expr = m.group(1).strip()
        if expr == "result":
            return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        if expr.startswith("result."):
            key = expr[len("result."):]
            val = result.get(key) if isinstance(result, dict) else None
            return "" if val is None else str(val)
        return m.group(0)

    return re.sub(r"\{\{(.*?)\}\}", _rep, template)


def format_tool_content(plugin_name: str, result: Any, msg: str = "") -> str:
    """工具结果以 XML 展开，追加给模型上下文。"""
    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2)
    parts = [f'<tool_result name="{plugin_name}">', str(text), "</tool_result>"]
    if msg:
        parts.append(str(msg))
    return "\n".join(parts)


def render_approver_prompt(template: str, plugin: Optional["Plugin"], args: Dict[str, Any], reason: str) -> str:
    """渲染审批 prompt 槽位。

    支持 {{plugin}}（函数信息，形如「插件名：描述」）、{{args}}（执行参数 JSON）、{{reason}}（申请理由）。
    """
    info = ""
    if plugin is not None:
        info = f"{plugin.name}：{plugin.description}" if plugin.description else plugin.name
    slots = {
        "plugin": info,
        "args": json.dumps(args or {}, ensure_ascii=False),
        "reason": reason or "",
    }
    for key, val in slots.items():
        template = template.replace("{{" + key + "}}", val)
    return template


@dataclass
class PluginContext:
    """插件运行时上下文。config 为该插件自己的配置（schema 默认值 + JSON 覆盖值解析而来）。"""

    cwd: str
    config: Dict[str, Any]
    runtime: "PluginRuntime"
    session_id: Optional[str] = None


class PluginRuntime:
    """插件运行时：权限解析 + 统一包装 + 审批编排。"""

    def __init__(
        self,
        plugins_dir: str,
        config: Dict[str, Any],
        cwd: str,
        task_manager=None,
        message_source=None,
        model_provider: Optional[Callable[[Optional[str]], Any]] = None,
    ):
        self.plugins = load_plugins(plugins_dir)
        self.config = config or {}
        self.cwd = cwd
        self.task_manager = task_manager
        self.message_source = message_source
        self.model_provider = model_provider or (lambda _mid=None: None)
        self.pending: Dict[str, PendingApproval] = {}
        self.last_overflow_result: Optional[str] = None
        self._lock = threading.RLock()

    def plugin_config(self, name: str) -> Dict[str, Any]:
        """该插件自己的配置：直接取 config.plugins.<name>，缺省空 dict。"""
        return (self.config.get("plugins") or {}).get(name) or {}

    def visible_tools(self) -> List[Dict[str, Any]]:
        tools: List[Dict[str, Any]] = []
        for name, p in self.plugins.items():
            if p.resolve_permission(self.plugin_config(name), {}, self.cwd) != "Denied":
                tools.append(p.tool_spec())
        return tools

    def execute(self, plugin_name: str, args: Dict[str, Any], session_id: Optional[str] = None) -> PluginResult:
        plugin = self.plugins.get(plugin_name)
        if plugin is None:
            return PluginResult({}, f"未知插件: {plugin_name}")
        args = args or {}
        level = plugin.resolve_permission(self.plugin_config(plugin_name), args, self.cwd)
        if level == "Denied":
            return PluginResult({}, f"插件 {plugin_name} 被禁止使用 (Denied)")
        if level == "Trusted":
            return self._run_and_wrap(plugin, args, session_id)
        uid = uuid.uuid4().hex
        pa = PendingApproval(uuid=uid, plugin_name=plugin_name, args=args, level=level, session_id=session_id)
        with self._lock:
            self.pending[uid] = pa
        return PluginResult(
            {},
            f'该操作需要审批（级别={level}）。审批 uuid={uid}。请调用 request_approval 插件发起审批：'
            f'uuid="{uid}"、task_name=任务名、reason=申请理由。',
        )

    def _run_and_wrap(self, plugin: Plugin, args: Dict[str, Any], session_id: Optional[str] = None) -> PluginResult:
        ctx = PluginContext(cwd=self.cwd, config=self.plugin_config(plugin.name), runtime=self, session_id=session_id)
        try:
            kwargs = {k: v for k, v in args.items() if k in plugin.arg_names}
            res = plugin.fn(ctx, **kwargs)
        except Exception as exc:  # noqa: BLE001
            return PluginResult({}, f"插件执行出错: {type(exc).__name__}: {exc}")
        if not isinstance(res, PluginResult):
            res = PluginResult(res, "")
        res.msg = render_msg(plugin.msg_template or res.msg, res.result)
        if plugin.truncate:
            self._handle_overflow(res)
        return res

    def _handle_overflow(self, res: PluginResult) -> None:
        text = res.result if isinstance(res.result, str) else json.dumps(res.result, ensure_ascii=False)
        if len(text) <= MAX_RESULT_LEN:
            return
        self.last_overflow_result = text
        res.result = text[:MAX_RESULT_LEN]
        hint = "结果过长已截断，完整结果已存为「上一次超长结果」，可用 get_last_overflow_result 插件按行/关键词检索。"
        res.msg = hint if not res.msg else f"{res.msg}\n{hint}"

    # ---------- 审批 ----------
    def request_approval(self, uuid_: str, task_name: str, reason: str, session_id: Optional[str] = None) -> PluginResult:
        with self._lock:
            pa = self.pending.get(uuid_)
        if pa is None:
            return PluginResult({}, f"找不到审批申请 uuid={uuid_}，可能已过期或被处理")
        pa.reason = reason or ""
        pa.task_name = task_name or pa.plugin_name
        if pa.session_id is None:
            pa.session_id = session_id
        if pa.level == "AgentReview":
            decision = self._run_approver(pa)
            if decision == "approve":
                with self._lock:
                    self.pending.pop(uuid_, None)
                plugin = self.plugins.get(pa.plugin_name)
                return self._run_and_wrap(plugin, pa.args, pa.session_id)
            if decision == "reject":
                with self._lock:
                    self.pending.pop(uuid_, None)
                return PluginResult({}, f"审批被拒绝：{reason}")
            return self._start_human_approval(pa)
        return self._start_human_approval(pa)

    def approve(self, uuid_: str, approved: bool, reason: str = "") -> PluginResult:
        with self._lock:
            pa = self.pending.pop(uuid_, None)
        if pa is None:
            return PluginResult({}, f"找不到审批申请 uuid={uuid_}")
        pa.approved = approved
        pa.decision_reason = reason or ""
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
        return PluginResult({}, "审批通过，后台自动执行。" if approved else f"审批被拒绝：{reason}")

    def _start_human_approval(self, pa: PendingApproval) -> PluginResult:
        tm = self.task_manager
        params = {"uuid": pa.uuid, "plugin": pa.plugin_name, "args": pa.args, "reason": pa.reason, "task_name": pa.task_name}
        if tm is not None and getattr(tm, "submit", None):
            task_id = tm.submit(
                pa.task_name or pa.plugin_name, pa.session_id,
                target=lambda: self._approval_target(pa),
                params=params, tool_name=pa.plugin_name, pending=True,
            )
            pa.task_id = task_id
        else:
            task_id = pa.uuid
        if self.message_source is not None and getattr(self.message_source, "notify_approval", None):
            try:
                self.message_source.notify_approval(pa)
            except Exception as exc:  # noqa: BLE001
                print(f"[plugin] 审批通知失败: {exc}")
        return PluginResult({}, f"已提交后台人工审批（级别=HumanReview），任务标识={task_id}，等待人工批准后自动执行。")

    def _approval_target(self, pa: PendingApproval) -> PluginResult:
        """人工审批的底层 target：阻塞等人工信号，通过则执行插件，否则返回拒绝。"""
        pa.event.wait()
        if pa.approved:
            plugin = self.plugins.get(pa.plugin_name)
            if plugin is None:
                return PluginResult({}, f"未知插件: {pa.plugin_name}")
            return self._run_and_wrap(plugin, pa.args, pa.session_id)
        return PluginResult({}, f"审批被拒绝：{pa.decision_reason or '未说明理由'}")

    def _run_approver(self, pa: PendingApproval) -> str:
        approver = self.config.get("approver") or {}
        model = self.model_provider(approver.get("model"))
        if model is None:
            return "escalate"
        system = render_approver_prompt(
            resolve_prompt(approver.get("prompt"), self.cwd),
            self.plugins.get(pa.plugin_name),
            pa.args,
            pa.reason,
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
            if data.get("reason"):
                pa.reason = str(data["reason"])
            return decision
        except Exception as exc:  # noqa: BLE001
            print(f"[plugin] 审批 Agent 调用失败，转人类审批: {exc}")
            return "escalate"
