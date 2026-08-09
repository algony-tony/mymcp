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


def test_render_unit_uses_real_settings(monkeypatch):
    """The template must be rendered by shipped code, not by this test.

    Issue #92: the only renderer was a test with values hardcoded inside it, so
    no code path and no document told an operator what to substitute.
    """
    monkeypatch.setenv("MYMCP_RECORDER_ENABLED", "true")
    from mymcp.config import get_settings, reset_settings_cache
    from mymcp.recorder.__main__ import render_unit

    reset_settings_cache()
    unit = render_unit(get_settings())

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
