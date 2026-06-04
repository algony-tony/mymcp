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
    assert "nginx" in overview
    assert "Fresh." in overview
    assert "_Last updated:" in overview
    tail = store.read_changelog_tail(5)
    assert tail and "installed nginx" in tail[-1]
    assert fake.call.call_args.kwargs["json_schema"] is not None


@pytest.mark.anyio
async def test_merge_accepts_tool_use_response(tmp_path):
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
        client=fake,
        tailer=tailer,
        store=store,
        max_events_per_cycle=10,
        max_tokens=4096,
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
        client=fake,
        tailer=tailer,
        store=store,
        max_events_per_cycle=10,
        max_tokens=32768,
    )
    await cycle.run_once()
    assert fake.call.call_args.kwargs["max_tokens"] == 32768


@pytest.mark.anyio
async def test_merge_python_owns_recent_changes(tmp_path):
    _write_log(
        tmp_path,
        _audit_line(tool="bash_execute", params={"command": "ls"}, output={"stdout_head": "x"}),
    )
    store = OverviewStore(tmp_path / "overview")
    store.write_overview("# Old\n")
    store.append_changelog(["2026-06-01 10:00 | bash_execute | older event"])
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    fake = AsyncMock()
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
    new_idx = overview.index("new event")
    older_idx = overview.index("older event")
    assert new_idx < older_idx
    assert "_Full changelog: changelog.md" in overview


@pytest.mark.anyio
async def test_merge_preserves_untouched_sections(tmp_path):
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
        return_value=_text_response(
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
        return_value=_text_response({"new_changelog_lines": [], "section_updates": ["wrong shape"]})
    )
    cycle = MergeCycle(client=fake, tailer=tailer, store=store, max_events_per_cycle=10)
    with pytest.raises(ValueError, match="section_updates"):
        await cycle.run_once()
