"""Tests for EventTailer.pending_count() and the pending_events callback gauge."""

from __future__ import annotations

import json

from mymcp.recorder.events import EventTailer


def _line(**fields) -> str:
    base = {
        "ts": "2026-06-05T10:00:00Z",
        "tool": "bash_execute",
        "result": "ok",
        "params": {"command": "true"},
        "output": {"stdout_head": "ok"},
    }
    base.update(fields)
    return json.dumps(base) + "\n"


def test_pending_count_zero_when_no_log(tmp_path):
    t = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    assert t.pending_count() == 0


def test_pending_count_counts_mutating_successful_entries(tmp_path):
    (tmp_path / "audit.log").write_text(
        _line(tool="bash_execute")
        + _line(tool="read_file", result="ok")  # read-only — not counted
        + _line(tool="write_file")
        + _line(tool="bash_execute", result="denied")  # failed — not counted
        + _line(tool="edit_file")
    )
    t = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    assert t.pending_count() == 3  # bash_execute + write_file + edit_file


def test_pending_count_does_not_consume_or_move_cursor(tmp_path):
    (tmp_path / "audit.log").write_text(_line() + _line() + _line())
    t = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    assert t.pending_count() == 3
    # Reading pending count must not advance the cursor — a subsequent
    # read_new()+commit() should still see all 3 events.
    assert t.pending_count() == 3
    consumed = list(t.read_new())
    assert len(consumed) == 3
    t.commit()
    # After commit, fresh tailer with same cursor sees 0.
    t2 = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    assert t2.pending_count() == 0


def test_pending_count_after_partial_commit(tmp_path):
    (tmp_path / "audit.log").write_text(_line() + _line() + _line())
    t = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    # Consume first event and commit
    it = t.read_new()
    next(it)
    t.commit()
    # 2 events remain pending
    assert t.pending_count() == 2


def test_observe_pending_events_returns_zero_when_no_supervisor():
    from mymcp.recorder.wiring import set_active_supervisor
    from mymcp.recorder.wiring import _observe_pending_events

    set_active_supervisor(None)
    obs = list(_observe_pending_events())
    assert obs[0].value == 0


def test_observe_pending_events_reads_tailer(tmp_path):
    """Callback queries the supervisor's tailer; arbitrary integer expected."""
    from unittest.mock import MagicMock

    from mymcp.recorder.wiring import set_active_supervisor
    from mymcp.recorder.task import RecorderSupervisor
    from mymcp.recorder.wiring import _observe_pending_events

    tailer = MagicMock()
    tailer.pending_count = MagicMock(return_value=42)
    merge = MagicMock()
    merge._tailer = tailer  # supervisor exposes tailer via merge_cycle
    sup = RecorderSupervisor(
        merge_cycle=merge,
        bootstrapper=MagicMock(),
        merge_interval_sec=300.0,
        provider="anthropic",
        model="x",
    )
    set_active_supervisor(sup)
    try:
        obs = list(_observe_pending_events())
        assert obs[0].value == 42
    finally:
        set_active_supervisor(None)


def test_recorder_status_pending_events_populated(tmp_path):
    """RecorderStatus.pending_events should mirror the tailer's count."""
    from unittest.mock import MagicMock

    from mymcp.recorder.task import RecorderSupervisor

    tailer = MagicMock()
    tailer.pending_count = MagicMock(return_value=7)
    merge = MagicMock()
    merge._tailer = tailer
    sup = RecorderSupervisor(
        merge_cycle=merge,
        bootstrapper=MagicMock(),
        merge_interval_sec=300.0,
        provider="anthropic",
        model="x",
    )
    status = sup.status()
    assert status.pending_events == 7
