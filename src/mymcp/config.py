"""mymcp configuration via pydantic-settings.

All settings come from MYMCP_-prefixed environment variables. An optional
.env file can be loaded — discovery order: MYMCP_ENV_FILE env var,
/etc/mymcp/.env, ./.env. Use get_settings() to retrieve the cached singleton.
"""
import os
from functools import lru_cache
from pathlib import Path

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
