"""Tests for the merge_last_success_timestamp observable gauge callback.

OpenTelemetry observable gauges bind their callbacks to the meter that existed
at registration time and do not rebind when the global MeterProvider is
replaced; that makes it impractical to assert real-meter values per test.
We instead test the callback directly: given a supervisor in state X, does it
emit the right Observation? The wiring is exercised separately by
`tests/recorder/test_metrics.py::test_recorder_instruments_registered`.

(v3: the live `/metrics` scrape assertions were dropped with the Python server —
the recorder sidecar has no HTTP endpoint of its own, and the server's `/metrics`
is now the Go core's, covered by go/ tests.)
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock


def _make_supervisor():
    from mymcp.recorder.task import RecorderSupervisor

    return RecorderSupervisor(
        merge_cycle=MagicMock(),
        bootstrapper=MagicMock(),
        merge_interval_sec=300.0,
        provider="anthropic",
        model="claude-test",
    )


def test_observe_callback_returns_zero_when_no_supervisor():
    from mymcp.recorder.wiring import _observe_last_success_ts, set_active_supervisor

    set_active_supervisor(None)
    obs = list(_observe_last_success_ts())
    assert len(obs) == 1
    assert obs[0].value == 0


def test_observe_callback_returns_zero_before_first_success():
    from mymcp.recorder.wiring import _observe_last_success_ts, set_active_supervisor

    sup = _make_supervisor()
    set_active_supervisor(sup)
    try:
        obs = list(_observe_last_success_ts())
        assert obs[0].value == 0
    finally:
        set_active_supervisor(None)


def test_observe_callback_returns_unix_timestamp_after_success():
    from mymcp.recorder.wiring import _observe_last_success_ts, set_active_supervisor

    sup = _make_supervisor()
    set_active_supervisor(sup)
    try:
        before = time.time()
        sup._last_merge_ts = before
        obs = list(_observe_last_success_ts())
        after = time.time()
        assert before <= obs[0].value <= after
    finally:
        set_active_supervisor(None)


# ---------------------------------------------------------------------------
# last_attempt_timestamp gauge — companion to last_success_timestamp.
# attempt advances on success OR failure (but not on idle); together with
# pending_events it is the canonical 'stuck' signal.
# ---------------------------------------------------------------------------


def test_last_attempt_callback_returns_zero_when_no_supervisor():
    from mymcp.recorder.wiring import _observe_last_attempt_ts, set_active_supervisor

    set_active_supervisor(None)
    obs = list(_observe_last_attempt_ts())
    assert len(obs) == 1
    assert obs[0].value == 0


def test_last_attempt_callback_returns_zero_before_first_attempt():
    from mymcp.recorder.wiring import _observe_last_attempt_ts, set_active_supervisor

    sup = _make_supervisor()
    set_active_supervisor(sup)
    try:
        obs = list(_observe_last_attempt_ts())
        assert obs[0].value == 0
    finally:
        set_active_supervisor(None)


def test_last_attempt_callback_returns_unix_timestamp():
    from mymcp.recorder.wiring import _observe_last_attempt_ts, set_active_supervisor

    sup = _make_supervisor()
    set_active_supervisor(sup)
    try:
        before = time.time()
        sup._last_merge_attempt_ts = before
        obs = list(_observe_last_attempt_ts())
        after = time.time()
        assert before <= obs[0].value <= after
    finally:
        set_active_supervisor(None)
