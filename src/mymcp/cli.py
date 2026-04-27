"""mymcp CLI entry point."""

from __future__ import annotations

import argparse
import logging
import os
import secrets
import signal
import sys
from typing import Any

from mymcp import __version__


def _configure_logging(level: str, fmt: str) -> None:
    log_level = getattr(logging, level.upper(), logging.INFO)
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stderr)
    if fmt == "json":
        from pythonjsonlogger import jsonlogger

        handler.setFormatter(
            jsonlogger.JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(log_level)


def _install_signal_handlers() -> None:
    from mymcp.tools.bash import shutdown_inflight_processes

    def _handler(signum: int, _frame: Any) -> None:
        shutdown_inflight_processes()
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)


def _maybe_set_temp_tokens(with_metrics: bool) -> None:
    """When no env file is present and no admin token is configured, generate
    in-memory tokens and inject them via env vars before settings load."""
    import tempfile

    from mymcp.config import _discover_env_file

    if _discover_env_file():
        return
    if os.environ.get("MYMCP_ADMIN_TOKEN"):
        return

    if not os.environ.get("MYMCP_TOKEN_FILE"):
        os.environ["MYMCP_TOKEN_FILE"] = os.path.join(
            tempfile.gettempdir(), f"mymcp-temp-{os.getpid()}.json"
        )

    admin = "tok_" + secrets.token_hex(16)
    rw = "tok_" + secrets.token_hex(16)
    os.environ["MYMCP_ADMIN_TOKEN"] = admin
    print(f"[mymcp] temp admin token: {admin}", file=sys.stderr)
    print(f"[mymcp] temp rw token:    {rw}", file=sys.stderr)
    print("[mymcp] tokens are in-memory; they vanish on exit.", file=sys.stderr)

    if with_metrics:
        metrics_token = "tok_" + secrets.token_hex(16)
        os.environ["MYMCP_METRICS_TOKEN"] = metrics_token
        print(f"[mymcp] temp metrics token: {metrics_token}", file=sys.stderr)

    os.environ["_MYMCP_TEMP_RW_TOKEN"] = rw


def cmd_serve(args: argparse.Namespace) -> int:
    if args.env_file:
        os.environ["MYMCP_ENV_FILE"] = args.env_file

    _configure_logging(args.log_level, args.log_format)
    _maybe_set_temp_tokens(args.with_metrics_token)

    from mymcp import config

    config.reset_settings_cache()
    s = config.get_settings()

    host = args.host if args.host is not None else s.host
    port = args.port if args.port is not None else s.port

    _install_signal_handlers()

    from mymcp.server import create_app

    app = create_app()

    rw = os.environ.pop("_MYMCP_TEMP_RW_TOKEN", "")
    if rw:
        from mymcp.auth import get_store

        store = get_store()
        with store._lock:  # noqa: SLF001
            store._data["tokens"][rw] = {
                "name": "temp-rw",
                "created_at": "ephemeral",
                "last_used": None,
                "enabled": True,
                "role": "rw",
            }

    import uvicorn

    uvicorn.run(app, host=host, port=port, log_config=None)
    return 0


def cmd_version(_args: argparse.Namespace) -> int:
    print(f"mymcp {__version__}")
    return 0


def _require_root() -> bool:
    if os.geteuid() != 0:
        print("error: this command requires root. Re-run with sudo.", file=sys.stderr)
        return False
    return True


def cmd_install_service(args: argparse.Namespace) -> int:
    if not _require_root():
        return 2

    from mymcp.deploy import service, setup

    if not service.systemd_available():
        print("error: systemd does not appear to be running.", file=sys.stderr)
        return 2

    exec_path = service.resolve_mymcp_executable()
    cfg_dir = os.path.abspath(args.config_dir)
    log_dir = os.path.abspath(args.log_dir)
    env_file = os.path.join(cfg_dir, ".env")
    token_file = os.path.join(cfg_dir, "tokens.json")

    if args.service_user != "root":
        service.ensure_service_user(args.service_user)

    setup.ensure_directory(cfg_dir, mode=0o750)
    setup.ensure_directory(log_dir, mode=0o750)

    admin_token = setup.make_token()
    metrics_token = setup.make_token() if args.enable_metrics else ""

    env = setup.build_env_dict(
        host=args.bind,
        port=args.port,
        admin_token=admin_token,
        metrics_token=metrics_token,
        token_file=token_file,
        audit_enabled=args.enable_audit,
        audit_log_dir=log_dir,
    )
    setup.write_env_file(env_file, env)
    setup.write_empty_token_store(token_file, admin_token=admin_token)

    if args.enable_audit:
        service.write_logrotate_config(service.render_logrotate_config(log_dir))

    if args.install_ripgrep:
        service.install_ripgrep()

    unit_text = service.render_service_unit(
        service_user=args.service_user,
        env_file=env_file,
        exec_start=f"{exec_path} serve --env-file {env_file}",
    )
    service.write_systemd_unit(unit_text)
    service.daemon_reload()
    service.enable_service()

    print("=== mymcp install-service complete ===")
    print(f"  Config dir:    {cfg_dir}")
    print(f"  Log dir:       {log_dir}")
    print(f"  Service user:  {args.service_user}")
    print(f"  Listening:     {args.bind}:{args.port}")
    print(f"\n  *** Admin token:   {admin_token} ***")
    if metrics_token:
        print(f"  *** Metrics token: {metrics_token} ***")
    print(f"  (Tokens are in {env_file} — last chance to copy them)\n")
    print("  Start:  sudo systemctl start mymcp")
    print("  Status: sudo systemctl status mymcp")
    print("  Logs:   sudo journalctl -u mymcp -f")
    return 0


def _resolve_env_path() -> str:
    from mymcp.config import _discover_env_file
    p = _discover_env_file()
    if not p:
        print(
            "error: no .env file found (set MYMCP_ENV_FILE or run install-service first)",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return p


def cmd_token_list(_args: argparse.Namespace) -> int:
    env_path = _resolve_env_path()
    os.environ["MYMCP_ENV_FILE"] = env_path
    from mymcp import config
    config.reset_settings_cache()
    from mymcp.auth import TokenStore
    s = config.get_settings()

    print(f"admin token:   {'set' if s.admin_token else 'NOT SET'}")
    print(f"metrics token: {'set' if s.metrics_token else 'disabled (empty)'}")
    print()
    if not s.token_file or not os.path.exists(s.token_file):
        print("(no tokens.json yet)")
        return 0
    store = TokenStore(s.token_file, s.admin_token)
    tokens = store.list_tokens()
    if not tokens:
        print("(no ro/rw tokens)")
        return 0
    for t, info in tokens.items():
        marker = "x" if info.get("enabled", True) else " "
        print(f"[{marker}] {info.get('role','rw'):2}  {info.get('name','-'):20}  {t}")
    return 0


def cmd_token_add(args: argparse.Namespace) -> int:
    env_path = _resolve_env_path()
    os.environ["MYMCP_ENV_FILE"] = env_path
    from mymcp.config import get_settings, reset_settings_cache
    reset_settings_cache()
    s = get_settings()
    from mymcp.auth import TokenStore
    store = TokenStore(s.token_file, s.admin_token)
    new = store.create_token(args.name, role=args.role)
    print(new)
    return 0


def cmd_token_revoke(args: argparse.Namespace) -> int:
    env_path = _resolve_env_path()
    os.environ["MYMCP_ENV_FILE"] = env_path
    from mymcp.config import get_settings, reset_settings_cache
    reset_settings_cache()
    s = get_settings()
    from mymcp.auth import TokenStore
    store = TokenStore(s.token_file, s.admin_token)
    if store.revoke_token(args.token):
        print(f"revoked {args.token}")
        return 0
    print(f"not found: {args.token}", file=sys.stderr)
    return 1


def _rotate_in_env(env_path: str, key: str) -> str:
    from mymcp.deploy.setup import make_token, update_env_file
    new = make_token()
    update_env_file(env_path, {key: new})
    return new


def cmd_token_rotate_admin(_args: argparse.Namespace) -> int:
    env_path = _resolve_env_path()
    new = _rotate_in_env(env_path, "MYMCP_ADMIN_TOKEN")
    print(new)
    return 0


def cmd_token_rotate_metrics(_args: argparse.Namespace) -> int:
    env_path = _resolve_env_path()
    new = _rotate_in_env(env_path, "MYMCP_METRICS_TOKEN")
    print(new)
    return 0


def cmd_token_disable_metrics(_args: argparse.Namespace) -> int:
    env_path = _resolve_env_path()
    from mymcp.deploy.setup import update_env_file
    update_env_file(env_path, {"MYMCP_METRICS_TOKEN": ""})
    print("metrics endpoint disabled.")
    return 0


def cmd_uninstall_service(args: argparse.Namespace) -> int:
    if not _require_root():
        return 2
    from mymcp.deploy import service
    if not service.systemd_available():
        print("error: systemd does not appear to be running.", file=sys.stderr)
        return 2
    service.stop_service()
    service.disable_service()
    if service._SYSTEMD_UNIT_PATH.exists():
        service._SYSTEMD_UNIT_PATH.unlink()
    if service._LOGROTATE_PATH.exists():
        service._LOGROTATE_PATH.unlink()
    service.daemon_reload()
    if args.purge:
        import shutil as _sh
        for p in (args.config_dir, args.log_dir):
            if os.path.isdir(p):
                _sh.rmtree(p)
        print(f"Purged: {args.config_dir} {args.log_dir}")
    print("mymcp service removed.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mymcp",
        description="MCP server for Linux system control",
    )
    parser.add_argument("--version", action="version", version=f"mymcp {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_serve = sub.add_parser("serve", help="Run the MCP server (foreground)")
    p_serve.add_argument("--env-file", help="Path to .env file")
    p_serve.add_argument("--host", help="Override bind host")
    p_serve.add_argument("--port", type=int, help="Override bind port")
    p_serve.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
    )
    p_serve.add_argument("--log-format", default="text", choices=["text", "json"])
    p_serve.add_argument(
        "--with-metrics-token",
        action="store_true",
        help="In temp-token mode, also generate an ephemeral metrics token",
    )
    p_serve.set_defaults(func=cmd_serve)

    p_version = sub.add_parser("version", help="Print the installed version")
    p_version.set_defaults(func=cmd_version)

    p_install = sub.add_parser(
        "install-service", help="Install systemd service (requires sudo)"
    )
    p_install.add_argument("--port", type=int, default=8765)
    p_install.add_argument("--bind", default="0.0.0.0")
    p_install.add_argument("--config-dir", default="/etc/mymcp")
    p_install.add_argument("--log-dir", default="/var/log/mymcp")
    p_install.add_argument(
        "--service-user", default="root", choices=["root", "mymcp"]
    )
    grp_m = p_install.add_mutually_exclusive_group()
    grp_m.add_argument(
        "--enable-metrics", dest="enable_metrics", action="store_true", default=True
    )
    grp_m.add_argument("--no-metrics", dest="enable_metrics", action="store_false")
    grp_a = p_install.add_mutually_exclusive_group()
    grp_a.add_argument(
        "--enable-audit", dest="enable_audit", action="store_true", default=True
    )
    grp_a.add_argument("--no-audit", dest="enable_audit", action="store_false")
    grp_r = p_install.add_mutually_exclusive_group()
    grp_r.add_argument(
        "--install-ripgrep", dest="install_ripgrep", action="store_true", default=True
    )
    grp_r.add_argument("--skip-ripgrep", dest="install_ripgrep", action="store_false")
    p_install.add_argument("--yes", action="store_true")
    p_install.set_defaults(func=cmd_install_service)

    p_uninst = sub.add_parser(
        "uninstall-service", help="Remove systemd service (requires sudo)"
    )
    p_uninst.add_argument("--config-dir", default="/etc/mymcp")
    p_uninst.add_argument("--log-dir", default="/var/log/mymcp")
    p_uninst.add_argument(
        "--purge", action="store_true", help="Also delete config-dir and log-dir"
    )
    p_uninst.set_defaults(func=cmd_uninstall_service)

    p_token = sub.add_parser("token", help="Manage tokens")
    p_token_sub = p_token.add_subparsers(dest="token_cmd", required=True)

    p_tok_list = p_token_sub.add_parser("list")
    p_tok_list.set_defaults(func=cmd_token_list)

    p_tok_add = p_token_sub.add_parser("add")
    p_tok_add.add_argument("--name", required=True)
    p_tok_add.add_argument("--role", choices=["ro", "rw"], required=True)
    p_tok_add.set_defaults(func=cmd_token_add)

    p_tok_rev = p_token_sub.add_parser("revoke")
    p_tok_rev.add_argument("token")
    p_tok_rev.set_defaults(func=cmd_token_revoke)

    p_tok_ra = p_token_sub.add_parser("rotate-admin")
    p_tok_ra.set_defaults(func=cmd_token_rotate_admin)

    p_tok_rm = p_token_sub.add_parser("rotate-metrics")
    p_tok_rm.set_defaults(func=cmd_token_rotate_metrics)

    p_tok_dm = p_token_sub.add_parser("disable-metrics")
    p_tok_dm.set_defaults(func=cmd_token_disable_metrics)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
