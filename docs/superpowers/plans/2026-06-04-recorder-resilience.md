# Recorder Resilience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the llm-recorder merge cycle against LLM output truncation, empty responses, permanently-stuck cursors, and provider-specific output guarantees. Companion: `docs/superpowers/specs/2026-06-04-recorder-resilience-design.md`.

**Architecture:** Refactor the merge protocol from "rewrite the whole overview" to "section-level updates with Python-owned header and Recent Changes". Add a `json_schema` parameter to `LLMClient.call()` so each provider can pick its strongest enforcement (OpenAI Structured Outputs; Anthropic forced `tool_use`). Add a circuit breaker in the supervisor and expose its state as an OTel gauge.

**Tech Stack:** Python 3.11+ • pydantic-settings • OpenTelemetry • OpenAI SDK 1.x • Anthropic SDK 0.34+ • pytest + anyio (asyncio).

---

## Conventions

- All commands run from the repo root: `/home/zhu/repos/mymcp`.
- Python interpreter: `.venv/bin/python`.
- Run tests with `.venv/bin/python -m pytest <path> --benchmark-disable`.
- Each task ends with `git add <files> && git commit -m "<msg>"`. Do **not** push until the final task.
- All tests use `@pytest.mark.anyio` for async functions (`anyio_backend` fixture returns `"asyncio"`).
- Settings tests reset the cache: `from mymcp.config import reset_settings_cache; reset_settings_cache()`.
- After every task, also run `.venv/bin/ruff format <changed files>` and `.venv/bin/ruff check <changed files>`.

---

## Task 1: Fix pre-existing mypy `[unused-ignore]` in openai_client

**Files:**
- Modify: `src/mymcp/recorder/llm/openai_client.py:29`

The `# type: ignore[import-not-found]` becomes unused when the `[recorder-openai]` extra is installed (dev env) but is required when it isn't. Widen the ignore to make itself conditional.

- [ ] **Step 1: Confirm the current error**

Run: `.venv/bin/mypy src/mymcp 2>&1 | grep openai_client`

Expected output includes: `src/mymcp/recorder/llm/openai_client.py:29: error: Unused "type: ignore" comment  [unused-ignore]`

- [ ] **Step 2: Apply the fix**

Edit `src/mymcp/recorder/llm/openai_client.py` line 29, change:

```python
        import openai  # type: ignore[import-not-found]
```

to:

```python
        import openai  # type: ignore[import-not-found, unused-ignore]
```

- [ ] **Step 3: Verify mypy is clean for this file**

Run: `.venv/bin/mypy src/mymcp 2>&1 | grep openai_client; echo "exit=$?"`

Expected: no `openai_client.py` lines. The whole mypy run should report `Found 0 errors`.

- [ ] **Step 4: Commit**

```bash
git add src/mymcp/recorder/llm/openai_client.py
git commit -m "fix(mypy): widen type:ignore on conditional openai import

Without [recorder-openai], the import-not-found ignore is required;
with the extra installed it becomes unused. Use a dual-key ignore so
the ignore itself is conditional."
```

---

## Task 2: Add `recorder_llm_max_tokens` + `recorder_circuit_breaker_threshold` settings

**Files:**
- Modify: `src/mymcp/config.py`
- Test: `tests/recorder/test_config.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/recorder/test_config.py`:

```python
def test_recorder_llm_max_tokens_default(monkeypatch):
    monkeypatch.delenv("MYMCP_RECORDER_LLM_MAX_TOKENS", raising=False)
    from mymcp.config import Settings, reset_settings_cache

    reset_settings_cache()
    s = Settings()
    assert s.recorder_llm_max_tokens == 16384


def test_recorder_llm_max_tokens_override(monkeypatch):
    monkeypatch.setenv("MYMCP_RECORDER_LLM_MAX_TOKENS", "65536")
    from mymcp.config import Settings, reset_settings_cache

    reset_settings_cache()
    s = Settings()
    assert s.recorder_llm_max_tokens == 65536


def test_recorder_circuit_breaker_threshold_default(monkeypatch):
    monkeypatch.delenv("MYMCP_RECORDER_CIRCUIT_BREAKER_THRESHOLD", raising=False)
    from mymcp.config import Settings, reset_settings_cache

    reset_settings_cache()
    s = Settings()
    assert s.recorder_circuit_breaker_threshold == 5
```

- [ ] **Step 2: Run, expect failures**

Run: `.venv/bin/python -m pytest tests/recorder/test_config.py -v --benchmark-disable`

Expected: 3 new tests fail with `AttributeError: 'Settings' object has no attribute 'recorder_llm_max_tokens'` / `recorder_circuit_breaker_threshold`.

- [ ] **Step 3: Add settings**

In `src/mymcp/config.py`, in the `Settings` class, after the line `recorder_llm_base_url: str | None = Field(default=None)`, insert:

```python
    # Per-call output ceiling for the recorder's LLM. Must stay ≤ the chosen
    # model's max output (Claude Haiku/Sonnet 4.6: 64k, Opus 4.8: 128k,
    # GPT-5.x: 128k, DeepSeek v4: 384k); the API rejects values above the
    # model's limit. 16384 is a safe cross-provider default; downstream
    # deployments can raise it for providers with larger ceilings.
    recorder_llm_max_tokens: int = Field(default=16384)
    # Recorder supervisor pauses LLM calls after this many consecutive
    # merge_cycle failures. Restart the service to resume. 0 disables.
    recorder_circuit_breaker_threshold: int = Field(default=5)
```

In the `_LEGACY_ATTRS` dict at the bottom of the file, after the line `"RECORDER_LLM_BASE_URL": "recorder_llm_base_url",`, insert:

```python
    "RECORDER_LLM_MAX_TOKENS": "recorder_llm_max_tokens",
    "RECORDER_CIRCUIT_BREAKER_THRESHOLD": "recorder_circuit_breaker_threshold",
```

- [ ] **Step 4: Run tests, expect pass**

Run: `.venv/bin/python -m pytest tests/recorder/test_config.py -v --benchmark-disable`

Expected: all tests pass.

- [ ] **Step 5: Format + commit**

```bash
.venv/bin/ruff format src/mymcp/config.py tests/recorder/test_config.py
git add src/mymcp/config.py tests/recorder/test_config.py
git commit -m "feat(config): add recorder_llm_max_tokens and circuit_breaker_threshold

Defaults: 16384 tokens (safe across Claude/OpenAI/DeepSeek output limits)
and 5 consecutive failures (restart to reset)."
```

---

## Task 3: Add `parse_sections` + `apply_section_updates` helpers

**Files:**
- Modify: `src/mymcp/recorder/overview.py`
- Test: `tests/recorder/test_overview.py`

Pure functions for splitting an overview into sections and folding partial updates back in. Used by `merge_cycle` to support the new incremental protocol.

- [ ] **Step 1: Write failing tests**

Append to `tests/recorder/test_overview.py`:

```python
from mymcp.recorder.overview import apply_section_updates, parse_sections


def test_parse_sections_splits_at_h2_headers():
    text = (
        "# Server Overview\n"
        "_meta_\n"
        "\n"
        "## TL;DR\n"
        "Short summary.\n"
        "\n"
        "## Installed Services\n"
        "- nginx\n"
        "- redis\n"
    )
    header, sections = parse_sections(text)
    assert "# Server Overview" in header and "_meta_" in header
    assert [name for name, _ in sections] == ["TL;DR", "Installed Services"]
    assert sections[0][1] == "Short summary."
    assert "nginx" in sections[1][1] and "redis" in sections[1][1]


def test_parse_sections_no_header_block():
    header, sections = parse_sections("## Only Section\nbody\n")
    assert header == ""
    assert sections == [("Only Section", "body")]


def test_parse_sections_empty_input():
    assert parse_sections("") == ("", [])


def test_apply_section_updates_replaces_only_listed_sections():
    current = (
        "# H\n_m_\n"
        "\n## TL;DR\nKeep me.\n"
        "\n## Known Quirks\n- preserve\n"
    )
    result = apply_section_updates(
        current, header=None, section_updates={"TL;DR": "Updated."}
    )
    assert "Keep me." not in result
    assert "Updated." in result
    assert "preserve" in result
    assert "_m_" in result  # header preserved when header=None


def test_apply_section_updates_appends_new_sections_at_end():
    current = "# H\n\n## A\nfoo\n"
    result = apply_section_updates(current, header=None, section_updates={"B": "bar"})
    assert result.index("## A") < result.index("## B")
    assert "foo" in result and "bar" in result


def test_apply_section_updates_overrides_header_when_given():
    current = "# Old\n_old_\n\n## TL;DR\nx\n"
    result = apply_section_updates(
        current, header="# New\n_new_", section_updates={}
    )
    assert "Old" not in result
    assert "New" in result and "_new_" in result
    assert "## TL;DR" in result and "x" in result
```

- [ ] **Step 2: Run, expect ImportError**

Run: `.venv/bin/python -m pytest tests/recorder/test_overview.py -v --benchmark-disable`

Expected: `ImportError: cannot import name 'apply_section_updates' from 'mymcp.recorder.overview'`.

- [ ] **Step 3: Implement helpers**

In `src/mymcp/recorder/overview.py`, replace the module docstring + imports + `OverviewStore` class header (lines 1-12) with:

```python
"""Atomic read/write of overview.md and append-only changelog.md.

OverviewStore owns the recorder's two output files. The overview is rewritten
in place on each merge (atomic via tmp + os.replace); the changelog is
append-only.

Section helpers (parse_sections / apply_section_updates) support incremental
merges: the LLM emits only the sections that changed, and Python folds them
into the existing overview without rewriting unchanged sections.
"""

import os
from pathlib import Path


def parse_sections(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Split overview markdown into (header_block, [(section_name, body)]).

    The header block is everything before the first '## ' line (typically
    '# Title' plus a metadata block). Section bodies are the content between
    a '## name' line and the next '## ' line (exclusive), with surrounding
    blank lines stripped. Returned in original order so callers can preserve
    layout when reassembling.
    """
    lines = text.split("\n")
    header_lines: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    current_name: str | None = None
    current_body: list[str] = []
    for line in lines:
        if line.startswith("## "):
            if current_name is not None:
                sections.append((current_name, current_body))
            current_name = line[3:].strip()
            current_body = []
        elif current_name is None:
            header_lines.append(line)
        else:
            current_body.append(line)
    if current_name is not None:
        sections.append((current_name, current_body))
    header = "\n".join(header_lines).rstrip("\n")
    out = [(name, "\n".join(body).strip("\n")) for name, body in sections]
    return header, out


def apply_section_updates(
    current: str,
    *,
    header: str | None,
    section_updates: dict[str, str],
) -> str:
    """Fold per-section updates into an existing overview, preserving order.

    Sections present in section_updates have their bodies replaced; new
    section names are appended at the end. header (if not None) replaces the
    pre-first-section block; pass None to keep the existing header. Sections
    not mentioned in section_updates keep their existing bodies.
    """
    existing_header, existing = parse_sections(current)
    body_map = dict(existing)
    order = [name for name, _ in existing]
    for name, body in section_updates.items():
        if name not in body_map:
            order.append(name)
        body_map[name] = body.strip("\n")

    parts: list[str] = []
    final_header = header if header is not None else existing_header
    if final_header:
        parts.append(final_header.rstrip("\n"))
    for name in order:
        parts.append(f"## {name}\n{body_map[name]}".rstrip("\n"))
    return "\n\n".join(parts).rstrip("\n") + "\n"


class OverviewStore:
```

(The rest of `OverviewStore` is unchanged.)

- [ ] **Step 4: Run tests, expect pass**

Run: `.venv/bin/python -m pytest tests/recorder/test_overview.py -v --benchmark-disable`

Expected: all overview tests pass (old + new).

- [ ] **Step 5: Format + commit**

```bash
.venv/bin/ruff format src/mymcp/recorder/overview.py tests/recorder/test_overview.py
git add src/mymcp/recorder/overview.py tests/recorder/test_overview.py
git commit -m "feat(recorder): add parse_sections + apply_section_updates helpers

Pure functions for splitting an overview into (header, [(name, body)])
and folding partial section updates back in. Used by the new incremental
merge protocol."
```

---

## Task 4: Add `render_recent_changes` helper

**Files:**
- Modify: `src/mymcp/recorder/overview.py`
- Test: `tests/recorder/test_overview.py`

Python-owned rendering of the `Recent Changes` section from the changelog tail. Lets merge_cycle stop asking the LLM to regenerate this every cycle.

- [ ] **Step 1: Write failing tests**

Append to `tests/recorder/test_overview.py`:

```python
from mymcp.recorder.overview import render_recent_changes


def test_render_recent_changes_newest_first():
    tail = [
        "2026-06-01 10:00 | bash_execute | installed nginx",
        "2026-06-02 11:00 | write_file | wrote /etc/foo",
        "2026-06-03 12:00 | bash_execute | restarted nginx",
    ]
    out = render_recent_changes(tail)
    lines = out.splitlines()
    # Newest first, prefixed with '- '
    assert lines[0] == "- 2026-06-03 12:00 | bash_execute | restarted nginx"
    assert lines[1] == "- 2026-06-02 11:00 | write_file | wrote /etc/foo"
    assert lines[2] == "- 2026-06-01 10:00 | bash_execute | installed nginx"
    # Trailing pointer to full changelog
    assert lines[-1] == "_Full changelog: changelog.md (use read_file)_"


def test_render_recent_changes_empty():
    out = render_recent_changes([])
    assert "_Full changelog:" in out
    # No bulleted items when there's nothing
    assert "- " not in out.split("\n")[0]


def test_render_recent_changes_caps_at_10():
    tail = [f"2026-06-{i:02d} 10:00 | bash_execute | event {i}" for i in range(1, 16)]
    out = render_recent_changes(tail)
    bullet_lines = [line for line in out.splitlines() if line.startswith("- ")]
    assert len(bullet_lines) == 10
    # Newest-first ⇒ first bullet should reference event 15.
    assert "event 15" in bullet_lines[0]
```

- [ ] **Step 2: Run, expect ImportError**

Run: `.venv/bin/python -m pytest tests/recorder/test_overview.py -k render_recent_changes -v --benchmark-disable`

Expected: `ImportError: cannot import name 'render_recent_changes'`.

- [ ] **Step 3: Implement**

Append to `src/mymcp/recorder/overview.py`, immediately after the `apply_section_updates` function (before the `class OverviewStore:` line):

```python
def render_recent_changes(changelog_tail: list[str], *, limit: int = 10) -> str:
    """Render the 'Recent Changes' section body from changelog lines.

    changelog_tail is expected in file order (oldest first). Output is
    newest-first, capped at ``limit`` entries, with a trailing pointer to
    the full changelog.
    """
    newest_first = list(reversed(changelog_tail))[:limit]
    bullets = [f"- {line}" for line in newest_first]
    footer = "_Full changelog: changelog.md (use read_file)_"
    if bullets:
        return "\n".join(bullets) + "\n" + footer
    return footer
```

- [ ] **Step 4: Run, expect pass**

Run: `.venv/bin/python -m pytest tests/recorder/test_overview.py -v --benchmark-disable`

Expected: all overview tests pass.

- [ ] **Step 5: Format + commit**

```bash
.venv/bin/ruff format src/mymcp/recorder/overview.py tests/recorder/test_overview.py
git add src/mymcp/recorder/overview.py tests/recorder/test_overview.py
git commit -m "feat(recorder): add render_recent_changes helper

Python-owned renderer for the Recent Changes section. Lets merge_cycle
stop asking the LLM to regenerate it every cycle, which is the only
mandatory section update today."
```

---

## Task 5: Add `json_schema` parameter to LLMClient Protocol

**Files:**
- Modify: `src/mymcp/recorder/llm/base.py`
- Test: `tests/recorder/llm/test_base.py`

Interface change only: add the parameter to the Protocol so subsequent tasks can implement it per-provider.

- [ ] **Step 1: Write a failing test**

Append to `tests/recorder/llm/test_base.py`:

```python
def test_llm_client_protocol_accepts_json_schema():
    """The Protocol must accept json_schema as a keyword-only argument.

    We can't directly call a Protocol, but we can confirm the signature has
    the parameter by checking the Protocol class's annotations.
    """
    import inspect

    from mymcp.recorder.llm.base import LLMClient

    sig = inspect.signature(LLMClient.call)
    assert "json_schema" in sig.parameters
    assert sig.parameters["json_schema"].default is None
```

- [ ] **Step 2: Run, expect failure**

Run: `.venv/bin/python -m pytest tests/recorder/llm/test_base.py -k json_schema -v --benchmark-disable`

Expected: `AssertionError: assert 'json_schema' in sig.parameters` (or similar).

- [ ] **Step 3: Update the Protocol**

In `src/mymcp/recorder/llm/base.py`, replace the `LLMClient` Protocol (the trailing class in the file) with:

```python
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
```

- [ ] **Step 4: Run, expect pass**

Run: `.venv/bin/python -m pytest tests/recorder/llm/test_base.py -v --benchmark-disable`

Expected: all pass.

- [ ] **Step 5: Format + commit**

```bash
.venv/bin/ruff format src/mymcp/recorder/llm/base.py tests/recorder/llm/test_base.py
git add src/mymcp/recorder/llm/base.py tests/recorder/llm/test_base.py
git commit -m "feat(recorder/llm): add json_schema param to LLMClient Protocol

Adapters will use this to enforce structured output: OpenAI via
response_format, Anthropic via forced tool_use."
```

---

## Task 6: OpenAI adapter — `json_schema` via Structured Outputs

**Files:**
- Modify: `src/mymcp/recorder/llm/openai_client.py`
- Test: `tests/recorder/llm/test_openai_client.py`

When `json_schema` is set, pass it as `response_format={"type":"json_schema",...,"strict":True}`. If the SDK/provider rejects strict-schema mode (DeepSeek may), fall back to `{"type":"json_object"}`.

- [ ] **Step 1: Write failing tests**

Append to `tests/recorder/llm/test_openai_client.py`:

```python
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
    assert kwargs["response_format"]["json_schema"]["name"]  # any non-empty name


@pytest.mark.anyio
async def test_openai_no_json_schema_omits_response_format(fake_openai):
    from mymcp.recorder.llm.openai_client import OpenAIClient

    c = OpenAIClient(api_key="x", model="m")
    await c.call(
        system="s", messages=[Message(role="user", content="hi")], max_tokens=10
    )
    kwargs = fake_openai.AsyncOpenAI.return_value.chat.completions.create.call_args.kwargs
    assert "response_format" not in kwargs


@pytest.mark.anyio
async def test_openai_json_schema_falls_back_to_json_object_on_rejection(
    fake_openai, monkeypatch
):
    """Some OpenAI-compatible providers (DeepSeek) don't support the
    json_schema variant. Detect the SDK rejection and retry with the
    simpler {"type":"json_object"} mode."""
    from mymcp.recorder.llm.openai_client import OpenAIClient

    call_count = {"n": 0}

    async def fake_create(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # First call uses json_schema → provider rejects.
            assert kwargs["response_format"]["type"] == "json_schema"
            raise TypeError("response_format.type 'json_schema' not supported")
        # Second call uses json_object → succeeds.
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
```

- [ ] **Step 2: Run, expect failures**

Run: `.venv/bin/python -m pytest tests/recorder/llm/test_openai_client.py -v --benchmark-disable`

Expected: 3 new tests fail (TypeError unexpected kwarg `json_schema`, or assertion mismatches).

- [ ] **Step 3: Implement**

In `src/mymcp/recorder/llm/openai_client.py`, replace the `async def call(` body with:

```python
    async def call(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        max_tokens: int = 4096,
        json_schema: dict | None = None,
    ) -> LLMResponse:
        sdk_messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in messages:
            sdk_messages.extend(self._to_sdk_messages(m))
        base_kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": sdk_messages,
            "max_tokens": max_tokens,
        }
        if tools:
            base_kwargs["tools"] = [self._to_sdk_tool(t) for t in tools]

        if json_schema is None:
            resp = await self._client.chat.completions.create(**base_kwargs)
            return self._from_sdk_response(resp)

        # Prefer Structured Outputs (strict json_schema); fall back to
        # json_object mode for providers that don't support it yet (DeepSeek).
        strict_kwargs = dict(base_kwargs)
        strict_kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "merge_output",
                "schema": json_schema,
                "strict": True,
            },
        }
        try:
            resp = await self._client.chat.completions.create(**strict_kwargs)
        except TypeError:
            loose_kwargs = dict(base_kwargs)
            loose_kwargs["response_format"] = {"type": "json_object"}
            resp = await self._client.chat.completions.create(**loose_kwargs)
        return self._from_sdk_response(resp)
```

- [ ] **Step 4: Run, expect pass**

Run: `.venv/bin/python -m pytest tests/recorder/llm/test_openai_client.py -v --benchmark-disable`

Expected: all OpenAI client tests pass.

- [ ] **Step 5: Format + commit**

```bash
.venv/bin/ruff format src/mymcp/recorder/llm/openai_client.py tests/recorder/llm/test_openai_client.py
git add src/mymcp/recorder/llm/openai_client.py tests/recorder/llm/test_openai_client.py
git commit -m "feat(recorder/llm): OpenAI adapter honors json_schema

Uses Structured Outputs (strict json_schema). Falls back to
{\"type\":\"json_object\"} when the provider rejects strict mode
(e.g. DeepSeek's OpenAI-compat endpoint)."
```

---

## Task 7: Anthropic adapter — `json_schema` via forced `tool_use`

**Files:**
- Modify: `src/mymcp/recorder/llm/anthropic_client.py`
- Test: `tests/recorder/llm/test_anthropic_client.py`

Inject a synthetic `emit_merge_output` tool whose `input_schema` is the requested json_schema, and force `tool_choice` to call it. The structured input lands in `LLMResponse.tool_uses[0].input`.

- [ ] **Step 1: Write failing tests**

Open `tests/recorder/llm/test_anthropic_client.py` (read it first if you need the fixture name; the fixture is conventionally `fake_anthropic`) and append:

```python
@pytest.mark.anyio
async def test_anthropic_json_schema_forces_tool_use(fake_anthropic):
    from unittest.mock import MagicMock

    from mymcp.recorder.llm.anthropic_client import AnthropicClient
    from mymcp.recorder.llm.base import Message

    # Stub a tool_use response.
    block = MagicMock()
    block.type = "tool_use"
    block.id = "tu1"
    block.name = "emit_merge_output"
    block.input = {"foo": "bar"}
    resp = MagicMock()
    resp.content = [block]
    resp.stop_reason = "tool_use"
    resp.usage = MagicMock(input_tokens=10, output_tokens=5)
    fake_anthropic.AsyncAnthropic.return_value.messages.create = AsyncMock(
        return_value=resp
    )

    schema = {"type": "object", "properties": {"foo": {"type": "string"}}}
    c = AnthropicClient(api_key="x", model="claude-test")
    result = await c.call(
        system="s",
        messages=[Message(role="user", content="hi")],
        max_tokens=100,
        json_schema=schema,
    )
    kwargs = fake_anthropic.AsyncAnthropic.return_value.messages.create.call_args.kwargs
    # Synthetic tool injected with our schema.
    assert kwargs["tools"][0]["name"] == "emit_merge_output"
    assert kwargs["tools"][0]["input_schema"] == schema
    # Forced tool_choice.
    assert kwargs["tool_choice"] == {"type": "tool", "name": "emit_merge_output"}
    # Result exposes the parsed input.
    assert result.tool_uses[0].input == {"foo": "bar"}
    assert result.text == ""


@pytest.mark.anyio
async def test_anthropic_no_json_schema_omits_tool_choice(fake_anthropic):
    from unittest.mock import MagicMock

    from mymcp.recorder.llm.anthropic_client import AnthropicClient
    from mymcp.recorder.llm.base import Message

    block = MagicMock()
    block.type = "text"
    block.text = "plain output"
    resp = MagicMock()
    resp.content = [block]
    resp.stop_reason = "end_turn"
    resp.usage = MagicMock(input_tokens=1, output_tokens=1)
    fake_anthropic.AsyncAnthropic.return_value.messages.create = AsyncMock(
        return_value=resp
    )

    c = AnthropicClient(api_key="x", model="claude-test")
    await c.call(
        system="s", messages=[Message(role="user", content="hi")], max_tokens=10
    )
    kwargs = fake_anthropic.AsyncAnthropic.return_value.messages.create.call_args.kwargs
    assert "tool_choice" not in kwargs
    # No tools either, since the caller didn't supply any.
    assert "tools" not in kwargs
```

- [ ] **Step 2: Run, expect failures**

Run: `.venv/bin/python -m pytest tests/recorder/llm/test_anthropic_client.py -v --benchmark-disable`

Expected: new tests fail (json_schema not accepted, tool_choice missing, etc.).

- [ ] **Step 3: Implement**

In `src/mymcp/recorder/llm/anthropic_client.py`, replace the `async def call(` body with:

```python
    async def call(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        max_tokens: int = 4096,
        json_schema: dict | None = None,
    ) -> LLMResponse:
        sdk_messages = [self._to_sdk_message(m) for m in messages]
        kwargs: dict[str, Any] = {
            "model": self._model,
            "system": system,
            "messages": sdk_messages,
            "max_tokens": max_tokens,
        }

        sdk_tools: list[dict[str, Any]] = []
        if tools:
            sdk_tools.extend(self._to_sdk_tool(t) for t in tools)
        if json_schema is not None:
            # Inject a forced-call tool so Claude must emit conforming JSON
            # as its input. The result lands in LLMResponse.tool_uses.
            sdk_tools.append(
                {
                    "name": "emit_merge_output",
                    "description": (
                        "Emit the structured merge output. The arguments object"
                        " must match the input_schema exactly."
                    ),
                    "input_schema": json_schema,
                }
            )
            kwargs["tool_choice"] = {"type": "tool", "name": "emit_merge_output"}
        if sdk_tools:
            kwargs["tools"] = sdk_tools

        resp = await self._client.messages.create(**kwargs)
        return self._from_sdk_response(resp)
```

- [ ] **Step 4: Run, expect pass**

Run: `.venv/bin/python -m pytest tests/recorder/llm/test_anthropic_client.py -v --benchmark-disable`

Expected: all anthropic client tests pass.

- [ ] **Step 5: Format + commit**

```bash
.venv/bin/ruff format src/mymcp/recorder/llm/anthropic_client.py tests/recorder/llm/test_anthropic_client.py
git add src/mymcp/recorder/llm/anthropic_client.py tests/recorder/llm/test_anthropic_client.py
git commit -m "feat(recorder/llm): Anthropic adapter honors json_schema via tool_use

Injects a synthetic emit_merge_output tool with the requested schema and
forces tool_choice. Output lands in LLMResponse.tool_uses[0].input."
```

---

## Task 8: New merge prompts (section_updates schema, no Recent Changes)

**Files:**
- Modify: `src/mymcp/recorder/prompts.py`
- Test: prompts are exercised end-to-end in Task 9's merge_cycle tests; no standalone test here.

Replace `MERGE_SYSTEM_PROMPT` with the new schema. Tell the LLM not to touch Recent Changes (Python owns it).

- [ ] **Step 1: Overwrite the prompts module**

Replace the entire contents of `src/mymcp/recorder/prompts.py` with:

```python
"""LLM prompt templates for the recorder.

Kept in a single module so prompt iteration doesn't touch logic.
"""

MERGE_SYSTEM_PROMPT = (
    "You maintain a Markdown document describing a Linux server's current"
    " state.\n"
    "\n"
    "Each cycle you receive recent audit events plus the current overview"
    " (split into named sections) and you produce a JSON object with two"
    " fields:\n"
    "\n"
    '  "new_changelog_lines": list of one-line entries to append, one per\n'
    "    distinct effect. Format each line as\n"
    '    "YYYY-MM-DD HH:MM | <tool> | <effect summary, <=120 chars>".\n'
    "    Empty list if the events don't warrant a changelog entry.\n"
    "\n"
    '  "section_updates": map of section name -> FULL new content for that\n'
    "    section (without the leading '## ' header). Only INCLUDE sections\n"
    "    that need to change. Sections you omit are preserved unchanged.\n"
    "    To add a new section, just include it in the map. Empty object\n"
    "    {} means 'no section needs updating, just append the changelog\n"
    "    lines'.\n"
    "\n"
    "Goals:\n"
    "- Keep the document compact and bounded. Prefer high-signal facts.\n"
    "- Touch as few sections as possible per cycle to save tokens.\n"
    "- Phrase changelog entries by *effect*, not by command\n"
    '  ("installed nginx", not "ran apt install -y nginx").\n'
    "- The Overview is a progressive-disclosure map, not an operation\n"
    "  manual. Skip per-file configs.\n"
    "\n"
    "IMPORTANT — do NOT include 'Recent Changes' in section_updates. The\n"
    "recorder rebuilds that section from the changelog tail; anything you\n"
    "put there will be discarded.\n"
    "\n"
    "Recommended section names (omit any that stay empty):\n"
    "- TL;DR\n"
    "- Installed Services\n"
    "- Deployed Applications\n"
    "- Network\n"
    "- Data Locations\n"
    "- Known Quirks\n"
    "\n"
    "Output JSON only, no commentary, matching this exact schema:\n"
    "{\n"
    '  "new_changelog_lines": ["..."],\n'
    '  "section_updates": {"Section Name": "<full new body>", ...}\n'
    "}\n"
)


def merge_user_prompt(
    *,
    current_overview: str | None,
    recent_changelog: list[str],
    events_json: str,
    metadata: dict,
) -> str:
    parts: list[str] = [
        f"Hostname: {metadata.get('hostname', 'unknown')}",
        f"OS: {metadata.get('os', 'unknown')}",
        f"Now: {metadata.get('now', 'unknown')}",
        "",
        "## Current overview.md",
        current_overview or "(none - first merge after bootstrap)",
        "",
        "## Recent changelog tail (last 10 lines, for tone consistency)",
        *(recent_changelog or ["(empty)"]),
        "",
        "## New events to fold in (JSON)",
        events_json,
        "",
        "Produce JSON per the schema in the system prompt.",
    ]
    return "\n".join(parts)
```

- [ ] **Step 2: Sanity check imports still work**

Run: `.venv/bin/python -c "from mymcp.recorder.prompts import MERGE_SYSTEM_PROMPT, merge_user_prompt; print(len(MERGE_SYSTEM_PROMPT))"`

Expected: prints a number (system prompt length).

- [ ] **Step 3: Format + commit (no test run yet — prompts are content-only; will be exercised in Task 9)**

```bash
.venv/bin/ruff format src/mymcp/recorder/prompts.py
git add src/mymcp/recorder/prompts.py
git commit -m "feat(recorder): rewrite merge system prompt for section_updates schema

Instructs the LLM to emit only the sections that changed and explicitly
forbids touching Recent Changes (Python owns that section)."
```

---

## Task 9: Overhaul `MergeCycle` — section_updates, early-fail, json_schema, Python-owned header + Recent Changes

**Files:**
- Modify: `src/mymcp/recorder/merge_cycle.py`
- Test: `tests/recorder/test_merge_cycle.py`

The biggest task. Combines the new schema, early-fail on bad responses, json_schema integration (reading either `tool_uses[0].input` or `text`), the Python-owned header, the Python-owned Recent Changes section, and `max_tokens` plumbing.

- [ ] **Step 1: Define `MERGE_OUTPUT_SCHEMA` constant**

Open `src/mymcp/recorder/merge_cycle.py` and check the current imports/constants. We'll add a module-level schema constant and rewrite `MergeCycle`. The full new file content for this task is shown in Step 3 below — do not edit piecemeal.

- [ ] **Step 2: Update existing tests that use the old schema, and add new failure-mode tests**

Replace the entire contents of `tests/recorder/test_merge_cycle.py` with:

```python
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from mymcp.recorder.events import EventTailer
from mymcp.recorder.llm.base import LLMResponse, ToolUse, Usage
from mymcp.recorder.merge_cycle import MergeCycle
from mymcp.recorder.overview import OverviewStore


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _audit_line(**fields) -> str:
    base = {"ts": "2026-05-29T10:00:00Z", "result": "ok"}
    base.update(fields)
    return json.dumps(base) + "\n"


def _write_log(tmp_path: Path, *entries: str) -> None:
    (tmp_path / "audit.log").write_text("".join(entries))


def _text_response(payload: dict, usage_in: int = 10, usage_out: int = 20) -> LLMResponse:
    return LLMResponse(
        text=json.dumps(payload),
        tool_uses=[],
        stop_reason="end_turn",
        usage=Usage(input_tokens=usage_in, output_tokens=usage_out),
    )


def _tool_response(payload: dict, usage_in: int = 10, usage_out: int = 20) -> LLMResponse:
    return LLMResponse(
        text="",
        tool_uses=[ToolUse(id="t1", name="emit_merge_output", input=payload)],
        stop_reason="tool_use",
        usage=Usage(input_tokens=usage_in, output_tokens=usage_out),
    )


@pytest.mark.anyio
async def test_merge_writes_section_updates_and_appends_changelog(tmp_path):
    _write_log(
        tmp_path,
        _audit_line(
            tool="bash_execute",
            params={"command": "apt install nginx"},
            output={"stdout_head": "ok"},
        ),
    )
    store = OverviewStore(tmp_path / "overview")
    store.write_overview("# Server Overview\n\n## TL;DR\nFresh.\n")
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    fake = AsyncMock()
    fake.call = AsyncMock(
        return_value=_text_response(
            {
                "new_changelog_lines": ["2026-05-29 10:00 | bash_execute | installed nginx"],
                "section_updates": {"Installed Services": "- nginx"},
            }
        )
    )
    cycle = MergeCycle(client=fake, tailer=tailer, store=store, max_events_per_cycle=10)
    result = await cycle.run_once()

    assert result.events_consumed == 1
    overview = store.read_overview() or ""
    assert "nginx" in overview              # new section added
    assert "Fresh." in overview             # untouched section preserved
    assert "_Last updated:" in overview     # Python-owned header present
    tail = store.read_changelog_tail(5)
    assert tail and "installed nginx" in tail[-1]
    # json_schema is requested so adapters can enforce structured output
    assert fake.call.call_args.kwargs["json_schema"] is not None


@pytest.mark.anyio
async def test_merge_accepts_tool_use_response(tmp_path):
    """When the adapter returns tool_uses (Anthropic forced tool_use path),
    merge_cycle reads from tool_uses[0].input instead of text."""
    _write_log(
        tmp_path,
        _audit_line(tool="bash_execute", params={"command": "ls"}, output={"stdout_head": "x"}),
    )
    store = OverviewStore(tmp_path / "overview")
    store.write_overview("# Old\n\n## TL;DR\nold\n")
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    fake = AsyncMock()
    fake.call = AsyncMock(
        return_value=_tool_response(
            {"new_changelog_lines": [], "section_updates": {"TL;DR": "new"}}
        )
    )
    cycle = MergeCycle(client=fake, tailer=tailer, store=store, max_events_per_cycle=10)
    await cycle.run_once()
    assert "new" in (store.read_overview() or "")


@pytest.mark.anyio
async def test_merge_no_events_is_noop(tmp_path):
    _write_log(tmp_path)
    store = OverviewStore(tmp_path / "overview")
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    fake = AsyncMock()
    cycle = MergeCycle(client=fake, tailer=tailer, store=store, max_events_per_cycle=10)
    result = await cycle.run_once()
    assert result.events_consumed == 0
    assert result.skipped_reason == "no_events"
    fake.call.assert_not_called()


@pytest.mark.anyio
async def test_merge_require_bootstrap_skips_when_overview_missing(tmp_path):
    _write_log(
        tmp_path,
        _audit_line(tool="bash_execute", params={"command": "ls"}, output={"stdout_head": "x"}),
    )
    store = OverviewStore(tmp_path / "overview")
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    fake = AsyncMock()
    cycle = MergeCycle(
        client=fake, tailer=tailer, store=store, max_events_per_cycle=10, require_bootstrap=True
    )
    result = await cycle.run_once()
    assert result.events_consumed == 0
    assert result.skipped_reason == "bootstrap_required"
    fake.call.assert_not_called()


@pytest.mark.anyio
async def test_merge_unparseable_text_raises_and_rolls_back(tmp_path):
    _write_log(
        tmp_path,
        _audit_line(tool="write_file", params={"file_path": "/x"}, output={"size_bytes": 1}),
    )
    store = OverviewStore(tmp_path / "overview")
    store.write_overview("# Existing\n")
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    fake = AsyncMock()
    fake.call = AsyncMock(
        return_value=LLMResponse(
            text="not json at all",
            tool_uses=[],
            stop_reason="end_turn",
            usage=Usage(input_tokens=1, output_tokens=1),
        )
    )
    cycle = MergeCycle(client=fake, tailer=tailer, store=store, max_events_per_cycle=10)
    with pytest.raises(ValueError):
        await cycle.run_once()
    # Untouched: atomic write didn't run.
    assert store.read_overview() == "# Existing\n"


@pytest.mark.anyio
async def test_merge_empty_response_raises_early(tmp_path):
    _write_log(
        tmp_path,
        _audit_line(tool="bash_execute", params={"command": "ls"}, output={"stdout_head": "x"}),
    )
    store = OverviewStore(tmp_path / "overview")
    store.write_overview("# Old\n")
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    fake = AsyncMock()
    fake.call = AsyncMock(
        return_value=LLMResponse(
            text="",
            tool_uses=[],
            stop_reason="end_turn",
            usage=Usage(input_tokens=1, output_tokens=0),
        )
    )
    cycle = MergeCycle(client=fake, tailer=tailer, store=store, max_events_per_cycle=10)
    with pytest.raises(ValueError, match="empty"):
        await cycle.run_once()
    assert store.read_overview() == "# Old\n"


@pytest.mark.anyio
async def test_merge_max_tokens_truncation_raises_early(tmp_path):
    _write_log(
        tmp_path,
        _audit_line(tool="bash_execute", params={"command": "ls"}, output={"stdout_head": "x"}),
    )
    store = OverviewStore(tmp_path / "overview")
    store.write_overview("# Old\n")
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    fake = AsyncMock()
    fake.call = AsyncMock(
        return_value=LLMResponse(
            text='{"section_updates": {"TL;DR": "lon',
            tool_uses=[],
            stop_reason="max_tokens",
            usage=Usage(input_tokens=100, output_tokens=4096),
        )
    )
    cycle = MergeCycle(
        client=fake, tailer=tailer, store=store, max_events_per_cycle=10, max_tokens=4096
    )
    with pytest.raises(ValueError, match="max_tokens"):
        await cycle.run_once()
    assert store.read_overview() == "# Old\n"


@pytest.mark.anyio
async def test_merge_passes_configured_max_tokens(tmp_path):
    _write_log(
        tmp_path,
        _audit_line(tool="bash_execute", params={"command": "ls"}, output={"stdout_head": "x"}),
    )
    store = OverviewStore(tmp_path / "overview")
    store.write_overview("# Old\n")
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    fake = AsyncMock()
    fake.call = AsyncMock(
        return_value=_text_response({"new_changelog_lines": [], "section_updates": {}})
    )
    cycle = MergeCycle(
        client=fake, tailer=tailer, store=store, max_events_per_cycle=10, max_tokens=32768
    )
    await cycle.run_once()
    assert fake.call.call_args.kwargs["max_tokens"] == 32768


@pytest.mark.anyio
async def test_merge_python_owns_recent_changes(tmp_path):
    """Recent Changes is rebuilt from the (existing tail + new lines), regardless
    of what the LLM put there."""
    _write_log(
        tmp_path,
        _audit_line(tool="bash_execute", params={"command": "ls"}, output={"stdout_head": "x"}),
    )
    store = OverviewStore(tmp_path / "overview")
    store.write_overview("# Old\n")
    store.append_changelog(["2026-06-01 10:00 | bash_execute | older event"])
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    fake = AsyncMock()
    # LLM tries to set Recent Changes to garbage — Python must override.
    fake.call = AsyncMock(
        return_value=_text_response(
            {
                "new_changelog_lines": ["2026-06-02 11:00 | bash_execute | new event"],
                "section_updates": {"Recent Changes": "should be ignored"},
            }
        )
    )
    cycle = MergeCycle(client=fake, tailer=tailer, store=store, max_events_per_cycle=10)
    await cycle.run_once()
    overview = store.read_overview() or ""
    assert "should be ignored" not in overview
    # Newest first: the brand-new line appears before the older one.
    new_idx = overview.index("new event")
    older_idx = overview.index("older event")
    assert new_idx < older_idx
    # Footer reference present
    assert "_Full changelog: changelog.md" in overview


@pytest.mark.anyio
async def test_merge_preserves_untouched_sections(tmp_path):
    _write_log(
        tmp_path,
        _audit_line(tool="bash_execute", params={"command": "ls"}, output={"stdout_head": "x"}),
    )
    store = OverviewStore(tmp_path / "overview")
    store.write_overview(
        "# Server Overview\n"
        "\n## TL;DR\nKeep me.\n"
        "\n## Known Quirks\n- preserve this\n"
    )
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    fake = AsyncMock()
    fake.call = AsyncMock(
        return_value=_text_response(
            {"new_changelog_lines": [], "section_updates": {"Installed Services": "- nginx"}}
        )
    )
    cycle = MergeCycle(client=fake, tailer=tailer, store=store, max_events_per_cycle=10)
    await cycle.run_once()
    overview = store.read_overview() or ""
    assert "Keep me." in overview
    assert "preserve this" in overview
    assert "nginx" in overview


@pytest.mark.anyio
async def test_merge_caps_events_per_cycle(tmp_path):
    entries = [
        _audit_line(tool="write_file", params={"file_path": f"/x{i}"}, output={"size_bytes": 1})
        for i in range(15)
    ]
    _write_log(tmp_path, *entries)
    store = OverviewStore(tmp_path / "overview")
    store.write_overview("# Old\n")
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    fake = AsyncMock()
    fake.call = AsyncMock(
        return_value=_text_response({"new_changelog_lines": ["x"], "section_updates": {}})
    )
    cycle = MergeCycle(client=fake, tailer=tailer, store=store, max_events_per_cycle=5)
    result = await cycle.run_once()
    assert result.events_consumed == 5


@pytest.mark.anyio
async def test_merge_rejects_bad_schema_types(tmp_path):
    _write_log(
        tmp_path,
        _audit_line(tool="bash_execute", params={"command": "ls"}, output={"stdout_head": "x"}),
    )
    store = OverviewStore(tmp_path / "overview")
    store.write_overview("# Old\n")
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    fake = AsyncMock()
    fake.call = AsyncMock(
        return_value=_text_response(
            {"new_changelog_lines": [], "section_updates": ["wrong shape"]}
        )
    )
    cycle = MergeCycle(client=fake, tailer=tailer, store=store, max_events_per_cycle=10)
    with pytest.raises(ValueError, match="section_updates"):
        await cycle.run_once()
```

- [ ] **Step 3: Run, expect failures**

Run: `.venv/bin/python -m pytest tests/recorder/test_merge_cycle.py -v --benchmark-disable`

Expected: all merge_cycle tests fail (new ones because feature doesn't exist; old ones because we replaced them with new-schema versions and the implementation still uses old schema).

- [ ] **Step 4: Rewrite merge_cycle.py**

Replace the entire contents of `src/mymcp/recorder/merge_cycle.py` with:

```python
"""Periodic merge cycle: read new events, ask LLM to fold them into the overview."""

import json
import logging
import platform
import socket
from dataclasses import dataclass
from datetime import UTC, datetime

from mymcp.observability import instruments
from mymcp.observability.tracing import get_tracer
from mymcp.recorder.events import AuditEvent, EventTailer
from mymcp.recorder.llm.base import LLMClient, Message
from mymcp.recorder.overview import (
    OverviewStore,
    apply_section_updates,
    render_recent_changes,
)
from mymcp.recorder.prompts import MERGE_SYSTEM_PROMPT, merge_user_prompt

_tracer = get_tracer(__name__)

log = logging.getLogger("mymcp.recorder")

# JSON Schema for the merge output. Adapters use it for structured-output
# enforcement (OpenAI Structured Outputs; Anthropic forced tool_use).
MERGE_OUTPUT_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["new_changelog_lines", "section_updates"],
    "properties": {
        "new_changelog_lines": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "One-line entries to append to changelog.md. Format:"
                ' "YYYY-MM-DD HH:MM | <tool> | <effect, <=120 chars>"'
            ),
        },
        "section_updates": {
            "type": "object",
            "additionalProperties": {"type": "string"},
            "description": (
                "Section-name -> full new content. Sections omitted are"
                " preserved unchanged. Do not include 'Recent Changes'."
            ),
        },
    },
}


@dataclass
class MergeResult:
    events_consumed: int
    skipped_reason: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0


class MergeCycle:
    """Drains pending events and asks an LLM to update the overview + changelog.

    Atomic semantics: the overview is rewritten via OverviewStore.write_overview
    (tmp + os.replace); changelog lines are appended; cursor is committed last.
    If the LLM returns a bad response or any step fails, the cursor stays put
    so the same events will be re-tried next cycle (at-least-once).
    """

    def __init__(
        self,
        *,
        client: LLMClient,
        tailer: EventTailer,
        store: OverviewStore,
        max_events_per_cycle: int = 50,
        require_bootstrap: bool = False,
        max_tokens: int = 16384,
    ):
        self._client = client
        self._tailer = tailer
        self._store = store
        self._max = max_events_per_cycle
        self._require_bootstrap = require_bootstrap
        self._max_tokens = max_tokens

    async def run_once(self) -> MergeResult:
        with _tracer.start_as_current_span("recorder.merge_cycle") as span:
            if self._require_bootstrap and self._store.read_overview() is None:
                span.set_attribute("events.in", 0)
                return MergeResult(events_consumed=0, skipped_reason="bootstrap_required")

            events: list[AuditEvent] = []
            for ev in self._tailer.read_new():
                events.append(ev)
                if len(events) >= self._max:
                    break
            span.set_attribute("events.in", len(events))
            if not events:
                return MergeResult(events_consumed=0, skipped_reason="no_events")

            current_overview = self._store.read_overview()
            prompt = merge_user_prompt(
                current_overview=current_overview,
                recent_changelog=self._store.read_changelog_tail(10),
                events_json=json.dumps(
                    [self._event_to_dict(e) for e in events], indent=2
                ),
                metadata={
                    "hostname": socket.gethostname(),
                    "os": platform.platform(),
                    "now": datetime.now(UTC).isoformat(),
                },
            )
            try:
                resp = await self._client.call(
                    system=MERGE_SYSTEM_PROMPT,
                    messages=[Message(role="user", content=prompt)],
                    max_tokens=self._max_tokens,
                    json_schema=MERGE_OUTPUT_SCHEMA,
                )
                instruments.recorder_llm_calls.add(
                    1, {"phase": "merge", "result": "success"}
                )
                instruments.recorder_llm_tokens.add(
                    resp.usage.input_tokens, {"phase": "merge", "direction": "input"}
                )
                instruments.recorder_llm_tokens.add(
                    resp.usage.output_tokens, {"phase": "merge", "direction": "output"}
                )
                span.set_attribute("tokens.in", resp.usage.input_tokens)
                span.set_attribute("tokens.out", resp.usage.output_tokens)

                # Early-fail on truncated / empty responses before parse.
                if resp.stop_reason == "max_tokens":
                    raise ValueError(
                        f"LLM hit max_tokens ({self._max_tokens}); response"
                        " truncated. Raise MYMCP_RECORDER_LLM_MAX_TOKENS (must"
                        " stay under your model's output limit)."
                    )
                parsed = self._extract_payload(resp)
                self._validate_payload(parsed)

                section_updates = dict(parsed.get("section_updates") or {})
                # Python owns Recent Changes — drop whatever the LLM put there.
                section_updates.pop("Recent Changes", None)
                changelog_lines = list(parsed.get("new_changelog_lines") or [])

                # Render Recent Changes from existing-tail + about-to-append lines.
                existing_tail = self._store.read_changelog_tail(10)
                effective_tail = existing_tail + changelog_lines
                section_updates["Recent Changes"] = render_recent_changes(effective_tail)

                new_overview = apply_section_updates(
                    current_overview or "",
                    header=self._build_header(),
                    section_updates=section_updates,
                )

                # Atomic: write overview first, then append changelog, then commit cursor.
                self._store.write_overview(new_overview)
                self._store.append_changelog(changelog_lines)
            except Exception:
                instruments.recorder_merge_cycles.add(1, {"result": "failure"})
                self._tailer.rollback()
                raise
            self._tailer.commit()
            instruments.recorder_merge_cycles.add(1, {"result": "success"})
            log.info(
                "recorder.merge_cycle.done",
                extra={
                    "events": len(events),
                    "tokens_in": resp.usage.input_tokens,
                    "tokens_out": resp.usage.output_tokens,
                    "sections_updated": len(section_updates),
                    "changelog_lines": len(changelog_lines),
                },
            )
            return MergeResult(
                events_consumed=len(events),
                tokens_in=resp.usage.input_tokens,
                tokens_out=resp.usage.output_tokens,
            )

    @staticmethod
    def _extract_payload(resp) -> dict:
        # Anthropic structured-output path: payload arrives as tool_uses[0].input.
        if resp.tool_uses:
            data = resp.tool_uses[0].input
            if not isinstance(data, dict):
                raise ValueError("tool_use input is not a JSON object")
            return data
        # OpenAI / fallback: parse text.
        text = resp.text or ""
        if not text.strip():
            raise ValueError("LLM returned empty response")
        return MergeCycle._parse_text_json(text)

    @staticmethod
    def _parse_text_json(text: str) -> dict:
        t = text.strip()
        if t.startswith("```"):
            t = t.split("\n", 1)[1] if "\n" in t else t
            if "```" in t:
                t = t.rsplit("```", 1)[0]
            t = t.strip()
        try:
            data = json.loads(t)
        except json.JSONDecodeError as e:
            log.warning("recorder.merge_cycle.unparseable", extra={"raw_head": text[:500]})
            raise ValueError(f"LLM returned unparseable JSON: {e}") from e
        if not isinstance(data, dict):
            raise ValueError("response is not a JSON object")
        return data

    @staticmethod
    def _validate_payload(data: dict) -> None:
        su = data.get("section_updates")
        if su is not None and not isinstance(su, dict):
            raise ValueError("section_updates must be an object")
        cl = data.get("new_changelog_lines")
        if cl is not None and not isinstance(cl, list):
            raise ValueError("new_changelog_lines must be a list")

    @staticmethod
    def _build_header() -> str:
        now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        return (
            "# Server Overview\n"
            f"_Last updated: {now}_\n"
            f"_Hostname: {socket.gethostname()} | OS: {platform.platform()}_"
        )

    @staticmethod
    def _event_to_dict(e: AuditEvent) -> dict:
        return {
            "ts": e.ts,
            "tool": e.tool,
            "params": e.params,
            "output": e.output,
        }
```

- [ ] **Step 5: Run, expect pass**

Run: `.venv/bin/python -m pytest tests/recorder/test_merge_cycle.py -v --benchmark-disable`

Expected: all merge_cycle tests pass.

- [ ] **Step 6: Format + commit**

```bash
.venv/bin/ruff format src/mymcp/recorder/merge_cycle.py tests/recorder/test_merge_cycle.py
git add src/mymcp/recorder/merge_cycle.py tests/recorder/test_merge_cycle.py
git commit -m "feat(recorder): overhaul merge cycle for resilience

- New schema: section_updates instead of full overview rewrite.
- Python owns the metadata header and the Recent Changes section
  (rebuilt from existing tail + new changelog lines).
- Early-fail on empty / stop_reason=max_tokens responses with clear
  actionable errors.
- Reads structured output from tool_uses[0].input when present
  (Anthropic), falls back to parsing text (OpenAI/DeepSeek).
- max_tokens flows from config via constructor argument.
- Requests json_schema enforcement from the adapter."
```

---

## Task 10: Bootstrap — thread `max_tokens` config

**Files:**
- Modify: `src/mymcp/recorder/bootstrap.py`
- Test: `tests/recorder/test_bootstrap.py`

Small. Bootstrap still uses the full-markdown protocol; just plumb the config through.

- [ ] **Step 1: Write failing test**

Append to `tests/recorder/test_bootstrap.py`:

```python
@pytest.mark.anyio
async def test_bootstrap_passes_configured_max_tokens(tmp_path):
    from unittest.mock import AsyncMock

    from mymcp.recorder.bootstrap import Bootstrapper
    from mymcp.recorder.llm.base import LLMResponse, Usage
    from mymcp.recorder.overview import OverviewStore

    store = OverviewStore(tmp_path / "overview")
    fake = AsyncMock()
    fake.call = AsyncMock(
        return_value=LLMResponse(
            text="# Server Overview\n\n## TL;DR\nok\n",
            tool_uses=[],
            stop_reason="end_turn",
            usage=Usage(input_tokens=1, output_tokens=1),
        )
    )
    bs = Bootstrapper(client=fake, store=store, max_iterations=2, max_tokens=32768)
    await bs.run_once()
    assert fake.call.call_args.kwargs["max_tokens"] == 32768
```

(If the test file doesn't already have `import pytest` + `anyio_backend` fixture, check the top of the file before adding — it likely does, since other tests in there use anyio.)

- [ ] **Step 2: Run, expect failure**

Run: `.venv/bin/python -m pytest tests/recorder/test_bootstrap.py -k passes_configured_max_tokens -v --benchmark-disable`

Expected: `TypeError: ... unexpected keyword argument 'max_tokens'` or assertion mismatch (4096 != 32768).

- [ ] **Step 3: Add `max_tokens` parameter**

In `src/mymcp/recorder/bootstrap.py`, modify `Bootstrapper.__init__`:

```python
    def __init__(
        self,
        *,
        client: LLMClient,
        store: OverviewStore,
        max_iterations: int = 200,
        token_budget: int = 10_000_000,
        probe_timeout_sec: int = 30,
        max_tokens: int = 16384,
    ):
        self._client = client
        self._store = store
        self._max_iterations = max_iterations
        self._token_budget = token_budget
        self._probe_timeout = probe_timeout_sec
        self._max_tokens = max_tokens
        self._lock = asyncio.Lock()
        self._state = BootstrapState.IDLE
        self._last_result: BootstrapResult | None = None
```

And in `_run_locked()`, find the `await self._client.call(...)` invocation and change `max_tokens=4096,` to `max_tokens=self._max_tokens,`.

- [ ] **Step 4: Run, expect pass**

Run: `.venv/bin/python -m pytest tests/recorder/test_bootstrap.py -v --benchmark-disable`

Expected: all bootstrap tests pass.

- [ ] **Step 5: Format + commit**

```bash
.venv/bin/ruff format src/mymcp/recorder/bootstrap.py tests/recorder/test_bootstrap.py
git add src/mymcp/recorder/bootstrap.py tests/recorder/test_bootstrap.py
git commit -m "feat(recorder): thread max_tokens config through Bootstrapper"
```

---

## Task 11: Circuit breaker in `RecorderSupervisor`

**Files:**
- Modify: `src/mymcp/recorder/task.py`
- Test: `tests/recorder/test_task.py`

Track consecutive merge failures; after threshold reached, stop calling `run_once()` until restart. Expose state via `RecorderStatus`.

- [ ] **Step 1: Add failing tests**

Append to `tests/recorder/test_task.py`:

```python
@pytest.mark.anyio
async def test_supervisor_opens_circuit_after_consecutive_failures(tmp_path):
    """After threshold consecutive merge failures, the supervisor must stop
    calling the LLM. Otherwise a poisoned event batch burns API quota forever."""
    import json

    store = OverviewStore(tmp_path / "overview")
    store.write_overview("# Server Overview\n\n## TL;DR\nok\n")
    audit_entry = json.dumps(
        {
            "ts": "2026-05-29T10:00:00Z",
            "result": "ok",
            "tool": "bash_execute",
            "params": {"command": "ls"},
            "output": {"stdout_head": "x"},
        }
    )
    (tmp_path / "audit.log").write_text(audit_entry + "\n")
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    fake = AsyncMock()
    fake.call = AsyncMock(return_value=_end("not json at all"))
    bootstrapper = Bootstrapper(client=fake, store=store, max_iterations=2)
    merge_cycle = MergeCycle(
        client=fake, tailer=tailer, store=store, max_events_per_cycle=10, require_bootstrap=True
    )
    sup = RecorderSupervisor(
        merge_cycle=merge_cycle,
        bootstrapper=bootstrapper,
        merge_interval_sec=0.01,
        circuit_breaker_threshold=3,
    )
    sup._backoff = 0.01
    sup._max_backoff = 0.01
    task = asyncio.create_task(sup.run())
    for _ in range(200):
        if sup.status().circuit_open:
            break
        await asyncio.sleep(0.02)
    sup.shutdown()
    await asyncio.wait_for(task, timeout=2)
    status = sup.status()
    assert status.circuit_open is True
    assert status.consecutive_failures >= 3
    calls_when_opened = fake.call.call_count
    await asyncio.sleep(0.05)
    assert fake.call.call_count == calls_when_opened


@pytest.mark.anyio
async def test_supervisor_clears_failure_count_on_success(tmp_path):
    """A successful cycle resets consecutive_failures so a single hiccup
    doesn't accumulate forever."""
    import json

    store = OverviewStore(tmp_path / "overview")
    store.write_overview("# Server Overview\n\n## TL;DR\nok\n")
    audit_entry = json.dumps(
        {
            "ts": "2026-05-29T10:00:00Z",
            "result": "ok",
            "tool": "bash_execute",
            "params": {"command": "ls"},
            "output": {"stdout_head": "x"},
        }
    )
    (tmp_path / "audit.log").write_text(audit_entry + "\n")
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    fake = AsyncMock()
    fake.call = AsyncMock(
        side_effect=[
            _end("not json at all"),
            _end('{"new_changelog_lines": [], "section_updates": {"TL;DR": "ok2"}}'),
        ]
    )
    bootstrapper = Bootstrapper(client=fake, store=store, max_iterations=2)
    merge_cycle = MergeCycle(
        client=fake, tailer=tailer, store=store, max_events_per_cycle=10, require_bootstrap=True
    )
    sup = RecorderSupervisor(
        merge_cycle=merge_cycle,
        bootstrapper=bootstrapper,
        merge_interval_sec=0.01,
        circuit_breaker_threshold=10,
    )
    sup._backoff = 0.01
    sup._max_backoff = 0.01
    task = asyncio.create_task(sup.run())
    for _ in range(200):
        if "ok2" in (store.read_overview() or ""):
            break
        await asyncio.sleep(0.02)
    sup.shutdown()
    await asyncio.wait_for(task, timeout=2)
    status = sup.status()
    assert status.circuit_open is False
    assert status.consecutive_failures == 0
```

- [ ] **Step 2: Run, expect failures**

Run: `.venv/bin/python -m pytest tests/recorder/test_task.py -k 'circuit or failure_count' -v --benchmark-disable`

Expected: failures (no `circuit_open` attribute, no `circuit_breaker_threshold` parameter).

- [ ] **Step 3: Add circuit breaker**

In `src/mymcp/recorder/task.py`:

(a) Update the `RecorderStatus` dataclass to include the new fields. Find it and replace with:

```python
@dataclass
class RecorderStatus:
    enabled: bool
    bootstrap_state: BootstrapState
    last_bootstrap_ts: str | None
    last_merge_ts: str | None
    last_merge_age_seconds: float | None
    pending_events: int
    last_error: str | None
    llm_provider: str
    llm_model: str | None
    consecutive_failures: int = 0
    circuit_open: bool = False
```

(b) Update `RecorderSupervisor.__init__` to accept the threshold and track state. Find the existing `__init__` and replace with:

```python
    def __init__(
        self,
        *,
        merge_cycle: MergeCycle,
        bootstrapper: Bootstrapper,
        merge_interval_sec: float = 300.0,
        provider: str = "anthropic",
        model: str | None = None,
        circuit_breaker_threshold: int = 5,
    ):
        self._merge_cycle = merge_cycle
        self._bootstrap = bootstrapper
        self._interval = merge_interval_sec
        self._provider = provider
        self._model = model
        self._stop = asyncio.Event()
        self._force_bootstrap = False
        self._last_merge_ts: float | None = None
        self._last_bootstrap_ts: float | None = None
        self._last_error: str | None = None
        self._backoff = 30.0
        self._max_backoff = 600.0
        self._circuit_threshold = circuit_breaker_threshold
        self._consecutive_failures = 0
        self._circuit_open = False
```

(c) Replace the main loop body in `run()` (the `while not self._stop.is_set():` block) with:

```python
            while not self._stop.is_set():
                if self._force_bootstrap or self.store.read_overview() is None:
                    self._force_bootstrap = False
                    await self._do_bootstrap()
                if self._circuit_open:
                    # Idle until shutdown / next tick. Restart of the process
                    # is the only reset path (intentional, no admin endpoint).
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
                    continue
                try:
                    result = await self._merge_cycle.run_once()
                    self._last_merge_ts = time.time()
                    self._consecutive_failures = 0
                    if self._bootstrap.state != BootstrapState.FAILED:
                        self._last_error = None
                    self._backoff = 30.0
                    _ = result
                except Exception as e:  # noqa: BLE001
                    log.exception("recorder.supervisor.cycle_error")
                    self._last_error = str(e)
                    self._consecutive_failures += 1
                    if (
                        self._circuit_threshold > 0
                        and self._consecutive_failures >= self._circuit_threshold
                    ):
                        self._circuit_open = True
                        log.error(
                            "recorder.supervisor.circuit_open",
                            extra={
                                "consecutive_failures": self._consecutive_failures,
                                "threshold": self._circuit_threshold,
                                "last_error": str(e),
                            },
                        )
                    self._backoff = min(self._backoff * 2, self._max_backoff)
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(self._stop.wait(), timeout=self._backoff)
                    continue
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
```

(d) Update the `status()` method to include the new fields:

```python
    def status(self) -> RecorderStatus:
        now = time.time()
        age = (now - self._last_merge_ts) if self._last_merge_ts is not None else None
        return RecorderStatus(
            enabled=True,
            bootstrap_state=self._bootstrap.state,
            last_bootstrap_ts=_iso(self._last_bootstrap_ts),
            last_merge_ts=_iso(self._last_merge_ts),
            last_merge_age_seconds=age,
            pending_events=0,
            last_error=self._last_error,
            llm_provider=self._provider,
            llm_model=self._model,
            consecutive_failures=self._consecutive_failures,
            circuit_open=self._circuit_open,
        )
```

(e) Add a public property so the metric callback can read state without touching internals:

After the `status()` method, add:

```python
    @property
    def circuit_open(self) -> bool:
        return self._circuit_open
```

- [ ] **Step 4: Run, expect pass**

Run: `.venv/bin/python -m pytest tests/recorder/test_task.py -v --benchmark-disable`

Expected: all task tests pass.

- [ ] **Step 5: Format + commit**

```bash
.venv/bin/ruff format src/mymcp/recorder/task.py tests/recorder/test_task.py
git add src/mymcp/recorder/task.py tests/recorder/test_task.py
git commit -m "feat(recorder): circuit breaker after N consecutive merge failures

Default threshold 5. Once tripped, supervisor idles instead of calling
the LLM. Restart-only recovery — no admin endpoint, by design.

Exposes consecutive_failures + circuit_open via RecorderStatus and a
new circuit_open property on the supervisor."
```

---

## Task 12: `recorder_circuit_open` observable gauge

**Files:**
- Modify: `src/mymcp/recorder/wiring.py`
- Test: `tests/recorder/test_wiring.py`

Use the existing `register_callback_gauge()` helper. The callback reads `supervisor.circuit_open` at scrape time.

- [ ] **Step 1: Add failing test**

Append to `tests/recorder/test_wiring.py`:

```python
def test_build_supervisor_registers_circuit_open_gauge(monkeypatch, tmp_path):
    from unittest.mock import MagicMock, patch

    _fake_anthropic(monkeypatch)
    s = Settings(
        recorder_enabled=True,
        recorder_data_dir=str(tmp_path / "recorder"),
        recorder_llm_provider="anthropic",
        recorder_llm_api_key="test-key",
        audit_log_dir=str(tmp_path / "audit"),
    )

    captured: dict = {}

    def fake_register(name, description, callback):
        captured["name"] = name
        captured["description"] = description
        captured["callback"] = callback

    with patch("mymcp.recorder.wiring.register_callback_gauge", side_effect=fake_register):
        sup = build_supervisor(s)

    assert captured["name"] == "mymcp.recorder.circuit_open"
    # Callback returns Observation iterable; value reflects supervisor.circuit_open.
    observations = list(captured["callback"]())
    assert len(observations) == 1
    assert observations[0].value == 0
    # Open the circuit and re-read.
    sup._circuit_open = True
    observations = list(captured["callback"]())
    assert observations[0].value == 1
```

(The test imports `Settings` and `build_supervisor` from existing fixtures; check that the file already has them at the top — yes, it does.)

- [ ] **Step 2: Run, expect failure**

Run: `.venv/bin/python -m pytest tests/recorder/test_wiring.py -k circuit_open -v --benchmark-disable`

Expected: `AttributeError: module ... has no attribute 'register_callback_gauge'` or `KeyError: 'name'`.

- [ ] **Step 3: Plumb settings + register gauge**

In `src/mymcp/recorder/wiring.py`:

(a) Add to the imports at the top:

```python
from opentelemetry import metrics

from mymcp.observability.instruments import register_callback_gauge
```

(b) Update `build_supervisor()` body — replace the existing function body with:

```python
def build_supervisor(settings: Settings) -> RecorderSupervisor:
    data_dir = Path(settings.recorder_data_dir)
    overview_dir = data_dir / "overview"
    cursor_path = data_dir / "cursor.json"

    register_protected_path(str(overview_dir), modes={"write"})

    client: Any = build_llm_client(
        provider=settings.recorder_llm_provider,
        api_key=settings.recorder_llm_api_key,
        model=settings.recorder_llm_model,
        base_url=settings.recorder_llm_base_url,
    )
    store = OverviewStore(overview_dir)
    tailer = EventTailer(log_dir=Path(settings.audit_log_dir), cursor_path=cursor_path)
    bootstrapper = Bootstrapper(
        client=client,
        store=store,
        max_iterations=settings.recorder_bootstrap_max_iterations,
        token_budget=settings.recorder_bootstrap_token_budget,
        probe_timeout_sec=settings.recorder_bootstrap_probe_timeout_sec,
        max_tokens=settings.recorder_llm_max_tokens,
    )
    merge = MergeCycle(
        client=client,
        tailer=tailer,
        store=store,
        max_events_per_cycle=settings.recorder_max_events_per_cycle,
        require_bootstrap=True,
        max_tokens=settings.recorder_llm_max_tokens,
    )
    supervisor = RecorderSupervisor(
        merge_cycle=merge,
        bootstrapper=bootstrapper,
        merge_interval_sec=settings.recorder_merge_interval_sec,
        provider=settings.recorder_llm_provider,
        model=settings.recorder_llm_model,
        circuit_breaker_threshold=settings.recorder_circuit_breaker_threshold,
    )

    def _circuit_observation():
        return [metrics.Observation(1 if supervisor.circuit_open else 0)]

    register_callback_gauge(
        "mymcp.recorder.circuit_open",
        "1 if recorder circuit breaker is open, 0 otherwise",
        _circuit_observation,
    )
    return supervisor
```

- [ ] **Step 4: Run, expect pass**

Run: `.venv/bin/python -m pytest tests/recorder/test_wiring.py -v --benchmark-disable`

Expected: all wiring tests pass.

- [ ] **Step 5: Format + commit**

```bash
.venv/bin/ruff format src/mymcp/recorder/wiring.py tests/recorder/test_wiring.py
git add src/mymcp/recorder/wiring.py tests/recorder/test_wiring.py
git commit -m "feat(recorder/obs): observable gauge mymcp.recorder.circuit_open

Registered during build_supervisor. Reads supervisor.circuit_open at
scrape time. Threads new max_tokens + circuit_breaker_threshold
settings from config into Bootstrapper / MergeCycle / RecorderSupervisor.

Recommended Prometheus alert (downstream ops, not shipped here):
  expr: mymcp_recorder_circuit_open == 1
  for: 1m"
```

---

## Task 13: Banner display fix — three distinct messages

**Files:**
- Modify: `src/mymcp/recorder/tool.py`
- Modify: `src/mymcp/mcp_server.py`
- Test: `tests/recorder/test_server_overview_tool.py`

Three prioritised messages: circuit-open > stale > recent-failure. Fix the misleading "0 minutes stale" when only `last_error` is set.

- [ ] **Step 1: Add failing tests**

Append to `tests/recorder/test_server_overview_tool.py`:

```python
def test_banner_shows_error_without_misleading_zero_minutes_stale(tmp_path):
    store = OverviewStore(tmp_path)
    store.write_overview("# Server Overview\n")
    result = server_overview_handler(
        store=store,
        schedule_bootstrap=lambda: None,
        stale_seconds=None,
        last_error="Unterminated string",
    )
    assert "0 minutes stale" not in result
    assert "last merge cycle failed" in result
    assert "Unterminated string" in result


def test_banner_shows_circuit_open_with_restart_hint(tmp_path):
    store = OverviewStore(tmp_path)
    store.write_overview("# Server Overview\n")
    result = server_overview_handler(
        store=store,
        schedule_bootstrap=lambda: None,
        stale_seconds=None,
        last_error="LLM returned unparseable JSON",
        circuit_open=True,
    )
    assert "paused" in result.lower()
    assert "restart" in result.lower()
    assert "unparseable JSON" in result
    assert "0 minutes stale" not in result


def test_banner_circuit_open_overrides_stale(tmp_path):
    """Circuit-open takes precedence even when stale_seconds is set."""
    store = OverviewStore(tmp_path)
    store.write_overview("# Server Overview\n")
    result = server_overview_handler(
        store=store,
        schedule_bootstrap=lambda: None,
        stale_seconds=1800,
        last_error="boom",
        circuit_open=True,
    )
    assert "paused" in result.lower()
    assert "30 minutes stale" not in result
```

- [ ] **Step 2: Run, expect failures**

Run: `.venv/bin/python -m pytest tests/recorder/test_server_overview_tool.py -v --benchmark-disable`

Expected: failures (`circuit_open` not accepted, "paused" not in result, etc.).

- [ ] **Step 3: Rewrite `tool.py`**

Replace the entire contents of `src/mymcp/recorder/tool.py` with:

```python
"""server_overview MCP tool handler.

Pure function over an OverviewStore plus a bootstrap-scheduling callback.
The MCP dispatcher wires it up with a real supervisor and passes the
current circuit-breaker state.
"""

from collections.abc import Callable

from mymcp.recorder.overview import OverviewStore

_STUB_TEMPLATE = (
    "# Server Overview\n\n"
    "_⚠️ Overview not initialized. Bootstrap scheduled in the background._\n"
    "_Pending events accumulate in audit.log meanwhile._\n"
    "_Once bootstrapped, full changelog at: {changelog}_\n"
)


def server_overview_handler(
    *,
    store: OverviewStore,
    schedule_bootstrap: Callable[[], None],
    stale_seconds: float | None = None,
    last_error: str | None = None,
    circuit_open: bool = False,
) -> str:
    overview = store.read_overview()
    if overview is None:
        schedule_bootstrap()
        return _STUB_TEMPLATE.format(changelog=str(store.changelog_path))
    banner = _build_banner(
        stale_seconds=stale_seconds,
        last_error=last_error,
        circuit_open=circuit_open,
    )
    return banner + overview if banner else overview


def _build_banner(
    *, stale_seconds: float | None, last_error: str | None, circuit_open: bool
) -> str:
    if circuit_open:
        msg = "⛔ recorder paused after repeated merge failures; restart service to retry"
        if last_error:
            msg += f". Last error: {last_error}"
        return f"_{msg}_\n\n"
    if stale_seconds is not None and stale_seconds > 0:
        minutes = int(stale_seconds / 60)
        msg = f"⚠️ overview is {minutes} minutes stale"
        if last_error:
            msg += f": {last_error}"
        return f"_{msg}_\n\n"
    if last_error:
        return f"_⚠️ last merge cycle failed: {last_error}_\n\n"
    return ""
```

- [ ] **Step 4: Update the mcp_server call site**

In `src/mymcp/mcp_server.py`, find the `server_overview_handler(` call (around line 352). Add `circuit_open=status.circuit_open,` as the last keyword argument:

```python
            overview_text = server_overview_handler(
                store=sup_typed.store,
                schedule_bootstrap=lambda: sup_typed.request_bootstrap(),
                stale_seconds=stale,
                last_error=status.last_error,
                circuit_open=status.circuit_open,
            )
```

- [ ] **Step 5: Run, expect pass**

Run: `.venv/bin/python -m pytest tests/recorder/test_server_overview_tool.py -v --benchmark-disable`

Expected: all server_overview_tool tests pass.

- [ ] **Step 6: Format + commit**

```bash
.venv/bin/ruff format src/mymcp/recorder/tool.py src/mymcp/mcp_server.py tests/recorder/test_server_overview_tool.py
git add src/mymcp/recorder/tool.py src/mymcp/mcp_server.py tests/recorder/test_server_overview_tool.py
git commit -m "fix(recorder): honest banner display, three prioritised messages

- Circuit-open ⇒ paused banner with restart hint (highest priority).
- Real stale ⇒ '⚠️ overview is N minutes stale: <err>' (only when
  stale_seconds is set; no more misleading '0 minutes stale').
- Recent failure ⇒ '⚠️ last merge cycle failed: <err>'.
- mcp_server passes circuit_open through from supervisor status."
```

---

## Task 14: Final verification + PR

- [ ] **Step 1: Run the full test suite**

Run: `.venv/bin/python -m pytest tests/ --benchmark-disable -q`

Expected: all tests pass (≥ 577 in the suite; this change adds ~15–20 new tests).

- [ ] **Step 2: Run ruff + mypy**

Run: `.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy src/mymcp`

Expected: all clean. (Pre-existing mypy error was fixed in Task 1.)

- [ ] **Step 3: Push**

Run: `git push -u origin fix/recorder-resilience`

- [ ] **Step 4: Open PR**

Run the following (one HEREDOC body):

```bash
gh pr create --title "fix(recorder): resilient merge cycle, structured output, circuit breaker, Python-owned Recent Changes" --body "$(cat <<'EOF'
## Summary
Re-do of withdrawn PR #46 with broader scope and proper plan-first flow.
Companion spec: \`docs/superpowers/specs/2026-06-04-recorder-resilience-design.md\`
Plan: \`docs/superpowers/plans/2026-06-04-recorder-resilience.md\`

## What changed
- **Configurable output ceiling** — \`MYMCP_RECORDER_LLM_MAX_TOKENS\` (default 16384). Documented per-provider caps (Haiku/Sonnet 4.6 64k, Opus 4.8 128k, GPT-5 128k, DeepSeek v4 384k).
- **Structured output for both providers** — \`LLMClient.call(json_schema=...)\`. OpenAI uses Structured Outputs (\`response_format=json_schema\` with strict mode; falls back to \`json_object\` for DeepSeek). Anthropic injects a forced \`tool_use\` with the schema.
- **Incremental section_updates protocol** — LLM emits only sections that changed. Python owns the metadata header and the **Recent Changes** section (rendered from the changelog tail). Steady-state output is near zero.
- **Early-fail on bad responses** — empty \`text\` or \`stop_reason=\"max_tokens\"\` raise actionable errors before parse.
- **Circuit breaker** — \`MYMCP_RECORDER_CIRCUIT_BREAKER_THRESHOLD\` (default 5). After N consecutive failures the supervisor pauses LLM calls. Recovery is restart-only by design.
- **\`mymcp.recorder.circuit_open\` observable gauge** — for Prometheus alerting. Suggested alert: \`mymcp_recorder_circuit_open == 1 for 1m\`.
- **Honest banner display** — three prioritised messages (paused > stale > recent-failure). Fixes the misleading \"0 minutes stale\".
- **Incidental** — fixed pre-existing \`[unused-ignore]\` mypy error in \`openai_client.py:29\`.

## Test plan
- [x] \`pytest tests/\` — all tests pass
- [x] \`ruff check . && ruff format --check . && mypy src/mymcp\` — clean
- [ ] Post-deploy on GCP: restart, call \`server_overview\`, confirm new \`_Last updated:\` header reflects current time and the 758-event backlog drains over the next several cycles.

## Out of scope (deferred)
- Prometheus alert rule YAML file (ops PR; spec documents the recommended expression).
- Bootstrap section-update protocol (one-off; \`max_tokens=16384\` default makes the full-overview path safe enough).
- Section-name normalisation (\"TL;DR\" vs \"TLDR\" still creates duplicates).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 5: Mark plan execution complete**

Update task #9 in the conversation task tracker to `completed`.

---

## Self-Review Notes

- **Spec coverage:** Each spec section maps to a task — 3.1 → Tasks 2/10/12; 3.2 → Tasks 3/9; 3.3 → Task 9; 3.4 → Tasks 5/6/7; 3.5 → Tasks 4/9; 3.6 → Task 11; 3.7 → Task 12; 3.8 → Task 13; Section 6 (incidental mypy) → Task 1.
- **Placeholders:** None. Every step has either exact commands or complete code.
- **Type consistency:** `MERGE_OUTPUT_SCHEMA` (Task 9), `json_schema` parameter (Tasks 5/6/7/9), `register_callback_gauge` (Task 12), `RecorderStatus.circuit_open` + `supervisor.circuit_open` property (Tasks 11/12/13) all reference each other consistently.
- **Bite-sized:** Each task is one logical commit; steps within a task are 2-5 minute actions.
