import pytest
from fastapi.testclient import TestClient

from mymcp.observability.request_id import current_request_id


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
