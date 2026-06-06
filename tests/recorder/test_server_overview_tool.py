from mymcp.recorder.overview import OverviewStore
from mymcp.recorder.tool import _build_banner, server_overview_handler


def test_returns_stub_when_missing(tmp_path):
    store = OverviewStore(tmp_path)
    scheduled: list[bool] = []
    result = server_overview_handler(
        store=store,
        schedule_bootstrap=lambda: scheduled.append(True),
    )
    assert "not initialized" in result.lower()
    assert str(store.changelog_path) in result
    assert scheduled == [True]


def test_returns_overview_when_present(tmp_path):
    store = OverviewStore(tmp_path)
    store.write_overview("# Server Overview\n\n## TL;DR\nGreat machine.\n")
    result = server_overview_handler(
        store=store,
        schedule_bootstrap=lambda: None,
    )
    assert "Great machine" in result
    assert "stalled" not in result.lower()
    assert "circuit" not in result.lower()


def test_no_banner_when_idle_and_healthy():
    """pending_events == 0 → no banner. Idle is normal, not failure.

    This is the key change vs the old time-based 'stale' banner: a quiet
    server with no audit events sitting around should NOT report itself
    as stalled — there's nothing to be stalled on.
    """
    banner = _build_banner(
        pending_events=0,
        last_merge_attempt_age_seconds=10000.0,
        consecutive_failures=0,
        last_error=None,
        circuit_open=False,
        merge_interval_sec=300.0,
    )
    assert banner == ""


def test_circuit_open_priority_1():
    banner = _build_banner(
        pending_events=12,
        last_merge_attempt_age_seconds=120.0,
        consecutive_failures=5,
        last_error="HTTP 429",
        circuit_open=True,
        merge_interval_sec=300.0,
    )
    assert "circuit breaker" in banner.lower()
    assert "429" in banner
    assert "waiting for next event" in banner.lower()


def test_stale_banner_when_backlog_and_stalled():
    """Backlog AND attempt-age > 2*interval → 'X events pending; stalled Y min'."""
    banner = _build_banner(
        pending_events=7,
        last_merge_attempt_age_seconds=1800.0,  # 30min > 2*5min = 10min
        consecutive_failures=0,
        last_error=None,
        circuit_open=False,
        merge_interval_sec=300.0,
    )
    assert "7 events pending" in banner
    assert "stalled" in banner.lower()


def test_no_stale_banner_when_backlog_but_attempt_recent():
    """Backlog but recent attempt → no banner; merge is keeping up."""
    banner = _build_banner(
        pending_events=3,
        last_merge_attempt_age_seconds=30.0,  # well inside 2*interval
        consecutive_failures=0,
        last_error=None,
        circuit_open=False,
        merge_interval_sec=300.0,
    )
    assert banner == ""


def test_recent_failure_banner_with_backlog():
    """Recent failure (not yet stale) → soft 'will retry on next event'."""
    banner = _build_banner(
        pending_events=2,
        last_merge_attempt_age_seconds=60.0,
        consecutive_failures=1,
        last_error="timeout",
        circuit_open=False,
        merge_interval_sec=300.0,
    )
    assert "last merge failed" in banner.lower()
    assert "timeout" in banner
    assert "retry on next event" in banner.lower()


def test_handler_threads_status_into_banner(tmp_path):
    """End-to-end: server_overview_handler forwards status fields to banner."""
    store = OverviewStore(tmp_path)
    store.write_overview("# Server Overview\nbody\n")
    result = server_overview_handler(
        store=store,
        schedule_bootstrap=lambda: None,
        pending_events=5,
        last_merge_attempt_age_seconds=1800.0,
        consecutive_failures=0,
        last_error=None,
        circuit_open=False,
        merge_interval_sec=300.0,
    )
    # Stale banner appears, then the original overview follows.
    assert "stalled" in result.lower()
    assert "5 events pending" in result
    assert "body" in result


def test_circuit_open_priority_over_stale(tmp_path):
    """When circuit_open the staleness fields are irrelevant — circuit takes priority."""
    store = OverviewStore(tmp_path)
    store.write_overview("# Server Overview\nbody\n")
    result = server_overview_handler(
        store=store,
        schedule_bootstrap=lambda: None,
        pending_events=5,
        last_merge_attempt_age_seconds=3600.0,
        consecutive_failures=5,
        last_error="boom",
        circuit_open=True,
        merge_interval_sec=300.0,
    )
    assert "circuit" in result.lower()
    assert "body" in result


def test_stub_does_not_schedule_when_callback_is_noop(tmp_path):
    """Defensive: handler still returns the stub even if scheduler is a noop."""
    store = OverviewStore(tmp_path)
    result = server_overview_handler(store=store, schedule_bootstrap=lambda: None)
    assert "not initialized" in result.lower()
