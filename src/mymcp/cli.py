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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
