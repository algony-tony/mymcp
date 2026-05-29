import pytest

from mymcp.recorder.probes import (
    BASH_PROBE_TOOL,
    READ_FILE_PROBE_TOOL,
    run_bash_probe,
    run_read_file_probe,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_bash_probe_basic():
    out = await run_bash_probe({"command": "echo hello"}, timeout_sec=5)
    assert "hello" in out["stdout_head"]
    assert out["exit_code"] == 0
    assert out["timed_out"] is False


@pytest.mark.anyio
async def test_bash_probe_timeout():
    out = await run_bash_probe({"command": "sleep 5"}, timeout_sec=1)
    assert out["timed_out"] is True


@pytest.mark.anyio
async def test_bash_probe_truncates_long_output():
    out = await run_bash_probe(
        {"command": "yes line | head -c 20000"},
        timeout_sec=5,
        head_bytes=2048,
        tail_bytes=2048,
    )
    assert out["stdout_truncated_bytes"] > 0


@pytest.mark.anyio
async def test_bash_probe_captures_stderr():
    out = await run_bash_probe(
        {"command": "echo ohno >&2"},
        timeout_sec=5,
    )
    assert "ohno" in out.get("stderr_head", "")


@pytest.mark.anyio
async def test_read_file_probe_reads(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("hello\nworld\n")
    out = await run_read_file_probe({"path": str(f)})
    assert "hello" in out["content"]
    assert out["truncated"] is False
    assert out["error"] is None


@pytest.mark.anyio
async def test_read_file_probe_missing(tmp_path):
    out = await run_read_file_probe({"path": str(tmp_path / "nope")})
    assert out["error"] is not None
    assert out["content"] == ""


@pytest.mark.anyio
async def test_read_file_probe_truncates(tmp_path):
    f = tmp_path / "big.txt"
    f.write_text("x" * 30_000)
    out = await run_read_file_probe({"path": str(f)}, max_bytes=16_384)
    assert out["truncated"] is True
    assert len(out["content"]) == 16_384


def test_tool_schemas_have_required_fields():
    for t in (BASH_PROBE_TOOL, READ_FILE_PROBE_TOOL):
        assert t.name
        assert t.description
        assert isinstance(t.input_schema, dict)
        assert t.input_schema.get("type") == "object"
