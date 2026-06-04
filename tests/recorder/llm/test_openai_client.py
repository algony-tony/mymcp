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
    # Mirror the real SDK: openai.BadRequestError is a concrete exception
    # class. Tests use it to simulate server-side 4xx rejections.
    mod.BadRequestError = type("BadRequestError", (Exception,), {})

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


@pytest.mark.anyio
async def test_openai_json_schema_sets_response_format(fake_openai):
    from mymcp.recorder.llm.openai_client import OpenAIClient

    schema = {"type": "object", "properties": {"x": {"type": "string"}}}
    c = OpenAIClient(api_key="x", model="m")
    await c.call(
        system="output JSON",
        messages=[Message(role="user", content="hi")],
        max_tokens=10,
        json_schema=schema,
    )
    kwargs = fake_openai.AsyncOpenAI.return_value.chat.completions.create.call_args.kwargs
    assert kwargs["response_format"]["type"] == "json_schema"
    assert kwargs["response_format"]["json_schema"]["schema"] == schema
    assert kwargs["response_format"]["json_schema"]["strict"] is True
    assert kwargs["response_format"]["json_schema"]["name"]


@pytest.mark.anyio
async def test_openai_no_json_schema_omits_response_format(fake_openai):
    from mymcp.recorder.llm.openai_client import OpenAIClient

    c = OpenAIClient(api_key="x", model="m")
    await c.call(system="s", messages=[Message(role="user", content="hi")], max_tokens=10)
    kwargs = fake_openai.AsyncOpenAI.return_value.chat.completions.create.call_args.kwargs
    assert "response_format" not in kwargs


@pytest.mark.anyio
async def test_openai_json_schema_falls_back_to_json_object_on_rejection(fake_openai, monkeypatch):
    """DeepSeek and other OpenAI-compat providers don't always support
    strict json_schema. Detect SDK rejection and retry with json_object."""
    from mymcp.recorder.llm.openai_client import OpenAIClient

    call_count = {"n": 0}

    async def fake_create(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            assert kwargs["response_format"]["type"] == "json_schema"
            raise TypeError("response_format.type 'json_schema' not supported")
        assert kwargs["response_format"] == {"type": "json_object"}
        from unittest.mock import MagicMock

        resp = MagicMock()
        msg = MagicMock(content='{"ok": true}', tool_calls=None)
        resp.choices = [MagicMock(message=msg, finish_reason="stop")]
        resp.usage = MagicMock(prompt_tokens=1, completion_tokens=1)
        return resp

    fake_openai.AsyncOpenAI.return_value.chat.completions.create = fake_create
    c = OpenAIClient(api_key="x", model="m")
    resp = await c.call(
        system="JSON only",
        messages=[Message(role="user", content="hi")],
        max_tokens=10,
        json_schema={"type": "object"},
    )
    assert call_count["n"] == 2
    assert resp.text == '{"ok": true}'


@pytest.mark.anyio
async def test_openai_json_schema_falls_back_on_bad_request_error(fake_openai):
    """DeepSeek (and other OpenAI-compat servers) accept the kwarg shape but
    reject strict json_schema as HTTP 400 → openai.BadRequestError. The
    fallback must catch it too, not just TypeError."""
    from mymcp.recorder.llm.openai_client import OpenAIClient

    call_count = {"n": 0}

    async def fake_create(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            assert kwargs["response_format"]["type"] == "json_schema"
            raise fake_openai.BadRequestError("response_format.type 'json_schema' is not supported")
        assert kwargs["response_format"] == {"type": "json_object"}
        resp = MagicMock()
        msg = MagicMock(content='{"ok": true}', tool_calls=None)
        resp.choices = [MagicMock(message=msg, finish_reason="stop")]
        resp.usage = MagicMock(prompt_tokens=1, completion_tokens=1)
        return resp

    fake_openai.AsyncOpenAI.return_value.chat.completions.create = fake_create
    c = OpenAIClient(api_key="x", model="m")
    resp = await c.call(
        system="JSON only",
        messages=[Message(role="user", content="hi")],
        max_tokens=10,
        json_schema={"type": "object"},
    )
    assert call_count["n"] == 2
    assert resp.text == '{"ok": true}'
