import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from mymcp.recorder.llm.base import Message, ToolResult, ToolSchema


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def fake_anthropic(monkeypatch):
    """Inject a fake `anthropic` SDK module for testing."""
    mod = MagicMock()

    block_text = MagicMock()
    block_text.type = "text"
    block_text.text = "hello"
    block_tu = MagicMock()
    block_tu.type = "tool_use"
    block_tu.id = "t1"
    block_tu.name = "bash_probe"
    block_tu.input = {"command": "ls"}

    resp = MagicMock()
    resp.content = [block_text, block_tu]
    resp.stop_reason = "tool_use"
    resp.usage = MagicMock(input_tokens=10, output_tokens=5)

    client_inst = MagicMock()
    client_inst.messages = MagicMock()
    client_inst.messages.create = AsyncMock(return_value=resp)
    mod.AsyncAnthropic = MagicMock(return_value=client_inst)

    monkeypatch.setitem(sys.modules, "anthropic", mod)
    # Force re-import of adapter so it picks up the fake module
    monkeypatch.delitem(sys.modules, "mymcp.recorder.llm.anthropic_client", raising=False)
    return mod


@pytest.mark.anyio
async def test_anthropic_call_translates_response(fake_anthropic):
    from mymcp.recorder.llm.anthropic_client import AnthropicClient

    c = AnthropicClient(api_key="x", model="claude-sonnet-4-6")
    resp = await c.call(
        system="sys",
        messages=[Message(role="user", content="hi")],
        tools=[ToolSchema(name="bash_probe", description="d", input_schema={"type": "object"})],
        max_tokens=1024,
    )
    assert resp.text == "hello"
    assert len(resp.tool_uses) == 1
    assert resp.tool_uses[0].name == "bash_probe"
    assert resp.tool_uses[0].input == {"command": "ls"}
    assert resp.stop_reason == "tool_use"
    assert resp.usage.input_tokens == 10
    assert resp.usage.output_tokens == 5
    # Verify create() was called with correct shape
    fake_anthropic.AsyncAnthropic.return_value.messages.create.assert_called_once()
    kwargs = fake_anthropic.AsyncAnthropic.return_value.messages.create.call_args.kwargs
    assert kwargs["system"] == "sys"
    assert kwargs["max_tokens"] == 1024
    assert kwargs["model"] == "claude-sonnet-4-6"
    assert kwargs["messages"] == [{"role": "user", "content": "hi"}]
    assert kwargs["tools"][0]["name"] == "bash_probe"
    assert kwargs["tools"][0]["input_schema"] == {"type": "object"}


@pytest.mark.anyio
async def test_anthropic_uses_default_model_when_none(fake_anthropic):
    from mymcp.recorder.llm.anthropic_client import DEFAULT_MODEL, AnthropicClient

    c = AnthropicClient(api_key="x", model=None)
    await c.call(system="s", messages=[Message(role="user", content="hi")], max_tokens=100)
    kwargs = fake_anthropic.AsyncAnthropic.return_value.messages.create.call_args.kwargs
    assert kwargs["model"] == DEFAULT_MODEL


@pytest.mark.anyio
async def test_anthropic_tool_result_message_translation(fake_anthropic):
    from mymcp.recorder.llm.anthropic_client import AnthropicClient

    c = AnthropicClient(api_key="x", model="m")
    await c.call(
        system="s",
        messages=[
            Message(role="user", content="hi"),
            Message(
                role="user",
                tool_results=[
                    ToolResult(tool_use_id="t1", content="ok"),
                    ToolResult(tool_use_id="t2", content="boom", is_error=True),
                ],
            ),
        ],
        max_tokens=100,
    )
    kwargs = fake_anthropic.AsyncAnthropic.return_value.messages.create.call_args.kwargs
    msgs = kwargs["messages"]
    assert msgs[0] == {"role": "user", "content": "hi"}
    blocks = msgs[1]["content"]
    assert blocks[0] == {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}
    assert blocks[1] == {
        "type": "tool_result",
        "tool_use_id": "t2",
        "content": "boom",
        "is_error": True,
    }


@pytest.mark.anyio
async def test_anthropic_passes_base_url(monkeypatch, fake_anthropic):
    from mymcp.recorder.llm.anthropic_client import AnthropicClient

    AnthropicClient(api_key="x", model="m", base_url="https://api.example.com")
    # base_url passed to constructor
    call_args = fake_anthropic.AsyncAnthropic.call_args
    assert call_args.kwargs["base_url"] == "https://api.example.com"


def test_anthropic_missing_sdk_raises_clear_error(monkeypatch):
    # Force ImportError on `import anthropic`
    monkeypatch.setitem(sys.modules, "anthropic", None)
    monkeypatch.delitem(sys.modules, "mymcp.recorder.llm.anthropic_client", raising=False)
    from mymcp.recorder.llm.anthropic_client import AnthropicClient

    with pytest.raises(RuntimeError, match="recorder-anthropic"):
        AnthropicClient(api_key="x", model="x")
