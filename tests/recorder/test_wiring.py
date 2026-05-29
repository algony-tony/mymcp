import sys
from unittest.mock import MagicMock

import pytest

from mymcp.config import Settings
from mymcp.recorder.wiring import build_supervisor


def _fake_anthropic(monkeypatch):
    mod = MagicMock()
    mod.AsyncAnthropic = MagicMock(return_value=MagicMock())
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    monkeypatch.delitem(sys.modules, "mymcp.recorder.llm.anthropic_client", raising=False)


def test_build_supervisor_with_anthropic(monkeypatch, tmp_path):
    _fake_anthropic(monkeypatch)
    s = Settings(
        recorder_enabled=True,
        recorder_data_dir=str(tmp_path / "recorder"),
        recorder_llm_provider="anthropic",
        recorder_llm_api_key="test-key",
        audit_log_dir=str(tmp_path / "audit"),
    )
    sup = build_supervisor(s)
    assert sup is not None
    assert sup.merge_interval == 300


def test_build_supervisor_protects_overview_dir(monkeypatch, tmp_path):
    """register_protected_path should make overview dir write-only protected."""
    from mymcp.tools.files import _runtime_protected, check_protected_path

    _runtime_protected.clear()
    _fake_anthropic(monkeypatch)
    s = Settings(
        recorder_enabled=True,
        recorder_data_dir=str(tmp_path / "recorder"),
        recorder_llm_provider="anthropic",
        recorder_llm_api_key="test-key",
        audit_log_dir=str(tmp_path / "audit"),
    )
    build_supervisor(s)
    overview_path = str(tmp_path / "recorder" / "overview" / "overview.md")
    assert check_protected_path(overview_path, mode="write") is not None
    assert check_protected_path(overview_path, mode="read") is None
    _runtime_protected.clear()


def test_build_supervisor_missing_api_key_raises(monkeypatch, tmp_path):
    _fake_anthropic(monkeypatch)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    s = Settings(
        recorder_enabled=True,
        recorder_data_dir=str(tmp_path / "recorder"),
        recorder_llm_provider="anthropic",
        recorder_llm_api_key=None,
        audit_log_dir=str(tmp_path / "audit"),
    )
    with pytest.raises(ValueError, match="API key"):
        build_supervisor(s)
