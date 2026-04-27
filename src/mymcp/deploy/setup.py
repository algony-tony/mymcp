"""Builders + writers for the install-service config bootstrap step."""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path


def make_token() -> str:
    return "tok_" + secrets.token_hex(16)


def build_env_dict(
    *,
    host: str,
    port: int,
    admin_token: str,
    metrics_token: str,
    token_file: str,
    audit_enabled: bool,
    audit_log_dir: str,
) -> dict[str, str]:
    return {
        "MYMCP_HOST": host,
        "MYMCP_PORT": str(port),
        "MYMCP_ADMIN_TOKEN": admin_token,
        "MYMCP_METRICS_TOKEN": metrics_token,
        "MYMCP_TOKEN_FILE": token_file,
        "MYMCP_AUDIT_ENABLED": "true" if audit_enabled else "false",
        "MYMCP_AUDIT_LOG_DIR": audit_log_dir,
    }


def format_env_file(env: dict[str, str]) -> str:
    lines = [f"{k}={v}" for k, v in env.items()]
    return "\n".join(lines) + "\n"


def write_env_file(path: Path | str, env: dict[str, str]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(format_env_file(env))
    os.chmod(p, 0o600)


def write_empty_token_store(path: Path | str, *, admin_token: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = {"tokens": {}, "admin_token": admin_token}
    p.write_text(json.dumps(body, indent=2))
    os.chmod(p, 0o600)


def ensure_directory(path: Path | str, mode: int = 0o750) -> None:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    os.chmod(p, mode)


def update_env_file(path: Path | str, updates: dict[str, str]) -> None:
    """Merge `updates` into an existing .env file, preserving order. Mode 600."""
    p = Path(path)
    if p.exists():
        existing: dict[str, str] = {}
        for raw in p.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            existing[k.strip()] = v.strip()
        existing.update(updates)
    else:
        existing = dict(updates)
    write_env_file(p, existing)
