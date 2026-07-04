"""Anthropic Messages API client implementing LLMClient.

Speaks /v1/messages directly over httpx, replacing the anthropic SDK
adapter. One non-streaming POST per merge cycle needs no SDK.
"""

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

DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_BASE_URL = "https://api.anthropic.com"
API_VERSION = "2023-06-01"


class AnthropicHTTPClient:
    """LLMClient over the Anthropic Messages HTTP API."""

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
            headers={"x-api-key": api_key, "anthropic-version": API_VERSION},
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
        payload: dict[str, Any] = {
            "model": self._model,
            "system": system,
            "messages": [self._to_wire_message(m) for m in messages],
            "max_tokens": max_tokens,
        }

        wire_tools: list[dict[str, Any]] = []
        if tools:
            wire_tools.extend(self._to_wire_tool(t) for t in tools)
        if json_schema is not None:
            # Inject a forced-call tool so Claude must emit conforming JSON
            # as its input. The result lands in LLMResponse.tool_uses.
            wire_tools.append(
                {
                    "name": "emit_merge_output",
                    "description": (
                        "Emit the structured merge output. The arguments object"
                        " must match the input_schema exactly."
                    ),
                    "input_schema": json_schema,
                }
            )
            payload["tool_choice"] = {"type": "tool", "name": "emit_merge_output"}
        if wire_tools:
            payload["tools"] = wire_tools

        data = await post_json(self._http, f"{self._base_url}/v1/messages", payload)
        return self._from_wire(data)

    @staticmethod
    def _to_wire_message(m: Message) -> dict[str, Any]:
        # tool_result blocks (user turn returning probe results)
        if m.tool_results:
            blocks: list[dict[str, Any]] = []
            for tr in m.tool_results:
                block: dict[str, Any] = {
                    "type": "tool_result",
                    "tool_use_id": tr.tool_use_id,
                    "content": tr.content,
                }
                if tr.is_error:
                    block["is_error"] = True
                blocks.append(block)
            return {"role": m.role, "content": blocks}

        # assistant turn with tool_use blocks
        if m.tool_uses:
            content_blocks: list[dict[str, Any]] = []
            text = m.content if isinstance(m.content, str) else ""
            if text:
                content_blocks.append({"type": "text", "text": text})
            for tu in m.tool_uses:
                content_blocks.append(
                    {"type": "tool_use", "id": tu.id, "name": tu.name, "input": tu.input}
                )
            return {"role": m.role, "content": content_blocks}

        # plain text turn
        text = m.content if isinstance(m.content, str) else ""
        return {"role": m.role, "content": text}

    @staticmethod
    def _to_wire_tool(t: ToolSchema) -> dict[str, Any]:
        return {
            "name": t.name,
            "description": t.description,
            "input_schema": t.input_schema,
        }

    @staticmethod
    def _from_wire(data: dict[str, Any]) -> LLMResponse:
        text_parts: list[str] = []
        tool_uses: list[ToolUse] = []
        for block in data.get("content") or []:
            btype = block.get("type")
            if btype == "text":
                text_parts.append(block["text"])
            elif btype == "tool_use":
                tool_uses.append(
                    ToolUse(id=block["id"], name=block["name"], input=dict(block["input"]))
                )
        usage = data.get("usage") or {}
        return LLMResponse(
            text="".join(text_parts),
            tool_uses=tool_uses,
            stop_reason=data["stop_reason"],
            usage=Usage(
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
            ),
        )
