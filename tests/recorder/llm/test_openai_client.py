import json
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from mymcp.recorder.llm.base import Message, ToolResult, ToolSchema


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def fake_openai(monkeypatch):
    mod = MagicMock()

    msg = MagicMock()
    msg.content = "hello"
    tc = MagicMock()
    tc.id = "t1"
    tc.function = MagicMock()
    tc.function.name = "bash_probe"
    tc.function.arguments = json.dumps({"command": "ls"})
    msg.tool_calls = [tc]

    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = "tool_calls"

    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = MagicMock(prompt_tokens=10, completion_tokens=5)

    client_inst = MagicMock()
    client_inst.chat = MagicMock()
    client_inst.chat.completions = MagicMock()
    client_inst.chat.completions.create = AsyncMock(return_value=resp)
    mod.AsyncOpenAI = MagicMock(return_value=client_inst)

    monkeypatch.setitem(sys.modules, "openai", mod)
    monkeypatch.delitem(sys.modules, "mymcp.recorder.llm.openai_client", raising=False)
    return mod


@pytest.mark.anyio
async def test_openai_call_translates_response(fake_openai):
    from mymcp.recorder.llm.openai_client import OpenAIClient

    c = OpenAIClient(api_key="x", model="gpt-4o")
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

    kwargs = fake_openai.AsyncOpenAI.return_value.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "gpt-4o"
    assert kwargs["max_tokens"] == 1024
    # system prepended into messages list
    assert kwargs["messages"][0] == {"role": "system", "content": "sys"}
    assert kwargs["messages"][1] == {"role": "user", "content": "hi"}
    # tool schema converted
    assert kwargs["tools"][0]["type"] == "function"
    assert kwargs["tools"][0]["function"]["name"] == "bash_probe"
    assert kwargs["tools"][0]["function"]["parameters"] == {"type": "object"}


@pytest.mark.anyio
async def test_openai_finish_reason_mapping(monkeypatch, fake_openai):
    from mymcp.recorder.llm.openai_client import OpenAIClient

    c = OpenAIClient(api_key="x", model="m")

    # Test stop → end_turn
    resp_stop = MagicMock()
    msg_stop = MagicMock(content="done", tool_calls=None)
    resp_stop.choices = [MagicMock(message=msg_stop, finish_reason="stop")]
    resp_stop.usage = MagicMock(prompt_tokens=1, completion_tokens=1)
    fake_openai.AsyncOpenAI.return_value.chat.completions.create = AsyncMock(return_value=resp_stop)

    r = await c.call(system="s", messages=[Message(role="user", content="hi")], max_tokens=10)
    assert r.stop_reason == "end_turn"
    assert r.tool_uses == []


@pytest.mark.anyio
async def test_openai_tool_result_message(fake_openai):
    from mymcp.recorder.llm.openai_client import OpenAIClient

    c = OpenAIClient(api_key="x", model="m")
    await c.call(
        system="s",
        messages=[
            Message(role="user", content="hi"),
            Message(
                role="user",
                tool_results=[ToolResult(tool_use_id="t1", content="ok")],
            ),
        ],
        max_tokens=10,
    )
    kwargs = fake_openai.AsyncOpenAI.return_value.chat.completions.create.call_args.kwargs
    msgs = kwargs["messages"]
    # system + user content + tool result
    assert msgs[-1] == {"role": "tool", "tool_call_id": "t1", "content": "ok"}


@pytest.mark.anyio
async def test_openai_passes_base_url(fake_openai):
    from mymcp.recorder.llm.openai_client import OpenAIClient

    OpenAIClient(api_key="x", model="m", base_url="https://api.deepseek.com")
    assert fake_openai.AsyncOpenAI.call_args.kwargs["base_url"] == "https://api.deepseek.com"


@pytest.mark.anyio
async def test_openai_uses_default_model_when_none(fake_openai):
    from mymcp.recorder.llm.openai_client import DEFAULT_MODEL, OpenAIClient

    c = OpenAIClient(api_key="x", model=None)
    await c.call(system="s", messages=[Message(role="user", content="hi")], max_tokens=10)
    kwargs = fake_openai.AsyncOpenAI.return_value.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == DEFAULT_MODEL


def test_openai_missing_sdk_raises_clear_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "openai", None)
    monkeypatch.delitem(sys.modules, "mymcp.recorder.llm.openai_client", raising=False)
    from mymcp.recorder.llm.openai_client import OpenAIClient

    with pytest.raises(RuntimeError, match="recorder-openai"):
        OpenAIClient(api_key="x", model="x")
