"""End-to-end test against a real uvicorn server.

The rest of the suite drives the FastAPI app through ASGITransport, which
skips uvicorn's lifespan handling, the real socket layer, /metrics's
prometheus exposition, and OS-level signal handling. This file spins up
ONE real server on an ephemeral port and exercises those gaps end-to-end
with a real HTTP client.

Note: ``StreamableHTTPSessionManager`` (mymcp.mcp_server.session_manager)
is a module-level singleton whose ``.run()`` can only be called once per
instance. So we run one server and bundle every check into it — running
multiple e2e tests in the same process would error on the second startup.
"""

from __future__ import annotations

import asyncio
import socket

import httpx
import pytest
import uvicorn


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _wait_started(server: uvicorn.Server, timeout: float = 5.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while not server.started:
        if asyncio.get_event_loop().time() > deadline:
            raise TimeoutError("uvicorn did not start within deadline")
        await asyncio.sleep(0.05)


@pytest.mark.anyio
async def test_real_uvicorn_serves_health_metrics_and_shuts_down(monkeypatch, tmp_path):
    """One real server. /health works. /metrics enforces the bearer token and
    emits exposition. SIGTERM-equivalent shutdown completes inside the grace
    window without leaking the asyncio task.

    This is the only test in this file because the project's session_manager
    is a process-wide singleton — a second uvicorn start in the same process
    fails with 'can only be called once per instance'.
    """
    port = _free_port()
    monkeypatch.setenv("MYMCP_HOST", "127.0.0.1")
    monkeypatch.setenv("MYMCP_PORT", str(port))
    monkeypatch.setenv("MYMCP_TOKEN_FILE", str(tmp_path / "tokens.json"))
    monkeypatch.setenv("MYMCP_AUDIT_LOG_DIR", str(tmp_path / "audit"))
    monkeypatch.setenv("MYMCP_ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("MYMCP_METRICS_TOKEN", "test-metrics-token")
    monkeypatch.setenv("MYMCP_SHUTDOWN_GRACE_SEC", "2")

    # Reset auth singleton — a prior test may have bound it to a different
    # admin token.
    from mymcp import auth as _auth

    _auth._store = None

    from mymcp.config import reset_settings_cache

    reset_settings_cache()
    from mymcp.server import create_app

    config = uvicorn.Config(
        create_app(),
        host="127.0.0.1",
        port=port,
        log_level="warning",
        lifespan="on",
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())

    try:
        await _wait_started(server)

        async with httpx.AsyncClient(timeout=5.0) as client:
            # /health responds with version
            r = await client.get(f"http://127.0.0.1:{port}/health")
            assert r.status_code == 200
            body = r.json()
            assert body["status"] == "ok"
            assert "version" in body

            # /metrics requires the bearer token
            r = await client.get(f"http://127.0.0.1:{port}/metrics")
            assert r.status_code == 401

            # /metrics emits prometheus exposition when authorised
            r = await client.get(
                f"http://127.0.0.1:{port}/metrics",
                headers={"Authorization": "Bearer test-metrics-token"},
            )
            assert r.status_code == 200
            text = r.text
            # Either our own series or the python_client defaults; both
            # mean the exposition layer is wired correctly under uvicorn.
            assert "mymcp_" in text or "python_info" in text
    finally:
        # SIGTERM-equivalent: should_exit triggers uvicorn's graceful shutdown.
        # Must complete within the grace window — a hang here means lifespan
        # teardown is stuck.
        server.should_exit = True
        await asyncio.wait_for(task, timeout=5.0)
        _auth._store = None
