#!/usr/bin/env python3
"""Assemble a v3 platform wheel from the pure wheel + a Go binary.

The v3 `mymcp` command IS the Go core binary, not a Python console script. This
takes the ordinary pure-Python wheel that `python -m build` produces and:

  1. drops the ``mymcp = mymcp.cli:main`` console entry (keeps ``mymcp-recorder``),
  2. injects the Go binary at ``<name>-<ver>.data/scripts/mymcp`` (0755), so pip
     installs it as the ``mymcp`` command,
  3. marks the wheel non-pure and re-tags it for the target platform,

then repacks (regenerating RECORD + the tagged filename). The recorder's Python
package and its ``[recorder]`` extra ride along unchanged.

Usage:
    assemble_wheel.py <pure_wheel> <go_binary> <platform_tag> <out_dir>

e.g. assemble_wheel.py dist/algony_mymcp-3.0.0-py3-none-any.whl \\
        mymcp-linux-amd64 manylinux2014_x86_64 dist/
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def assemble(pure_wheel: Path, go_binary: Path, platform_tag: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / "_assemble"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    subprocess.run(
        [sys.executable, "-m", "wheel", "unpack", str(pure_wheel), "-d", str(work)],
        check=True,
        capture_output=True,
    )
    unpacked = next(p for p in work.iterdir() if p.is_dir())
    distinfo = next(unpacked.glob("*.dist-info"))

    # 1. drop the Python `mymcp` console script; keep everything else (mymcp-recorder).
    ep = distinfo / "entry_points.txt"
    kept = [line for line in ep.read_text().splitlines() if line.split("=")[0].strip() != "mymcp"]
    ep.write_text("\n".join(kept) + "\n")

    # 2. inject the Go binary as the `mymcp` script.
    scripts_dir = unpacked / f"{unpacked.name}.data" / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    dest = scripts_dir / "mymcp"
    shutil.copy2(go_binary, dest)
    dest.chmod(0o755)

    # 3. non-pure + platform tag.
    wheelmeta = distinfo / "WHEEL"
    out_lines = []
    for line in wheelmeta.read_text().splitlines():
        if line.startswith("Root-Is-Purelib:"):
            line = "Root-Is-Purelib: false"
        elif line.startswith("Tag:"):
            line = f"Tag: py3-none-{platform_tag}"
        out_lines.append(line)
    wheelmeta.write_text("\n".join(out_lines) + "\n")

    # 4. repack — regenerates RECORD and names the file from the WHEEL Tag.
    subprocess.run(
        [sys.executable, "-m", "wheel", "pack", str(unpacked), "--dest-dir", str(out_dir)],
        check=True,
        capture_output=True,
    )
    shutil.rmtree(work)

    tagged = list(out_dir.glob(f"*-py3-none-{platform_tag}.whl"))
    if not tagged:
        raise SystemExit(f"assemble_wheel: no wheel tagged {platform_tag} produced in {out_dir}")
    return tagged[0]


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    pure_wheel, go_binary, platform_tag, out_dir = argv
    result = assemble(Path(pure_wheel), Path(go_binary), platform_tag, Path(out_dir))
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
