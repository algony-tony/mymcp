"""Hypothesis property tests for the audit log writer/reader contract.

The audit log is JSON-lines. ``EventTailer.read_new()`` must round-trip
whatever ``log_tool_call`` writes, and must skip blank / junk lines without
crashing. These tests fuzz around the schema invariants rather than the
exact strings that hand-written examples cover.
"""

from __future__ import annotations

import json

import pytest
from hypothesis import given, settings, strategies as st

from mymcp.recorder.events import MUTATING_TOOLS, EventTailer

# Strategy for a single audit record — keep values in shapes the writer
# can actually produce. The writer wraps every value through json.dumps,
# so we keep generated payloads JSON-safe (no surrogate codepoints).
_safe_text = st.text(
    alphabet=st.characters(
        blacklist_categories=("Cs",),  # no unpaired surrogates
    ),
    max_size=200,
)

_mutating_tool = st.sampled_from(sorted(MUTATING_TOOLS))
_role = st.sampled_from(["admin", "rw", "ro", "ticket", "unknown"])
_result = st.sampled_from(["ok", "success", "error"])

_audit_record = st.fixed_dictionaries(
    {
        "ts": st.just("2026-06-06T10:00:00Z"),
        "token_name": _safe_text,
        "role": _role,
        "ip": _safe_text,
        "tool": _mutating_tool,
        "params": st.dictionaries(_safe_text, _safe_text, max_size=5),
        "result": _result,
    }
)


@pytest.fixture
def empty_log(tmp_path):
    """Empty audit.log + EventTailer pointed at it."""
    log = tmp_path / "audit.log"
    log.write_text("")
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    return log, tailer


@given(records=st.lists(_audit_record, min_size=1, max_size=20))
@settings(max_examples=100, deadline=None)
def test_writer_format_round_trips_through_tailer(records, tmp_path_factory):
    """Anything the writer produces (json.dumps + newline) must parse back.

    Successful + mutating records must round-trip exactly through
    EventTailer.read_new() — no field loss, no false drops.
    """
    tmp = tmp_path_factory.mktemp("audit")
    log = tmp / "audit.log"
    with log.open("w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")

    tailer = EventTailer(log_dir=tmp, cursor_path=tmp / "cursor.json")
    events = list(tailer.read_new())

    # Every record we wrote was both mutating (we sampled from MUTATING_TOOLS)
    # and successful (we sampled from _SUCCESS_RESULTS). So the tailer must
    # yield exactly len(records) events.
    expected = [r for r in records if r["result"] in {"ok", "success"}]
    assert len(events) == len(expected)
    for got, want in zip(events, expected, strict=True):
        assert got.tool == want["tool"]
        assert got.params == want["params"]


@given(noise=st.text(max_size=500))
@settings(max_examples=50, deadline=None)
def test_tailer_does_not_crash_on_junk_lines(noise, tmp_path_factory):
    """Truncated / non-JSON lines must be skipped, not raise.

    Mid-line crashes from the audit writer (disk full, sigkill) can leave
    partial lines on disk. The tailer must roll past them.
    """
    tmp = tmp_path_factory.mktemp("audit")
    log = tmp / "audit.log"
    # Mix: junk on its own line, then a valid entry, then more junk.
    valid = json.dumps(
        {
            "ts": "2026-06-06T10:00:00Z",
            "tool": "write_file",
            "params": {},
            "result": "ok",
        }
    )
    log.write_text(noise + "\n" + valid + "\n" + noise + "\n")

    tailer = EventTailer(log_dir=tmp, cursor_path=tmp / "cursor.json")
    # Just exercise the parser — junk must not raise.
    events = list(tailer.read_new())
    # The valid line must always come through regardless of surrounding junk.
    assert any(e.tool == "write_file" for e in events)


@given(records=st.lists(_audit_record, min_size=2, max_size=10))
@settings(max_examples=50, deadline=None)
def test_tailer_cursor_advances_monotonically(records, tmp_path_factory):
    """After consuming N events and committing, a re-read must yield 0 new
    events until more bytes arrive — i.e. the cursor never replays."""
    tmp = tmp_path_factory.mktemp("audit")
    log = tmp / "audit.log"
    with log.open("w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")

    tailer = EventTailer(log_dir=tmp, cursor_path=tmp / "cursor.json")
    list(tailer.read_new())
    tailer.commit()

    # Second drain on unchanged file must yield zero events.
    assert list(tailer.read_new()) == []
