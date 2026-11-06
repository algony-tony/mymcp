"""Contract tests ensuring .env.example remains in sync with code defaults.

This test prevents documentation drift:
1. Every field defined on mymcp.config.Settings must be present in .env.example.
2. Every MYMCP_* variable in .env.example must be a valid Settings field.
3. Every default value specified in .env.example must match the actual default
   defined in code (mymcp.config.Settings).
"""

import re
from pathlib import Path
from typing import Any

from mymcp.config import Settings


def _find_env_example() -> Path:
    """Locate .env.example by walking up directory tree from this file."""
    for parent in Path(__file__).resolve().parents:
        candidate = parent / ".env.example"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(".env.example not found in directory ancestry")


def _parse_env_example(path: Path) -> dict[str, str]:
    """Parse all active and commented-out KEY=VAL lines from .env.example."""
    content = path.read_text(encoding="utf-8")
    matches = re.findall(
        r"^[#\s]*([A-Z0-9_]+)\s*=\s*(.*?)\s*(?:#.*)?$",
        content,
        re.MULTILINE,
    )
    env_vars: dict[str, str] = {}
    for key, val in matches:
        if key.startswith("MYMCP_"):
            env_vars[key] = val.strip()
    return env_vars


def test_env_example_contains_all_settings_fields():
    """All Settings fields must be documented in .env.example."""
    env_vars = _parse_env_example(_find_env_example())
    all_fields = set(Settings.model_fields.keys())

    missing = []
    for field_name in all_fields:
        expected_var = f"MYMCP_{field_name.upper()}"
        if expected_var not in env_vars:
            missing.append(expected_var)

    assert not missing, (
        f"Settings fields missing from .env.example: {missing}\n"
        "Please document newly added settings in .env.example."
    )


def test_no_unknown_variables_in_env_example():
    """Every MYMCP_* variable in .env.example must correspond to a Settings field."""
    env_vars = _parse_env_example(_find_env_example())
    all_fields = set(Settings.model_fields.keys())

    unknown = []
    for var_name in env_vars:
        field_name = var_name.removeprefix("MYMCP_").lower()
        if field_name not in all_fields:
            unknown.append(var_name)

    assert not unknown, (
        f"Unknown MYMCP_* variables in .env.example: {unknown}\n"
        "Ensure variable names in .env.example match Settings field names."
    )


def test_every_variable_has_descriptive_comment():
    """Ensure every variable in .env.example has at least one comment explaining it."""
    path = _find_env_example()
    lines = path.read_text(encoding="utf-8").splitlines()

    undocumented = []
    for i, line in enumerate(lines):
        m = re.match(r"^[#\s]*(MYMCP_[A-Z0-9_]+)\s*=", line)
        if m:
            var = m.group(1)
            has_doc = False
            j = i - 1
            while j >= 0:
                prev = lines[j].strip()
                if prev.startswith("# ===") or prev == "":
                    break
                if prev.startswith("#") and not re.match(r"^[#\s]*MYMCP_", prev):
                    has_doc = True
                    break
                j -= 1
            if not has_doc:
                undocumented.append((var, i + 1))

    assert not undocumented, (
        f"Variables without documentation comment in .env.example: {undocumented}\n"
        "Every configuration variable must have an explanatory comment above it."
    )


def test_env_example_defaults_match_settings():
    """All non-empty default values in .env.example must match Settings defaults."""
    env_vars = _parse_env_example(_find_env_example())
    settings = Settings()

    mismatches = []
    for var_name, doc_val in env_vars.items():
        field_name = var_name.removeprefix("MYMCP_").lower()
        code_val: Any = getattr(settings, field_name)

        if doc_val == "":
            # Empty in .env.example represents unset / empty default (e.g. None or "")
            if code_val not in (None, ""):
                mismatches.append(
                    f"{var_name}: .env.example has empty default, code default is {code_val!r}"
                )
            continue

        if isinstance(code_val, bool):
            expected = str(code_val).lower()
            if doc_val.lower() != expected:
                mismatches.append(
                    f"{var_name}: .env.example default is {doc_val}, code default is {expected}"
                )
        elif isinstance(code_val, int):
            try:
                parsed_val = int(doc_val)
            except ValueError:
                mismatches.append(f"{var_name}: invalid integer {doc_val} in .env.example")
                continue
            if parsed_val != code_val:
                mismatches.append(
                    f"{var_name}: .env.example default is {parsed_val}, code default is {code_val}"
                )
        elif isinstance(code_val, str):
            if doc_val != code_val:
                mismatches.append(
                    f"{var_name}: .env.example default is {doc_val!r}, code default is {code_val!r}"
                )

    assert not mismatches, "Defaults in .env.example diverged from code:\n" + "\n".join(mismatches)
