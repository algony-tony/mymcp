"""SIGTERM to the parent must propagate to in-flight bash subprocesses."""
import os
import signal
import subprocess
import sys
import time

import pytest


pytestmark = pytest.mark.skipif(
    sys.platform != "linux", reason="signal/process group test is Linux-only",
)


def test_shutdown_inflight_processes_kills_running_child():
    """Unit test: directly call the cleanup function with a known child."""
    from mymcp.tools.bash import _track_process, shutdown_inflight_processes

    p = subprocess.Popen(
        ["sleep", "30"],
        start_new_session=True,
    )
    try:
        _track_process(p)
        assert p.poll() is None

        shutdown_inflight_processes(grace_sec=2)

        for _ in range(30):
            if p.poll() is not None:
                break
            time.sleep(0.1)
        assert p.poll() is not None, "child still alive after shutdown_inflight_processes"
    finally:
        if p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            p.wait(timeout=2)


def test_shutdown_inflight_processes_handles_already_exited():
    """Cleanup must not raise if the child has already exited."""
    from mymcp.tools.bash import _track_process, shutdown_inflight_processes

    p = subprocess.Popen(["true"], start_new_session=True)
    p.wait(timeout=5)
    _track_process(p)

    shutdown_inflight_processes(grace_sec=1)
