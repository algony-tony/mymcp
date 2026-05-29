"""server_overview MCP tool handler.

This is a pure function over an OverviewStore plus a bootstrap-scheduling
callback. The MCP dispatcher wires it up with a real supervisor.
"""

from collections.abc import Callable

from mymcp.recorder.overview import OverviewStore

_STUB_TEMPLATE = (
    "# Server Overview\n\n"
    "_⚠️ Overview not initialized. Bootstrap scheduled in the background._\n"
    "_Pending events accumulate in audit.log meanwhile._\n"
    "_Once bootstrapped, full changelog at: {changelog}_\n"
)


def server_overview_handler(
    *,
    store: OverviewStore,
    schedule_bootstrap: Callable[[], None],
    stale_seconds: float | None = None,
    last_error: str | None = None,
) -> str:
    overview = store.read_overview()
    if overview is None:
        schedule_bootstrap()
        return _STUB_TEMPLATE.format(changelog=str(store.changelog_path))
    if (stale_seconds is not None and stale_seconds > 0) or last_error:
        minutes = int((stale_seconds or 0) / 60) if stale_seconds else 0
        banner = f"_⚠️ overview is {minutes} minutes stale"
        if last_error:
            banner += f": {last_error}"
        banner += "_\n\n"
        return banner + overview
    return overview
