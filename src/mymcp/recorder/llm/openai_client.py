"""OpenAI SDK adapter implementing the LLMClient protocol.

Lazy-imports the `openai` SDK so the module loads without it. Supports
OpenAI-compatible endpoints (e.g. DeepSeek) via base_url.
"""

import json
from typing import Any

from mymcp.recorder.llm.base import (
    LLMResponse,
    Message,
    ToolSchema,
    ToolUse,
    Usage,
)

DEFAULT_MODEL = "gpt-4o"

_FINISH_REASON_MAP: dict[str, str] = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "length": "max_tokens",
}


def _import_sdk() -> Any:
    try:
        import openai  # type: ignore[import-not-found, unused-ignore]
    except ImportError as e:
        raise RuntimeError(
            "openai SDK not installed. Install with: pip install 'algony-mymcp[recorder-openai]'"
        ) from e
    if openai is None:
        raise RuntimeError(
            "openai SDK not installed. Install with: pip install 'algony-mymcp[recorder-openai]'"
        )
    return openai


class OpenAIClient:
    """Adapter exposing the LLMClient protocol over the openai SDK."""

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
        self._client = sdk.AsyncOpenAI(**kwargs)
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
        sdk_messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in messages:
            sdk_messages.extend(self._to_sdk_messages(m))
        base_kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": sdk_messages,
            "max_tokens": max_tokens,
        }
        if tools:
            base_kwargs["tools"] = [self._to_sdk_tool(t) for t in tools]

        if json_schema is None:
            resp = await self._client.chat.completions.create(**base_kwargs)
            return self._from_sdk_response(resp)

        # Prefer Structured Outputs (strict json_schema); fall back to
        # json_object mode for providers that don't support it yet (DeepSeek).
        strict_kwargs = dict(base_kwargs)
        strict_kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "merge_output",
                "schema": json_schema,
                "strict": True,
            },
        }
        try:
            resp = await self._client.chat.completions.create(**strict_kwargs)
        except TypeError:
            loose_kwargs = dict(base_kwargs)
            loose_kwargs["response_format"] = {"type": "json_object"}
            resp = await self._client.chat.completions.create(**loose_kwargs)
        return self._from_sdk_response(resp)

    @staticmethod
    def _to_sdk_messages(m: Message) -> list[dict[str, Any]]:
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
    def _to_sdk_tool(t: ToolSchema) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            },
        }

    @staticmethod
    def _from_sdk_response(resp: Any) -> LLMResponse:
        choice = resp.choices[0]
        msg = choice.message
        text = msg.content or ""
        tool_uses: list[ToolUse] = []
        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments)
            except (json.JSONDecodeError, TypeError):
                args = {}
            tool_uses.append(ToolUse(id=tc.id, name=tc.function.name, input=args))
        stop = _FINISH_REASON_MAP.get(choice.finish_reason, "end_turn")
        return LLMResponse(
            text=text,
            tool_uses=tool_uses,
            stop_reason=stop,  # type: ignore[arg-type]
            usage=Usage(
                input_tokens=resp.usage.prompt_tokens,
                output_tokens=resp.usage.completion_tokens,
            ),
        )
