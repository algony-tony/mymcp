"""Unit test for scripts/assemble_wheel.py — the v3 platform-wheel assembler.

Builds a minimal pure wheel fixture with `wheel pack` (no project build toolchain
needed), runs the assembler, and inspects the result.
"""

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("wheel")

REPO = Path(__file__).resolve().parents[1]
ASSEMBLE = REPO / "scripts" / "assemble_wheel.py"

if not ASSEMBLE.exists():
    # e.g. the mutmut `mutants/` sandbox, which only mirrors the mutated dirs.
    pytest.skip("assemble_wheel.py not present in this sandbox", allow_module_level=True)


def _make_pure_wheel(tmp: Path) -> Path:
    """Hand-build a minimal pure wheel via `wheel pack`."""
    root = tmp / "algony_mymcp-1.0"
    di = root / "algony_mymcp-1.0.dist-info"
    di.mkdir(parents=True)
    (di / "METADATA").write_text("Metadata-Version: 2.1\nName: algony-mymcp\nVersion: 1.0\n")
    (di / "WHEEL").write_text(
        "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    )
    (di / "entry_points.txt").write_text(
        "[console_scripts]\nmymcp = mymcp.cli:main\nmymcp-recorder = mymcp.recorder.__main__:main\n"
    )
    out = tmp / "pure"
    out.mkdir()
    subprocess.run(
        [sys.executable, "-m", "wheel", "pack", str(root), "--dest-dir", str(out)],
        check=True,
        capture_output=True,
    )
    return next(out.glob("*.whl"))


def test_assemble_produces_platform_wheel_with_binary(tmp_path):
    pure = _make_pure_wheel(tmp_path)
    fake_binary = tmp_path / "mymcp-linux-amd64"
    fake_binary.write_bytes(b"\x7fELF-fake-go-binary")

    out_dir = tmp_path / "out"
    result = subprocess.run(
        [
            sys.executable,
            str(ASSEMBLE),
            str(pure),
            str(fake_binary),
            "manylinux2014_x86_64",
            str(out_dir),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    produced = Path(result.stdout.strip())
    assert produced.name.endswith("-py3-none-manylinux2014_x86_64.whl")

    z = zipfile.ZipFile(produced)
    names = z.namelist()

    # Binary injected as the mymcp script, bytes preserved.
    binpath = [n for n in names if n.endswith(".data/scripts/mymcp")]
    assert len(binpath) == 1, names
    assert z.read(binpath[0]) == b"\x7fELF-fake-go-binary"

    # Python `mymcp` console entry dropped; mymcp-recorder kept.
    ep = z.read([n for n in names if n.endswith("entry_points.txt")][0]).decode()
    assert "mymcp-recorder = mymcp.recorder.__main__:main" in ep
    assert "mymcp = mymcp.cli:main" not in ep

    # Wheel marked non-pure + platform-tagged.
    wheel_meta = z.read([n for n in names if n.endswith("/WHEEL")][0]).decode()
    assert "Root-Is-Purelib: false" in wheel_meta
    assert "Tag: py3-none-manylinux2014_x86_64" in wheel_meta
