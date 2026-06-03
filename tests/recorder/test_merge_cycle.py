import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from mymcp.recorder.events import EventTailer
from mymcp.recorder.llm.base import LLMResponse, Usage
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


def _fake_response(payload: dict, usage_in: int = 10, usage_out: int = 20) -> LLMResponse:
    return LLMResponse(
        text=json.dumps(payload),
        tool_uses=[],
        stop_reason="end_turn",
        usage=Usage(input_tokens=usage_in, output_tokens=usage_out),
    )


@pytest.mark.anyio
async def test_merge_with_events_writes_overview_and_changelog(tmp_path):
    _write_log(
        tmp_path,
        _audit_line(
            tool="bash_execute",
            params={"command": "apt install nginx"},
            output={"stdout_head": "ok"},
        ),
    )
    store = OverviewStore(tmp_path / "overview")
    # Seed an existing overview so this is a true merge cycle (not bootstrap).
    store.write_overview("# Server Overview\n\n## TL;DR\nFresh.\n")
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    fake = AsyncMock()
    fake.call = AsyncMock(
        return_value=_fake_response(
            {
                "new_changelog_lines": ["2026-05-29 10:00 | bash_execute | installed nginx"],
                "section_updates": {"Installed Services": "- nginx"},
            }
        )
    )
    cycle = MergeCycle(client=fake, tailer=tailer, store=store, max_events_per_cycle=10)
    result = await cycle.run_once()

    assert result.events_consumed == 1
    assert result.skipped_reason is None
    assert result.tokens_in == 10
    assert result.tokens_out == 20
    overview = store.read_overview() or ""
    assert "nginx" in overview
    # Untouched section should be preserved.
    assert "Fresh." in overview
    # Header is rewritten by Python with current metadata.
    assert "_Last updated:" in overview
    tail = store.read_changelog_tail(5)
    assert tail and "installed nginx" in tail[-1]
    # json_mode should be requested for structured output.
    assert fake.call.call_args.kwargs["json_mode"] is True


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
    store = OverviewStore(tmp_path / "overview")  # no overview written yet
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    fake = AsyncMock()
    cycle = MergeCycle(
        client=fake,
        tailer=tailer,
        store=store,
        max_events_per_cycle=10,
        require_bootstrap=True,
    )
    result = await cycle.run_once()
    assert result.events_consumed == 0
    assert result.skipped_reason == "bootstrap_required"
    fake.call.assert_not_called()


@pytest.mark.anyio
async def test_merge_unparseable_json_raises_and_does_not_advance(tmp_path):
    _write_log(
        tmp_path,
        _audit_line(
            tool="write_file",
            params={"file_path": "/x"},
            output={"path": "/x", "size_bytes": 5, "sha256": "a", "first_line": "x"},
        ),
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
    # Overview unchanged (atomic write never happened)
    assert store.read_overview() == "# Existing\n"
    # Cursor not committed → next run sees same event
    fake.call = AsyncMock(
        return_value=_fake_response(
            {
                "new_changelog_lines": ["2026-05-29 10:00 | write_file | wrote /x"],
                "section_updates": {"TL;DR": "ok"},
            }
        )
    )
    result = await cycle.run_once()
    assert result.events_consumed == 1


@pytest.mark.anyio
async def test_merge_parses_code_fenced_json(tmp_path):
    """Some models wrap JSON in ```json fences."""
    _write_log(
        tmp_path,
        _audit_line(tool="bash_execute", params={"command": "ls"}, output={"stdout_head": "x"}),
    )
    store = OverviewStore(tmp_path / "overview")
    store.write_overview("# Old\n")
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    fenced_text = (
        "```json\n"
        + json.dumps(
            {
                "new_changelog_lines": ["2026-05-29 10:00 | bash_execute | did stuff"],
                "section_updates": {"TL;DR": "Updated."},
            }
        )
        + "\n```"
    )
    fake = AsyncMock()
    fake.call = AsyncMock(
        return_value=LLMResponse(
            text=fenced_text,
            tool_uses=[],
            stop_reason="end_turn",
            usage=Usage(input_tokens=1, output_tokens=1),
        )
    )
    cycle = MergeCycle(client=fake, tailer=tailer, store=store, max_events_per_cycle=10)
    result = await cycle.run_once()
    assert result.events_consumed == 1
    assert "Updated." in (store.read_overview() or "")


@pytest.mark.anyio
async def test_merge_caps_events_per_cycle(tmp_path):
    entries = [
        _audit_line(
            tool="write_file",
            params={"file_path": f"/x{i}"},
            output={"path": f"/x{i}", "size_bytes": 1, "sha256": "a", "first_line": ""},
        )
        for i in range(15)
    ]
    _write_log(tmp_path, *entries)
    store = OverviewStore(tmp_path / "overview")
    store.write_overview("# Old\n")
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    fake = AsyncMock()
    fake.call = AsyncMock(
        return_value=_fake_response({"new_changelog_lines": ["x"], "section_updates": {}})
    )
    cycle = MergeCycle(client=fake, tailer=tailer, store=store, max_events_per_cycle=5)
    result = await cycle.run_once()
    assert result.events_consumed == 5


@pytest.mark.anyio
async def test_merge_empty_response_raises(tmp_path):
    """Empty LLM response is a known failure (rate limit, model refusal, etc.)
    and must short-circuit before we try to parse half-JSON."""
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
    assert store.read_overview() == "# Old\n"  # untouched


@pytest.mark.anyio
async def test_merge_max_tokens_truncation_raises_early(tmp_path):
    """When the LLM hits max_tokens, the response is truncated mid-JSON.
    Detect via stop_reason and surface a clear actionable error instead of
    a confusing 'Unterminated string' downstream."""
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
            text='{"new_changelog_lines": ["abc"], "section_updates": {"TL;DR": "long',
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
    """max_tokens from config flows through to the LLM call."""
    _write_log(
        tmp_path,
        _audit_line(tool="bash_execute", params={"command": "ls"}, output={"stdout_head": "x"}),
    )
    store = OverviewStore(tmp_path / "overview")
    store.write_overview("# Old\n")
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    fake = AsyncMock()
    fake.call = AsyncMock(
        return_value=_fake_response({"new_changelog_lines": [], "section_updates": {}})
    )
    cycle = MergeCycle(
        client=fake, tailer=tailer, store=store, max_events_per_cycle=10, max_tokens=32768
    )
    await cycle.run_once()
    assert fake.call.call_args.kwargs["max_tokens"] == 32768


@pytest.mark.anyio
async def test_merge_preserves_untouched_sections(tmp_path):
    """Sections not mentioned in section_updates must keep their existing bodies."""
    _write_log(
        tmp_path,
        _audit_line(tool="bash_execute", params={"command": "ls"}, output={"stdout_head": "x"}),
    )
    store = OverviewStore(tmp_path / "overview")
    store.write_overview(
        "# Server Overview\n\n## TL;DR\nKeep me.\n\n## Known Quirks\n- preserve this\n"
    )
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    fake = AsyncMock()
    fake.call = AsyncMock(
        return_value=_fake_response(
            {
                "new_changelog_lines": [],
                "section_updates": {"Installed Services": "- nginx"},
            }
        )
    )
    cycle = MergeCycle(client=fake, tailer=tailer, store=store, max_events_per_cycle=10)
    await cycle.run_once()
    overview = store.read_overview() or ""
    assert "Keep me." in overview
    assert "preserve this" in overview
    assert "nginx" in overview


@pytest.mark.anyio
async def test_merge_rejects_wrong_schema_types(tmp_path):
    """Defensive parse: reject responses whose section_updates isn't an object."""
    _write_log(
        tmp_path,
        _audit_line(tool="bash_execute", params={"command": "ls"}, output={"stdout_head": "x"}),
    )
    store = OverviewStore(tmp_path / "overview")
    store.write_overview("# Old\n")
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    fake = AsyncMock()
    fake.call = AsyncMock(
        return_value=_fake_response({"new_changelog_lines": [], "section_updates": ["wrong shape"]})
    )
    cycle = MergeCycle(client=fake, tailer=tailer, store=store, max_events_per_cycle=10)
    with pytest.raises(ValueError, match="section_updates"):
        await cycle.run_once()
