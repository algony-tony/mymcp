"""tools/list: the three read tools must be present with byte-identical schemas."""

import pytest

from mymcp.tool_definitions import TOOL_DEFS

M1_TOOLS = ("read_file", "glob", "grep")


@pytest.mark.anyio
@pytest.mark.parametrize("name", M1_TOOLS)
async def test_tool_present_with_exact_schema(rw, name):
    tools = {t.name: t for t in await rw.list_tools()}
    assert name in tools, f"{name} missing from tools/list"
    golden = TOOL_DEFS[name]
    got = tools[name]
    assert got.description == golden.description
    assert got.inputSchema == golden.inputSchema


@pytest.mark.anyio
async def test_ro_token_sees_read_tools(ro):
    names = {t.name for t in await ro.list_tools()}
    assert set(M1_TOOLS) <= names
