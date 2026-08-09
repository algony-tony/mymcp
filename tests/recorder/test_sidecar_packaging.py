from importlib import resources


def test_recorder_service_template_is_packaged():
    """The unit template ships in package data (present in the wheel)."""
    text = (
        resources.files("mymcp.recorder.templates")
        .joinpath("mymcp-recorder.service.in")
        .read_text()
    )
    assert "Description=MyMCP Recorder" in text
    assert "{exec_start}" in text


def test_recorder_entry_point_declared():
    import tomllib

    with open("pyproject.toml", "rb") as f:
        data = tomllib.load(f)
    scripts = data["project"]["scripts"]
    assert scripts["mymcp-recorder"] == "mymcp.recorder.__main__:main"


def test_render_unit_leaves_no_placeholders():
    """render_unit must substitute every template placeholder.

    Issue #92: the only renderer was a test with values hardcoded inside it, so
    no code path and no document told an operator what to substitute.
    """
    from mymcp.recorder.__main__ import render_unit

    unit = render_unit()

    assert "{" not in unit, f"unsubstituted placeholder left in unit:\n{unit}"
    assert "[Unit]" in unit and "[Service]" in unit and "[Install]" in unit
    assert "ExecStart=" in unit
    assert "NoNewPrivileges=true" in unit


def test_install_unit_writes_to_output(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MYMCP_RECORDER_ENABLED", "true")
    from mymcp.config import reset_settings_cache
    from mymcp.recorder.__main__ import main

    reset_settings_cache()
    dest = tmp_path / "mymcp-recorder.service"
    assert main(["--install-unit", "--output", str(dest)]) == 0
    assert "ExecStart=" in dest.read_text()


def test_install_unit_prints_to_stdout(monkeypatch, capsys):
    monkeypatch.setenv("MYMCP_RECORDER_ENABLED", "true")
    from mymcp.config import reset_settings_cache
    from mymcp.recorder.__main__ import main

    reset_settings_cache()
    assert main(["--install-unit"]) == 0
    assert "[Service]" in capsys.readouterr().out


def test_install_unit_survives_malformed_env(tmp_path, monkeypatch, capsys):
    """A malformed .env must not crash --install-unit with a raw traceback.

    Issue #92 finding 2: main() used to call get_settings() unconditionally
    before the --install-unit dispatch, even though render_unit() uses no
    settings at all. A typo'd MYMCP_RECORDER_LLM_PROVIDER (or any other value
    that fails pydantic validation) made get_settings() raise an uncaught
    ValidationError and crash the one command meant to work *before* config is
    fully correct.
    """
    import pytest
    from pydantic import ValidationError

    env_file = tmp_path / "mymcp.env"
    env_file.write_text("MYMCP_RECORDER_LLM_PROVIDER=not-a-real-provider\n")
    monkeypatch.setenv("MYMCP_ENV_FILE", str(env_file))
    from mymcp.config import get_settings, reset_settings_cache
    from mymcp.recorder.__main__ import main

    reset_settings_cache()
    # Confirm the premise: this .env really does fail Settings validation, so
    # the test is exercising the crash this fix prevents, not a no-op.
    with pytest.raises(ValidationError):
        get_settings()
    reset_settings_cache()

    assert main(["--install-unit"]) == 0
    out = capsys.readouterr().out
    assert "Traceback" not in out
    assert "[Service]" in out


def test_render_unit_honors_explicit_env_file(tmp_path, monkeypatch):
    """render_unit's EnvironmentFile= must follow MYMCP_ENV_FILE (finding 4)."""
    env_file = tmp_path / "custom.env"
    env_file.write_text("MYMCP_HOST=127.0.0.1\n")
    monkeypatch.setenv("MYMCP_ENV_FILE", str(env_file))
    from mymcp.recorder.__main__ import render_unit

    unit = render_unit()
    assert f"EnvironmentFile={env_file}" in unit


def test_render_unit_falls_back_to_default_for_relative_discovery(tmp_path, monkeypatch):
    """A relative ./.env discovery result is meaningless to systemd.

    mymcp.config's discovery order falls back to ./.env, which is a path
    relative to the process's cwd — not something a systemd unit can use.
    render_unit must fall back to the /etc/mymcp/.env default instead of
    emitting the relative path verbatim.
    """
    monkeypatch.delenv("MYMCP_ENV_FILE", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("MYMCP_HOST=127.0.0.1\n")
    from mymcp.recorder.__main__ import render_unit

    unit = render_unit()
    assert "EnvironmentFile=/etc/mymcp/.env" in unit


def test_render_unit_defaults_env_file_when_nothing_discovered(tmp_path, monkeypatch):
    monkeypatch.delenv("MYMCP_ENV_FILE", raising=False)
    monkeypatch.chdir(tmp_path)  # no ./.env here, and no /etc/mymcp/.env on CI
    from mymcp.recorder.__main__ import render_unit

    unit = render_unit()
    assert "EnvironmentFile=/etc/mymcp/.env" in unit


def test_install_unit_output_error_is_reported_not_raised(tmp_path, monkeypatch, capsys):
    """A bad --output path must produce a readable stderr message, not a traceback.

    This whole task is about error messages that name the real cause — a new
    tool emitting a raw traceback on a bad path would be self-defeating.
    """
    monkeypatch.setenv("MYMCP_RECORDER_ENABLED", "true")
    from mymcp.config import reset_settings_cache
    from mymcp.recorder.__main__ import main

    reset_settings_cache()
    dest = tmp_path / "does-not-exist" / "mymcp-recorder.service"
    assert main(["--install-unit", "--output", str(dest)]) == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert str(dest) in err
