import json

import httpx
import pytest

from mymcp.recorder.llm.anthropic_http import DEFAULT_MODEL, AnthropicHTTPClient
from mymcp.recorder.llm.base import Message, ToolResult, ToolSchema


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _messages_response(blocks=None, stop_reason="end_turn"):
    return {
        "content": blocks if blocks is not None else [{"type": "text", "text": "hello"}],
        "stop_reason": stop_reason,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


def _client(responses, **kwargs):
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        item = responses[len(captured) - 1]
        if isinstance(item, int):
            return httpx.Response(item, json={"error": {"message": "boom"}})
        return httpx.Response(200, json=item)

    kwargs.setdefault("model", "m")
    c = AnthropicHTTPClient(
        api_key="secret-key",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )
    return c, captured


@pytest.mark.anyio
async def test_call_translates_response_and_request():
    blocks = [
        {"type": "text", "text": "hello"},
        {"type": "tool_use", "id": "t1", "name": "bash_probe", "input": {"command": "ls"}},
    ]
    c, captured = _client([_messages_response(blocks=blocks, stop_reason="tool_use")])
    resp = await c.call(
        system="sys",
        messages=[Message(role="user", content="hi")],
        tools=[ToolSchema(name="bash_probe", description="d", input_schema={"type": "object"})],
        max_tokens=1024,
    )
    assert resp.text == "hello"
    assert resp.tool_uses[0].name == "bash_probe"
    assert resp.tool_uses[0].input == {"command": "ls"}
    assert resp.stop_reason == "tool_use"
    assert resp.usage.input_tokens == 10
    assert resp.usage.output_tokens == 5

    req = captured[0]
    assert str(req.url) == "https://api.anthropic.com/v1/messages"
    assert req.headers["x-api-key"] == "secret-key"
    assert req.headers["anthropic-version"] == "2023-06-01"
    payload = json.loads(req.content)
    assert payload["model"] == "m"
    assert payload["system"] == "sys"
    assert payload["max_tokens"] == 1024
    assert payload["messages"][0] == {"role": "user", "content": "hi"}
    assert payload["tools"][0] == {
        "name": "bash_probe",
        "description": "d",
        "input_schema": {"type": "object"},
    }
    await c.aclose()


@pytest.mark.anyio
async def test_tool_result_blocks():
    c, captured = _client([_messages_response()])
    await c.call(
        system="s",
        messages=[
            Message(role="user", content="hi"),
            Message(
                role="user",
                tool_results=[
                    ToolResult(tool_use_id="t1", content="oops", is_error=True),
                    ToolResult(tool_use_id="t2", content="ok"),
                ],
            ),
        ],
        max_tokens=10,
    )
    payload = json.loads(captured[0].content)
    blocks = payload["messages"][-1]["content"]
    assert blocks[0] == {
        "type": "tool_result",
        "tool_use_id": "t1",
        "content": "oops",
        "is_error": True,
    }
    assert blocks[1] == {"type": "tool_result", "tool_use_id": "t2", "content": "ok"}
    assert "is_error" not in blocks[1]
    await c.aclose()


@pytest.mark.anyio
async def test_assistant_tool_use_blocks():
    from mymcp.recorder.llm.base import ToolUse

    c, captured = _client([_messages_response()])
    await c.call(
        system="s",
        messages=[
            Message(role="user", content="hi"),
            Message(
                role="assistant",
                content="thinking",
                tool_uses=[ToolUse(id="t1", name="bash_probe", input={"command": "ls"})],
            ),
        ],
        max_tokens=10,
    )
    payload = json.loads(captured[0].content)
    blocks = payload["messages"][-1]["content"]
    assert blocks[0] == {"type": "text", "text": "thinking"}
    assert blocks[1] == {
        "type": "tool_use",
        "id": "t1",
        "name": "bash_probe",
        "input": {"command": "ls"},
    }
    await c.aclose()


@pytest.mark.anyio
async def test_json_schema_forces_emit_tool():
    schema = {"type": "object", "properties": {"x": {"type": "string"}}}
    resp_blocks = [
        {"type": "tool_use", "id": "x", "name": "emit_merge_output", "input": {"key": "val"}}
    ]
    c, captured = _client([_messages_response(blocks=resp_blocks, stop_reason="tool_use")])
    resp = await c.call(
        system="s",
        messages=[Message(role="user", content="hi")],
        max_tokens=10,
        json_schema=schema,
    )
    payload = json.loads(captured[0].content)
    assert payload["tool_choice"] == {"type": "tool", "name": "emit_merge_output"}
    assert payload["tools"][-1]["name"] == "emit_merge_output"
    assert payload["tools"][-1]["input_schema"] == schema
    assert resp.tool_uses[0].name == "emit_merge_output"
    assert resp.tool_uses[0].input == {"key": "val"}
    assert resp.stop_reason == "tool_use"
    await c.aclose()


@pytest.mark.anyio
async def test_no_tools_omits_tools_and_tool_choice():
    c, captured = _client([_messages_response()])
    await c.call(system="s", messages=[Message(role="user", content="hi")], max_tokens=10)
    payload = json.loads(captured[0].content)
    assert "tools" not in payload
    assert "tool_choice" not in payload
    await c.aclose()


@pytest.mark.anyio
async def test_base_url_override():
    c, captured = _client([_messages_response()], base_url="https://proxy.example.com")
    await c.call(system="s", messages=[Message(role="user", content="hi")], max_tokens=10)
    assert str(captured[0].url) == "https://proxy.example.com/v1/messages"
    await c.aclose()


@pytest.mark.anyio
async def test_default_model_when_none():
    c, captured = _client([_messages_response()], model=None)
    await c.call(system="s", messages=[Message(role="user", content="hi")], max_tokens=10)
    assert json.loads(captured[0].content)["model"] == DEFAULT_MODEL
    await c.aclose()


@pytest.mark.anyio
async def test_non_2xx_propagates():
    c, captured = _client([500])
    with pytest.raises(httpx.HTTPStatusError):
        await c.call(system="s", messages=[Message(role="user", content="hi")], max_tokens=10)
    assert len(captured) == 1
    await c.aclose()


@pytest.mark.anyio
async def test_timeout_propagates():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("boom")

    c = AnthropicHTTPClient(api_key="x", model="m", transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.ReadTimeout):
        await c.call(system="s", messages=[Message(role="user", content="hi")], max_tokens=10)
    await c.aclose()
