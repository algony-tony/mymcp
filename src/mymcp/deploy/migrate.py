"""Migrate a 1.x install at /opt/mymcp to the 2.0 layout."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

_DROP_KEYS = {"APP_DIR"}
_KEY_RE = re.compile(r"^\s*MCP_([A-Z_][A-Z0-9_]*)\s*=(.*)$")


def legacy_dir_present(path: Path | str) -> bool:
    return (Path(path) / ".env").is_file()


def rewrite_env_keys(text: str, new_cfg_dir: Path | str | None = None) -> str:
    """Replace `MCP_FOO=` with `MYMCP_FOO=` line by line. Drop legacy keys."""
    out_lines: list[str] = []
    for raw in text.splitlines():
        m = _KEY_RE.match(raw)
        if m:
            key, val = m.group(1), m.group(2)
            if key in _DROP_KEYS:
                continue
            if key == "TOKEN_FILE" and new_cfg_dir:
                # Remove possible quotes from val for path resolution
                clean_val = val.strip().strip("'\"")
                if not Path(clean_val).is_absolute():
                    # Resolve relative to the new config directory
                    val = str((Path(new_cfg_dir) / clean_val).resolve())
            out_lines.append(f"MYMCP_{key}={val}")
        else:
            out_lines.append(raw)
    return "\n".join(out_lines) + "\n"


def copy_tokens(legacy_dir: Path, target_path: Path) -> None:
    src = legacy_dir / "tokens.json"
    if src.exists():
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target_path)
