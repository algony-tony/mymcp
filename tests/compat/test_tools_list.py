"""tools/list: the three read tools must be present with byte-identical schemas."""

import pytest

from mymcp.tool_definitions import TOOL_DEFS

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
    assert got.description == golden.description
    assert got.inputSchema == golden.inputSchema


@pytest.mark.anyio
async def test_ro_token_read_set(ro):
    names = {t.name for t in await ro.list_tools()}
    assert set(READ_TOOLS) <= names
    assert not (set(WRITE_TOOLS) & names), "ro must not see write tools"
