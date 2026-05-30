"""Live bootstrap smoke test: small caps to keep cost down."""

import pytest

from mymcp.recorder.bootstrap import Bootstrapper, BootstrapState
from mymcp.recorder.overview import OverviewStore


@pytest.mark.live
@pytest.mark.anyio
async def test_tiny_bootstrap_against_live_llm(tmp_path, live_client):
    """Run a tiny bootstrap. Either succeeds with an overview or fails on caps.

    Both outcomes are acceptable — this verifies the loop is well-formed end-
    to-end against a real LLM. We do NOT assert success because a tiny budget
    may not be enough to finish on a real host.
    """
    store = OverviewStore(tmp_path / "overview")
    b = Bootstrapper(
        client=live_client,
        store=store,
        max_iterations=10,
        token_budget=200_000,
        probe_timeout_sec=10,
    )
    result = await b.run_once()
    assert result.state in {BootstrapState.SUCCEEDED, BootstrapState.FAILED}
    if result.state == BootstrapState.SUCCEEDED:
        overview = store.read_overview() or ""
        assert overview.startswith("#")
