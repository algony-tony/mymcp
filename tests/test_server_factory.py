"""Server module must expose a side-effect-free `create_app()` factory."""


def test_create_app_returns_fastapi_instance(monkeypatch, tmp_path):
    monkeypatch.setenv("MYMCP_ADMIN_TOKEN", "tok_test")
    monkeypatch.setenv("MYMCP_TOKEN_FILE", str(tmp_path / "tokens.json"))
    monkeypatch.setenv("MYMCP_AUDIT_LOG_DIR", str(tmp_path / "audit"))
    (tmp_path / "audit").mkdir()
    monkeypatch.delenv("MYMCP_ENV_FILE", raising=False)

    from mymcp import config
    config.reset_settings_cache()

    from mymcp.server import create_app
    app = create_app()

    from fastapi import FastAPI
    assert isinstance(app, FastAPI)


def test_importing_server_does_not_configure_logging():
    """Importing mymcp.server must not call logging.basicConfig()."""
    import logging
    root = logging.getLogger()
    pre_handlers = list(root.handlers)
    pre_level = root.level

    import importlib
    import mymcp.server
    importlib.reload(mymcp.server)

    assert list(root.handlers) == pre_handlers
    assert root.level == pre_level
