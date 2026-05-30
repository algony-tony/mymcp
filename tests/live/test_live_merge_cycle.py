"""Live merge cycle test: real LLM ingests one event, produces an overview update."""

import json

import pytest

from mymcp.recorder.events import EventTailer
from mymcp.recorder.merge_cycle import MergeCycle
from mymcp.recorder.overview import OverviewStore


@pytest.mark.live
@pytest.mark.anyio
async def test_one_merge_cycle_against_live_llm(tmp_path, live_client):
    """Drive a single merge cycle through a real LLM.

    Seed an existing overview so this is a true merge (not a bootstrap), then
    feed one bash_execute event and assert the new overview reflects it.
    """
    audit = tmp_path / "audit.log"
    audit.write_text(
        json.dumps(
            {
                "ts": "2026-05-29T10:00:00Z",
                "tool": "bash_execute",
                "result": "ok",
                "params": {"command": "apt install -y nginx"},
                "output": {
                    "stdout_head": "Setting up nginx (1.24.0)...",
                    "stdout_tail": "",
                    "stdout_truncated_bytes": 0,
                    "stdout_sha256": "abc",
                    "exit_code": 0,
                    "timed_out": False,
                },
            }
        )
        + "\n"
    )
    store = OverviewStore(tmp_path / "overview")
    store.write_overview(
        "# Server Overview\n\n## TL;DR\nFresh Ubuntu host.\n\n## Installed Services\n(none yet)\n"
    )
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    cycle = MergeCycle(client=live_client, tailer=tailer, store=store, max_events_per_cycle=10)

    result = await cycle.run_once()

    assert result.events_consumed == 1
    overview = store.read_overview() or ""
    # The model should mention nginx somewhere in the updated overview.
    assert "nginx" in overview.lower(), f"expected 'nginx' in overview, got: {overview[:500]}"
