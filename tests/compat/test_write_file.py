import os

import pytest


@pytest.mark.anyio
async def test_write_creates_and_reports(rw, scratch):
    p = os.path.join(scratch, "w.txt")
    res = await rw.call("write_file", {"file_path": p, "content": "hello\nworld\n"})
    assert res["success"] is True
    assert res["bytes_written"] == 12
    with open(p) as f:
        assert f.read() == "hello\nworld\n"


@pytest.mark.anyio
async def test_write_protected_denied(rw):
    res = await rw.call("write_file", {"file_path": "/tmp/mymcp-compat-protected/x", "content": "no"})
    assert res["success"] is False
    assert res["error"] == "ProtectedPath"


@pytest.mark.anyio
async def test_ro_cannot_write(ro, scratch):
    res = await ro.call("write_file", {"file_path": os.path.join(scratch, "x"), "content": "y"})
    assert res == {
        "success": False,
        "error": "PermissionDenied",
        "message": "Permission denied: tool 'write_file' requires rw role",
    }
