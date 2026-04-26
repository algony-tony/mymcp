import importlib

import pytest


def _reload_config(monkeypatch, env: dict[str, str]):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import mymcp.config
    importlib.reload(mymcp.config)
    return mymcp.config


def test_settings_reads_mymcp_prefixed_vars(monkeypatch, tmp_path):
    monkeypatch.delenv("MYMCP_ENV_FILE", raising=False)
    cfg = _reload_config(monkeypatch, {
        "MYMCP_HOST": "127.0.0.1",
        "MYMCP_PORT": "9000",
        "MYMCP_ADMIN_TOKEN": "tok_abc",
        "MYMCP_AUDIT_ENABLED": "true",
        "MYMCP_AUDIT_LOG_DIR": str(tmp_path),
    })
    s = cfg.get_settings()
    assert s.host == "127.0.0.1"
    assert s.port == 9000
    assert s.admin_token == "tok_abc"
    assert s.audit_enabled is True
    assert s.audit_log_dir == str(tmp_path)


def test_settings_ignores_unprefixed_mcp_vars(monkeypatch):
    """Hard rename: legacy MCP_* must NOT be honored."""
    monkeypatch.delenv("MYMCP_ENV_FILE", raising=False)
    monkeypatch.delenv("MYMCP_HOST", raising=False)
    monkeypatch.setenv("MCP_HOST", "10.0.0.1")
    cfg = _reload_config(monkeypatch, {})
    s = cfg.get_settings()
    assert s.host != "10.0.0.1"
    assert s.host == "0.0.0.0"


def test_settings_metrics_token_empty_means_disabled(monkeypatch):
    monkeypatch.delenv("MYMCP_ENV_FILE", raising=False)
    monkeypatch.delenv("MYMCP_METRICS_TOKEN", raising=False)
    cfg = _reload_config(monkeypatch, {})
    s = cfg.get_settings()
    assert s.metrics_token == ""


def test_settings_protected_paths_includes_log_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("MYMCP_ENV_FILE", raising=False)
    log_dir = tmp_path / "audit"
    log_dir.mkdir()
    cfg = _reload_config(monkeypatch, {
        "MYMCP_AUDIT_LOG_DIR": str(log_dir),
    })
    paths = cfg.get_protected_paths()
    assert str(log_dir) in paths


def test_settings_extra_protected_paths(monkeypatch):
    monkeypatch.delenv("MYMCP_ENV_FILE", raising=False)
    cfg = _reload_config(monkeypatch, {
        "MYMCP_PROTECTED_PATHS": "/extra/one,/extra/two",
    })
    paths = cfg.get_protected_paths()
    assert "/extra/one" in paths
    assert "/extra/two" in paths
