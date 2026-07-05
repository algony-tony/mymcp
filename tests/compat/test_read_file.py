import os

import pytest


@pytest.mark.anyio
async def test_basic_line_format(rw, scratch):
    p = os.path.join(scratch, "basic.txt")
    with open(p, "w") as f:
        f.write("alpha\nbeta\n")
    res = await rw.call("read_file", {"file_path": p})
    assert res["content"] == "   1\talpha\n   2\tbeta"
    assert res["total_lines"] == 2
    assert res["truncated"] is False


@pytest.mark.anyio
async def test_offset_limit_truncated(rw, scratch):
    p = os.path.join(scratch, "5lines.txt")
    with open(p, "w") as f:
        f.write("".join(f"l{i}\n" for i in range(1, 6)))
    res = await rw.call("read_file", {"file_path": p, "offset": 2, "limit": 2})
    assert res["content"] == "   2\tl2\n   3\tl3"
    assert res["truncated"] is True


@pytest.mark.anyio
async def test_missing_file_error_shape(rw, scratch):
    res = await rw.call("read_file", {"file_path": os.path.join(scratch, "nope.txt")})
    assert res["success"] is False
    assert res["error"] == "FileNotFoundError"
    assert res["message"].startswith("File not found: ")


@pytest.mark.anyio
async def test_directory_error_shape(rw, scratch):
    res = await rw.call("read_file", {"file_path": scratch})
    assert res["success"] is False
    assert res["error"] == "IsADirectoryError"


@pytest.mark.anyio
async def test_protected_path_denied(rw):
    # CI sets MYMCP_PROTECTED_PATHS to this directory for both servers.
    res = await rw.call("read_file", {"file_path": "/tmp/mymcp-compat-protected/x"})
    assert res["success"] is False
    assert res["error"] == "ProtectedPath"
    assert "protected directory" in res["message"]
