"""Assemble a configured RecorderSupervisor from settings."""

from pathlib import Path
from typing import Any

from opentelemetry.metrics import Observation

from mymcp.config import Settings
from mymcp.observability.instruments import register_callback_gauge
from mymcp.recorder.bootstrap import Bootstrapper
from mymcp.recorder.events import EventTailer
from mymcp.recorder.llm.factory import build_llm_client
from mymcp.recorder.merge_cycle import MergeCycle
from mymcp.recorder.overview import OverviewStore
from mymcp.recorder.task import RecorderSupervisor
from mymcp.tools.files import register_protected_path


def build_supervisor(settings: Settings) -> RecorderSupervisor:
    data_dir = Path(settings.recorder_data_dir)
    overview_dir = data_dir / "overview"
    cursor_path = data_dir / "cursor.json"

    # The overview directory is mymcp-owned; external file tools may READ it
    # (so external LLMs can fetch changelog.md) but not WRITE to it.
    register_protected_path(str(overview_dir), modes={"write"})

    client: Any = build_llm_client(
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
    return RecorderSupervisor(
        merge_cycle=merge,
        bootstrapper=bootstrapper,
        merge_interval_sec=settings.recorder_merge_interval_sec,
        provider=settings.recorder_llm_provider,
        model=settings.recorder_llm_model,
        circuit_breaker_threshold=settings.recorder_circuit_breaker_threshold,
    )


def _observe_circuit_open() -> list[Observation]:
    # Late import to avoid circular wiring at module import time.
    from mymcp.mcp_server import get_recorder_supervisor

    sup = get_recorder_supervisor()
    if sup is None:
        return [Observation(0)]
    return [Observation(1 if getattr(sup, "circuit_open", False) else 0)]


register_callback_gauge(
    "mymcp.recorder.circuit_open",
    "1 when the recorder's merge-failure circuit breaker has tripped, else 0",
    _observe_circuit_open,
)
