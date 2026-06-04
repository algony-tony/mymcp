"""Anthropic SDK adapter implementing the LLMClient protocol.

The `anthropic` SDK is lazy-imported so the module loads without it.
Instantiating AnthropicClient raises a clear RuntimeError if the SDK is
missing, telling the user which extra to install.
"""

from typing import Any

from mymcp.recorder.llm.base import (
    LLMResponse,
    Message,
    ToolSchema,
    ToolUse,
    Usage,
)

DEFAULT_MODEL = "claude-sonnet-4-6"


def _import_sdk() -> Any:
    try:
        import anthropic  # type: ignore[import-not-found]
    except ImportError as e:
        raise RuntimeError(
            "anthropic SDK not installed. "
            "Install with: pip install 'algony-mymcp[recorder-anthropic]'"
        ) from e
    if anthropic is None:
        # Tests can blank out sys.modules['anthropic']
        raise RuntimeError(
            "anthropic SDK not installed. "
            "Install with: pip install 'algony-mymcp[recorder-anthropic]'"
        )
    return anthropic


class AnthropicClient:
    """Adapter exposing the LLMClient protocol over the anthropic SDK."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str | None = None,
        base_url: str | None = None,
    ):
        sdk = _import_sdk()
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = sdk.AsyncAnthropic(**kwargs)
        self._model = model or DEFAULT_MODEL

    async def call(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        max_tokens: int = 4096,
        json_schema: dict | None = None,
    ) -> LLMResponse:
        sdk_messages = [self._to_sdk_message(m) for m in messages]
        kwargs: dict[str, Any] = {
            "model": self._model,
            "system": system,
            "messages": sdk_messages,
            "max_tokens": max_tokens,
        }

        sdk_tools: list[dict[str, Any]] = []
        if tools:
            sdk_tools.extend(self._to_sdk_tool(t) for t in tools)
        if json_schema is not None:
            # Inject a forced-call tool so Claude must emit conforming JSON
            # as its input. The result lands in LLMResponse.tool_uses.
            sdk_tools.append(
                {
                    "name": "emit_merge_output",
                    "description": (
                        "Emit the structured merge output. The arguments object"
                        " must match the input_schema exactly."
                    ),
                    "input_schema": json_schema,
                }
            )
            kwargs["tool_choice"] = {"type": "tool", "name": "emit_merge_output"}
        if sdk_tools:
            kwargs["tools"] = sdk_tools

        resp = await self._client.messages.create(**kwargs)
        return self._from_sdk_response(resp)

    @staticmethod
    def _to_sdk_message(m: Message) -> dict[str, Any]:
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
    def _to_sdk_tool(t: ToolSchema) -> dict[str, Any]:
        return {
            "name": t.name,
            "description": t.description,
            "input_schema": t.input_schema,
        }

    @staticmethod
    def _from_sdk_response(resp: Any) -> LLMResponse:
        text_parts: list[str] = []
        tool_uses: list[ToolUse] = []
        for block in resp.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(block.text)
            elif btype == "tool_use":
                tool_uses.append(ToolUse(id=block.id, name=block.name, input=dict(block.input)))
        return LLMResponse(
            text="".join(text_parts),
            tool_uses=tool_uses,
            stop_reason=resp.stop_reason,
            usage=Usage(
                input_tokens=resp.usage.input_tokens,
                output_tokens=resp.usage.output_tokens,
            ),
        )
