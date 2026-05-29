import hashlib

from mymcp.audit_output import (
    edit_file_output,
    truncate_bash_output,
    write_file_output,
)


def test_truncate_bash_short_passthrough():
    out = truncate_bash_output(b"hello", head_bytes=10, tail_bytes=10)
    assert out["stdout_head"] == "hello"
    assert out["stdout_tail"] == ""
    assert out["stdout_truncated_bytes"] == 0
    assert out["stdout_sha256"] == hashlib.sha256(b"hello").hexdigest()


def test_truncate_bash_long_keeps_head_and_tail():
    raw = b"a" * 4096 + b"X" * 100 + b"b" * 4096
    out = truncate_bash_output(raw, head_bytes=4096, tail_bytes=4096)
    assert out["stdout_head"].endswith("a")
    assert out["stdout_tail"].startswith("b")
    assert out["stdout_truncated_bytes"] == 100
    assert out["stdout_sha256"] == hashlib.sha256(raw).hexdigest()


def test_truncate_bash_utf8_safe():
    raw = ("数" * 2000).encode("utf-8") + b"X"
    out = truncate_bash_output(raw, head_bytes=4096, tail_bytes=4096)
    # decode must not raise; "数" still appears
    assert "数" in out["stdout_head"]


def test_write_file_output_no_content_leak():
    content = b"secret-key-12345\nline2\n"
    out = write_file_output(path="/tmp/foo", content=content)
    assert out["path"] == "/tmp/foo"
    assert out["size_bytes"] == len(content)
    assert out["sha256"] == hashlib.sha256(content).hexdigest()
    assert out["first_line"] == "secret-key-12345"
    # extra keys (besides first_line) must not echo content
    leak_test = {k: v for k, v in out.items() if k != "first_line"}
    assert "secret-key" not in str(leak_test)


def test_edit_file_output_shape():
    out = edit_file_output(path="/tmp/foo", lines_added=3, lines_removed=1, hunk_count=2)
    assert out == {
        "path": "/tmp/foo",
        "lines_added": 3,
        "lines_removed": 1,
        "hunk_count": 2,
    }
