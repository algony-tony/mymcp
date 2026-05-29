import json
from pathlib import Path

from mymcp.recorder.events import MUTATING_TOOLS, EventTailer


def _audit_lines(*entries) -> str:
    return "".join(json.dumps(e) + "\n" for e in entries)


def _write(tmp_path: Path, content: str, name: str = "audit.log") -> Path:
    p = tmp_path / name
    p.write_text(content)
    return p


def test_tailer_reads_all_from_fresh_cursor(tmp_path):
    _write(
        tmp_path,
        _audit_lines(
            {"ts": "t1", "tool": "bash_execute", "result": "ok", "output": {"stdout_head": "ok"}},
            {"ts": "t2", "tool": "read_file", "result": "ok"},  # filtered: read-only
            {"ts": "t3", "tool": "write_file", "result": "ok", "output": {"path": "/a"}},
        ),
    )
    cursor_path = tmp_path / "cursor.json"
    tailer = EventTailer(log_dir=tmp_path, cursor_path=cursor_path)
    events = list(tailer.read_new())
    assert [e.tool for e in events] == ["bash_execute", "write_file"]
    assert events[0].output == {"stdout_head": "ok"}
    tailer.commit()
    # second read returns nothing because cursor is at end
    assert list(tailer.read_new()) == []


def test_tailer_uncommitted_events_re_yielded(tmp_path):
    _write(
        tmp_path,
        _audit_lines(
            {"ts": "t1", "tool": "write_file", "result": "ok"},
        ),
    )
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    list(tailer.read_new())
    # No commit() → fresh tailer (same cursor file) re-reads from offset 0
    tailer2 = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    events = list(tailer2.read_new())
    assert len(events) == 1


def test_tailer_skips_failed_events(tmp_path):
    _write(
        tmp_path,
        _audit_lines(
            {"ts": "t1", "tool": "bash_execute", "result": "error"},
            {"ts": "t2", "tool": "write_file", "result": "denied"},
        ),
    )
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    assert list(tailer.read_new()) == []


def test_tailer_skips_read_only_tools(tmp_path):
    _write(
        tmp_path,
        _audit_lines(
            {"ts": "t1", "tool": "read_file", "result": "ok"},
            {"ts": "t2", "tool": "glob", "result": "ok"},
            {"ts": "t3", "tool": "grep", "result": "ok"},
        ),
    )
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    assert list(tailer.read_new()) == []


def test_tailer_handles_corrupt_lines(tmp_path):
    p = tmp_path / "audit.log"
    p.write_text(
        "not-json\n"
        + json.dumps({"ts": "t", "tool": "write_file", "result": "ok"})
        + "\n"
        + "\n"  # blank line
        + "{partial\n"
    )
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    events = list(tailer.read_new())
    assert len(events) == 1
    assert events[0].tool == "write_file"


def test_tailer_advances_cursor_only_on_commit(tmp_path):
    _write(
        tmp_path,
        _audit_lines(
            {"ts": "t1", "tool": "write_file", "result": "ok"},
        ),
    )
    cursor_path = tmp_path / "cursor.json"
    tailer = EventTailer(log_dir=tmp_path, cursor_path=cursor_path)
    list(tailer.read_new())
    # cursor file not yet written
    assert not cursor_path.exists()
    tailer.commit()
    assert cursor_path.exists()


def test_tailer_handles_rotation_new_inode(tmp_path):
    # First write + read + commit
    _write(
        tmp_path,
        _audit_lines(
            {"ts": "t1", "tool": "write_file", "result": "ok"},
        ),
    )
    cursor_path = tmp_path / "cursor.json"
    t1 = EventTailer(log_dir=tmp_path, cursor_path=cursor_path)
    list(t1.read_new())
    t1.commit()
    # Simulate rotation: rename current audit.log → audit.log.1, write fresh audit.log
    (tmp_path / "audit.log").rename(tmp_path / "audit.log.1")
    _write(
        tmp_path,
        _audit_lines(
            {"ts": "t2", "tool": "bash_execute", "result": "ok"},
        ),
    )
    # Fresh tailer detects inode change and reads from start of new file
    t2 = EventTailer(log_dir=tmp_path, cursor_path=cursor_path)
    events = list(t2.read_new())
    tools = [e.tool for e in events]
    # Must at least include the new event; may also include tail of rotated file
    assert "bash_execute" in tools


def test_tailer_no_audit_log_yet(tmp_path):
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    assert list(tailer.read_new()) == []


def test_mutating_tools_set_membership():
    assert "bash_execute" in MUTATING_TOOLS
    assert "write_file" in MUTATING_TOOLS
    assert "edit_file" in MUTATING_TOOLS
    assert "read_file" not in MUTATING_TOOLS
    assert "glob" not in MUTATING_TOOLS
