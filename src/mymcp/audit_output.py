"""Tool-specific output summaries for audit log enrichment (T1).

Each helper returns a JSON-serialisable dict that becomes the `output` field
of an audit entry. Content is summarised — never store full file contents.
"""

import hashlib
from typing import Any


def _safe_decode(b: bytes) -> str:
    return b.decode("utf-8", errors="replace")


def truncate_bash_output(
    raw: bytes,
    *,
    head_bytes: int,
    tail_bytes: int,
) -> dict[str, Any]:
    total = len(raw)
    sha = hashlib.sha256(raw).hexdigest()
    if total <= head_bytes + tail_bytes:
        return {
            "stdout_head": _safe_decode(raw),
            "stdout_tail": "",
            "stdout_truncated_bytes": 0,
            "stdout_sha256": sha,
        }
    head = raw[:head_bytes]
    tail = raw[-tail_bytes:]
    return {
        "stdout_head": _safe_decode(head),
        "stdout_tail": _safe_decode(tail),
        "stdout_truncated_bytes": total - head_bytes - tail_bytes,
        "stdout_sha256": sha,
    }


def write_file_output(*, path: str, content: bytes) -> dict[str, Any]:
    first_line = _safe_decode(content.split(b"\n", 1)[0]) if content else ""
    return {
        "path": path,
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "first_line": first_line,
    }


def edit_file_output(
    *,
    path: str,
    lines_added: int,
    lines_removed: int,
    hunk_count: int,
) -> dict[str, Any]:
    return {
        "path": path,
        "lines_added": lines_added,
        "lines_removed": lines_removed,
        "hunk_count": hunk_count,
    }
