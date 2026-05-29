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
                "updated_overview_md": "# Server Overview\n\n## Installed Services\n- nginx\n",
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
    tail = store.read_changelog_tail(5)
    assert tail and "installed nginx" in tail[-1]


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
                "updated_overview_md": "# New\n",
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
                "updated_overview_md": "# Updated\n",
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
    assert store.read_overview() == "# Updated\n"


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
        return_value=_fake_response({"new_changelog_lines": ["x"], "updated_overview_md": "# X\n"})
    )
    cycle = MergeCycle(client=fake, tailer=tailer, store=store, max_events_per_cycle=5)
    result = await cycle.run_once()
    assert result.events_consumed == 5
