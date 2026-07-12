"""tools/list: every tool must be present with byte-identical schema.

The golden schemas are vendored in ``golden_tools.json`` — a snapshot of the
Python core's ``TOOL_DEFS`` captured when the Go and Python servers were proven
byte-identical (through the compat suite). v3 deleted the Python server, so the
JSON fixture is now the frozen contract the Go server must keep matching.
"""

import json
from pathlib import Path

import pytest

TOOL_DEFS = json.loads((Path(__file__).parent / "golden_tools.json").read_text())

M1_TOOLS = ("read_file", "glob", "grep")
M2_WRITE_TOOLS = ("bash_execute", "write_file", "edit_file")
M3_READ_TOOLS = ("prepare_download", "server_overview")
M3_WRITE_TOOLS = ("prepare_upload",)

ALL_TOOLS = M1_TOOLS + M2_WRITE_TOOLS + M3_READ_TOOLS + M3_WRITE_TOOLS
READ_TOOLS = M1_TOOLS + M3_READ_TOOLS
WRITE_TOOLS = M2_WRITE_TOOLS + M3_WRITE_TOOLS


@pytest.mark.anyio
@pytest.mark.parametrize("name", ALL_TOOLS)
async def test_tool_present_with_exact_schema(rw, name):
    tools = {t.name: t for t in await rw.list_tools()}
    assert name in tools, f"{name} missing from tools/list"
    golden = TOOL_DEFS[name]
    got = tools[name]
    assert got.description == golden["description"]
    assert got.inputSchema == golden["inputSchema"]


@pytest.mark.anyio
async def test_ro_token_read_set(ro):
    names = {t.name for t in await ro.list_tools()}
    assert set(READ_TOOLS) <= names
    assert not (set(WRITE_TOOLS) & names), "ro must not see write tools"
