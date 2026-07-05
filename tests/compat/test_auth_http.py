import os

import httpx
import pytest

BASE_URL = os.environ.get("MYMCP_COMPAT_URL", "http://127.0.0.1:8765")


def test_mcp_requires_bearer():
    r = httpx.post(f"{BASE_URL}/mcp", json={})
    assert r.status_code == 401
    assert r.json() == {"detail": "Missing Bearer token"}


def test_mcp_rejects_bad_token():
    r = httpx.post(f"{BASE_URL}/mcp", json={}, headers={"Authorization": "Bearer tok_bogus"})
    assert r.status_code == 401
    assert r.json() == {"detail": "Invalid or disabled token"}


def test_health_and_version_unauthenticated():
    h = httpx.get(f"{BASE_URL}/health").json()
    assert h["status"] == "ok" and h["version"]
    v = httpx.get(f"{BASE_URL}/version").json()
    assert v["version"] == h["version"]


@pytest.mark.anyio
async def test_unknown_tool_permission_denied(rw):
    res = await rw.call("no_such_tool", {})
    assert res == {
        "success": False,
        "error": "PermissionDenied",
        "message": "Unknown tool: no_such_tool",
    }
