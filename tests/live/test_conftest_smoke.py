"""Smoke test: live marker is detected and config loads (or skips cleanly)."""

import os

import pytest


@pytest.mark.live
def test_live_marker_loads_env():
    assert "MYMCP_RECORDER_LIVE_TEST_API_KEY" in os.environ
    assert os.environ["MYMCP_RECORDER_LIVE_TEST_API_KEY"]
