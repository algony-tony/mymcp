import pytest
from pydantic import ValidationError

from mymcp import config


def setup_function():
    config.reset_settings_cache()


def test_recorder_disabled_by_default(monkeypatch):
    monkeypatch.delenv("MYMCP_RECORDER_ENABLED", raising=False)
    config.reset_settings_cache()
    s = config.get_settings()
    assert s.recorder_enabled is False


def test_recorder_enable(monkeypatch):
    monkeypatch.setenv("MYMCP_RECORDER_ENABLED", "true")
    config.reset_settings_cache()
    s = config.get_settings()
    assert s.recorder_enabled is True


def test_recorder_defaults(monkeypatch):
    monkeypatch.setenv("MYMCP_RECORDER_ENABLED", "true")
    config.reset_settings_cache()
    s = config.get_settings()
    assert s.recorder_data_dir == "/var/lib/mymcp/recorder"
    assert s.recorder_merge_interval_sec == 300
    assert s.recorder_max_events_per_cycle == 50
    assert s.recorder_bootstrap_max_iterations == 200
    assert s.recorder_bootstrap_token_budget == 10_000_000
    assert s.recorder_bootstrap_probe_timeout_sec == 30
    assert s.recorder_bootstrap_retry_interval_sec == 3600
    assert s.recorder_llm_provider == "anthropic"
    assert s.recorder_llm_model is None
    assert s.recorder_llm_api_key is None
    assert s.recorder_llm_base_url is None
    assert s.audit_output_bash_head_bytes == 4096
    assert s.audit_output_bash_tail_bytes == 4096


def test_recorder_provider_validation(monkeypatch):
    monkeypatch.setenv("MYMCP_RECORDER_LLM_PROVIDER", "bogus")
    config.reset_settings_cache()
    with pytest.raises(ValidationError):
        config.get_settings()


def test_recorder_uppercase_alias(monkeypatch):
    """Existing-style code using config.RECORDER_ENABLED should still work."""
    monkeypatch.setenv("MYMCP_RECORDER_ENABLED", "true")
    config.reset_settings_cache()
    assert config.RECORDER_ENABLED is True
