"""End-to-end: init -> serve -> doctor -> tools/list, against the real binary.

Runs unprivileged with every path redirected into tmp_path, using -files-only
because managing a live systemd unit needs root: `systemctl daemon-reload`
fails with "Interactive authentication required" for a non-root caller. That
degrade must be asked for explicitly — inferring it from uid would turn a
forgotten `sudo` into a silent partial install — so this test asks for it, and
exercises the same shape a container install gets. Unit *writing* is covered by
the Go unit tests; what this proves is that init -> serve -> doctor ->
tools/list actually connects.
"""

import json
import os
import subprocess
import time
import urllib.request

import pytest

BINARY = os.environ.get("MYMCP_BINARY", "/tmp/mymcp")


@pytest.mark.skipif(not os.path.exists(BINARY), reason="build /tmp/mymcp first")
def test_init_then_serve_then_doctor(tmp_path):
    cfg = tmp_path / "etc"
    port = 18765

    rc = subprocess.run(
        [
            BINARY,
            "init",
            "-yes",
            "-config-dir",
            str(cfg),
            "-log-dir",
            str(tmp_path / "log"),
            "-recorder-data-dir",
            str(tmp_path / "rec"),
            "-files-only",
            "-port",
            str(port),
            "-bind",
            "127.0.0.1",
            "-start=false",
        ],
        capture_output=True,
        text=True,
    )
    assert rc.returncode == 0, rc.stderr

    env_text = (cfg / ".env").read_text()
    assert "MYMCP_AUDIT_ENABLED=true" in env_text
    assert (cfg / ".env").stat().st_mode & 0o777 == 0o600
    tokens = json.loads((cfg / "tokens.json").read_text())
    client_token = next(tok for tok, info in tokens["tokens"].items() if info["name"] == "default")

    server = subprocess.Popen(
        [BINARY, "serve", "-env-file", str(cfg / ".env")],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_for_port(port)

        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/mcp",
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode(),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {client_token}",
            },
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read().decode()
        assert '"name"' in body, body

        doctor = subprocess.run(
            [BINARY, "doctor", "-config-dir", str(cfg), "-json"],
            capture_output=True,
            text=True,
        )
        checks = json.loads(doctor.stdout)
        by_name = {c["name"]: c for c in checks}
        assert by_name["env permissions"]["severity"] == "ok"
        assert by_name["admin token"]["severity"] == "ok"
        assert by_name["audit enabled"]["severity"] == "ok"
    finally:
        server.terminate()
        server.wait(timeout=10)


def _wait_for_port(port, timeout=10.0):
    # A plain TCP connect, not an HTTP probe: init generates a metrics token by
    # default, so /metrics answers 401 and urlopen would raise forever.
    import socket

    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            s.settimeout(1)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)
    raise AssertionError(f"server never listened on {port}")


@pytest.mark.skipif(not os.path.exists(BINARY), reason="build /tmp/mymcp first")
def test_init_is_idempotent_and_keeps_tokens(tmp_path):
    cfg = tmp_path / "etc"
    args = [
        BINARY,
        "init",
        "-yes",
        "-config-dir",
        str(cfg),
        "-log-dir",
        str(tmp_path / "log"),
        "-recorder-data-dir",
        str(tmp_path / "rec"),
        "-files-only",
        "-start=false",
    ]
    assert subprocess.run(args, capture_output=True).returncode == 0
    first_env = (cfg / ".env").read_text()
    first_tokens = (cfg / "tokens.json").read_text()

    assert subprocess.run(args, capture_output=True).returncode == 0
    assert (cfg / ".env").read_text() == first_env, "re-run must not change .env"
    assert (cfg / "tokens.json").read_text() == first_tokens, "re-run must not add tokens"
