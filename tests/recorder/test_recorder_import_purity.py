"""Guard: the recorder sidecar must not import the (soon-deleted) Python core.

After v3 the Python package is recorder-only; the Go binary is the server. This
test fails loudly if anyone re-introduces a dependency on server/mcp_server/
tools/transfer/auth/cli, which would break the standalone ``mymcp-recorder``.

Runs in a *fresh* subprocess so sibling tests (which legitimately import the
core) can't pollute this process's ``sys.modules`` and mask a real regression.
"""

import subprocess
import sys

# Executed in a clean interpreter: import every recorder module, then report any
# forbidden core module that got pulled into sys.modules.
_CHECK = r"""
import importlib, pkgutil, sys
import mymcp.recorder

FORBIDDEN = (
    "mymcp.server", "mymcp.mcp_server", "mymcp.cli", "mymcp.tool_definitions",
    "mymcp.auth", "mymcp.audit", "mymcp.tools", "mymcp.transfer",
)

names = ["mymcp.recorder"]
for m in pkgutil.walk_packages(mymcp.recorder.__path__, "mymcp.recorder."):
    names.append(m.name)

for name in names:
    importlib.import_module(name)

loaded = sorted(
    root for root in FORBIDDEN
    for mod in list(sys.modules)
    if mod == root or mod.startswith(root + ".")
)
if loaded:
    print("CORE_LEAK:" + ",".join(loaded))
    sys.exit(1)
print("OK")
"""


def test_recorder_modules_do_not_import_core():
    proc = subprocess.run(
        [sys.executable, "-c", _CHECK],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"recorder pulled forbidden core modules:\nstdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    )
    assert "OK" in proc.stdout
