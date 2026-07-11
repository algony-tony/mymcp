import tomllib


def _pyproject() -> dict:
    with open("pyproject.toml", "rb") as f:
        return tomllib.load(f)


def test_recorder_extra_lists_sidecar_dependencies():
    extras = _pyproject()["project"]["optional-dependencies"]
    recorder = extras["recorder"]
    names = {req.split(">")[0].split("=")[0].split("[")[0].strip().lower() for req in recorder}
    # Exactly what `import mymcp.recorder.__main__` needs to run standalone.
    required = {
        "httpx",
        "anyio",
        "pydantic-settings",
        "python-json-logger",
        "opentelemetry-api",
        "opentelemetry-sdk",
        "opentelemetry-exporter-prometheus",
    }
    missing = required - names
    assert not missing, f"[recorder] extra missing: {missing}"
    # The sidecar must NOT drag in the Python-core web stack.
    assert "fastapi" not in names and "uvicorn" not in names and "mcp" not in names


def test_recorder_alias_extras_still_present():
    # Kept (possibly empty) so old install commands don't break.
    extras = _pyproject()["project"]["optional-dependencies"]
    assert "recorder-anthropic" in extras
    assert "recorder-openai" in extras
