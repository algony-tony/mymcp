"""Tests for deploy/service.py — unit render, helpers, ripgrep install."""

import pytest


def test_render_service_unit_substitutes_fields():
    from mymcp.deploy.service import render_service_unit

    out = render_service_unit(
        service_user="root",
        env_file="/etc/mymcp/.env",
        exec_start="/usr/local/bin/mymcp serve --env-file /etc/mymcp/.env",
    )
    assert "User=root" in out
    assert "WorkingDirectory=/etc/mymcp" in out
    assert "EnvironmentFile=/etc/mymcp/.env" in out
    assert "ExecStart=/usr/local/bin/mymcp serve --env-file /etc/mymcp/.env" in out
    assert out.strip().startswith("[Unit]")


def test_rendered_unit_has_no_new_privileges_enabled():
    """Always-on hardening: NoNewPrivileges=true must ship as a live directive."""
    from mymcp.deploy.service import render_service_unit

    out = render_service_unit(
        service_user="mymcp",
        env_file="/etc/mymcp/.env",
        exec_start="/usr/local/bin/mymcp serve",
    )
    # Active (not just commented out)
    assert "\nNoNewPrivileges=true\n" in out


def test_rendered_unit_keeps_opt_in_hardening_as_comments():
    """Stronger isolation must ship commented — uncommenting limits LLM scope."""
    from mymcp.deploy.service import render_service_unit

    out = render_service_unit(
        service_user="mymcp",
        env_file="/etc/mymcp/.env",
        exec_start="/usr/local/bin/mymcp serve",
    )
    for directive in (
        "ProtectSystem",
        "ProtectHome",
        "PrivateTmp",
        "ReadWritePaths",
        "CapabilityBoundingSet",
        "RestrictAddressFamilies",
    ):
        # Must be present in commented form…
        assert f"# {directive}" in out, f"{directive} missing as opt-in comment"
        # …and NOT present as an active directive
        for line in out.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            assert not stripped.startswith(f"{directive}="), (
                f"{directive} must ship commented, not active"
            )


def test_resolve_mymcp_executable_uses_which(monkeypatch):
    from mymcp.deploy import service

    monkeypatch.setattr(service.shutil, "which", lambda name: "/opt/pipx/bin/mymcp")
    assert service.resolve_mymcp_executable() == "/opt/pipx/bin/mymcp"


def test_resolve_mymcp_executable_raises_if_missing(monkeypatch):
    from mymcp.deploy import service

    monkeypatch.setattr(service.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="not on PATH"):
        service.resolve_mymcp_executable()


def test_systemd_available_checks_run_systemd_dir(monkeypatch, tmp_path):
    from mymcp.deploy import service

    monkeypatch.setattr(service, "_RUN_SYSTEMD", str(tmp_path))
    assert service.systemd_available() is True

    nonex = tmp_path / "absent"
    monkeypatch.setattr(service, "_RUN_SYSTEMD", str(nonex))
    assert service.systemd_available() is False


def test_render_logrotate_config():
    from mymcp.deploy.service import render_logrotate_config

    out = render_logrotate_config("/var/log/mymcp")
    assert "/var/log/mymcp/audit.log" in out
    assert "rotate" in out
