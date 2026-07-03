import json

import httpx
import pytest

from mymcp.recorder.llm.base import Message, ToolResult, ToolSchema
from mymcp.recorder.llm.openai_compat import DEFAULT_MODEL, OpenAICompatClient


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _chat_response(content="hello", tool_calls=None, finish_reason="stop"):
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": tool_calls,
                },
                "finish_reason": finish_reason,
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def _client(responses, **kwargs):
    """Client backed by MockTransport. `responses` items are dicts (200 JSON
    bodies) or ints (error status codes), consumed one per request.
    Returns (client, captured_requests)."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        item = responses[len(captured) - 1]
        if isinstance(item, int):
            return httpx.Response(item, json={"error": {"message": "boom"}})
        return httpx.Response(200, json=item)

    kwargs.setdefault("model", "m")
    c = OpenAICompatClient(
        api_key="secret-key",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )
    return c, captured


@pytest.mark.anyio
async def test_call_translates_response_and_request():
    tool_calls = [
        {
            "id": "t1",
            "type": "function",
            "function": {"name": "bash_probe", "arguments": json.dumps({"command": "ls"})},
        }
    ]
    c, captured = _client([_chat_response(tool_calls=tool_calls, finish_reason="tool_calls")])
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
    assert str(req.url) == "https://api.openai.com/v1/chat/completions"
    assert req.headers["authorization"] == "Bearer secret-key"
    payload = json.loads(req.content)
    assert payload["model"] == "m"
    assert payload["max_tokens"] == 1024
    assert payload["messages"][0] == {"role": "system", "content": "sys"}
    assert payload["messages"][1] == {"role": "user", "content": "hi"}
    assert payload["tools"][0]["type"] == "function"
    assert payload["tools"][0]["function"]["name"] == "bash_probe"
    assert payload["tools"][0]["function"]["parameters"] == {"type": "object"}
    await c.aclose()


@pytest.mark.anyio
async def test_finish_reason_stop_maps_to_end_turn():
    c, captured = _client([_chat_response(content="done", finish_reason="stop")])
    r = await c.call(system="s", messages=[Message(role="user", content="hi")], max_tokens=10)
    assert r.stop_reason == "end_turn"
    assert r.tool_uses == []
    assert "tools" not in json.loads(captured[0].content)
    await c.aclose()


@pytest.mark.anyio
async def test_unknown_finish_reason_defaults_to_end_turn():
    c, _ = _client([_chat_response(finish_reason="content_filter")])
    r = await c.call(system="s", messages=[Message(role="user", content="hi")], max_tokens=10)
    assert r.stop_reason == "end_turn"
    await c.aclose()


@pytest.mark.anyio
async def test_tool_result_message_becomes_tool_role():
    c, captured = _client([_chat_response()])
    await c.call(
        system="s",
        messages=[
            Message(role="user", content="hi"),
            Message(role="user", tool_results=[ToolResult(tool_use_id="t1", content="ok")]),
        ],
        max_tokens=10,
    )
    payload = json.loads(captured[0].content)
    assert payload["messages"][-1] == {"role": "tool", "tool_call_id": "t1", "content": "ok"}
    await c.aclose()


@pytest.mark.anyio
async def test_assistant_tool_uses_round_trip():
    from mymcp.recorder.llm.base import ToolUse

    c, captured = _client([_chat_response()])
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
    assistant = payload["messages"][-1]
    assert assistant["role"] == "assistant"
    assert assistant["content"] == "thinking"
    assert assistant["tool_calls"][0]["id"] == "t1"
    assert assistant["tool_calls"][0]["function"]["name"] == "bash_probe"
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {"command": "ls"}
    await c.aclose()


@pytest.mark.anyio
async def test_base_url_override():
    c, captured = _client([_chat_response()], base_url="https://api.deepseek.com")
    await c.call(system="s", messages=[Message(role="user", content="hi")], max_tokens=10)
    assert str(captured[0].url) == "https://api.deepseek.com/chat/completions"
    await c.aclose()


@pytest.mark.anyio
async def test_default_model_when_none():
    c, captured = _client([_chat_response()], model=None)
    await c.call(system="s", messages=[Message(role="user", content="hi")], max_tokens=10)
    assert json.loads(captured[0].content)["model"] == DEFAULT_MODEL
    await c.aclose()


@pytest.mark.anyio
async def test_json_schema_sets_strict_response_format():
    schema = {"type": "object", "properties": {"x": {"type": "string"}}}
    c, captured = _client([_chat_response(content='{"x": "y"}')])
    await c.call(
        system="JSON only",
        messages=[Message(role="user", content="hi")],
        max_tokens=10,
        json_schema=schema,
    )
    rf = json.loads(captured[0].content)["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["schema"] == schema
    assert rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["name"]
    await c.aclose()


@pytest.mark.anyio
async def test_no_json_schema_omits_response_format():
    c, captured = _client([_chat_response()])
    await c.call(system="s", messages=[Message(role="user", content="hi")], max_tokens=10)
    assert "response_format" not in json.loads(captured[0].content)
    await c.aclose()


@pytest.mark.anyio
async def test_json_schema_falls_back_to_json_object_on_400():
    """DeepSeek et al. reject strict json_schema with HTTP 400; retry once
    with json_object."""
    c, captured = _client([400, _chat_response(content='{"ok": true}')])
    resp = await c.call(
        system="JSON only",
        messages=[Message(role="user", content="hi")],
        max_tokens=10,
        json_schema={"type": "object"},
    )
    assert len(captured) == 2
    assert json.loads(captured[0].content)["response_format"]["type"] == "json_schema"
    assert json.loads(captured[1].content)["response_format"] == {"type": "json_object"}
    assert resp.text == '{"ok": true}'
    await c.aclose()


@pytest.mark.anyio
async def test_non_400_errors_propagate_without_fallback():
    """Auth/quota/server errors must NOT trigger the json_object retry."""
    c, captured = _client([401])
    with pytest.raises(httpx.HTTPStatusError):
        await c.call(
            system="JSON only",
            messages=[Message(role="user", content="hi")],
            max_tokens=10,
            json_schema={"type": "object"},
        )
    assert len(captured) == 1
    await c.aclose()


@pytest.mark.anyio
async def test_timeout_propagates():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("boom")

    c = OpenAICompatClient(api_key="x", model="m", transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.ReadTimeout):
        await c.call(system="s", messages=[Message(role="user", content="hi")], max_tokens=10)
    await c.aclose()


@pytest.mark.anyio
async def test_malformed_tool_arguments_become_empty_dict():
    tool_calls = [
        {"id": "t1", "type": "function", "function": {"name": "f", "arguments": "not json"}}
    ]
    c, _ = _client([_chat_response(tool_calls=tool_calls, finish_reason="tool_calls")])
    resp = await c.call(system="s", messages=[Message(role="user", content="hi")], max_tokens=10)
    assert resp.tool_uses[0].input == {}
    await c.aclose()


@pytest.mark.anyio
async def test_null_content_becomes_empty_text():
    c, _ = _client([_chat_response(content=None)])
    resp = await c.call(system="s", messages=[Message(role="user", content="hi")], max_tokens=10)
    assert resp.text == ""
    await c.aclose()
