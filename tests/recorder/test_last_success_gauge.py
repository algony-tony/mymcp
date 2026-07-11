"""Tests for the merge_last_success_timestamp observable gauge callback.

OpenTelemetry observable gauges bind their callbacks to the meter that existed
at registration time and do not rebind when the global MeterProvider is
replaced; that makes it impractical to assert real-meter values per test.
We instead test the callback directly: given a supervisor in state X, does it
emit the right Observation? The wiring is exercised separately by
`tests/recorder/test_metrics.py::test_recorder_instruments_registered` plus
the actual /metrics scrape test below.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


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


@pytest.fixture
def client(monkeypatch, tmp_path):
    """Real /metrics wire test — verifies gauge name appears in scrape output."""
    monkeypatch.setenv("MYMCP_TOKEN_FILE", str(tmp_path / "tokens.json"))
    monkeypatch.setenv("MYMCP_AUDIT_LOG_DIR", str(tmp_path / "audit"))
    monkeypatch.setenv("MYMCP_METRICS_TOKEN", "test-metrics-token")
    from mymcp.config import reset_settings_cache

    reset_settings_cache()
    from mymcp.server import create_app

    return TestClient(create_app())


def test_gauge_appears_in_prometheus_scrape(client):
    # Importing wiring registers the callback gauges against the live meter.
    import mymcp.recorder.wiring  # noqa: F401

    body = client.get("/metrics", headers={"Authorization": "Bearer test-metrics-token"}).text
    assert "mymcp_recorder_merge_last_success_timestamp" in body


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


def test_last_attempt_gauge_appears_in_prometheus_scrape(client):
    import mymcp.recorder.wiring  # noqa: F401

    body = client.get("/metrics", headers={"Authorization": "Bearer test-metrics-token"}).text
    assert "mymcp_recorder_merge_last_attempt_timestamp" in body
