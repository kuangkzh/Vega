"""OpenAI 协议模型实现（requests 直连 /chat/completions，支持 SSE 流式）。"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterator, List, Optional

import requests

from models.base import BaseModel, ModelResponse, StreamEvent, retry_call


class OpenAIModel(BaseModel):
    def _url(self) -> str:
        base = self.config.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    def _payload(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]], stream: bool) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"model": self.config.id, "messages": messages, "stream": stream}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if stream:
            payload["stream_options"] = {"include_usage": True}
        return payload

    def chat(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None) -> ModelResponse:
        def _do() -> Dict[str, Any]:
            resp = requests.post(
                self._url(), headers=self._headers(),
                json=self._payload(messages, tools, stream=False), timeout=300,
            )
            resp.raise_for_status()
            return resp.json()

        data = retry_call(_do, attempts=3)
        return self._parse_completion(data)

    def chat_stream(
        self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None
    ) -> Iterator[StreamEvent]:
        def _do() -> requests.Response:
            resp = requests.post(
                self._url(), headers=self._headers(),
                json=self._payload(messages, tools, stream=True), timeout=300, stream=True,
            )
            resp.raise_for_status()
            return resp

        resp = retry_call(_do, attempts=3)

        content_parts: List[str] = []
        tool_calls_acc: Dict[int, Dict[str, str]] = {}
        usage_data: Optional[Dict[str, Any]] = None

        for raw_line in resp.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            line = raw_line.strip()
            if not line.startswith("data:"):
                continue
            data_str = line[len("data:"):].strip()
            if data_str == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            if chunk.get("usage"):
                usage_data = chunk["usage"]
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            if delta.get("content"):
                content_parts.append(delta["content"])
                yield StreamEvent(kind="text", text=delta["content"])
            for tc in delta.get("tool_calls") or []:
                idx = int(tc.get("index", 0))
                acc = tool_calls_acc.setdefault(idx, {"id": "", "name": "", "args": ""})
                if tc.get("id"):
                    acc["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    acc["name"] = fn["name"]
                if fn.get("arguments"):
                    acc["args"] += fn["arguments"]

        tool_calls: List[Dict[str, Any]] = []
        for idx in sorted(tool_calls_acc):
            acc = tool_calls_acc[idx]
            try:
                args_obj = json.loads(acc["args"]) if acc["args"] else {}
            except json.JSONDecodeError:
                args_obj = {}
            tool_calls.append({
                "id": acc["id"],
                "type": "function",
                "function": {
                    "name": acc["name"],
                    "arguments": json.dumps(args_obj, ensure_ascii=False),
                },
            })

        if tool_calls:
            yield StreamEvent(kind="tool_calls", tool_calls=tool_calls)
        yield StreamEvent(kind="usage", usage=self.get_usage({"usage": usage_data} if usage_data else {}))
        yield StreamEvent(kind="done")

    def _parse_completion(self, data: Dict[str, Any]) -> ModelResponse:
        choices = data.get("choices") or []
        content = ""
        tool_calls: List[Dict[str, Any]] = []
        if choices:
            msg = choices[0].get("message") or {}
            content = msg.get("content") or ""
            tool_calls = msg.get("tool_calls") or []
        return ModelResponse(content=content, tool_calls=tool_calls, usage=self.get_usage(data))