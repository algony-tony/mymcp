import asyncio
import contextlib
import os
import signal
import threading
import time
from weakref import WeakSet

from opentelemetry.metrics import Observation

from mymcp import config
from mymcp.observability.instruments import register_callback_gauge
from mymcp.observability.tracing import get_tracer

_tracer = get_tracer(__name__)

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


def _signal_process_tree(p, sig: int) -> None:  # pragma: no mutate
    # If start_new_session was ever skipped (e.g. mutated away) the child
    # would share our pgid; killpg would then signal ourselves and take down
    # the whole runner. Fall back to a per-process signal in that case.
    # pragma directives below: mutations on these lines turn the safety check
    # into a self-SIGTERM that kills mutmut/hammett along with the test.
    with contextlib.suppress(ProcessLookupError, PermissionError):  # pragma: no mutate
        target_pgid = os.getpgid(p.pid)  # pragma: no mutate
        if target_pgid == os.getpgid(0):  # pragma: no mutate
            p.send_signal(sig)  # pragma: no mutate
            return  # pragma: no mutate
        os.killpg(target_pgid, sig)  # pragma: no mutate


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
        _signal_process_tree(p, signal.SIGTERM)

    deadline = time.monotonic() + max(0, grace_sec)
    while time.monotonic() < deadline:
        if all(not _is_alive(p) for p in snapshot):
            return
        time.sleep(0.05)

    for p in snapshot:
        if not _is_alive(p):
            continue
        _signal_process_tree(p, signal.SIGKILL)


async def run_bash_execute(
    command: str,
    timeout: int = 30,
    working_dir: str = "/",
    max_output_bytes: int = config.BASH_MAX_OUTPUT_BYTES,
) -> dict:
    timeout = min(max(1, timeout), 600)
    max_output_bytes = min(max(1, max_output_bytes), config.BASH_MAX_OUTPUT_BYTES_HARD)

    with _tracer.start_as_current_span(
        "mymcp.bash.execute",
        attributes={"bash.timeout_sec": timeout},
    ) as span:
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=working_dir,
                start_new_session=True,  # pragma: no mutate
            )
        except FileNotFoundError:
            span.set_attribute("error.type", "FileNotFoundError")
            return {
                "success": False,
                "error": "FileNotFoundError",
                "message": f"Working directory not found: {working_dir}",
                "suggestion": "Check that the working_dir path exists",
            }
        except PermissionError as e:
            span.set_attribute("error.type", "PermissionError")
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
            except (asyncio.TimeoutError, TimeoutError):  # noqa: UP041
                _signal_process_tree(proc, signal.SIGTERM)
                try:
                    await asyncio.wait_for(proc.communicate(), timeout=2)
                except (asyncio.TimeoutError, TimeoutError):  # noqa: UP041
                    _signal_process_tree(proc, signal.SIGKILL)
                    await proc.communicate()
                span.set_attribute("bash.exit_code", -1)
                span.set_attribute("bash.timed_out", True)
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

        exit_code = proc.returncode
        truncated = (
            len(stdout_bytes) > max_output_bytes or len(stderr_bytes) > max_output_bytes
        )
        span.set_attribute("bash.exit_code", exit_code if exit_code is not None else -1)
        span.set_attribute("bash.timed_out", False)
        span.set_attribute("bash.stdout_bytes", len(stdout_bytes))
        span.set_attribute("bash.stderr_bytes", len(stderr_bytes))
        span.set_attribute("bash.output_truncated", truncated)

        return {
            "stdout": _truncate(stdout_bytes, max_output_bytes),
            "stderr": _truncate(stderr_bytes, max_output_bytes),
            "exit_code": exit_code,
            "timed_out": False,
        }


def _observe_inflight():
    with _inflight_lock:
        count = len(_inflight)
    return [Observation(count)]


register_callback_gauge(
    "mymcp.bash.inflight_processes",
    "Live count of tracked bash subprocesses",
    _observe_inflight,
)
