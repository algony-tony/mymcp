import asyncio
import os
import signal
import threading
import time
from weakref import WeakSet

from mymcp import config

_inflight_lock = threading.Lock()
_inflight: WeakSet = WeakSet()


def _track_process(p) -> None:
    """Register a subprocess (sync Popen or asyncio.subprocess.Process) for
    SIGTERM cleanup. The object is held weakly; callers retain ownership."""
    with _inflight_lock:
        _inflight.add(p)


def _untrack_process(p) -> None:
    with _inflight_lock:
        _inflight.discard(p)


def _is_alive(p) -> bool:
    if hasattr(p, "poll"):
        return p.poll() is None
    return getattr(p, "returncode", None) is None


def shutdown_inflight_processes(grace_sec: int | None = None) -> None:
    """Send SIGTERM to all tracked process groups, then SIGKILL after grace.

    Idempotent and safe to call from a signal handler.
    """
    if grace_sec is None:
        try:
            grace_sec = config.get_settings().shutdown_grace_sec
        except Exception:
            grace_sec = 5

    with _inflight_lock:
        snapshot = list(_inflight)

    for p in snapshot:
        if not _is_alive(p):
            continue
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass

    deadline = time.monotonic() + max(0, grace_sec)
    while time.monotonic() < deadline:
        if all(not _is_alive(p) for p in snapshot):
            return
        time.sleep(0.05)

    for p in snapshot:
        if not _is_alive(p):
            continue
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


async def run_bash_execute(
    command: str,
    timeout: int = 30,
    working_dir: str = "/",
    max_output_bytes: int = config.BASH_MAX_OUTPUT_BYTES,
) -> dict:
    timeout = min(max(1, timeout), 600)
    max_output_bytes = min(max(1, max_output_bytes), config.BASH_MAX_OUTPUT_BYTES_HARD)

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=working_dir,
            start_new_session=True,
        )
    except FileNotFoundError:
        return {
            "success": False,
            "error": "FileNotFoundError",
            "message": f"Working directory not found: {working_dir}",
            "suggestion": "Check that the working_dir path exists",
        }
    except PermissionError as e:
        return {
            "success": False,
            "error": "PermissionError",
            "message": str(e),
            "suggestion": "Check directory permissions",
        }

    _track_process(proc)
    try:
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=float(timeout)
            )
        except asyncio.TimeoutError:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                await asyncio.wait_for(proc.communicate(), timeout=2)
            except asyncio.TimeoutError:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
                await proc.communicate()
            return {
                "stdout": "",
                "stderr": f"Command timed out after {timeout}s",
                "exit_code": -1,
                "timed_out": True,
            }
    finally:
        _untrack_process(proc)

    def _truncate(data: bytes, limit: int) -> str:
        if len(data) <= limit:
            return data.decode("utf-8", errors="replace")
        shown = data[:limit].decode("utf-8", errors="replace")
        return f"{shown}\n[TRUNCATED: total {len(data)} bytes, showing first {limit} bytes]"

    return {
        "stdout": _truncate(stdout_bytes, max_output_bytes),
        "stderr": _truncate(stderr_bytes, max_output_bytes),
        "exit_code": proc.returncode,
        "timed_out": False,
    }
