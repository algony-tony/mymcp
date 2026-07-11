"""Assemble a configured RecorderSupervisor from settings."""

from pathlib import Path

from opentelemetry.metrics import Observation

from mymcp.config import Settings
from mymcp.observability.instruments import register_callback_gauge
from mymcp.recorder.bootstrap import Bootstrapper
from mymcp.recorder.events import EventTailer
from mymcp.recorder.llm.base import LLMClient
from mymcp.recorder.llm.factory import build_llm_client
from mymcp.recorder.merge_cycle import MergeCycle
from mymcp.recorder.overview import OverviewStore
from mymcp.recorder.task import RecorderSupervisor
from mymcp.tools.files import register_protected_path

# The active supervisor for this process. Set by build_supervisor so the
# Prometheus gauge callbacks below can report its state without importing the
# Python core (mcp_server). The in-process core and the standalone
# mymcp-recorder sidecar both go through build_supervisor.
_active_supervisor: "RecorderSupervisor | None" = None


def set_active_supervisor(sup: "RecorderSupervisor | None") -> None:
    global _active_supervisor
    _active_supervisor = sup


def build_supervisor(settings: Settings) -> RecorderSupervisor:
    data_dir = Path(settings.recorder_data_dir)
    overview_dir = data_dir / "overview"
    cursor_path = data_dir / "cursor.json"

    # The overview directory is mymcp-owned; external file tools may READ it
    # (so external LLMs can fetch changelog.md) but not WRITE to it.
    register_protected_path(str(overview_dir), modes={"write"})

    client: LLMClient = build_llm_client(
        provider=settings.recorder_llm_provider,
        api_key=settings.recorder_llm_api_key,
        model=settings.recorder_llm_model,
        base_url=settings.recorder_llm_base_url,
    )
    store = OverviewStore(overview_dir)
    tailer = EventTailer(log_dir=Path(settings.audit_log_dir), cursor_path=cursor_path)
    bootstrapper = Bootstrapper(
        client=client,
        store=store,
        max_iterations=settings.recorder_bootstrap_max_iterations,
        token_budget=settings.recorder_bootstrap_token_budget,
        probe_timeout_sec=settings.recorder_bootstrap_probe_timeout_sec,
        max_tokens=settings.recorder_llm_max_tokens,
    )
    merge = MergeCycle(
        client=client,
        tailer=tailer,
        store=store,
        max_events_per_cycle=settings.recorder_max_events_per_cycle,
        require_bootstrap=True,
        max_tokens=settings.recorder_llm_max_tokens,
    )
    supervisor = RecorderSupervisor(
        merge_cycle=merge,
        bootstrapper=bootstrapper,
        merge_interval_sec=settings.recorder_merge_interval_sec,
        provider=settings.recorder_llm_provider,
        model=settings.recorder_llm_model,
        circuit_breaker_threshold=settings.recorder_circuit_breaker_threshold,
        llm_client=client,
    )
    set_active_supervisor(supervisor)
    return supervisor


def _observe_circuit_open() -> list[Observation]:
    sup = _active_supervisor
    if sup is None:
        return [Observation(0)]
    return [Observation(1 if getattr(sup, "circuit_open", False) else 0)]


def _observe_pending_events() -> list[Observation]:
    """Number of mutating audit events queued past the recorder's cursor.

    A growing value means the recorder is falling behind (LLM failing, circuit
    open, slow merges). Useful as both a saturation gauge and an alert source.
    """
    sup = _active_supervisor
    if sup is None:
        return [Observation(0)]
    try:
        merge = getattr(sup, "_merge_cycle", None)
        tailer = getattr(merge, "_tailer", None)
        n = int(tailer.pending_count()) if tailer is not None else 0
    except Exception:
        n = 0
    return [Observation(n)]


def _observe_last_success_ts() -> list[Observation]:
    """Unix seconds of the last successful merge cycle; 0 if never.

    Informational only — kept for historical health trending. The canonical
    staleness signal is now the composite of pending_events and
    last_attempt_timestamp (see _observe_last_attempt_ts).
    """
    sup = _active_supervisor
    if sup is None:
        return [Observation(0)]
    ts = sup.last_merge_ts_unix
    return [Observation(ts if ts is not None else 0)]


def _observe_last_attempt_ts() -> list[Observation]:
    """Unix seconds of the last merge attempt (success OR failure); 0 if never.

    Differs from last_success_timestamp: this advances even when the LLM
    failed, but does NOT advance on idle ticks (pending_count == 0). Together
    with pending_events it is the canonical 'recorder is stuck' signal:

        Recommended composite (project ships no alert rules):
          ( mymcp_recorder_pending_events > 0
            AND time() - mymcp_recorder_merge_last_attempt_timestamp > 1800 )
          OR mymcp_recorder_circuit_open == 1
    """
    sup = _active_supervisor
    if sup is None:
        return [Observation(0)]
    ts = sup.last_merge_attempt_ts_unix
    return [Observation(ts if ts is not None else 0)]


register_callback_gauge(
    "mymcp.recorder.circuit_open",
    "1 when the recorder's merge-failure circuit breaker has tripped, else 0",
    _observe_circuit_open,
)
register_callback_gauge(
    "mymcp.recorder.merge.last_success_timestamp",
    "Unix seconds of the last successful recorder merge cycle; 0 if never (informational)",
    _observe_last_success_ts,
)
register_callback_gauge(
    "mymcp.recorder.merge.last_attempt_timestamp",
    "Unix seconds of the last recorder merge attempt (success or failure); 0 if never",
    _observe_last_attempt_ts,
)
register_callback_gauge(
    "mymcp.recorder.pending_events",
    "Mutating audit events queued past the recorder's cursor (backlog size)",
    _observe_pending_events,
)
