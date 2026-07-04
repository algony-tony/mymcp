import pytest

from mymcp.recorder.llm.anthropic_http import AnthropicHTTPClient
from mymcp.recorder.llm.factory import build_llm_client
from mymcp.recorder.llm.openai_compat import OpenAICompatClient


def test_anthropic_factory():
    c = build_llm_client(provider="anthropic", api_key="k", model=None, base_url=None)
    assert isinstance(c, AnthropicHTTPClient)


def test_openai_factory():
    c = build_llm_client(
        provider="openai", api_key="k", model=None, base_url="https://api.deepseek.com"
    )
    assert isinstance(c, OpenAICompatClient)


def test_unknown_provider_raises():
    with pytest.raises(ValueError, match="unknown provider"):
        build_llm_client(provider="grok", api_key="k", model=None, base_url=None)  # type: ignore[arg-type]


def test_missing_api_key_anthropic(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValueError, match="API key"):
        build_llm_client(provider="anthropic", api_key=None, model=None, base_url=None)


def test_missing_api_key_openai(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="API key"):
        build_llm_client(provider="openai", api_key=None, model=None, base_url=None)


def test_anthropic_falls_back_to_env_var(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    c = build_llm_client(provider="anthropic", api_key=None, model=None, base_url=None)
    assert isinstance(c, AnthropicHTTPClient)


def test_openai_falls_back_to_env_var(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "from-env")
    c = build_llm_client(provider="openai", api_key=None, model=None, base_url=None)
    assert isinstance(c, OpenAICompatClient)
