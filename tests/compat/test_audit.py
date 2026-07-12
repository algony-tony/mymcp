"""Acceptance: the Python recorder EventTailer consumes the audit.log the Go
server writes (the compat suite's audit-format contract)."""

import os
import time
from pathlib import Path

import pytest

from mymcp.recorder.events import EventTailer

AUDIT_DIR = os.environ.get("MYMCP_COMPAT_AUDIT_DIR", "")
PROTECTED_FILE = "/tmp/mymcp-compat-protected/x"
pytestmark = pytest.mark.skipif(not AUDIT_DIR, reason="MYMCP_COMPAT_AUDIT_DIR not set")


@pytest.mark.anyio
async def test_tailer_consumes_mutating_success_events(rw, scratch, tmp_path):
    u1 = os.path.join(scratch, "audit-target.txt")

    assert (await rw.call("write_file", {"file_path": u1, "content": "hello\nworld\n"}))["success"]
    assert (
        await rw.call("edit_file", {"file_path": u1, "old_string": "hello", "new_string": "HELLO"})
    )["success"]
    assert (await rw.call("bash_execute", {"command": "true"}))["exit_code"] == 0

    # Mutating but FAILED — must be filtered out by the tailer.
    assert (await rw.call("bash_execute", {"command": "exit 3"}))["exit_code"] == 3
    assert (await rw.call("write_file", {"file_path": PROTECTED_FILE, "content": "no"}))[
        "success"
    ] is False
    # Read-only — never mutating.
    await rw.call("read_file", {"file_path": u1})

    time.sleep(0.2)  # audit writes are synchronous, but be gentle on CI FS

    tailer = EventTailer(log_dir=Path(AUDIT_DIR), cursor_path=tmp_path / "cursor.json")
    events = list(tailer.read_new())

    def out(e):
        return e.output or {}

    assert [e for e in events if e.tool == "write_file" and out(e).get("path") == u1]
    assert [e for e in events if e.tool == "edit_file" and out(e).get("path") == u1]
    assert [e for e in events if e.tool == "bash_execute" and out(e).get("exit_code") == 0]

    # Failures + reads must never surface.
    assert not [e for e in events if e.tool == "bash_execute" and out(e).get("exit_code") == 3]
    assert not [
        e for e in events if e.tool == "write_file" and out(e).get("path") == PROTECTED_FILE
    ]
    assert not [e for e in events if e.tool == "read_file"]
