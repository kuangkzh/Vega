"""插件框架：基类、权限解析、运行时统一包装、插件发现加载、审批流程。"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import threading
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

DEFAULT_LEVEL = "Denied"
LEVELS = ("Denied", "Trusted", "AgentReview", "HumanReview")
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


class Plugin(ABC):
    """插件基类。子类需定义 name/description/parameters 并实现 run。"""

    name: str = ""
    description: str = ""
    parameters: Dict[str, Any] = {"type": "object", "properties": {}}

    def resolve_permission(self, config: Dict[str, Any], args: Dict[str, Any], cwd: str = "") -> str:
        """默认权限：plugins.<name>.global，缺省回退 permissions.default（默认 Denied）。"""
        perms = config.get("permissions") or {}
        plugins = perms.get("plugins") or {}
        p = plugins.get(self.name) or {}
        return str(p.get("global") or perms.get("default") or DEFAULT_LEVEL)

    @abstractmethod
    def run(self, ctx: "PluginContext", args: Dict[str, Any]) -> PluginResult: ...

    def tool_spec(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }


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


def discover(plugins_dir: str) -> Dict[str, Plugin]:
    """扫描 plugins/*.py，收集所有 Plugin 子类。base.py 只有基类，不会被收集。"""
    found: Dict[str, Plugin] = {}
    if not plugins_dir or not os.path.isdir(plugins_dir):
        return found
    for fn in sorted(os.listdir(plugins_dir)):
        if not fn.endswith(".py") or fn.startswith("_") or fn == "base.py":
            continue
        path = os.path.join(plugins_dir, fn)
        mod_name = f"ca_plugin_{fn[:-3]}"
        spec = importlib.util.spec_from_file_location(mod_name, path)
        if not spec or not spec.loader:
            continue
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod  # dataclass 需要模块已注册
        try:
            spec.loader.exec_module(mod)
        except Exception as exc:  # noqa: BLE001
            sys.modules.pop(mod_name, None)
            print(f"[plugins] 加载 {fn} 失败: {exc}")
            continue
        for obj in vars(mod).values():
            if isinstance(obj, type) and issubclass(obj, Plugin) and obj is not Plugin:
                inst = obj()
                if inst.name:
                    found[inst.name] = inst
    return found


@dataclass
class PluginContext:
    """插件运行时上下文。"""

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
        store,
        model_registry,
        task_manager=None,
        message_source=None,
        model_provider: Optional[Callable[[Optional[str]], Any]] = None,
    ):
        self.plugins = discover(plugins_dir)
        self.config = config or {}
        self.cwd = cwd
        self.store = store
        self.registry = model_registry
        self.task_manager = task_manager
        self.message_source = message_source
        self.model_provider = model_provider or (lambda _mid=None: None)
        self.pending: Dict[str, PendingApproval] = {}
        self.last_overflow_result: Optional[str] = None
        self._lock = threading.RLock()

    def get(self, name: str) -> Optional[Plugin]:
        return self.plugins.get(name)

    def visible_tools(self) -> List[Dict[str, Any]]:
        tools: List[Dict[str, Any]] = []
        for name, p in self.plugins.items():
            if p.resolve_permission(self.config, {}, self.cwd) != "Denied":
                tools.append(p.tool_spec())
        return tools

    def execute(self, plugin_name: str, args: Dict[str, Any], session_id: Optional[str] = None) -> PluginResult:
        plugin = self.plugins.get(plugin_name)
        if plugin is None:
            return PluginResult({}, f"未知插件: {plugin_name}")
        args = args or {}
        level = plugin.resolve_permission(self.config, args, self.cwd)
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
        ctx = PluginContext(cwd=self.cwd, config=self.config, runtime=self, session_id=session_id)
        try:
            res = plugin.run(ctx, args)
        except Exception as exc:  # noqa: BLE001
            return PluginResult({}, f"插件执行出错: {type(exc).__name__}: {exc}")
        if not isinstance(res, PluginResult):
            res = PluginResult(res, "")
        res.msg = render_msg(res.msg, res.result)
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
            pa = self.pending.get(uuid_)
        if pa is None:
            return PluginResult({}, f"找不到审批申请 uuid={uuid_}")
        with self._lock:
            self.pending.pop(uuid_, None)
        tm = self.task_manager
        if not approved:
            if tm is not None and getattr(tm, "finish_task", None):
                tm.finish_task(pa.task_id, "done", {"result": {}, "msg": f"审批被拒绝：{reason}"})
            return PluginResult({}, f"审批被拒绝：{reason}")
        plugin = self.plugins.get(pa.plugin_name)
        if plugin is None:
            return PluginResult({}, f"未知插件: {pa.plugin_name}")
        if tm is not None and getattr(tm, "run_pending", None):
            tm.run_pending(
                pa.task_id or "", pa.session_id,
                lambda: self._run_and_wrap(plugin, pa.args, pa.session_id),
                pa.plugin_name,
            )
            return PluginResult({}, "审批通过，已启动后台执行。")
        return self._run_and_wrap(plugin, pa.args, pa.session_id)

    def _start_human_approval(self, pa: PendingApproval) -> PluginResult:
        tm = self.task_manager
        params = {"uuid": pa.uuid, "plugin": pa.plugin_name, "args": pa.args, "reason": pa.reason, "task_name": pa.task_name}
        if tm is not None and getattr(tm, "submit_pending", None):
            task_id = tm.submit_pending(pa.task_name or pa.plugin_name, pa.session_id, params)
            pa.task_id = task_id
        else:
            task_id = pa.uuid
        if self.message_source is not None and getattr(self.message_source, "notify_approval", None):
            try:
                self.message_source.notify_approval(pa)
            except Exception as exc:  # noqa: BLE001
                print(f"[plugin] 审批通知失败: {exc}")
        return PluginResult({}, f"已提交后台人工审批（级别=HumanReview），任务标识={task_id}，等待人工批准后自动执行。")

    def _run_approver(self, pa: PendingApproval) -> str:
        approver = self.config.get("approver") or {}
        model = self.model_provider(approver.get("model"))
        if model is None:
            return "escalate"
        prompt = approver.get("prompt") or "你是权限审批助手，请判断是否批准以下工具调用。"
        payload = {"plugin": pa.plugin_name, "args": pa.args, "reason": pa.reason}
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": "请审批以下请求，只输出 JSON(decision=approve|reject|escalate, reason)：\n" + json.dumps(payload, ensure_ascii=False)},
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