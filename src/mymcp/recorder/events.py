"""Tail audit.log for successful mutating tool events.

The tailer reads JSON-lines from the audit log starting at the persisted
cursor offset, yields events for mutating tools that succeeded, and advances
the cursor only when commit() is called. This provides at-least-once delivery
if the consumer crashes between read_new() and commit().

Rotation handling: when the audit.log inode differs from the cursor's recorded
inode, the previous file (audit.log.1) is read from its remembered offset
first (best-effort), then reading resumes at the head of the new file.
"""

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mymcp.observability import instruments
from mymcp.recorder.cursor import Cursor

log = logging.getLogger("mymcp.recorder")


# Tools whose successful invocations mutate the host state and should be
# folded into the overview by the recorder. The recorder ignores read-only
# tools (read_file, glob, grep) and any tool that returned an error/denied.
MUTATING_TOOLS: frozenset[str] = frozenset(
    {
        "bash_execute",
        "write_file",
        "edit_file",
        "prepare_upload",
        "prepare_download",
    }
)


# Audit "result" values that count as successful. mcp_server writes "ok" for
# success; we tolerate "success" too for forward-compat.
_SUCCESS_RESULTS: frozenset[str] = frozenset({"ok", "success"})


@dataclass
class AuditEvent:
    ts: str
    tool: str
    params: dict[str, Any]
    output: dict[str, Any] | None
    request_id: str | None
    trace_id: str | None


class EventTailer:
    """Pull mutating-and-successful events from audit.log since last commit."""

    def __init__(self, *, log_dir: Path, cursor_path: Path):
        self._log_dir = Path(log_dir)
        self._cursor_path = Path(cursor_path)
        self._committed = Cursor.load(self._cursor_path)
        self._pending = Cursor(
            file=self._committed.file,
            inode=self._committed.inode,
            offset=self._committed.offset,
        )

    def read_new(self) -> Iterator[AuditEvent]:
        audit_path = self._log_dir / "audit.log"
        if not audit_path.exists():
            return
        st = audit_path.stat()

        if self._pending.inode is not None and self._pending.inode != st.st_ino:
            # Rotation: best-effort read of remaining tail of the old file.
            rotated = self._log_dir / "audit.log.1"
            if rotated.exists():
                try:
                    if rotated.stat().st_ino == self._pending.inode:
                        yield from self._read_from(rotated, self._pending.offset, update=False)
                except OSError:
                    pass
            self._pending = Cursor(file="audit.log", inode=st.st_ino, offset=0)
        elif self._pending.inode is None:
            self._pending = Cursor(file="audit.log", inode=st.st_ino, offset=0)

        yield from self._read_from(audit_path, self._pending.offset, update=True)

    def _read_from(self, path: Path, start_offset: int, *, update: bool) -> Iterator[AuditEvent]:
        try:
            with path.open("rb") as f:
                f.seek(start_offset)
                while True:
                    raw = f.readline()
                    if not raw:
                        break
                    new_offset = f.tell()
                    if update:
                        self._pending.offset = new_offset
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if entry.get("result") not in _SUCCESS_RESULTS:
                        continue
                    tool = entry.get("tool")
                    if tool not in MUTATING_TOOLS:
                        continue
                    instruments.recorder_events_consumed.add(1, {"tool": str(tool)})
                    yield AuditEvent(
                        ts=str(entry.get("ts", "")),
                        tool=str(tool),
                        params=entry.get("params") or {},
                        output=entry.get("output"),
                        request_id=entry.get("request_id"),
                        trace_id=entry.get("trace_id"),
                    )
        except OSError as e:
            log.warning("recorder.events.read_error", extra={"path": str(path), "err": str(e)})

    def commit(self) -> None:
        self._pending.save(self._cursor_path)
        self._committed = Cursor(
            file=self._pending.file,
            inode=self._pending.inode,
            offset=self._pending.offset,
        )

    def rollback(self) -> None:
        """Reset the pending cursor back to the last committed position.

        Call this when a processing error occurs after read_new() but before
        commit() so the next read_new() will re-yield the same events.
        """
        self._pending = Cursor(
            file=self._committed.file,
            inode=self._committed.inode,
            offset=self._committed.offset,
        )

    def pending_offset(self) -> int:
        return self._pending.offset

    def committed_offset(self) -> int:
        return self._committed.offset or 0

    def pending_count(self) -> int:
        """Count mutating-and-successful events sitting unconsumed past the cursor.

        Read-only: does not advance or mutate the cursor. Iterates the file
        from the committed offset to EOF, applying the same mutating-tool +
        success-result filter as ``read_new``. Intended for the Prometheus
        backlog gauge, so safe to call on every scrape.
        """
        audit_path = self._log_dir / "audit.log"
        if not audit_path.exists():
            return 0
        try:
            st = audit_path.stat()
        except OSError:
            return 0
        count = 0
        # Rotation case: count the unread tail of the previous file too.
        if self._committed.inode is not None and self._committed.inode != st.st_ino:
            rotated = self._log_dir / "audit.log.1"
            if rotated.exists():
                try:
                    if rotated.stat().st_ino == self._committed.inode:
                        count += self._count_from(rotated, self._committed.offset)
                except OSError:
                    pass
            count += self._count_from(audit_path, 0)
        else:
            start = self._committed.offset or 0
            count += self._count_from(audit_path, start)
        return count

    @staticmethod
    def _count_from(path: Path, start_offset: int) -> int:
        try:
            with path.open("rb") as f:
                f.seek(start_offset)
                n = 0
                for raw in f:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if entry.get("result") not in _SUCCESS_RESULTS:
                        continue
                    if entry.get("tool") not in MUTATING_TOOLS:
                        continue
                    n += 1
                return n
        except OSError:
            return 0
