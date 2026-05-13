import pytest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

from mymcp.observability.request_id import RequestIdMiddleware, current_request_id


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("MYMCP_TOKEN_FILE", str(tmp_path / "tokens.json"))
    monkeypatch.setenv("MYMCP_AUDIT_LOG_DIR", str(tmp_path / "audit"))
    from mymcp.config import reset_settings_cache

    reset_settings_cache()
    from mymcp.server import create_app

    return TestClient(create_app())


def test_request_id_generated_when_absent(client):
    response = client.get("/health")
    assert response.status_code == 200
    rid = response.headers.get("x-request-id")
    assert rid
    # UUID4 form: 8-4-4-4-12 hex
    assert len(rid) == 36 and rid.count("-") == 4


def test_request_id_preserved_when_present(client):
    response = client.get("/health", headers={"X-Request-ID": "abc-123-xyz"})
    assert response.headers["x-request-id"] == "abc-123-xyz"


def test_contextvar_unset_outside_request():
    assert current_request_id.get() is None


def test_contextvar_visible_to_route_handler():
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    @app.get("/echo-rid")
    def echo_rid():
        return PlainTextResponse(current_request_id.get() or "")

    test_client = TestClient(app)
    response = test_client.get("/echo-rid", headers={"X-Request-ID": "known-id-42"})
    assert response.status_code == 200
    assert response.text == "known-id-42"
    assert response.headers["x-request-id"] == "known-id-42"


def test_malicious_request_id_is_replaced():
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    @app.get("/ping")
    def ping():
        return PlainTextResponse("ok")

    captured: dict = {}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            captured["headers"] = message["headers"]

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/ping",
        "raw_path": b"/ping",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"x-request-id", b"foo\r\nInjected: yes")],
        "server": ("testserver", 80),
        "client": ("testclient", 12345),
    }

    import asyncio

    asyncio.run(app(scope, receive, send))

    headers = captured["headers"]
    rid_values = [v.decode("ascii") for k, v in headers if k.lower() == b"x-request-id"]
    assert len(rid_values) == 1
    rid = rid_values[0]
    # Should be a fresh UUID, not the malicious value
    assert rid != "foo\r\nInjected: yes"
    assert "foo" not in rid
    assert "Injected" not in rid
    assert len(rid) == 36 and rid.count("-") == 4
    # No injected header should appear
    assert not any(k.lower() == b"injected" for k, _ in headers)
