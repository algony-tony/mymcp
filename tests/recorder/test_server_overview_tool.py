from mymcp.recorder.overview import OverviewStore
from mymcp.recorder.tool import server_overview_handler


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
    assert "stale" not in result.lower()


def test_prepends_stale_banner(tmp_path):
    store = OverviewStore(tmp_path)
    store.write_overview("# Server Overview\n")
    result = server_overview_handler(
        store=store,
        schedule_bootstrap=lambda: None,
        stale_seconds=1800,
        last_error="rate-limited",
    )
    assert "stale" in result.lower()
    assert "rate-limited" in result
    # banner is prepended, then original overview follows
    assert result.endswith("# Server Overview\n")


def test_stale_banner_omitted_when_no_age_or_error(tmp_path):
    store = OverviewStore(tmp_path)
    store.write_overview("# X\n")
    result = server_overview_handler(
        store=store,
        schedule_bootstrap=lambda: None,
        stale_seconds=None,
        last_error=None,
    )
    assert "stale" not in result.lower()


def test_stub_does_not_schedule_when_callback_is_noop(tmp_path):
    """Defensive: handler still returns the stub even if scheduler is a noop."""
    store = OverviewStore(tmp_path)
    result = server_overview_handler(store=store, schedule_bootstrap=lambda: None)
    assert "not initialized" in result.lower()
