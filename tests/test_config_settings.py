import importlib


def _reload_config(monkeypatch, env: dict[str, str]):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import mymcp.config

    importlib.reload(mymcp.config)
    return mymcp.config


def test_settings_reads_mymcp_prefixed_vars(monkeypatch, tmp_path):
    monkeypatch.delenv("MYMCP_ENV_FILE", raising=False)
    cfg = _reload_config(
        monkeypatch,
        {
            "MYMCP_HOST": "127.0.0.1",
            "MYMCP_PORT": "9000",
            "MYMCP_ADMIN_TOKEN": "tok_abc",
            "MYMCP_AUDIT_ENABLED": "true",
            "MYMCP_AUDIT_LOG_DIR": str(tmp_path),
        },
    )
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
    cfg = _reload_config(
        monkeypatch,
        {
            "MYMCP_AUDIT_LOG_DIR": str(log_dir),
        },
    )
    paths = cfg.get_protected_paths()
    assert str(log_dir) in paths


def test_settings_extra_protected_paths(monkeypatch):
    monkeypatch.delenv("MYMCP_ENV_FILE", raising=False)
    cfg = _reload_config(
        monkeypatch,
        {
            "MYMCP_PROTECTED_PATHS": "/extra/one,/extra/two",
        },
    )
    paths = cfg.get_protected_paths()
    assert "/extra/one" in paths
    assert "/extra/two" in paths


def test_transfer_settings_defaults(monkeypatch):
    for var in (
        "MYMCP_TRANSFER_ENABLED",
        "MYMCP_TRANSFER_MAX_BYTES",
        "MYMCP_TRANSFER_DEFAULT_TTL_SEC",
        "MYMCP_TRANSFER_MAX_TTL_SEC",
        "MYMCP_PUBLIC_BASE_URL",
    ):
        monkeypatch.delenv(var, raising=False)
    cfg = _reload_config(monkeypatch, {})
    s = cfg.get_settings()
    assert s.transfer_enabled is True
    assert s.transfer_max_bytes == 2 * 1024 * 1024 * 1024
    assert s.transfer_default_ttl_sec == 300
    assert s.transfer_max_ttl_sec == 900
    assert s.public_base_url == ""


def test_transfer_settings_env_override(monkeypatch):
    cfg = _reload_config(
        monkeypatch,
        {
            "MYMCP_TRANSFER_ENABLED": "false",
            "MYMCP_TRANSFER_MAX_BYTES": "5242880",
            "MYMCP_TRANSFER_DEFAULT_TTL_SEC": "60",
            "MYMCP_TRANSFER_MAX_TTL_SEC": "120",
            "MYMCP_PUBLIC_BASE_URL": "https://mcp.example.com",
        },
    )
    s = cfg.get_settings()
    assert s.transfer_enabled is False
    assert s.transfer_max_bytes == 5_242_880
    assert s.transfer_default_ttl_sec == 60
    assert s.transfer_max_ttl_sec == 120
    assert s.public_base_url == "https://mcp.example.com"
    assert cfg.TRANSFER_ENABLED is False
    assert cfg.TRANSFER_MAX_BYTES == 5_242_880
    assert cfg.PUBLIC_BASE_URL == "https://mcp.example.com"


def test_recorder_llm_max_tokens_default_accommodates_reasoning_models():
    """Reasoning models bill thinking tokens against the output budget.

    16384 truncated real merges on deepseek-v4-flash (issue #92 item 5).
    """
    from mymcp.config import get_settings, reset_settings_cache

    reset_settings_cache()
    assert get_settings().recorder_llm_max_tokens == 32768
