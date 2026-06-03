"""mymcp configuration via pydantic-settings.

All settings come from MYMCP_-prefixed environment variables. An optional
.env file can be loaded — discovery order: MYMCP_ENV_FILE env var,
/etc/mymcp/.env, ./.env. Use get_settings() to retrieve the cached singleton.
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _discover_env_file() -> str | None:
    explicit = os.environ.get("MYMCP_ENV_FILE")
    if explicit and Path(explicit).is_file():
        return explicit
    for candidate in ("/etc/mymcp/.env", ".env"):
        if Path(candidate).is_file():
            return candidate
    return None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MYMCP_",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Server
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8765)

    # Auth
    admin_token: str = Field(default="")
    metrics_token: str = Field(default="")
    token_file: str = Field(default="/etc/mymcp/tokens.json")

    # bash_execute output limits
    bash_max_output_bytes: int = Field(default=102400)
    bash_max_output_bytes_hard: int = Field(default=1048576)

    # read_file limits
    read_file_default_limit: int = Field(default=2000)
    read_file_max_limit: int = Field(default=50000)
    read_file_max_line_bytes: int = Field(default=32768)

    # write_file / edit limits
    write_file_max_bytes: int = Field(default=10 * 1024 * 1024)
    edit_string_max_bytes: int = Field(default=1024 * 1024)

    # glob / grep limits
    glob_max_results: int = Field(default=1000)
    grep_default_max_results: int = Field(default=500)
    grep_max_results: int = Field(default=5000)

    # Audit
    audit_enabled: bool = Field(default=False)
    audit_log_dir: str = Field(default="/var/log/mymcp")
    audit_max_bytes: int = Field(default=10 * 1024 * 1024)
    audit_backup_count: int = Field(default=5)

    # Shutdown
    shutdown_grace_sec: int = Field(default=5)

    # Extra protected paths (CSV)
    protected_paths: str = Field(default="")

    # File transfer (binary / large file)
    transfer_enabled: bool = Field(default=True)
    transfer_max_bytes: int = Field(default=2 * 1024 * 1024 * 1024)
    transfer_default_ttl_sec: int = Field(default=300)
    transfer_max_ttl_sec: int = Field(default=900)
    public_base_url: str = Field(default="")

    # Recorder (optional llm-recorder module)
    recorder_enabled: bool = Field(default=False)
    recorder_data_dir: str = Field(default="/var/lib/mymcp/recorder")
    recorder_merge_interval_sec: int = Field(default=300)
    recorder_max_events_per_cycle: int = Field(default=50)
    recorder_bootstrap_max_iterations: int = Field(default=200)
    recorder_bootstrap_token_budget: int = Field(default=10_000_000)
    recorder_bootstrap_probe_timeout_sec: int = Field(default=30)
    recorder_bootstrap_retry_interval_sec: int = Field(default=3600)
    recorder_llm_provider: Literal["anthropic", "openai"] = Field(default="anthropic")
    recorder_llm_model: str | None = Field(default=None)
    recorder_llm_api_key: str | None = Field(default=None)
    recorder_llm_base_url: str | None = Field(default=None)
    # Per-call output ceiling for the recorder's LLM. Must stay ≤ the chosen
    # model's max output (Claude Haiku/Sonnet 4.6: 64k, Opus 4.8: 128k,
    # GPT-5.x: 128k, DeepSeek v4: 384k); the API rejects values above the
    # model's limit. 16384 is a safe cross-provider default; downstream
    # deployments can raise it for providers with larger ceilings.
    recorder_llm_max_tokens: int = Field(default=16384)
    # Recorder supervisor pauses LLM calls after this many consecutive
    # merge_cycle failures. Restart the service to resume. 0 disables.
    recorder_circuit_breaker_threshold: int = Field(default=5)

    # T1 truncation knobs
    audit_output_bash_head_bytes: int = Field(default=4096)
    audit_output_bash_tail_bytes: int = Field(default=4096)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    env_file = _discover_env_file()
    if env_file:
        return Settings(_env_file=env_file)  # type: ignore[call-arg]
    return Settings()


def reset_settings_cache() -> None:
    """Test-only helper to force re-read of env vars/files."""
    get_settings.cache_clear()


def get_protected_paths() -> list[str]:
    """Always-protected paths the file tools must refuse access to.

    Composed from the audit log dir and any extras from MYMCP_PROTECTED_PATHS.
    """
    s = get_settings()
    paths: list[str] = [s.audit_log_dir]
    if s.protected_paths.strip():
        paths.extend(p.strip() for p in s.protected_paths.split(",") if p.strip())
    return paths


# Module-level convenience attributes for back-compat with existing call sites.
# These resolve lazily via __getattr__.
_LEGACY_ATTRS = {
    "HOST": "host",
    "PORT": "port",
    "ADMIN_TOKEN": "admin_token",
    "METRICS_TOKEN": "metrics_token",
    "TOKEN_FILE": "token_file",
    "BASH_MAX_OUTPUT_BYTES": "bash_max_output_bytes",
    "BASH_MAX_OUTPUT_BYTES_HARD": "bash_max_output_bytes_hard",
    "READ_FILE_DEFAULT_LIMIT": "read_file_default_limit",
    "READ_FILE_MAX_LIMIT": "read_file_max_limit",
    "READ_FILE_MAX_LINE_BYTES": "read_file_max_line_bytes",
    "WRITE_FILE_MAX_BYTES": "write_file_max_bytes",
    "EDIT_STRING_MAX_BYTES": "edit_string_max_bytes",
    "GLOB_MAX_RESULTS": "glob_max_results",
    "GREP_DEFAULT_MAX_RESULTS": "grep_default_max_results",
    "GREP_MAX_RESULTS": "grep_max_results",
    "AUDIT_ENABLED": "audit_enabled",
    "AUDIT_LOG_DIR": "audit_log_dir",
    "AUDIT_MAX_BYTES": "audit_max_bytes",
    "AUDIT_BACKUP_COUNT": "audit_backup_count",
    "TRANSFER_ENABLED": "transfer_enabled",
    "TRANSFER_MAX_BYTES": "transfer_max_bytes",
    "TRANSFER_DEFAULT_TTL_SEC": "transfer_default_ttl_sec",
    "TRANSFER_MAX_TTL_SEC": "transfer_max_ttl_sec",
    "PUBLIC_BASE_URL": "public_base_url",
    "RECORDER_ENABLED": "recorder_enabled",
    "RECORDER_DATA_DIR": "recorder_data_dir",
    "RECORDER_MERGE_INTERVAL_SEC": "recorder_merge_interval_sec",
    "RECORDER_MAX_EVENTS_PER_CYCLE": "recorder_max_events_per_cycle",
    "RECORDER_BOOTSTRAP_MAX_ITERATIONS": "recorder_bootstrap_max_iterations",
    "RECORDER_BOOTSTRAP_TOKEN_BUDGET": "recorder_bootstrap_token_budget",
    "RECORDER_BOOTSTRAP_PROBE_TIMEOUT_SEC": "recorder_bootstrap_probe_timeout_sec",
    "RECORDER_BOOTSTRAP_RETRY_INTERVAL_SEC": "recorder_bootstrap_retry_interval_sec",
    "RECORDER_LLM_PROVIDER": "recorder_llm_provider",
    "RECORDER_LLM_MODEL": "recorder_llm_model",
    "RECORDER_LLM_API_KEY": "recorder_llm_api_key",
    "RECORDER_LLM_BASE_URL": "recorder_llm_base_url",
    "RECORDER_LLM_MAX_TOKENS": "recorder_llm_max_tokens",
    "RECORDER_CIRCUIT_BREAKER_THRESHOLD": "recorder_circuit_breaker_threshold",
    "AUDIT_OUTPUT_BASH_HEAD_BYTES": "audit_output_bash_head_bytes",
    "AUDIT_OUTPUT_BASH_TAIL_BYTES": "audit_output_bash_tail_bytes",
}


def __getattr__(name: str):
    if name == "PROTECTED_PATHS":
        return get_protected_paths()
    if name == "APP_VERSION":
        from mymcp import __version__

        return __version__
    if name in _LEGACY_ATTRS:
        return getattr(get_settings(), _LEGACY_ATTRS[name])
    raise AttributeError(f"module 'mymcp.config' has no attribute {name!r}")
