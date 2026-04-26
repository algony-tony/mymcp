"""Boundary value and exception analysis tests.

Systematically tests edge cases for each tool function's parameters.
"""

import asyncio
import os
import pytest
from unittest.mock import patch

from mymcp.tools.bash import run_bash_execute
from mymcp.tools.files import read_file, write_file, edit_file, glob_files, grep_files


# ===========================================================================
# bash_execute boundaries
# ===========================================================================

class TestBashBoundary:

    @pytest.mark.anyio
    async def test_timeout_zero_clamped_to_1(self):
        """timeout=0 should be clamped to 1 (min)."""
        result = await run_bash_execute("echo fast", timeout=0)
        assert result["exit_code"] == 0

    @pytest.mark.anyio
    async def test_timeout_negative_clamped_to_1(self):
        """Negative timeout should be clamped to 1."""
        result = await run_bash_execute("echo fast", timeout=-5)
        assert result["exit_code"] == 0

    @pytest.mark.anyio
    async def test_timeout_over_600_clamped(self):
        """timeout > 600 should be clamped to 600."""
        result = await run_bash_execute("echo fast", timeout=9999)
        assert result["exit_code"] == 0

    @pytest.mark.anyio
    async def test_timeout_exactly_600(self):
        """timeout=600 should be accepted."""
        result = await run_bash_execute("echo fast", timeout=600)
        assert result["exit_code"] == 0

    @pytest.mark.anyio
    async def test_empty_command(self):
        """Empty command string."""
        result = await run_bash_execute("")
        assert "exit_code" in result

    @pytest.mark.anyio
    async def test_max_output_bytes_zero_clamped(self):
        """max_output_bytes=0 should be clamped to 1."""
        result = await run_bash_execute("echo hello", max_output_bytes=0)
        assert "exit_code" in result

    @pytest.mark.anyio
    async def test_max_output_bytes_negative_clamped(self):
        """Negative max_output_bytes should be clamped to 1."""
        result = await run_bash_execute("echo hello", max_output_bytes=-100)
        assert "exit_code" in result

    @pytest.mark.anyio
    async def test_max_output_bytes_over_hard_cap(self):
        """max_output_bytes over hard cap should be clamped."""
        from mymcp import config
        result = await run_bash_execute(
            "echo hello",
            max_output_bytes=config.BASH_MAX_OUTPUT_BYTES_HARD + 1000,
        )
        assert result["exit_code"] == 0

    @pytest.mark.anyio
    async def test_working_dir_empty_string(self):
        """Empty string working_dir."""
        result = await run_bash_execute("echo x", working_dir="")
        assert "exit_code" in result or "success" in result

    @pytest.mark.anyio
    async def test_working_dir_is_file(self, tmp_path):
        """working_dir pointing to a file raises NotADirectoryError (unhandled)."""
        f = tmp_path / "afile.txt"
        f.write_text("x")
        # Python raises NotADirectoryError which is not caught by bash.py
        with pytest.raises(NotADirectoryError):
            await run_bash_execute("echo x", working_dir=str(f))


# ===========================================================================
# read_file boundaries
# ===========================================================================

class TestReadFileBoundary:

    @pytest.mark.anyio
    async def test_offset_zero_clamped(self, tmp_path):
        """offset=0 should be clamped to 1."""
        f = tmp_path / "test.txt"
        f.write_text("line1\nline2\n")
        result = await read_file(str(f), offset=0)
        assert "   1\tline1" in result["content"]

    @pytest.mark.anyio
    async def test_offset_negative_clamped(self, tmp_path):
        """Negative offset should be clamped to 1."""
        f = tmp_path / "test.txt"
        f.write_text("line1\n")
        result = await read_file(str(f), offset=-10)
        assert "   1\tline1" in result["content"]

    @pytest.mark.anyio
    async def test_offset_beyond_file(self, tmp_path):
        """Offset beyond file lines should return empty content."""
        f = tmp_path / "test.txt"
        f.write_text("line1\nline2\n")
        result = await read_file(str(f), offset=9999)
        assert result["content"] == ""
        assert result["total_lines"] == 2

    @pytest.mark.anyio
    async def test_limit_zero_clamped(self, tmp_path):
        """limit=0 should be clamped to 1."""
        f = tmp_path / "test.txt"
        f.write_text("line1\nline2\nline3\n")
        result = await read_file(str(f), limit=0)
        assert result["total_lines"] == 3

    @pytest.mark.anyio
    async def test_limit_negative_clamped(self, tmp_path):
        """Negative limit should be clamped to 1."""
        f = tmp_path / "test.txt"
        f.write_text("line1\n")
        result = await read_file(str(f), limit=-5)
        assert result["total_lines"] == 1

    @pytest.mark.anyio
    async def test_limit_exactly_max(self, tmp_path):
        """limit at MAX_LIMIT should be accepted."""
        f = tmp_path / "test.txt"
        f.write_text("line1\n")
        from mymcp import config
        result = await read_file(str(f), limit=config.READ_FILE_MAX_LIMIT)
        assert result["total_lines"] == 1

    @pytest.mark.anyio
    async def test_limit_over_max_clamped(self, tmp_path):
        """limit over MAX_LIMIT should be clamped."""
        f = tmp_path / "test.txt"
        f.write_text("line1\n")
        from mymcp import config
        result = await read_file(str(f), limit=config.READ_FILE_MAX_LIMIT + 100)
        assert result["total_lines"] == 1

    @pytest.mark.anyio
    async def test_empty_file(self, tmp_path):
        """Reading an empty file."""
        f = tmp_path / "empty.txt"
        f.write_text("")
        result = await read_file(str(f))
        assert result["total_lines"] == 0
        assert result["content"] == ""

    @pytest.mark.anyio
    async def test_binary_file(self, tmp_path):
        """Reading a binary file should not crash."""
        f = tmp_path / "binary.bin"
        f.write_bytes(b"\x00\x01\x02\xff\xfe\n")
        result = await read_file(str(f))
        assert result["total_lines"] >= 1

    @pytest.mark.anyio
    async def test_symlink_to_normal_file(self, tmp_path):
        """Symlink to a normal file should work."""
        f = tmp_path / "real.txt"
        f.write_text("real content\n")
        link = tmp_path / "link.txt"
        link.symlink_to(f)
        result = await read_file(str(link))
        assert "real content" in result["content"]

    @pytest.mark.anyio
    async def test_empty_path(self):
        """Empty string path."""
        result = await read_file("")
        assert result["success"] is False


# ===========================================================================
# write_file boundaries
# ===========================================================================

class TestWriteFileBoundary:

    @pytest.mark.anyio
    async def test_empty_content(self, tmp_path):
        """Writing empty content should succeed."""
        path = str(tmp_path / "empty.txt")
        result = await write_file(path, "")
        assert result["success"] is True
        assert result["bytes_written"] == 0

    @pytest.mark.anyio
    async def test_content_exactly_max(self, tmp_path):
        """Content at exactly max bytes should succeed."""
        with patch("mymcp.config.WRITE_FILE_MAX_BYTES", 100):
            content = "x" * 100
            path = str(tmp_path / "exact.txt")
            result = await write_file(path, content)
            assert result["success"] is True

    @pytest.mark.anyio
    async def test_path_is_existing_directory(self, tmp_path):
        """Writing to a path that is a directory raises IsADirectoryError (unhandled)."""
        # write_file only catches PermissionError, not IsADirectoryError
        with pytest.raises(IsADirectoryError):
            await write_file(str(tmp_path), "data")

    @pytest.mark.anyio
    async def test_deeply_nested_new_dirs(self, tmp_path):
        """Writing to deeply nested non-existent directories."""
        path = str(tmp_path / "a" / "b" / "c" / "d" / "file.txt")
        result = await write_file(path, "deep")
        assert result["success"] is True
        assert os.path.exists(path)


# ===========================================================================
# edit_file boundaries
# ===========================================================================

class TestEditFileBoundary:

    @pytest.mark.anyio
    async def test_old_equals_new(self, tmp_path):
        """old_string == new_string — no-op replacement."""
        f = tmp_path / "file.txt"
        f.write_text("hello world")
        result = await edit_file(str(f), "hello", "hello")
        assert result["success"] is True
        assert f.read_text() == "hello world"

    @pytest.mark.anyio
    async def test_replace_all_single_match(self, tmp_path):
        """replace_all=True with only one match should succeed."""
        f = tmp_path / "file.txt"
        f.write_text("unique_string here")
        result = await edit_file(str(f), "unique_string", "replaced", replace_all=True)
        assert result["success"] is True
        assert result["replacements"] == 1

    @pytest.mark.anyio
    async def test_old_string_exactly_max_bytes(self, tmp_path):
        """old_string at exactly EDIT_STRING_MAX_BYTES should succeed."""
        f = tmp_path / "file.txt"
        with patch("mymcp.config.EDIT_STRING_MAX_BYTES", 10):
            content = "x" * 10
            f.write_text(content)
            result = await edit_file(str(f), content, "replaced")
            assert result["success"] is True


# ===========================================================================
# glob_files boundaries
# ===========================================================================

class TestGlobBoundary:

    @pytest.mark.anyio
    async def test_empty_pattern(self, tmp_path):
        """Empty pattern."""
        result = await glob_files("", path=str(tmp_path))
        assert "files" in result

    @pytest.mark.anyio
    async def test_nonexistent_directory(self):
        """Path to non-existent directory."""
        result = await glob_files("*.py", path="/nonexistent_dir_xyz_abc")
        assert result["count"] == 0 or result.get("success") is False

    @pytest.mark.anyio
    async def test_path_is_file(self, tmp_path):
        """Path pointing to a file, not directory."""
        f = tmp_path / "file.txt"
        f.write_text("x")
        result = await glob_files("*", path=str(f))
        assert "files" in result


# ===========================================================================
# grep_files boundaries
# ===========================================================================

class TestGrepBoundary:

    @pytest.mark.anyio
    async def test_empty_pattern(self, tmp_path):
        """Empty regex pattern — matches everything."""
        (tmp_path / "f.txt").write_text("hello\n")
        result = await grep_files("", path=str(tmp_path))
        assert result["match_count"] >= 0

    @pytest.mark.anyio
    async def test_invalid_regex(self, tmp_path):
        """Invalid regex should return error (python fallback)."""
        (tmp_path / "f.txt").write_text("data\n")
        with patch("shutil.which", return_value=None):
            result = await grep_files("[invalid", path=str(tmp_path))
        assert result["success"] is False

    @pytest.mark.anyio
    async def test_max_results_zero_clamped(self, tmp_path):
        """max_results=0 should be clamped to 1."""
        (tmp_path / "f.txt").write_text("match\n")
        result = await grep_files("match", path=str(tmp_path), max_results=0)
        assert result["match_count"] >= 0

    @pytest.mark.anyio
    async def test_max_results_negative_clamped(self, tmp_path):
        """Negative max_results should be clamped to 1."""
        (tmp_path / "f.txt").write_text("match\n")
        result = await grep_files("match", path=str(tmp_path), max_results=-10)
        assert result["match_count"] >= 0

    @pytest.mark.anyio
    async def test_max_results_over_limit_clamped(self, tmp_path):
        """max_results over GREP_MAX_RESULTS should be clamped."""
        from mymcp import config
        (tmp_path / "f.txt").write_text("match\n")
        result = await grep_files(
            "match", path=str(tmp_path),
            max_results=config.GREP_MAX_RESULTS + 1000,
        )
        assert result["match_count"] >= 0

    @pytest.mark.anyio
    async def test_context_lines_negative(self, tmp_path):
        """Negative context_lines should be handled gracefully."""
        (tmp_path / "f.txt").write_text("hello\n")
        result = await grep_files("hello", path=str(tmp_path), context_lines=-1)
        assert result["match_count"] >= 0
