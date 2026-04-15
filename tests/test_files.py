import os
import stat

import pytest
from unittest.mock import patch

from tools.files import read_file, write_file, edit_file, glob_files, grep_files


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_read_file_basic(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("line one\nline two\nline three\n")
    result = await read_file(str(f))
    assert "   1\tline one" in result["content"]
    assert "   3\tline three" in result["content"]
    assert result["total_lines"] == 3
    assert result["truncated"] is False


@pytest.mark.anyio
async def test_read_file_offset_and_limit(tmp_path):
    f = tmp_path / "big.txt"
    lines = [f"line {i}" for i in range(1, 201)]
    f.write_text("\n".join(lines))
    result = await read_file(str(f), offset=5, limit=3)
    assert "   5\tline 5" in result["content"]
    assert "   6\tline 6" in result["content"]
    assert "   7\tline 7" in result["content"]
    assert "   8\tline 8" not in result["content"]


@pytest.mark.anyio
async def test_read_file_truncated_flag(tmp_path):
    f = tmp_path / "big.txt"
    f.write_text("\n".join(f"line {i}" for i in range(1, 3001)))
    result = await read_file(str(f), limit=2000)
    assert result["total_lines"] == 3000
    assert result["truncated"] is True


@pytest.mark.anyio
async def test_read_file_not_found():
    result = await read_file("/nonexistent_xyz/file.txt")
    assert result["success"] is False
    assert result["error"] == "FileNotFoundError"


@pytest.mark.anyio
async def test_read_file_is_directory(tmp_path):
    result = await read_file(str(tmp_path))
    assert result["success"] is False
    assert result["error"] == "IsADirectoryError"


@pytest.mark.anyio
async def test_read_file_long_line_truncated(tmp_path):
    f = tmp_path / "long.txt"
    f.write_bytes(b"x" * 40000 + b"\n")
    result = await read_file(str(f))
    assert "[LINE TRUNCATED]" in result["content"]


@pytest.mark.anyio
async def test_read_file_permission_denied(tmp_path):
    f = tmp_path / "noperm.txt"
    f.write_text("secret")
    f.chmod(0o000)
    try:
        result = await read_file(str(f))
        assert result["success"] is False
        assert result["error"] == "PermissionError"
    finally:
        f.chmod(0o644)


@pytest.mark.anyio
async def test_read_file_offset_below_one(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("line1\nline2\n")
    result = await read_file(str(f), offset=0)
    assert "   1\tline1" in result["content"]


@pytest.mark.anyio
async def test_read_file_limit_clamped(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("line1\nline2\n")
    result = await read_file(str(f), limit=0)
    assert result["total_lines"] == 2


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_write_file_creates_file(tmp_path):
    path = str(tmp_path / "new.txt")
    result = await write_file(path, "hello world\n")
    assert result["success"] is True
    assert result["bytes_written"] == 12
    assert (tmp_path / "new.txt").read_text() == "hello world\n"


@pytest.mark.anyio
async def test_write_file_overwrites_existing(tmp_path):
    f = tmp_path / "existing.txt"
    f.write_text("old content")
    result = await write_file(str(f), "new content")
    assert result["success"] is True
    assert f.read_text() == "new content"


@pytest.mark.anyio
async def test_write_file_creates_parent_dirs(tmp_path):
    path = str(tmp_path / "deep" / "nested" / "file.txt")
    result = await write_file(path, "data")
    assert result["success"] is True


@pytest.mark.anyio
async def test_write_file_too_large():
    import config
    big = "x" * (config.WRITE_FILE_MAX_BYTES + 1)
    result = await write_file("/tmp/toobig.txt", big)
    assert result["success"] is False
    assert result["error"] == "FileTooLarge"


@pytest.mark.anyio
async def test_write_file_permission_denied(tmp_path):
    d = tmp_path / "readonly_dir"
    d.mkdir()
    d.chmod(0o555)
    try:
        result = await write_file(str(d / "file.txt"), "data")
        assert result["success"] is False
        assert result["error"] == "PermissionError"
    finally:
        d.chmod(0o755)


# ---------------------------------------------------------------------------
# edit_file
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_edit_file_replaces_string(tmp_path):
    f = tmp_path / "code.py"
    f.write_text("def old_name():\n    pass\n")
    result = await edit_file(str(f), "old_name", "new_name")
    assert result["success"] is True
    assert result["replacements"] == 1
    assert "new_name" in f.read_text()


@pytest.mark.anyio
async def test_edit_file_ambiguous_fails(tmp_path):
    f = tmp_path / "dup.txt"
    f.write_text("foo foo foo")
    result = await edit_file(str(f), "foo", "bar")
    assert result["success"] is False
    assert result["error"] == "AmbiguousMatch"


@pytest.mark.anyio
async def test_edit_file_replace_all(tmp_path):
    f = tmp_path / "dup.txt"
    f.write_text("foo foo foo")
    result = await edit_file(str(f), "foo", "bar", replace_all=True)
    assert result["success"] is True
    assert result["replacements"] == 3
    assert f.read_text() == "bar bar bar"


@pytest.mark.anyio
async def test_edit_file_string_not_found(tmp_path):
    f = tmp_path / "code.py"
    f.write_text("hello world")
    result = await edit_file(str(f), "nonexistent_string", "replacement")
    assert result["success"] is False
    assert result["error"] == "StringNotFound"


@pytest.mark.anyio
async def test_edit_file_not_found():
    result = await edit_file("/nonexistent_xyz/file.py", "old", "new")
    assert result["success"] is False
    assert result["error"] == "FileNotFoundError"


@pytest.mark.anyio
async def test_edit_file_old_string_too_large(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("hello")
    with patch("config.EDIT_STRING_MAX_BYTES", 10):
        result = await edit_file(str(f), "x" * 20, "new")
    assert result["success"] is False
    assert result["error"] == "FileTooLarge"
    assert "old_string" in result["message"]


@pytest.mark.anyio
async def test_edit_file_new_string_too_large(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("hello")
    with patch("config.EDIT_STRING_MAX_BYTES", 10):
        result = await edit_file(str(f), "hello", "x" * 20)
    assert result["success"] is False
    assert result["error"] == "FileTooLarge"
    assert "new_string" in result["message"]


@pytest.mark.anyio
async def test_edit_file_read_permission_denied(tmp_path):
    f = tmp_path / "noperm.txt"
    f.write_text("content")
    f.chmod(0o000)
    try:
        result = await edit_file(str(f), "content", "new")
        assert result["success"] is False
        assert result["error"] == "PermissionError"
    finally:
        f.chmod(0o644)


@pytest.mark.anyio
async def test_edit_file_write_permission_denied(tmp_path):
    f = tmp_path / "readonly.txt"
    f.write_text("content")
    f.chmod(0o444)
    try:
        result = await edit_file(str(f), "content", "new")
        assert result["success"] is False
        assert result["error"] == "PermissionError"
    finally:
        f.chmod(0o644)


# ---------------------------------------------------------------------------
# glob
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_glob_finds_files(tmp_path):
    (tmp_path / "a.py").write_text("a")
    (tmp_path / "b.py").write_text("b")
    (tmp_path / "c.txt").write_text("c")
    result = await glob_files("*.py", path=str(tmp_path))
    assert result["count"] >= 2
    assert any(p.endswith("a.py") for p in result["files"])
    assert any(p.endswith("b.py") for p in result["files"])
    assert not any(p.endswith("c.txt") for p in result["files"])


@pytest.mark.anyio
async def test_glob_recursive(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "deep.py").write_text("x")
    result = await glob_files("**/*.py", path=str(tmp_path))
    assert any("deep.py" in p for p in result["files"])


@pytest.mark.anyio
async def test_glob_empty_result(tmp_path):
    result = await glob_files("*.nonexistent", path=str(tmp_path))
    assert result["count"] == 0
    assert result["files"] == []


# ---------------------------------------------------------------------------
# grep
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_grep_content_mode(tmp_path):
    f = tmp_path / "log.txt"
    f.write_text("error: connection failed\ninfo: all good\nerror: timeout\n")
    result = await grep_files("error", path=str(tmp_path))
    assert result["match_count"] == 2
    assert "error: connection failed" in result["results"]
    assert "error: timeout" in result["results"]
    assert "all good" not in result["results"]


@pytest.mark.anyio
async def test_grep_files_mode(tmp_path):
    (tmp_path / "match.txt").write_text("contains error here")
    (tmp_path / "nomatch.txt").write_text("nothing relevant")
    result = await grep_files("error", path=str(tmp_path), output_mode="files")
    assert any("match.txt" in r for r in result["results"].splitlines())
    assert not any("nomatch.txt" in r for r in result["results"].splitlines())


@pytest.mark.anyio
async def test_grep_case_insensitive(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("ERROR found here\n")
    result = await grep_files("error", path=str(tmp_path), case_insensitive=True)
    assert result["match_count"] >= 1


@pytest.mark.anyio
async def test_grep_glob_filter(tmp_path):
    (tmp_path / "a.log").write_text("target line\n")
    (tmp_path / "b.txt").write_text("target line\n")
    result = await grep_files("target", path=str(tmp_path), glob="*.log")
    assert any("a.log" in line for line in result["results"].splitlines())
    assert not any("b.txt" in line for line in result["results"].splitlines())


@pytest.mark.anyio
async def test_grep_truncates_at_max_results(tmp_path):
    f = tmp_path / "big.log"
    f.write_text("\n".join(f"match line {i}" for i in range(300)))
    result = await grep_files("match", path=str(tmp_path), max_results=10)
    assert "[TRUNCATED" in result["results"]
    assert result["match_count"] == 300


@pytest.mark.anyio
async def test_grep_count_mode(tmp_path):
    f = tmp_path / "data.txt"
    f.write_text("apple\nbanana\napple pie\n")
    result = await grep_files("apple", path=str(tmp_path), output_mode="count")
    assert result["match_count"] >= 1
    assert "2" in result["results"]


@pytest.mark.anyio
async def test_grep_context_lines(tmp_path):
    f = tmp_path / "ctx.txt"
    f.write_text("aaa\nbbb\nccc\nddd\neee\n")
    result = await grep_files("ccc", path=str(tmp_path), context_lines=1)
    assert "ccc" in result["results"]
    assert result["match_count"] >= 1


@pytest.mark.anyio
async def test_grep_single_file(tmp_path):
    f = tmp_path / "single.txt"
    f.write_text("needle in haystack\n")
    result = await grep_files("needle", path=str(f))
    assert result["match_count"] >= 1


@pytest.mark.anyio
async def test_grep_no_matches(tmp_path):
    f = tmp_path / "empty_match.txt"
    f.write_text("nothing here\n")
    result = await grep_files("zzz_nonexistent", path=str(tmp_path))
    assert result["match_count"] == 0
