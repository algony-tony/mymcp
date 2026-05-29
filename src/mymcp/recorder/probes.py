"""Internal probe tools used by the bootstrap agent loop.

These are NOT exposed via MCP. The bootstrap LLM uses them to inspect the
host: run shell commands (read-only intent), read text files. Output is
truncated to keep prompt sizes manageable. Timeouts are enforced.
"""

import asyncio
import contextlib
from typing import Any

from mymcp.audit_output import truncate_bash_output
from mymcp.observability import instruments
from mymcp.recorder.llm.base import ToolSchema

BASH_PROBE_TOOL = ToolSchema(
    name="bash_probe",
    description=(
        "Run a shell command on this server to probe its state. "
        "Read-only intent; output is truncated. Use for: detecting OS/distro, "
        "listing services, scanning ports, examining filesystem layout."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to run"},
        },
        "required": ["command"],
    },
)


READ_FILE_PROBE_TOOL = ToolSchema(
    name="read_file_probe",
    description=(
        "Read a small text file (configs, /etc/os-release, unit files) to inform the overview."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
        },
        "required": ["path"],
    },
)


async def run_bash_probe(
    input: dict[str, Any],
    *,
    timeout_sec: int = 30,
    head_bytes: int = 4096,
    tail_bytes: int = 4096,
) -> dict[str, Any]:
    cmd = str(input.get("command", ""))
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
        timed_out = False
    except TimeoutError:
        proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()
        stdout, stderr = b"", b""
        timed_out = True
    summary = truncate_bash_output(stdout, head_bytes=head_bytes, tail_bytes=tail_bytes)
    stderr_summary = truncate_bash_output(stderr, head_bytes=2048, tail_bytes=2048)
    summary.update(
        {
            "exit_code": proc.returncode if proc.returncode is not None else -1,
            "timed_out": timed_out,
            "stderr_head": stderr_summary["stdout_head"],
            "stderr_tail": stderr_summary["stdout_tail"],
        }
    )
    if timed_out:
        instruments.recorder_bash_probe_runs.add(1, {"result": "timeout"})
    elif summary.get("exit_code") == 0:
        instruments.recorder_bash_probe_runs.add(1, {"result": "success"})
    else:
        instruments.recorder_bash_probe_runs.add(1, {"result": "error"})
    return summary


async def run_read_file_probe(
    input: dict[str, Any],
    *,
    max_bytes: int = 16_384,
) -> dict[str, Any]:
    path = str(input.get("path", ""))
    try:
        with open(path, "rb") as f:
            raw = f.read(max_bytes + 1)
        truncated = len(raw) > max_bytes
        text = raw[:max_bytes].decode("utf-8", errors="replace")
        return {"content": text, "truncated": truncated, "error": None}
    except OSError as e:
        return {"content": "", "truncated": False, "error": str(e)}
