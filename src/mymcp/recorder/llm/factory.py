"""Build a configured LLMClient based on provider settings."""

import os
from typing import Literal

from mymcp.recorder.llm.anthropic_http import AnthropicHTTPClient
from mymcp.recorder.llm.base import LLMClient
from mymcp.recorder.llm.openai_compat import OpenAICompatClient


def build_llm_client(
    *,
    provider: Literal["anthropic", "openai"],
    api_key: str | None,
    model: str | None,
    base_url: str | None,
) -> LLMClient:
    if provider == "anthropic":
        api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    elif provider == "openai":
        api_key = api_key or os.environ.get("OPENAI_API_KEY")
    else:
        raise ValueError(f"unknown provider: {provider!r}")

    if not api_key:
        raise ValueError(
            f"recorder LLM provider {provider!r} requires an API key. "
            f"Set MYMCP_RECORDER_LLM_API_KEY or {provider.upper()}_API_KEY."
        )

    if provider == "anthropic":
        return AnthropicHTTPClient(api_key=api_key, model=model, base_url=base_url)

    return OpenAICompatClient(api_key=api_key, model=model, base_url=base_url)
