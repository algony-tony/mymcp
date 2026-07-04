# Recorder LLM HTTP Clients Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the openai/anthropic SDK adapters in the recorder with direct httpx clients, cutting ~13 MB RSS and removing both SDK dependency trees.

**Architecture:** Two new clients (`OpenAICompatClient`, `AnthropicHTTPClient`) implement the existing `LLMClient` protocol (`recorder/llm/base.py`, unchanged) by POSTing JSON to `/chat/completions` and `/v1/messages` respectively, sharing a tiny `post_json` helper. The factory swaps implementations; SDK adapters and their extras are deleted. No env var or provider-value changes.

**Tech Stack:** Python 3.12+, httpx (already a core dep), pytest + anyio + `httpx.MockTransport` for tests.

**Spec:** `docs/superpowers/specs/2026-07-04-recorder-llm-http-clients-design.md`

**Branch:** work on `feat/recorder-llm-http-clients` (already exists, contains the spec).

**Setup check before starting:**

```bash
cd /home/zhu/repos/mymcp
git checkout feat/recorder-llm-http-clients
source venv/bin/activate 2>/dev/null || true  # if a venv exists
pytest tests/recorder/llm/ -v --benchmark-disable  # must pass before you begin
```

---

### Task 1: Shared HTTP helper (`http_common.py`)

**Files:**
- Create: `src/mymcp/recorder/llm/http_common.py`
- Test: `tests/recorder/llm/test_http_common.py`

- [ ] **Step 1: Write the failing test**

Create `tests/recorder/llm/test_http_common.py`:

```python
import httpx
import pytest

from mymcp.recorder.llm.http_common import LLM_TIMEOUT, post_json


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.anyio
async def test_post_json_returns_parsed_body():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        return httpx.Response(200, json={"ok": True})

    async with _client(handler) as c:
        data = await post_json(c, "https://api.test/v1/x", {"a": 1})
    assert data == {"ok": True}


@pytest.mark.anyio
async def test_post_json_sends_payload_as_json():
    import json

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["content-type"] == "application/json"
        assert json.loads(request.content) == {"a": 1}
        return httpx.Response(200, json={})

    async with _client(handler) as c:
        await post_json(c, "https://api.test/v1/x", {"a": 1})


@pytest.mark.anyio
async def test_post_json_raises_on_non_2xx():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "bad key"})

    async with _client(handler) as c:
        with pytest.raises(httpx.HTTPStatusError):
            await post_json(c, "https://api.test/v1/x", {})


def test_llm_timeout_has_long_read():
    # Merge calls can take minutes on slow providers.
    assert LLM_TIMEOUT.read >= 600
    assert LLM_TIMEOUT.connect <= 10
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/recorder/llm/test_http_common.py -v --benchmark-disable`
Expected: FAIL with `ModuleNotFoundError: No module named 'mymcp.recorder.llm.http_common'`

- [ ] **Step 3: Write the implementation**

Create `src/mymcp/recorder/llm/http_common.py`:

```python
"""Shared HTTP plumbing for the direct LLM clients.

The recorder issues one non-streaming JSON POST per merge cycle. httpx
(already a core dependency) covers this natively — the vendor SDKs cost
~13 MB RSS for that single call. No client-level retries: recorder
resilience lives at the cycle level (circuit breaker, event-driven retry).
"""

import httpx

# Merge calls can legitimately take minutes on slow providers; match the
# SDKs' generous read timeout rather than httpx's 5s default.
LLM_TIMEOUT = httpx.Timeout(connect=5.0, read=600.0, write=30.0, pool=5.0)


async def post_json(client: httpx.AsyncClient, url: str, payload: dict) -> dict:
    """POST payload as JSON, raise on non-2xx, return the parsed body."""
    resp = await client.post(url, json=payload)
    resp.raise_for_status()
    return resp.json()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/recorder/llm/test_http_common.py -v --benchmark-disable`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add src/mymcp/recorder/llm/http_common.py tests/recorder/llm/test_http_common.py
git commit -m "feat(recorder): shared httpx helper for direct LLM clients"
```

---

### Task 2: OpenAI-compatible client (`openai_compat.py`)

**Files:**
- Create: `src/mymcp/recorder/llm/openai_compat.py`
- Test: `tests/recorder/llm/test_openai_compat.py`

Behavior contract (mirrors the SDK adapter being replaced):
- POST `{base_url or https://api.openai.com/v1}/chat/completions`, `Authorization: Bearer` header.
- `json_schema` given → try strict `json_schema` response_format; on HTTP **400 only**, retry once with `{"type": "json_object"}`. Any other error propagates.
- finish_reason map: `stop→end_turn`, `tool_calls→tool_use`, `length→max_tokens`, unknown→`end_turn`.
- Malformed `tool_calls[].function.arguments` → `input={}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/recorder/llm/test_openai_compat.py`:

```python
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


@pytest.mark.anyio
async def test_finish_reason_stop_maps_to_end_turn():
    c, _ = _client([_chat_response(content="done", finish_reason="stop")])
    r = await c.call(system="s", messages=[Message(role="user", content="hi")], max_tokens=10)
    assert r.stop_reason == "end_turn"
    assert r.tool_uses == []


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


@pytest.mark.anyio
async def test_base_url_override():
    c, captured = _client([_chat_response()], base_url="https://api.deepseek.com")
    await c.call(system="s", messages=[Message(role="user", content="hi")], max_tokens=10)
    assert str(captured[0].url) == "https://api.deepseek.com/chat/completions"


@pytest.mark.anyio
async def test_default_model_when_none():
    c, captured = _client([_chat_response()], model=None)
    await c.call(system="s", messages=[Message(role="user", content="hi")], max_tokens=10)
    assert json.loads(captured[0].content)["model"] == DEFAULT_MODEL


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


@pytest.mark.anyio
async def test_no_json_schema_omits_response_format():
    c, captured = _client([_chat_response()])
    await c.call(system="s", messages=[Message(role="user", content="hi")], max_tokens=10)
    assert "response_format" not in json.loads(captured[0].content)


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


@pytest.mark.anyio
async def test_timeout_propagates():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("boom")

    c = OpenAICompatClient(api_key="x", model="m", transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.ReadTimeout):
        await c.call(system="s", messages=[Message(role="user", content="hi")], max_tokens=10)


@pytest.mark.anyio
async def test_malformed_tool_arguments_become_empty_dict():
    tool_calls = [
        {"id": "t1", "type": "function", "function": {"name": "f", "arguments": "not json"}}
    ]
    c, _ = _client([_chat_response(tool_calls=tool_calls, finish_reason="tool_calls")])
    resp = await c.call(system="s", messages=[Message(role="user", content="hi")], max_tokens=10)
    assert resp.tool_uses[0].input == {}


@pytest.mark.anyio
async def test_null_content_becomes_empty_text():
    c, _ = _client([_chat_response(content=None)])
    resp = await c.call(system="s", messages=[Message(role="user", content="hi")], max_tokens=10)
    assert resp.text == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/recorder/llm/test_openai_compat.py -v --benchmark-disable`
Expected: FAIL with `ModuleNotFoundError: No module named 'mymcp.recorder.llm.openai_compat'`

- [ ] **Step 3: Write the implementation**

Create `src/mymcp/recorder/llm/openai_compat.py`:

```python
"""OpenAI-compatible chat-completions client implementing LLMClient.

Speaks the /chat/completions wire format directly over httpx — works with
OpenAI and compatible endpoints (e.g. DeepSeek) via base_url. Replaces the
openai SDK adapter, which cost ~13 MB RSS for a single non-streaming call
per merge cycle.
"""

import json
from typing import Any

import httpx

from mymcp.recorder.llm.base import (
    LLMResponse,
    Message,
    ToolSchema,
    ToolUse,
    Usage,
)
from mymcp.recorder.llm.http_common import LLM_TIMEOUT, post_json

DEFAULT_MODEL = "gpt-4o"
DEFAULT_BASE_URL = "https://api.openai.com/v1"

_FINISH_REASON_MAP: dict[str, str] = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "length": "max_tokens",
}


class OpenAICompatClient:
    """LLMClient over the OpenAI-compatible chat-completions HTTP API."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str | None = None,
        base_url: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._model = model or DEFAULT_MODEL
        self._http = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=LLM_TIMEOUT,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def call(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        max_tokens: int = 4096,
        json_schema: dict | None = None,
    ) -> LLMResponse:
        wire_messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in messages:
            wire_messages.extend(self._to_wire_messages(m))
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": wire_messages,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = [self._to_wire_tool(t) for t in tools]

        url = f"{self._base_url}/chat/completions"
        if json_schema is None:
            return self._from_wire(await post_json(self._http, url, payload))

        # Prefer Structured Outputs (strict json_schema); fall back to
        # json_object mode for providers that reject it with 400 (DeepSeek).
        strict = dict(payload)
        strict["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "merge_output",
                "schema": json_schema,
                "strict": True,
            },
        }
        try:
            return self._from_wire(await post_json(self._http, url, strict))
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 400:
                raise
            loose = dict(payload)
            loose["response_format"] = {"type": "json_object"}
            return self._from_wire(await post_json(self._http, url, loose))

    @staticmethod
    def _to_wire_messages(m: Message) -> list[dict[str, Any]]:
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
    def _to_wire_tool(t: ToolSchema) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            },
        }

    @staticmethod
    def _from_wire(data: dict) -> LLMResponse:
        choice = data["choices"][0]
        msg = choice["message"]
        text = msg.get("content") or ""
        tool_uses: list[ToolUse] = []
        for tc in msg.get("tool_calls") or []:
            try:
                args = json.loads(tc["function"]["arguments"])
            except (json.JSONDecodeError, TypeError):
                args = {}
            tool_uses.append(ToolUse(id=tc["id"], name=tc["function"]["name"], input=args))
        stop = _FINISH_REASON_MAP.get(choice.get("finish_reason"), "end_turn")
        usage = data.get("usage") or {}
        return LLMResponse(
            text=text,
            tool_uses=tool_uses,
            stop_reason=stop,  # type: ignore[arg-type]
            usage=Usage(
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
            ),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/recorder/llm/test_openai_compat.py -v --benchmark-disable`
Expected: 13 PASS

- [ ] **Step 5: Commit**

```bash
git add src/mymcp/recorder/llm/openai_compat.py tests/recorder/llm/test_openai_compat.py
git commit -m "feat(recorder): direct httpx OpenAI-compatible LLM client"
```

---

### Task 3: Anthropic HTTP client (`anthropic_http.py`)

**Files:**
- Create: `src/mymcp/recorder/llm/anthropic_http.py`
- Test: `tests/recorder/llm/test_anthropic_http.py`

Behavior contract (mirrors the SDK adapter being replaced):
- POST `{base_url or https://api.anthropic.com}/v1/messages`, headers `x-api-key` + `anthropic-version: 2023-06-01`.
- `json_schema` given → inject `emit_merge_output` tool and force it via `tool_choice`.
- Response: concatenate `text` blocks, collect `tool_use` blocks, pass `stop_reason`/usage through.

- [ ] **Step 1: Write the failing tests**

Create `tests/recorder/llm/test_anthropic_http.py`:

```python
import json

import httpx
import pytest

from mymcp.recorder.llm.base import Message, ToolResult, ToolSchema
from mymcp.recorder.llm.anthropic_http import DEFAULT_MODEL, AnthropicHTTPClient


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


@pytest.mark.anyio
async def test_tool_result_blocks():
    c, captured = _client([_messages_response()])
    await c.call(
        system="s",
        messages=[
            Message(role="user", content="hi"),
            Message(
                role="user",
                tool_results=[ToolResult(tool_use_id="t1", content="oops", is_error=True)],
            ),
        ],
        max_tokens=10,
    )
    payload = json.loads(captured[0].content)
    block = payload["messages"][-1]["content"][0]
    assert block == {
        "type": "tool_result",
        "tool_use_id": "t1",
        "content": "oops",
        "is_error": True,
    }


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


@pytest.mark.anyio
async def test_json_schema_forces_emit_tool():
    schema = {"type": "object", "properties": {"x": {"type": "string"}}}
    c, captured = _client([_messages_response()])
    await c.call(
        system="s",
        messages=[Message(role="user", content="hi")],
        max_tokens=10,
        json_schema=schema,
    )
    payload = json.loads(captured[0].content)
    assert payload["tool_choice"] == {"type": "tool", "name": "emit_merge_output"}
    assert payload["tools"][-1]["name"] == "emit_merge_output"
    assert payload["tools"][-1]["input_schema"] == schema


@pytest.mark.anyio
async def test_base_url_override():
    c, captured = _client([_messages_response()], base_url="https://proxy.example.com")
    await c.call(system="s", messages=[Message(role="user", content="hi")], max_tokens=10)
    assert str(captured[0].url) == "https://proxy.example.com/v1/messages"


@pytest.mark.anyio
async def test_default_model_when_none():
    c, captured = _client([_messages_response()], model=None)
    await c.call(system="s", messages=[Message(role="user", content="hi")], max_tokens=10)
    assert json.loads(captured[0].content)["model"] == DEFAULT_MODEL


@pytest.mark.anyio
async def test_non_2xx_propagates():
    c, captured = _client([500])
    with pytest.raises(httpx.HTTPStatusError):
        await c.call(system="s", messages=[Message(role="user", content="hi")], max_tokens=10)
    assert len(captured) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/recorder/llm/test_anthropic_http.py -v --benchmark-disable`
Expected: FAIL with `ModuleNotFoundError: No module named 'mymcp.recorder.llm.anthropic_http'`

- [ ] **Step 3: Write the implementation**

Create `src/mymcp/recorder/llm/anthropic_http.py`:

```python
"""Anthropic Messages API client implementing LLMClient.

Speaks /v1/messages directly over httpx, replacing the anthropic SDK
adapter. One non-streaming POST per merge cycle needs no SDK.
"""

from typing import Any

import httpx

from mymcp.recorder.llm.base import (
    LLMResponse,
    Message,
    ToolSchema,
    ToolUse,
    Usage,
)
from mymcp.recorder.llm.http_common import LLM_TIMEOUT, post_json

DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_BASE_URL = "https://api.anthropic.com"
API_VERSION = "2023-06-01"


class AnthropicHTTPClient:
    """LLMClient over the Anthropic Messages HTTP API."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str | None = None,
        base_url: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._model = model or DEFAULT_MODEL
        self._http = httpx.AsyncClient(
            headers={"x-api-key": api_key, "anthropic-version": API_VERSION},
            timeout=LLM_TIMEOUT,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def call(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        max_tokens: int = 4096,
        json_schema: dict | None = None,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self._model,
            "system": system,
            "messages": [self._to_wire_message(m) for m in messages],
            "max_tokens": max_tokens,
        }

        wire_tools: list[dict[str, Any]] = []
        if tools:
            wire_tools.extend(self._to_wire_tool(t) for t in tools)
        if json_schema is not None:
            # Inject a forced-call tool so Claude must emit conforming JSON
            # as its input. The result lands in LLMResponse.tool_uses.
            wire_tools.append(
                {
                    "name": "emit_merge_output",
                    "description": (
                        "Emit the structured merge output. The arguments object"
                        " must match the input_schema exactly."
                    ),
                    "input_schema": json_schema,
                }
            )
            payload["tool_choice"] = {"type": "tool", "name": "emit_merge_output"}
        if wire_tools:
            payload["tools"] = wire_tools

        data = await post_json(self._http, f"{self._base_url}/v1/messages", payload)
        return self._from_wire(data)

    @staticmethod
    def _to_wire_message(m: Message) -> dict[str, Any]:
        # tool_result blocks (user turn returning probe results)
        if m.tool_results:
            blocks: list[dict[str, Any]] = []
            for tr in m.tool_results:
                block: dict[str, Any] = {
                    "type": "tool_result",
                    "tool_use_id": tr.tool_use_id,
                    "content": tr.content,
                }
                if tr.is_error:
                    block["is_error"] = True
                blocks.append(block)
            return {"role": m.role, "content": blocks}

        # assistant turn with tool_use blocks
        if m.tool_uses:
            content_blocks: list[dict[str, Any]] = []
            text = m.content if isinstance(m.content, str) else ""
            if text:
                content_blocks.append({"type": "text", "text": text})
            for tu in m.tool_uses:
                content_blocks.append(
                    {"type": "tool_use", "id": tu.id, "name": tu.name, "input": tu.input}
                )
            return {"role": m.role, "content": content_blocks}

        # plain text turn
        text = m.content if isinstance(m.content, str) else ""
        return {"role": m.role, "content": text}

    @staticmethod
    def _to_wire_tool(t: ToolSchema) -> dict[str, Any]:
        return {
            "name": t.name,
            "description": t.description,
            "input_schema": t.input_schema,
        }

    @staticmethod
    def _from_wire(data: dict) -> LLMResponse:
        text_parts: list[str] = []
        tool_uses: list[ToolUse] = []
        for block in data.get("content") or []:
            btype = block.get("type")
            if btype == "text":
                text_parts.append(block["text"])
            elif btype == "tool_use":
                tool_uses.append(
                    ToolUse(id=block["id"], name=block["name"], input=dict(block["input"]))
                )
        usage = data.get("usage") or {}
        return LLMResponse(
            text="".join(text_parts),
            tool_uses=tool_uses,
            stop_reason=data["stop_reason"],
            usage=Usage(
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
            ),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/recorder/llm/test_anthropic_http.py -v --benchmark-disable`
Expected: 7 PASS

- [ ] **Step 5: Commit**

```bash
git add src/mymcp/recorder/llm/anthropic_http.py tests/recorder/llm/test_anthropic_http.py
git commit -m "feat(recorder): direct httpx Anthropic LLM client"
```

---

### Task 4: Switch the factory to the new clients

**Files:**
- Modify: `src/mymcp/recorder/llm/factory.py`
- Modify: `tests/recorder/llm/test_factory.py`
- Modify: `tests/recorder/test_wiring.py`

- [ ] **Step 1: Rewrite the factory tests (no SDK fakes needed anymore)**

Replace the entire content of `tests/recorder/llm/test_factory.py` with:

```python
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
```

- [ ] **Step 2: Run factory tests to verify they fail**

Run: `pytest tests/recorder/llm/test_factory.py -v --benchmark-disable`
Expected: `test_anthropic_factory` / `test_openai_factory` FAIL (factory still returns SDK adapter classes; SDKs not installed → RuntimeError). Key-validation tests may still pass — that's fine.

- [ ] **Step 3: Rewrite the factory**

Replace the entire content of `src/mymcp/recorder/llm/factory.py` with:

```python
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
```

- [ ] **Step 4: Clean up test_wiring.py (SDK fake no longer needed)**

In `tests/recorder/test_wiring.py`:

1. Delete the `_fake_anthropic` function (lines ~10-14):

```python
def _fake_anthropic(monkeypatch):
    mod = MagicMock()
    mod.AsyncAnthropic = MagicMock(return_value=MagicMock())
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    monkeypatch.delitem(sys.modules, "mymcp.recorder.llm.anthropic_client", raising=False)
```

2. Delete every `    _fake_anthropic(monkeypatch)` call line (use replace-all; there are several throughout the file).
3. Remove now-unused imports at the top (`import sys`, `from unittest.mock import MagicMock`) — verify with ruff in the next step. If a test function's `monkeypatch` parameter becomes unused, leave it; pytest tolerates unused fixtures.

- [ ] **Step 5: Run tests and lint**

Run: `pytest tests/recorder/llm/test_factory.py tests/recorder/test_wiring.py -v --benchmark-disable`
Expected: all PASS

Run: `ruff check tests/recorder/test_wiring.py src/mymcp/recorder/llm/factory.py`
Expected: clean (fix any unused-import findings it reports)

- [ ] **Step 6: Commit**

```bash
git add src/mymcp/recorder/llm/factory.py tests/recorder/llm/test_factory.py tests/recorder/test_wiring.py
git commit -m "feat(recorder): factory builds direct HTTP clients"
```

---

### Task 5: Delete the SDK adapters

**Files:**
- Delete: `src/mymcp/recorder/llm/openai_client.py`
- Delete: `src/mymcp/recorder/llm/anthropic_client.py`
- Delete: `tests/recorder/llm/test_openai_client.py`
- Delete: `tests/recorder/llm/test_anthropic_client.py`
- Modify: `tests/live/.env.live.example` (comment references)

- [ ] **Step 1: Delete the files**

```bash
git rm src/mymcp/recorder/llm/openai_client.py \
       src/mymcp/recorder/llm/anthropic_client.py \
       tests/recorder/llm/test_openai_client.py \
       tests/recorder/llm/test_anthropic_client.py
```

- [ ] **Step 2: Update module references in live-test env example**

In `tests/live/.env.live.example`, update the two comment lines:
- `# OpenAI-compatible (exercises mymcp.recorder.llm.openai_client)` → `# OpenAI-compatible (exercises mymcp.recorder.llm.openai_compat)`
- `# Anthropic-compatible (exercises mymcp.recorder.llm.anthropic_client)` → `# Anthropic-compatible (exercises mymcp.recorder.llm.anthropic_http)`

(Do not touch `tests/live/.env.live` — it is a local untracked file.)

- [ ] **Step 3: Verify nothing references the deleted modules**

Run: `grep -rn "openai_client\|anthropic_client" src/ tests/ --include="*.py" --include="*.example"`
Expected: no output (egg-info artifacts don't count; they regenerate on build)

- [ ] **Step 4: Run the full recorder test suite**

Run: `pytest tests/recorder/ -v --benchmark-disable`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add tests/live/.env.live.example
git commit -m "refactor(recorder): delete SDK adapters, superseded by direct HTTP clients"
```

---

### Task 6: Empty the extras; update docs

**Files:**
- Modify: `pyproject.toml` (recorder extras)
- Modify: `README.md` (3 locations)
- Modify: `CLAUDE.md` (recorder install paragraph)

- [ ] **Step 1: Empty the recorder extras in pyproject.toml**

Replace:

```toml
recorder-anthropic = ["anthropic>=0.40"]
recorder-openai    = ["openai>=1.40"]
recorder           = ["algony-mymcp[recorder-anthropic,recorder-openai]"]
```

with:

```toml
# The recorder speaks HTTP directly via httpx (a core dependency); no SDKs
# needed. Extras kept (empty) so existing install commands don't break.
recorder-anthropic = []
recorder-openai    = []
recorder           = []
```

- [ ] **Step 2: Verify the lockfile is unaffected**

The `dev` extra never included the recorder SDKs, so `requirements-dev.txt` should not change. Confirm:

```bash
grep -i "^openai\|^anthropic" requirements-dev.txt
```

Expected: no output. If pip-compile is available, optionally re-run the lockfile command from CLAUDE.md and confirm `git diff requirements-dev.txt` is empty.

- [ ] **Step 3: Update README.md**

Location 1 (~line 14), change:

```markdown
- **9 MCP tools**: `bash_execute`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`, `prepare_upload`, `prepare_download`, `server_overview` (optional, requires the recorder module)
```

to:

```markdown
- **9 MCP tools**: `bash_execute`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`, `prepare_upload`, `prepare_download`, `server_overview` (optional, enabled via `MYMCP_RECORDER_ENABLED=true`)
```

Location 2 (lines 129-133), replace the whole paragraph:

```markdown
`pip install algony-mymcp[recorder-anthropic]` (or `recorder-openai`, or
`recorder` for both) adds an asyncio module that maintains a self-updating
server overview document via LLM. Disabled by default; enable with
`MYMCP_RECORDER_ENABLED=true`. See `docs/superpowers/specs/2026-05-29-llm-recorder-design.md`
for full details.
```

with:

```markdown
An asyncio module that maintains a self-updating server overview document
via LLM. No extra install needed — LLM calls go through httpx, a core
dependency. Disabled by default; enable with `MYMCP_RECORDER_ENABLED=true`.
See `docs/superpowers/specs/2026-05-29-llm-recorder-design.md` for full details.
```

Location 3 (~lines 267-268), change:

```markdown
These only apply when the `[recorder]` / `[recorder-anthropic]` /
`[recorder-openai]` extra is installed.
```

to:

```markdown
These only apply when the recorder is enabled. No extra install is needed —
the recorder uses httpx, which is a core dependency.
```

- [ ] **Step 4: Update CLAUDE.md**

In the "Optional: llm-recorder" section, change:

```markdown
When installed (`pip install algony-mymcp[recorder]`, or `[recorder-anthropic]` /
`[recorder-openai]` for a single provider) and enabled
(`MYMCP_RECORDER_ENABLED=true`), `mymcp.recorder` runs an asyncio background
task that:
```

to:

```markdown
When enabled (`MYMCP_RECORDER_ENABLED=true`), `mymcp.recorder` runs an
asyncio background task that (no extra install needed — LLM calls go
through httpx, a core dependency):
```

- [ ] **Step 5: Sanity-check install metadata**

Run: `pip install -e . --dry-run 2>&1 | tail -5`
Expected: resolves without errors (empty extras are valid).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml README.md CLAUDE.md
git commit -m "chore: drop openai/anthropic SDK deps; recorder needs no extras"
```

---

### Task 7: Full verification and PR

- [ ] **Step 1: Full test suite**

Run: `pytest tests/ -v --benchmark-disable`
Expected: all PASS (live tests under `tests/live/` are skipped without `.env.live` keys — that's normal)

- [ ] **Step 2: Lint and type-check**

Run: `ruff check . && ruff format --check . && mypy src/mymcp`
Expected: clean

- [ ] **Step 3: Push and open PR**

```bash
git push
gh pr create \
  --title "refactor(recorder): replace LLM SDKs with direct httpx clients" \
  --body "$(cat <<'EOF'
## Summary
- Replace the openai/anthropic SDK adapters with direct httpx clients (`openai_compat.py`, `anthropic_http.py`) implementing the existing `LLMClient` protocol
- Measured on the deployed VPS: the openai SDK cost ~13 MB marginal RSS (~30% of the process footprint) for one non-streaming POST per merge cycle
- Recorder now needs no extras — httpx is a core dependency; `recorder-*` extras kept as empty stubs
- Behavior change (accepted in spec): no client-level retries; resilience stays at the cycle level (circuit breaker, event-driven retry)

Spec: docs/superpowers/specs/2026-07-04-recorder-llm-http-clients-design.md

## Test plan
- [x] New MockTransport suites for both clients (payload shapes, response mapping, 400→json_object fallback, non-400 propagation, timeout)
- [x] Factory/wiring tests updated, SDK fakes removed
- [x] Full suite + ruff + mypy

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4 (post-merge, manual): deploy to the ucloud instance and confirm the RSS drop**

```bash
pipx upgrade algony-mymcp && sudo systemctl restart mymcp
# after a merge cycle or two:
systemctl status mymcp | grep Memory   # expect ~30 MB or less
```
