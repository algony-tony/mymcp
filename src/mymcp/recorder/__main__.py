"""Standalone entry point for the recorder sidecar (`mymcp-recorder`).

In v3 the recorder is a separate process from the Go core. It tails the core's
audit.log, folds mutating events into overview.md via an LLM, and exits cleanly
on SIGTERM/SIGINT. It serves no HTTP; bootstrap runs automatically when no
overview exists.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import shutil
import signal
import sys
from importlib import resources
from pathlib import Path

from mymcp.config import _discover_env_file, get_settings

log = logging.getLogger("mymcp.recorder")


async def _run(supervisor, stop: asyncio.Event) -> None:
    """Drive supervisor.run() until stop is set, then shut it down and drain."""
    task = asyncio.create_task(supervisor.run())
    await stop.wait()
    supervisor.shutdown()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(task, timeout=10)


async def _amain(supervisor) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    await _run(supervisor, stop)


def render_unit() -> str:
    """Render the packaged systemd unit template with this install's values.

    v3 dropped `install-service` (Python-CLI machinery), which left the
    template shipped but unrenderable — issue #92. This is the renderer.
    """
    template = (
        resources.files("mymcp.recorder.templates")
        .joinpath("mymcp-recorder.service.in")
        .read_text(encoding="utf-8")
    )
    exec_start = shutil.which("mymcp-recorder") or "/usr/local/bin/mymcp-recorder"
    # Mirror mymcp.config's own discovery (MYMCP_ENV_FILE, then
    # /etc/mymcp/.env, then ./.env) so an install using either alternative
    # gets a correct EnvironmentFile= line — issue #92 finding 4. A relative
    # path (the ./.env case) is meaningless to systemd, so fall back to the
    # /etc/mymcp/.env default rather than emit it verbatim. This calls the
    # path-discovery helper only — never get_settings()/Settings() — so
    # render_unit keeps working even when the discovered .env fails
    # validation (finding 2).
    discovered = _discover_env_file()
    env_file = discovered if discovered and Path(discovered).is_absolute() else "/etc/mymcp/.env"
    return template.format(
        service_user="mymcp",
        working_directory="/etc/mymcp",
        env_file=env_file,
        exec_start=exec_start,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mymcp-recorder")
    parser.add_argument(
        "--install-unit",
        action="store_true",
        help="print a systemd unit for this install and exit",
    )
    parser.add_argument(
        "--output",
        metavar="PATH",
        help="with --install-unit, write the unit to PATH instead of stdout",
    )
    args = parser.parse_args(argv)

    if args.install_unit:
        # render_unit() uses no Settings at all, and --install-unit is the one
        # command meant to work *before* config is fully correct — get_settings()
        # must not run on this path. A malformed .env (bad MYMCP_RECORDER_LLM_PROVIDER,
        # a non-numeric int field) would otherwise raise an uncaught
        # pydantic.ValidationError here and crash with a raw traceback (issue #92
        # finding 2).
        unit = render_unit()
        if args.output:
            try:
                Path(args.output).write_text(unit, encoding="utf-8")
            except OSError as e:
                print(
                    f"mymcp-recorder: could not write unit to {args.output}: {e}",
                    file=sys.stderr,
                )
                return 1
        else:
            print(unit)
        return 0

    settings = get_settings()

    logging.basicConfig(level=logging.INFO)
    if not settings.recorder_enabled:
        print(
            "mymcp-recorder: MYMCP_RECORDER_ENABLED is not true; refusing to start.",
            file=sys.stderr,
        )
        return 1
    try:
        from mymcp.recorder.wiring import build_supervisor
    except ImportError as e:
        # `mymcp-recorder` is an unconditional [project.scripts] entry while
        # the recorder's real deps live in the [recorder] extra, so a base
        # install puts this command on PATH with nothing behind it.
        print(
            f"mymcp-recorder: recorder dependencies are missing ({e}).\n"
            '  Install them with: pipx inject algony-mymcp "algony-mymcp[recorder]"',
            file=sys.stderr,
        )
        return 1
    supervisor = build_supervisor(settings)
    log.info("mymcp-recorder: starting (data_dir=%s)", settings.recorder_data_dir)
    asyncio.run(_amain(supervisor))
    log.info("mymcp-recorder: stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
