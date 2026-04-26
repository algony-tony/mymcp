"""CLI argparse parsing and entry-point behavior."""
import subprocess
import sys


def test_mymcp_version_flag():
    result = subprocess.run(
        [sys.executable, "-m", "mymcp", "--version"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    assert "mymcp" in result.stdout.lower()


def test_mymcp_version_subcommand():
    result = subprocess.run(
        [sys.executable, "-m", "mymcp", "version"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    assert "mymcp" in result.stdout.lower()


def test_mymcp_serve_help():
    result = subprocess.run(
        [sys.executable, "-m", "mymcp", "serve", "--help"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    assert "--env-file" in result.stdout
    assert "--host" in result.stdout
    assert "--port" in result.stdout
    assert "--log-level" in result.stdout
    assert "--log-format" in result.stdout


def test_mymcp_no_subcommand_shows_help():
    result = subprocess.run(
        [sys.executable, "-m", "mymcp"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 2
    assert "usage:" in (result.stderr + result.stdout).lower()
