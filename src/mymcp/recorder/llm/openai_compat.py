"""OpenAI-compatible chat-completions client implementing LLMClient.

Speaks the /chat/completions wire format directly over httpx — works with
OpenAI and compatible endpoints (e.g. DeepSeek) via base_url. Replaces the
openai SDK adapter, which cost ~13 MB RSS for a single non-streaming call
per merge cycle.
"""

import json
from typing import Any

import httpx

from mymcp.recorder.llm.base import (
    LLMResponse,
    Message,
    ToolSchema,
    ToolUse,
    Usage,
)
from mymcp.recorder.llm.http_common import LLM_TIMEOUT, post_json

DEFAULT_MODEL = "gpt-4o"
DEFAULT_BASE_URL = "https://api.openai.com/v1"

_FINISH_REASON_MAP: dict[str, str] = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "length": "max_tokens",
}


class OpenAICompatClient:
    """LLMClient over the OpenAI-compatible chat-completions HTTP API."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str | None = None,
        base_url: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._model = model or DEFAULT_MODEL
        self._http = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=LLM_TIMEOUT,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def call(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        max_tokens: int = 4096,
        json_schema: dict | None = None,
    ) -> LLMResponse:
        wire_messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in messages:
            wire_messages.extend(self._to_wire_messages(m))
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": wire_messages,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = [self._to_wire_tool(t) for t in tools]

        url = f"{self._base_url}/chat/completions"
        if json_schema is None:
            return self._from_wire(await post_json(self._http, url, payload))

        # Prefer Structured Outputs (strict json_schema); fall back to
        # json_object mode for providers that reject it with 400 (DeepSeek).
        strict = dict(payload)
        strict["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "merge_output",
                "schema": json_schema,
                "strict": True,
            },
        }
        try:
            return self._from_wire(await post_json(self._http, url, strict))
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 400:
                raise
            loose = dict(payload)
            loose["response_format"] = {"type": "json_object"}
            return self._from_wire(await post_json(self._http, url, loose))

    @staticmethod
    def _to_wire_messages(m: Message) -> list[dict[str, Any]]:
        # tool_results → multiple "tool" role messages
        if m.tool_results:
            return [
                {"role": "tool", "tool_call_id": tr.tool_use_id, "content": tr.content}
                for tr in m.tool_results
            ]

        # assistant with tool_calls
        if m.tool_uses:
            text = m.content if isinstance(m.content, str) else ""
            return [
                {
                    "role": m.role,
                    "content": text,
                    "tool_calls": [
                        {
                            "id": tu.id,
                            "type": "function",
                            "function": {
                                "name": tu.name,
                                "arguments": json.dumps(tu.input),
                            },
                        }
                        for tu in m.tool_uses
                    ],
                }
            ]

        # plain text turn
        text = m.content if isinstance(m.content, str) else ""
        return [{"role": m.role, "content": text}]

    @staticmethod
    def _to_wire_tool(t: ToolSchema) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            },
        }

    @staticmethod
    def _from_wire(data: dict) -> LLMResponse:
        choice = data["choices"][0]
        msg = choice["message"]
        text = msg.get("content") or ""
        tool_uses: list[ToolUse] = []
        for tc in msg.get("tool_calls") or []:
            try:
                args = json.loads(tc["function"]["arguments"])
            except (json.JSONDecodeError, TypeError):
                args = {}
            tool_uses.append(ToolUse(id=tc["id"], name=tc["function"]["name"], input=args))
        stop = _FINISH_REASON_MAP.get(choice.get("finish_reason"), "end_turn")
        usage = data.get("usage") or {}
        return LLMResponse(
            text=text,
            tool_uses=tool_uses,
            stop_reason=stop,  # type: ignore[arg-type]
            usage=Usage(
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
            ),
        )
