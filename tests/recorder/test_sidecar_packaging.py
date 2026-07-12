from importlib import resources


def test_recorder_service_template_is_packaged_and_formats():
    text = (
        resources.files("mymcp.recorder.templates")
        .joinpath("mymcp-recorder.service.in")
        .read_text()
    )
    rendered = text.format(
        service_user="mymcp",
        working_directory="/etc/mymcp",
        env_file="/etc/mymcp/.env",
        exec_start="/usr/local/bin/mymcp-recorder",
    )
    assert "ExecStart=/usr/local/bin/mymcp-recorder" in rendered
    assert "Description=MyMCP Recorder" in rendered


def test_recorder_entry_point_declared():
    import tomllib

    with open("pyproject.toml", "rb") as f:
        data = tomllib.load(f)
    scripts = data["project"]["scripts"]
    assert scripts["mymcp-recorder"] == "mymcp.recorder.__main__:main"
