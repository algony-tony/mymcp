"""LLM client types and protocol.

The recorder needs to call an LLM without binding to a specific provider's
wire format. This module defines provider-agnostic message/tool types and an
LLMClient Protocol that the HTTP clients implement.

The abstraction intentionally covers only what recorder needs: text + tool
use + token usage. No streaming, vision, prompt caching, or batch.
"""

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class ToolUse:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class ToolResult:
    tool_use_id: str
    content: str
    is_error: bool = False


@dataclass
class Message:
    """A conversation message.

    Plain text: set content to a string, leave tool_uses/tool_results empty.
    Assistant turn requesting tool use: content + tool_uses.
    User turn returning tool results: tool_results (content typically empty).
    Clients translate these to/from provider-specific wire formats.
    """

    role: Literal["user", "assistant"]
    content: str | list[Any] = ""
    tool_uses: list[ToolUse] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)


@dataclass
class ToolSchema:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class LLMResponse:
    text: str
    tool_uses: list[ToolUse]
    stop_reason: Literal["end_turn", "tool_use", "max_tokens", "stop_sequence"]
    usage: Usage

    @property
    def usage_total(self) -> int:
        return self.usage.input_tokens + self.usage.output_tokens

    @property
    def is_end_turn(self) -> bool:
        return self.stop_reason == "end_turn"


class LLMClient(Protocol):
    """Provider-agnostic LLM call interface.

    When ``json_schema`` is given, the adapter must coerce the model to emit
    JSON conforming to that schema. The parsed dict lands in either
    ``LLMResponse.tool_uses[0].input`` (Anthropic uses forced tool_use) or
    ``LLMResponse.text`` (OpenAI uses response_format). Callers should look
    at tool_uses first and fall back to parsing text.
    """

    async def call(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        max_tokens: int = 4096,
        json_schema: dict | None = None,
    ) -> LLMResponse: ...
