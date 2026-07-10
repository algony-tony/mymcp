import pytest


@pytest.mark.anyio
async def test_bash_basic(rw):
    res = await rw.call("bash_execute", {"command": "printf hi"})
    assert res["stdout"] == "hi"
    assert res["exit_code"] == 0
    assert res["timed_out"] is False


@pytest.mark.anyio
async def test_bash_nonzero_exit(rw):
    res = await rw.call("bash_execute", {"command": "exit 3"})
    assert res["exit_code"] == 3
    assert res["timed_out"] is False


@pytest.mark.anyio
async def test_bash_timeout(rw):
    res = await rw.call("bash_execute", {"command": "sleep 5", "timeout": 1})
    assert res["timed_out"] is True
    assert res["exit_code"] == -1
    assert res["stderr"] == "Command timed out after 1s"


@pytest.mark.anyio
async def test_bash_truncation(rw):
    res = await rw.call("bash_execute", {"command": "printf 'aaaaaaaaaa'", "max_output_bytes": 4})
    assert res["stdout"].startswith("aaaa\n[TRUNCATED: total 10 bytes, showing first 4 bytes]")


@pytest.mark.anyio
async def test_ro_cannot_bash(ro):
    res = await ro.call("bash_execute", {"command": "id"})
    assert res["error"] == "PermissionDenied"
