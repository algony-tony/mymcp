import pytest
import json
from mcp_server import dispatch_tool


@pytest.mark.anyio
async def test_dispatch_bash_execute():
    result = await dispatch_tool("bash_execute", {"command": "echo mcp_test"})
    data = json.loads(result)
    assert "mcp_test" in data["stdout"]
    assert data["exit_code"] == 0


@pytest.mark.anyio
async def test_dispatch_read_file(tmp_path):
    f = tmp_path / "hello.txt"
    f.write_text("hello mcp\n")
    result = await dispatch_tool("read_file", {"file_path": str(f)})
    data = json.loads(result)
    assert "hello mcp" in data["content"]


@pytest.mark.anyio
async def test_dispatch_write_file(tmp_path):
    path = str(tmp_path / "out.txt")
    result = await dispatch_tool("write_file", {"file_path": path, "content": "written"})
    data = json.loads(result)
    assert data["success"] is True
    assert (tmp_path / "out.txt").read_text() == "written"


@pytest.mark.anyio
async def test_dispatch_edit_file(tmp_path):
    f = tmp_path / "edit.txt"
    f.write_text("replace_me")
    result = await dispatch_tool(
        "edit_file",
        {"file_path": str(f), "old_string": "replace_me", "new_string": "replaced"},
    )
    data = json.loads(result)
    assert data["success"] is True


@pytest.mark.anyio
async def test_dispatch_glob(tmp_path):
    (tmp_path / "a.py").write_text("")
    result = await dispatch_tool("glob", {"pattern": "*.py", "path": str(tmp_path)})
    data = json.loads(result)
    assert data["count"] >= 1


@pytest.mark.anyio
async def test_dispatch_grep(tmp_path):
    (tmp_path / "f.txt").write_text("needle in haystack\n")
    result = await dispatch_tool("grep", {"pattern": "needle", "path": str(tmp_path)})
    data = json.loads(result)
    assert data["match_count"] >= 1


@pytest.mark.anyio
async def test_dispatch_unknown_tool():
    result = await dispatch_tool("nonexistent_tool", {})
    data = json.loads(result)
    assert data["success"] is False
    assert data["error"] == "UnknownTool"
