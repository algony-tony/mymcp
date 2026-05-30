import sys
from unittest.mock import MagicMock

import pytest

from mymcp.recorder.llm.factory import build_llm_client


def _fake_anthropic(monkeypatch):
    mod = MagicMock()
    mod.AsyncAnthropic = MagicMock(return_value=MagicMock())
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    monkeypatch.delitem(sys.modules, "mymcp.recorder.llm.anthropic_client", raising=False)


def _fake_openai(monkeypatch):
    mod = MagicMock()
    mod.AsyncOpenAI = MagicMock(return_value=MagicMock())
    monkeypatch.setitem(sys.modules, "openai", mod)
    monkeypatch.delitem(sys.modules, "mymcp.recorder.llm.openai_client", raising=False)


def test_anthropic_factory(monkeypatch):
    _fake_anthropic(monkeypatch)
    c = build_llm_client(
        provider="anthropic",
        api_key="k",
        model=None,
        base_url=None,
    )
    assert c.__class__.__name__ == "AnthropicClient"


def test_openai_factory(monkeypatch):
    _fake_openai(monkeypatch)
    c = build_llm_client(
        provider="openai",
        api_key="k",
        model=None,
        base_url="https://api.deepseek.com",
    )
    assert c.__class__.__name__ == "OpenAIClient"


def test_unknown_provider_raises():
    with pytest.raises(ValueError, match="unknown provider"):
        build_llm_client(provider="grok", api_key="k", model=None, base_url=None)  # type: ignore[arg-type]


def test_missing_api_key_anthropic(monkeypatch):
    _fake_anthropic(monkeypatch)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValueError, match="API key"):
        build_llm_client(provider="anthropic", api_key=None, model=None, base_url=None)


def test_missing_api_key_openai(monkeypatch):
    _fake_openai(monkeypatch)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="API key"):
        build_llm_client(provider="openai", api_key=None, model=None, base_url=None)


def test_anthropic_falls_back_to_env_var(monkeypatch):
    _fake_anthropic(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    c = build_llm_client(provider="anthropic", api_key=None, model=None, base_url=None)
    assert c.__class__.__name__ == "AnthropicClient"


def test_openai_falls_back_to_env_var(monkeypatch):
    _fake_openai(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "from-env")
    c = build_llm_client(provider="openai", api_key=None, model=None, base_url=None)
    assert c.__class__.__name__ == "OpenAIClient"
