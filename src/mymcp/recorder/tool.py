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
    pending_events: int = 0,
    last_merge_attempt_age_seconds: float | None = None,
    consecutive_failures: int = 0,
    last_error: str | None = None,
    circuit_open: bool = False,
    merge_interval_sec: float = 300.0,
) -> str:
    """Return the curated overview text, optionally prefixed by a status banner.

    The banner is composed from runtime status — see ``_build_banner`` for the
    priority rules. Idle systems (no backlog) get no banner at all.
    """
    overview = store.read_overview()
    if overview is None:
        schedule_bootstrap()
        return _STUB_TEMPLATE.format(changelog=str(store.changelog_path))

    banner = _build_banner(
        pending_events=pending_events,
        last_merge_attempt_age_seconds=last_merge_attempt_age_seconds,
        consecutive_failures=consecutive_failures,
        last_error=last_error,
        circuit_open=circuit_open,
        merge_interval_sec=merge_interval_sec,
    )
    if banner:
        return banner + overview
    return overview


def _build_banner(
    *,
    pending_events: int,
    last_merge_attempt_age_seconds: float | None,
    consecutive_failures: int,
    last_error: str | None,
    circuit_open: bool,
    merge_interval_sec: float = 300.0,
) -> str:
    """Backlog-driven status banner. Idle == no banner.

    Priority:
      1. circuit_open  — breaker tripped; recovery on next event.
      2. backlog stalled — pending > 0 AND attempt_age > 2 * interval.
      3. backlog + recent failure — pending > 0 AND consecutive_failures > 0.
      4. idle / healthy — empty string (no banner). Idle is normal, not
         failure — the prior 'X minutes stale' design treated quiet servers
         as broken.
    """
    # Priority 1: breaker open. Recovery is event-driven now — a new event
    # arriving past the high-water mark triggers a single retry.
    if circuit_open:
        msg = (
            f"_🛑 recorder circuit breaker open after {consecutive_failures}"
            f" consecutive failures; waiting for next event to retry_"
        )
        if last_error:
            msg = msg.rstrip("_") + f" (last error: {last_error})_"
        return msg + "\n\n"

    # Priority 2: real staleness — there IS work and attempts have stalled.
    # Threshold is 2x the merge interval so a single slow cycle isn't flagged.
    stale_threshold = 2.0 * merge_interval_sec
    if (
        pending_events > 0
        and last_merge_attempt_age_seconds is not None
        and last_merge_attempt_age_seconds > stale_threshold
    ):
        minutes = int(last_merge_attempt_age_seconds / 60)
        msg = f"_⚠️ {pending_events} events pending; merge stalled for {minutes} minutes_"
        if last_error:
            msg = msg.rstrip("_") + f": {last_error}_"
        return msg + "\n\n"

    # Priority 3: backlog with a recent failure (not yet stale).
    if pending_events > 0 and consecutive_failures > 0 and last_error:
        return f"_⚠️ last merge failed: {last_error} — will retry on next event_\n\n"

    # Idle (pending==0) or healthy — no banner.
    return ""
