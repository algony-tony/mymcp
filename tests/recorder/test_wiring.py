import pytest

from mymcp.config import Settings
from mymcp.recorder.wiring import build_supervisor


def test_build_supervisor_with_anthropic(monkeypatch, tmp_path):
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


def test_build_supervisor_missing_api_key_raises(monkeypatch, tmp_path):
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
