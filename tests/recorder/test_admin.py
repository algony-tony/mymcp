from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mymcp.recorder import admin as recorder_admin
from mymcp.recorder.bootstrap import Bootstrapper
from mymcp.recorder.events import EventTailer
from mymcp.recorder.llm.base import LLMResponse, Usage
from mymcp.recorder.merge_cycle import MergeCycle
from mymcp.recorder.overview import OverviewStore
from mymcp.recorder.task import RecorderSupervisor


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _make_supervisor(tmp_path):
    store = OverviewStore(tmp_path / "overview")
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    fake = AsyncMock()
    fake.call = AsyncMock(
        return_value=LLMResponse(
            text="x",
            tool_uses=[],
            stop_reason="end_turn",
            usage=Usage(input_tokens=1, output_tokens=1),
        )
    )
    bootstrapper = Bootstrapper(client=fake, store=store, max_iterations=5, token_budget=1000)
    merge_cycle = MergeCycle(
        client=fake,
        tailer=tailer,
        store=store,
        max_events_per_cycle=10,
        require_bootstrap=True,
    )
    return RecorderSupervisor(
        merge_cycle=merge_cycle,
        bootstrapper=bootstrapper,
        merge_interval_sec=60,
        provider="anthropic",
        model="m",
    )


def _make_app(supervisor, monkeypatch, tmp_path):
    """Build a minimal FastAPI app with auth + recorder admin router."""
    monkeypatch.setenv("MYMCP_TOKEN_FILE", str(tmp_path / "tokens.json"))
    monkeypatch.setenv("MYMCP_ADMIN_TOKEN", "test-admin-token")
    from mymcp import config

    config.reset_settings_cache()

    recorder_admin.set_supervisor(supervisor)
    app = FastAPI()
    app.include_router(recorder_admin.router)
    return app


@pytest.fixture(autouse=True)
def _reset_singletons():
    """Reset module-level singletons that other tests may have populated.

    auth._store is a process-wide singleton bound to whatever MYMCP_ADMIN_TOKEN
    was set at first get_store() — any test that seeded a different admin
    token earlier in the run would otherwise produce 403 here regardless of
    how monkeypatch.setenv is used in _make_app.
    """
    from mymcp import auth as _auth

    _auth._store = None
    recorder_admin.set_supervisor(None)
    yield
    _auth._store = None
    recorder_admin.set_supervisor(None)


def test_status_requires_admin_token(tmp_path, monkeypatch):
    sup = _make_supervisor(tmp_path)
    app = _make_app(sup, monkeypatch, tmp_path)
    client = TestClient(app)
    # no token → 401
    assert client.get("/admin/overview/status").status_code == 401
    # wrong token → 403
    assert (
        client.get("/admin/overview/status", headers={"Authorization": "Bearer wrong"}).status_code
        == 403
    )


def test_status_returns_shape(tmp_path, monkeypatch):
    sup = _make_supervisor(tmp_path)
    app = _make_app(sup, monkeypatch, tmp_path)
    client = TestClient(app)
    resp = client.get(
        "/admin/overview/status",
        headers={"Authorization": "Bearer test-admin-token"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert body["bootstrap_state"] == "idle"
    assert body["last_bootstrap_ts"] is None
    assert body["last_merge_ts"] is None
    assert body["last_error"] is None
    assert body["llm_provider"] == "anthropic"
    assert body["llm_model"] == "m"


def test_bootstrap_endpoint_schedules(tmp_path, monkeypatch):
    sup = _make_supervisor(tmp_path)
    app = _make_app(sup, monkeypatch, tmp_path)
    client = TestClient(app)
    resp = client.post(
        "/admin/overview/bootstrap",
        headers={"Authorization": "Bearer test-admin-token"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "state" in body
    assert "run_id" in body
    # state may be "idle" (just-scheduled) — main point: it accepted the request
    # and the supervisor's _force_bootstrap flag is now set
    assert sup._force_bootstrap is True


def test_bootstrap_endpoint_requires_admin(tmp_path, monkeypatch):
    sup = _make_supervisor(tmp_path)
    app = _make_app(sup, monkeypatch, tmp_path)
    client = TestClient(app)
    # no token → 401
    assert client.post("/admin/overview/bootstrap").status_code == 401
    # wrong token → 403
    assert (
        client.post(
            "/admin/overview/bootstrap", headers={"Authorization": "Bearer wrong"}
        ).status_code
        == 403
    )


def test_endpoints_503_when_recorder_disabled(tmp_path, monkeypatch):
    """When supervisor is None, return 503."""
    app = _make_app(None, monkeypatch, tmp_path)
    client = TestClient(app)
    headers = {"Authorization": "Bearer test-admin-token"}
    assert client.get("/admin/overview/status", headers=headers).status_code == 503
    assert client.post("/admin/overview/bootstrap", headers=headers).status_code == 503
