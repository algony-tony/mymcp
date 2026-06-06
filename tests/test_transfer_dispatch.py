import json

import pytest

from mymcp.mcp_server import (
    _TOOL_DEFS,
    READ_TOOLS,
    WRITE_TOOLS,
    check_tool_permission,
    dispatch_tool,
)
from mymcp.transfer import reset_ticket_store


@pytest.fixture(autouse=True)
def _reset():
    reset_ticket_store()
    yield
    reset_ticket_store()


def test_transfer_tools_registered():
    assert "prepare_upload" in _TOOL_DEFS
    assert "prepare_download" in _TOOL_DEFS
    assert "prepare_upload" in WRITE_TOOLS
    assert "prepare_download" in READ_TOOLS


def test_prepare_upload_descriptions_bounded():
    """Tool descriptions are loaded into every client session — keep them bounded.

    Cap is set high enough to allow the curl-on-client workflow note that
    LLMs need to use these tools correctly (returning a ticket URL, not
    pulling from the client). It is NOT meant to fit long-form docs.
    """
    assert len(_TOOL_DEFS["prepare_upload"].description) < 400
    assert len(_TOOL_DEFS["prepare_download"].description) < 400


def test_ro_role_cannot_call_prepare_upload():
    err = check_tool_permission("prepare_upload", "ro")
    assert err is not None
    assert "rw" in err


def test_ro_role_can_call_prepare_download():
    assert check_tool_permission("prepare_download", "ro") is None


@pytest.mark.anyio
async def test_dispatch_prepare_upload(tmp_path):
    out = await dispatch_tool(
        "prepare_upload", {"dest_path": str(tmp_path / "x.bin"), "max_bytes": 100}
    )
    data = json.loads(out)
    assert data["success"] is True
    assert data["method"] == "PUT"


@pytest.mark.anyio
async def test_dispatch_prepare_download(tmp_path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"abc")
    out = await dispatch_tool("prepare_download", {"src_path": str(p)})
    data = json.loads(out)
    assert data["success"] is True
    assert data["method"] == "GET"
    assert data["size"] == 3
