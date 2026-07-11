import os

import httpx
import pytest

BASE_URL = os.environ.get("MYMCP_COMPAT_URL", "http://127.0.0.1:8765")


def test_metrics_requires_token(metrics_token):
    r = httpx.get(f"{BASE_URL}/metrics")
    assert r.status_code == 401


def test_metrics_with_token_exposes_mymcp_names(metrics_token):
    r = httpx.get(f"{BASE_URL}/metrics", headers={"Authorization": f"Bearer {metrics_token}"})
    assert r.status_code == 200
    body = r.text
    assert "mymcp_tool_calls_total" in body
    assert "mymcp_http_requests_total" in body


@pytest.mark.anyio
async def test_tool_call_increments_counter(rw, metrics_token, scratch):
    await rw.call("write_file", {"file_path": os.path.join(scratch, "m.txt"), "content": "x"})
    r = httpx.get(f"{BASE_URL}/metrics", headers={"Authorization": f"Bearer {metrics_token}"})
    assert 'tool="write_file"' in r.text
