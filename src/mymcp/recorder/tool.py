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
    circuit_open: bool = False,
) -> str:
    overview = store.read_overview()
    if overview is None:
        schedule_bootstrap()
        return _STUB_TEMPLATE.format(changelog=str(store.changelog_path))
    banner = _build_banner(
        stale_seconds=stale_seconds, last_error=last_error, circuit_open=circuit_open
    )
    if banner:
        return banner + overview
    return overview


def _build_banner(
    *, stale_seconds: float | None, last_error: str | None, circuit_open: bool
) -> str:
    if circuit_open:
        msg = "⛔ recorder paused after repeated merge failures; restart service to retry"
        if last_error:
            msg += f". Last error: {last_error}"
        return f"_{msg}_\n\n"
    if stale_seconds is not None and stale_seconds > 0:
        minutes = int(stale_seconds / 60)
        msg = f"⚠️ overview is {minutes} minutes stale"
        if last_error:
            msg += f": {last_error}"
        return f"_{msg}_\n\n"
    if last_error:
        return f"_⚠️ last merge cycle failed: {last_error}_\n\n"
    return ""
