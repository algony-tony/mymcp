"""Drive scripts/install.sh against a local release mirror.

The script's whole job is fetch-verify-place, so these tests cover exactly
that: the happy path, a tampered binary, and an unsupported architecture.
`mymcp init` is stubbed by putting a fake `mymcp` earlier on PATH, because
install.sh must not care what init does.
"""

import hashlib
import http.server
import os
import subprocess
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "install.sh"
if not SCRIPT.exists():
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "scripts" / "install.sh"
        if candidate.exists():
            REPO = parent
            SCRIPT = candidate
            break


@pytest.fixture
def release_mirror(tmp_path):
    """Serve a fake 'releases/latest/download/' tree over HTTP."""
    root = tmp_path / "mirror"
    root.mkdir()
    payload = b"#!/bin/sh\necho fake-mymcp\n"
    (root / "mymcp-linux-amd64").write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    (root / "SHA256SUMS").write_text(f"{digest}  mymcp-linux-amd64\n")

    # SimpleHTTPRequestHandler resolves against cwd, so pin the directory.
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(root), **kw)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", root
    server.shutdown()


def run_install(base_url, tmp_path, *args, arch="x86_64"):
    """Run install.sh with a stub `uname` and a stub `mymcp` on PATH."""
    stub = tmp_path / "stub"
    stub.mkdir(exist_ok=True)
    (stub / "uname").write_text(
        f'#!/bin/sh\ncase "$1" in -m) echo {arch};; -s) echo Linux;; *) echo Linux;; esac\n'
    )
    (stub / "uname").chmod(0o755)
    env = dict(os.environ)
    env["MYMCP_INSTALL_BASE_URL"] = base_url
    env["PATH"] = f"{stub}:{env['PATH']}"
    return subprocess.run(
        ["bash", str(SCRIPT), "--bin-dir", str(tmp_path / "bin"), *args],
        capture_output=True,
        text=True,
        env=env,
    )


def test_installs_verified_binary_and_execs_init(release_mirror, tmp_path):
    base_url, _ = release_mirror
    r = run_install(base_url, tmp_path, "--method", "binary", "--yes")
    assert r.returncode == 0, r.stderr
    installed = tmp_path / "bin" / "mymcp"
    assert installed.exists(), r.stderr
    assert os.access(installed, os.X_OK)
    # install.sh exec's `mymcp init`; the fake binary echoes, proving handoff.
    assert "fake-mymcp" in r.stdout


def test_no_extra_args_reaches_handoff(release_mirror, tmp_path):
    # Regression test: `${INIT_ARGS[@]}` with `set -u` and no elements raises
    # "unbound variable" on bash < 4.4 (RHEL/CentOS 7, Amazon Linux 2). The
    # README headline command passes no extra args at all, so this is the
    # path that must never crash on old bash.
    base_url, _ = release_mirror
    r = run_install(base_url, tmp_path, "--method", "binary")
    assert r.returncode == 0, r.stderr
    assert "fake-mymcp" in r.stdout


def test_aborts_on_checksum_mismatch(release_mirror, tmp_path):
    base_url, root = release_mirror
    (root / "mymcp-linux-amd64").write_bytes(b"tampered\n")
    r = run_install(base_url, tmp_path, "--method", "binary", "--yes")
    assert r.returncode != 0
    assert "checksum" in (r.stderr + r.stdout).lower()
    assert not (tmp_path / "bin" / "mymcp").exists(), "a failed verify must leave nothing behind"


def test_rejects_unsupported_architecture(release_mirror, tmp_path):
    base_url, _ = release_mirror
    r = run_install(base_url, tmp_path, "--method", "binary", arch="riscv64")
    assert r.returncode != 0
    assert "riscv64" in (r.stderr + r.stdout)


def test_shellcheck_is_clean():
    if subprocess.run(["which", "shellcheck"], capture_output=True).returncode != 0:
        pytest.skip("shellcheck not installed")
    r = subprocess.run(["shellcheck", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
