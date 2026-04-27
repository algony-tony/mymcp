"""End-to-end tests for the install-service / uninstall-service CLI subcommands.

System-side calls (systemctl, useradd, ripgrep install) are patched. File
writes go to tmp_path. The point: verify the orchestration calls the right
helpers in the right order with the right arguments."""
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mocked_root(monkeypatch):
    monkeypatch.setattr("os.geteuid", lambda: 0)


def _run_cli(*args):
    from mymcp.cli import main
    return main(list(args))


def test_install_service_requires_root(monkeypatch, capsys):
    monkeypatch.setattr("os.geteuid", lambda: 1000)
    rc = _run_cli("install-service", "--yes")
    out = capsys.readouterr()
    assert rc != 0
    assert "sudo" in (out.out + out.err).lower() or "root" in (out.out + out.err).lower()


def test_install_service_writes_files_and_unit(tmp_path, mocked_root, monkeypatch):
    cfg = tmp_path / "mymcp"
    log = tmp_path / "mymcp-log"
    monkeypatch.setattr(
        "mymcp.deploy.service.systemd_available", lambda: True,
    )
    monkeypatch.setattr(
        "mymcp.deploy.service.resolve_mymcp_executable", lambda: "/usr/bin/mymcp",
    )
    monkeypatch.setattr("mymcp.deploy.service.daemon_reload", lambda: None)
    monkeypatch.setattr("mymcp.deploy.service.enable_service", lambda *a, **kw: None)
    written_unit = {}

    def _write_unit(text, path=None):
        written_unit["text"] = text
        written_unit["path"] = path

    monkeypatch.setattr("mymcp.deploy.service.write_systemd_unit", _write_unit)
    monkeypatch.setattr("mymcp.deploy.service.write_logrotate_config", lambda *a, **kw: None)
    monkeypatch.setattr("mymcp.deploy.service.install_ripgrep", lambda: True)
    monkeypatch.setattr("mymcp.deploy.service.ensure_service_user", lambda u: None)

    rc = _run_cli(
        "install-service",
        "--config-dir", str(cfg),
        "--log-dir", str(log),
        "--port", "9999",
        "--bind", "127.0.0.1",
        "--no-metrics",
        "--enable-audit",
        "--service-user", "root",
        "--skip-ripgrep",
        "--yes",
    )
    assert rc == 0
    assert (cfg / ".env").exists()
    env_text = (cfg / ".env").read_text()
    assert "MYMCP_PORT=9999" in env_text
    assert "MYMCP_HOST=127.0.0.1" in env_text
    assert "MYMCP_AUDIT_ENABLED=true" in env_text
    assert "MYMCP_METRICS_TOKEN=" in env_text
    assert (cfg / "tokens.json").exists()
    assert (log).exists()

    assert "User=root" in written_unit["text"]
    assert f"EnvironmentFile={cfg}/.env" in written_unit["text"]
    assert f"--env-file {cfg}/.env" in written_unit["text"]


def test_uninstall_service_calls_stop_disable(monkeypatch, mocked_root):
    calls = []
    monkeypatch.setattr("mymcp.deploy.service.systemd_available", lambda: True)
    monkeypatch.setattr(
        "mymcp.deploy.service.stop_service",
        lambda *a, **kw: calls.append(("stop", a, kw)),
    )
    monkeypatch.setattr(
        "mymcp.deploy.service.disable_service",
        lambda *a, **kw: calls.append(("disable", a, kw)),
    )
    monkeypatch.setattr("mymcp.deploy.service.daemon_reload", lambda: calls.append(("reload",)))
    import mymcp.deploy.service as svc
    from pathlib import Path
    fake_unit = MagicMock(spec=Path)
    fake_unit.exists.return_value = True
    fake_unit.unlink = lambda: calls.append(("unlink-unit",))
    monkeypatch.setattr(svc, "_SYSTEMD_UNIT_PATH", fake_unit)
    fake_lr = MagicMock(spec=Path)
    fake_lr.exists.return_value = False
    monkeypatch.setattr(svc, "_LOGROTATE_PATH", fake_lr)

    rc = _run_cli("uninstall-service")
    assert rc == 0
    op_names = [c[0] for c in calls]
    assert "stop" in op_names
    assert "disable" in op_names
    assert "unlink-unit" in op_names
    assert "reload" in op_names
