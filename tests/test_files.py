from unittest.mock import patch

import pytest

from mymcp.tools.files import edit_file, glob_files, grep_files, read_file, write_file

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
async def test_read_file_default_limit_reads_current_settings(tmp_path):
    """Default for `limit` must be resolved at call time, not at import.

    Regression: previously `limit: int = config.READ_FILE_DEFAULT_LIMIT` was
    evaluated once at module import and captured in `__defaults__`, so
    patching the setting had no effect when the test called read_file()
    without an explicit limit kwarg.
    """
    f = tmp_path / "many.txt"
    f.write_text("\n".join(f"line{i}" for i in range(50)) + "\n")
    with patch("mymcp.config.READ_FILE_DEFAULT_LIMIT", 5):
        result = await read_file(str(f))  # no limit kwarg
    # File has 50 lines; with default=5 only the first 5 must come back.
    assert "line0" in result["content"]
    assert "line4" in result["content"]
    assert "line5" not in result["content"]
    assert result["truncated"] is True
    assert result["total_lines"] == 50


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
    from mymcp import config

    big = "x" * (config.WRITE_FILE_MAX_BYTES + 1)
    result = await write_file("/tmp/toobig.txt", big)
    assert result["success"] is False
    assert result["error"] == "FileTooLarge"
    assert str(config.WRITE_FILE_MAX_BYTES + 1) in result["message"]
    assert str(config.WRITE_FILE_MAX_BYTES) in result["message"]


@pytest.mark.anyio
async def test_write_file_exactly_at_max_succeeds(tmp_path):
    """Content at exactly WRITE_FILE_MAX_BYTES should be accepted (<=, not <)."""
    with patch("mymcp.config.WRITE_FILE_MAX_BYTES", 100):
        result = await write_file(str(tmp_path / "exact.txt"), "x" * 100)
        assert result["success"] is True
        assert result["bytes_written"] == 100


@pytest.mark.anyio
async def test_write_file_one_over_max_rejected(tmp_path):
    """Content at MAX+1 must be rejected — kills off-by-one on > / >=."""
    with patch("mymcp.config.WRITE_FILE_MAX_BYTES", 100):
        result = await write_file(str(tmp_path / "big.txt"), "x" * 101)
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
    assert "3 times" in result["message"]
    assert "replace_all" in result["message"]


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
    with patch("mymcp.config.EDIT_STRING_MAX_BYTES", 10):
        result = await edit_file(str(f), "x" * 20, "new")
    assert result["success"] is False
    assert result["error"] == "FileTooLarge"
    assert "old_string" in result["message"]


@pytest.mark.anyio
async def test_edit_file_old_string_one_over_max_rejected(tmp_path):
    """old_string at EDIT_STRING_MAX_BYTES+1 must be rejected (off-by-one)."""
    f = tmp_path / "file.txt"
    f.write_text("x" * 11)
    with patch("mymcp.config.EDIT_STRING_MAX_BYTES", 10):
        result = await edit_file(str(f), "x" * 11, "new")
        assert result["success"] is False
        assert result["error"] == "FileTooLarge"


@pytest.mark.anyio
async def test_edit_file_new_string_one_over_max_rejected(tmp_path):
    """new_string at EDIT_STRING_MAX_BYTES+1 must be rejected (off-by-one)."""
    f = tmp_path / "file.txt"
    f.write_text("hello")
    with patch("mymcp.config.EDIT_STRING_MAX_BYTES", 10):
        result = await edit_file(str(f), "hello", "x" * 11)
        assert result["success"] is False
        assert result["error"] == "FileTooLarge"
        assert "new_string" in result["message"]


@pytest.mark.anyio
async def test_edit_file_new_string_too_large(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("hello")
    with patch("mymcp.config.EDIT_STRING_MAX_BYTES", 10):
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


# ---------------------------------------------------------------------------
# glob_files — exception path
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_glob_exception_returns_error(tmp_path):
    """glob_files should catch exceptions and return error dict."""
    with patch("mymcp.tools.files._glob_module.glob", side_effect=OSError("disk error")):
        result = await glob_files("*.py", path=str(tmp_path))
    assert result["success"] is False
    assert result["error"] == "OSError"
    assert "disk error" in result["message"]


# ---------------------------------------------------------------------------
# _grep_python fallback — full coverage
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_grep_python_invalid_regex(tmp_path):
    """Invalid regex should return error."""
    (tmp_path / "f.txt").write_text("data\n")
    with patch("shutil.which", return_value=None):
        result = await grep_files("[invalid", path=str(tmp_path))
    assert result["success"] is False
    assert result["error"] == "InvalidRegex"


@pytest.mark.anyio
async def test_grep_python_single_file(tmp_path):
    """When path is a file, search that single file."""
    f = tmp_path / "single.txt"
    f.write_text("hello world\nfoo bar\nhello again\n")
    with patch("shutil.which", return_value=None):
        result = await grep_files("hello", path=str(f))
    assert result["match_count"] == 2


@pytest.mark.anyio
async def test_grep_python_files_mode(tmp_path):
    """Python fallback files output_mode."""
    (tmp_path / "a.txt").write_text("match here\n")
    (tmp_path / "b.txt").write_text("nothing\n")
    with patch("shutil.which", return_value=None):
        result = await grep_files("match", path=str(tmp_path), output_mode="files")
    assert any("a.txt" in line for line in result["results"].splitlines())
    assert not any("b.txt" in line for line in result["results"].splitlines())


@pytest.mark.anyio
async def test_grep_python_count_mode(tmp_path):
    """Python fallback count output_mode."""
    (tmp_path / "data.txt").write_text("apple\nbanana\napple pie\n")
    with patch("shutil.which", return_value=None):
        result = await grep_files("apple", path=str(tmp_path), output_mode="count")
    assert result["match_count"] >= 1
    assert "2" in result["results"]


@pytest.mark.anyio
async def test_grep_python_glob_filter(tmp_path):
    """Python fallback glob filter."""
    (tmp_path / "a.log").write_text("target\n")
    (tmp_path / "b.txt").write_text("target\n")
    with patch("shutil.which", return_value=None):
        result = await grep_files("target", path=str(tmp_path), glob="*.log")
    assert any("a.log" in line for line in result["results"].splitlines())
    assert not any("b.txt" in line for line in result["results"].splitlines())


@pytest.mark.anyio
async def test_grep_python_permission_error_skipped(tmp_path):
    """Python fallback should skip files with permission errors."""
    ok = tmp_path / "ok.txt"
    ok.write_text("findme\n")
    noperm = tmp_path / "noperm.txt"
    noperm.write_text("findme\n")
    noperm.chmod(0o000)
    try:
        with patch("shutil.which", return_value=None):
            result = await grep_files("findme", path=str(tmp_path))
        assert result["match_count"] >= 1
        assert "ok.txt" in result["results"]
    finally:
        noperm.chmod(0o644)


@pytest.mark.anyio
async def test_grep_python_case_insensitive(tmp_path):
    """Python fallback case-insensitive search."""
    (tmp_path / "f.txt").write_text("ERROR found\n")
    with patch("shutil.which", return_value=None):
        result = await grep_files("error", path=str(tmp_path), case_insensitive=True)
    assert result["match_count"] >= 1


@pytest.mark.anyio
async def test_grep_python_truncates(tmp_path):
    """Python fallback truncation."""
    f = tmp_path / "big.txt"
    f.write_text("\n".join(f"match line {i}" for i in range(100)))
    with patch("shutil.which", return_value=None):
        result = await grep_files("match", path=str(tmp_path), max_results=5)
    assert "[TRUNCATED" in result["results"]


# ---------------------------------------------------------------------------
# _grep_rg — explicit tests (only run if rg is available)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_grep_rg_files_mode(tmp_path):
    """ripgrep files output_mode."""
    import shutil

    if not shutil.which("rg"):
        pytest.skip("ripgrep not installed")
    (tmp_path / "a.txt").write_text("target line\n")
    (tmp_path / "b.txt").write_text("nothing\n")
    result = await grep_files("target", path=str(tmp_path), output_mode="files")
    assert any("a.txt" in line for line in result["results"].splitlines())


@pytest.mark.anyio
async def test_grep_rg_count_mode(tmp_path):
    """ripgrep count output_mode."""
    import shutil

    if not shutil.which("rg"):
        pytest.skip("ripgrep not installed")
    (tmp_path / "data.txt").write_text("apple\nbanana\napple pie\n")
    result = await grep_files("apple", path=str(tmp_path), output_mode="count")
    assert result["match_count"] >= 1


@pytest.mark.anyio
async def test_grep_rg_context_lines(tmp_path):
    """ripgrep with context lines."""
    import shutil

    if not shutil.which("rg"):
        pytest.skip("ripgrep not installed")
    f = tmp_path / "ctx.txt"
    f.write_text("aaa\nbbb\nccc\nddd\neee\n")
    result = await grep_files("ccc", path=str(tmp_path), context_lines=1)
    assert "ccc" in result["results"]


@pytest.mark.anyio
async def test_grep_rg_timeout(tmp_path):
    """ripgrep timeout should return error."""
    import shutil

    if not shutil.which("rg"):
        pytest.skip("ripgrep not installed")
    with patch("asyncio.wait_for", side_effect=TimeoutError()):
        result = await grep_files("pattern", path=str(tmp_path))
    assert result["success"] is False
    assert result["error"] == "TimeoutError"


# ---------------------------------------------------------------------------
# Mutation killers — boundary / format / default-value tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_read_file_content_line_format(tmp_path):
    """Each output line is exactly '%4d\\t<content>' — pin the format string."""
    f = tmp_path / "fmt.txt"
    f.write_text("alpha\nbeta\n")
    result = await read_file(str(f))
    lines = result["content"].split("\n")
    # Right-aligned 4-wide, then tab, then content
    assert lines[0] == "   1\talpha"
    assert lines[1] == "   2\tbeta"


@pytest.mark.anyio
async def test_read_file_offset_zero_treated_as_one(tmp_path):
    """offset=0 must be raised to 1 (kills `max(1, offset)` → `max(0, offset)`)."""
    f = tmp_path / "f.txt"
    f.write_text("a\nb\nc\n")
    result = await read_file(str(f), offset=0)
    assert result["content"].startswith("   1\ta")


@pytest.mark.anyio
async def test_read_file_limit_zero_treated_as_one(tmp_path):
    """limit=0 must be raised to 1; returns exactly one line."""
    f = tmp_path / "f.txt"
    f.write_text("a\nb\nc\n")
    result = await read_file(str(f), limit=0)
    assert result["content"] == "   1\ta"


@pytest.mark.anyio
async def test_read_file_truncated_boundary(tmp_path):
    """truncated is True iff (offset-1+limit) < total_lines.

    Exactly reading to the last line must have truncated=False.
    """
    f = tmp_path / "f.txt"
    f.write_text("a\nb\nc\n")  # 3 lines
    # offset=1, limit=3 → covers all → not truncated
    full = await read_file(str(f), offset=1, limit=3)
    assert full["truncated"] is False
    # offset=1, limit=2 → 2 lines remain → truncated
    partial = await read_file(str(f), offset=1, limit=2)
    assert partial["truncated"] is True


@pytest.mark.anyio
async def test_read_file_missing_includes_path_and_suggestion(tmp_path):
    """FileNotFoundError response must include path + a suggestion field."""
    bad = str(tmp_path / "nope.txt")
    result = await read_file(bad)
    assert result["success"] is False
    assert result["error"] == "FileNotFoundError"
    assert bad in result["message"]
    assert "suggestion" in result


@pytest.mark.anyio
async def test_read_file_is_a_directory(tmp_path):
    """Reading a directory returns IsADirectoryError with the path."""
    result = await read_file(str(tmp_path))
    assert result["success"] is False
    assert result["error"] == "IsADirectoryError"
    assert str(tmp_path) in result["message"]


@pytest.mark.anyio
async def test_write_file_bytes_written_counts_utf8_bytes(tmp_path):
    """bytes_written must reflect UTF-8 byte length, not character count."""
    f = tmp_path / "u.txt"
    # 中 = 3 bytes UTF-8
    result = await write_file(str(f), "中")
    assert result["success"] is True
    assert result["bytes_written"] == 3


@pytest.mark.anyio
async def test_write_file_too_large_returns_error(tmp_path):
    """Content over WRITE_FILE_MAX_BYTES returns FileTooLarge with exact byte counts."""
    from mymcp import config

    f = tmp_path / "huge.txt"
    big = "x" * (config.WRITE_FILE_MAX_BYTES + 1)
    result = await write_file(str(f), big)
    assert result["success"] is False
    assert result["error"] == "FileTooLarge"
    assert str(len(big)) in result["message"]
    assert "suggestion" in result


@pytest.mark.anyio
async def test_edit_file_replacements_count(tmp_path):
    """replace_all=True returns exact replacements count."""
    f = tmp_path / "e.txt"
    f.write_text("a a a a a")
    result = await edit_file(str(f), "a", "b", replace_all=True)
    assert result["success"] is True
    assert result["replacements"] == 5
    assert f.read_text() == "b b b b b"


@pytest.mark.anyio
async def test_edit_file_single_replacement_when_unique(tmp_path):
    """Unique old_string with replace_all=False → replacements=1."""
    f = tmp_path / "e.txt"
    f.write_text("the only one")
    result = await edit_file(str(f), "only", "best")
    assert result["success"] is True
    assert result["replacements"] == 1
    assert f.read_text() == "the best one"


@pytest.mark.anyio
async def test_edit_file_ambiguous_message_has_count(tmp_path):
    """AmbiguousMatch message includes the exact match count."""
    f = tmp_path / "e.txt"
    f.write_text("x x x")
    result = await edit_file(str(f), "x", "y")
    assert result["error"] == "AmbiguousMatch"
    assert "3" in result["message"]
    assert "replace_all" in result["message"]


@pytest.mark.anyio
async def test_edit_file_string_not_found_returns_specific_error(tmp_path):
    f = tmp_path / "e.txt"
    f.write_text("hello")
    result = await edit_file(str(f), "missing", "x")
    assert result["success"] is False
    assert result["error"] == "StringNotFound"


@pytest.mark.anyio
async def test_glob_files_sorted_by_mtime_descending(tmp_path):
    """glob must sort newest mtime first."""
    import os
    import time

    old = tmp_path / "old.txt"
    new = tmp_path / "new.txt"
    old.write_text("o")
    time.sleep(0.05)
    new.write_text("n")
    # Force mtime ordering deterministically
    os.utime(str(old), (1000, 1000))
    os.utime(str(new), (2000, 2000))

    result = await glob_files("*.txt", path=str(tmp_path))
    assert result["files"][0].endswith("new.txt")
    assert result["files"][1].endswith("old.txt")


@pytest.mark.anyio
async def test_glob_truncated_field(tmp_path):
    """When matches > GLOB_MAX_RESULTS, truncated=True and files cap at the limit."""
    from mymcp import config

    n = config.GLOB_MAX_RESULTS + 5
    for i in range(n):
        (tmp_path / f"f{i}.txt").write_text("")
    result = await glob_files("*.txt", path=str(tmp_path))
    assert result["count"] == n
    assert result["truncated"] is True
    assert len(result["files"]) == config.GLOB_MAX_RESULTS


@pytest.mark.anyio
async def test_grep_python_output_mode_files(tmp_path):
    """output_mode='files' returns filename-only matches (no line numbers)."""
    (tmp_path / "a.txt").write_text("hello\n")
    (tmp_path / "b.txt").write_text("goodbye\n")
    with patch("shutil.which", return_value=None):  # force python fallback
        result = await grep_files("hello", path=str(tmp_path), output_mode="files")
    assert result["match_count"] == 1
    # files mode → no ':lineno:' separator
    assert ":1:" not in result["results"]
    assert "a.txt" in result["results"]
    assert "b.txt" not in result["results"]


@pytest.mark.anyio
async def test_grep_python_output_mode_count(tmp_path):
    """output_mode='count' returns 'path: N' format with exact match counts."""
    (tmp_path / "a.txt").write_text("x\nx\nx\n")
    with patch("shutil.which", return_value=None):
        result = await grep_files("x", path=str(tmp_path), output_mode="count")
    assert ": 3" in result["results"]


@pytest.mark.anyio
async def test_grep_python_case_sensitive_vs_insensitive(tmp_path):
    (tmp_path / "a.txt").write_text("HELLO world\n")
    with patch("shutil.which", return_value=None):
        sensitive = await grep_files("hello", path=str(tmp_path))
        insensitive = await grep_files("hello", path=str(tmp_path), case_insensitive=True)
    assert sensitive["match_count"] == 0
    assert insensitive["match_count"] == 1


@pytest.mark.anyio
async def test_grep_python_invalid_regex_returns_specific_error(tmp_path):
    with patch("shutil.which", return_value=None):
        result = await grep_files("[unclosed", path=str(tmp_path))
    assert result["success"] is False
    assert result["error"] == "InvalidRegex"


@pytest.mark.anyio
async def test_grep_truncation_message_has_extra_count(tmp_path):
    """When matches > max_results, TRUNCATED message reports `total - max_results`."""
    f = tmp_path / "many.txt"
    f.write_text("\n".join(["hit"] * 10))
    with patch("shutil.which", return_value=None):
        result = await grep_files("hit", path=str(f), max_results=3)
    assert result["match_count"] == 10
    assert "[TRUNCATED: 7 more matches not shown]" in result["results"]
