"""Tests for the mode parameter of check_protected_path and register_protected_path."""

import pytest

from mymcp import config
from mymcp.tools.files import (
    _runtime_protected,
    check_protected_path,
    register_protected_path,
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    monkeypatch.setenv("MYMCP_AUDIT_LOG_DIR", str(tmp_path / "audit"))
    monkeypatch.setenv("MYMCP_PROTECTED_PATHS", "")
    config.reset_settings_cache()
    _runtime_protected.clear()
    yield
    _runtime_protected.clear()


def test_legacy_protected_blocks_both_modes(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setenv("MYMCP_PROTECTED_PATHS", str(vault))
    config.reset_settings_cache()
    assert check_protected_path(str(vault / "x"), mode="read") is not None
    assert check_protected_path(str(vault / "x"), mode="write") is not None


def test_write_only_protected_allows_read(tmp_path):
    overview_dir = tmp_path / "overview"
    overview_dir.mkdir()
    register_protected_path(str(overview_dir), modes={"write"})
    assert check_protected_path(str(overview_dir / "overview.md"), mode="read") is None
    assert check_protected_path(str(overview_dir / "overview.md"), mode="write") is not None


def test_read_only_protected_allows_write(tmp_path):
    """Symmetric: a path registered with modes={'read'} blocks reads but allows writes."""
    secret_dir = tmp_path / "secret"
    secret_dir.mkdir()
    register_protected_path(str(secret_dir), modes={"read"})
    assert check_protected_path(str(secret_dir / "x"), mode="read") is not None
    assert check_protected_path(str(secret_dir / "x"), mode="write") is None


def test_default_mode_is_write_for_back_compat():
    import inspect

    sig = inspect.signature(check_protected_path)
    assert sig.parameters["mode"].default == "write"


def test_register_idempotent(tmp_path):
    register_protected_path(str(tmp_path / "x"), modes={"write"})
    register_protected_path(str(tmp_path / "x"), modes={"write"})
    # Only one entry in registry
    matches = [e for e in _runtime_protected if e[0] == str(tmp_path / "x")]
    assert len(matches) == 1
