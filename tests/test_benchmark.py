"""Performance benchmark tests using pytest-benchmark.

Run: pytest tests/test_benchmark.py --benchmark-only -v
Save baseline: pytest tests/test_benchmark.py --benchmark-save=baseline
Compare: pytest tests/test_benchmark.py --benchmark-compare=baseline
"""

import asyncio
import os
import pytest

from mymcp.tools.files import read_file, write_file, edit_file, glob_files, grep_files
from mymcp.tools.bash import run_bash_execute


def _run(coro):
    """Helper to run async functions in sync benchmark context."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


@pytest.fixture
def small_file(tmp_path):
    f = tmp_path / "small.txt"
    f.write_text("\n".join(f"line {i}" for i in range(10)))
    return str(f)


@pytest.fixture
def medium_file(tmp_path):
    f = tmp_path / "medium.txt"
    f.write_text("\n".join(f"line {i} with some content here" for i in range(1000)))
    return str(f)


@pytest.fixture
def large_file(tmp_path):
    f = tmp_path / "large.txt"
    f.write_text("\n".join(f"line {i} with more content for searching" for i in range(5000)))
    return str(f)


@pytest.fixture
def many_files(tmp_path):
    """Create a directory with many small files."""
    for i in range(100):
        (tmp_path / f"file_{i:03d}.txt").write_text(f"content of file {i}\nsearchable line\n")
    sub = tmp_path / "subdir"
    sub.mkdir()
    for i in range(50):
        (sub / f"nested_{i:03d}.py").write_text(f"# python file {i}\ndef func_{i}(): pass\n")
    return str(tmp_path)


# ---------------------------------------------------------------------------
# read_file benchmarks
# ---------------------------------------------------------------------------

@pytest.mark.benchmark(group="read_file")
def test_bench_read_small(benchmark, small_file):
    benchmark.pedantic(lambda: _run(read_file(small_file)), rounds=20)


@pytest.mark.benchmark(group="read_file")
def test_bench_read_medium(benchmark, medium_file):
    benchmark.pedantic(lambda: _run(read_file(medium_file)), rounds=20)


@pytest.mark.benchmark(group="read_file")
def test_bench_read_large_with_pagination(benchmark, large_file):
    benchmark.pedantic(
        lambda: _run(read_file(large_file, offset=2000, limit=500)),
        rounds=20,
    )


# ---------------------------------------------------------------------------
# write_file benchmarks
# ---------------------------------------------------------------------------

@pytest.mark.benchmark(group="write_file")
def test_bench_write_small(benchmark, tmp_path):
    path = str(tmp_path / "bench_write.txt")
    benchmark.pedantic(lambda: _run(write_file(path, "small content\n")), rounds=20)


@pytest.mark.benchmark(group="write_file")
def test_bench_write_medium(benchmark, tmp_path):
    path = str(tmp_path / "bench_write_med.txt")
    content = "x" * 10000
    benchmark.pedantic(lambda: _run(write_file(path, content)), rounds=20)


# ---------------------------------------------------------------------------
# edit_file benchmarks
# ---------------------------------------------------------------------------

@pytest.mark.benchmark(group="edit_file")
def test_bench_edit_single(benchmark, tmp_path):
    f = tmp_path / "bench_edit.txt"

    def setup():
        f.write_text("old_value is here once")

    def run():
        _run(edit_file(str(f), "old_value", "new_value"))

    benchmark.pedantic(run, setup=setup, rounds=20)


@pytest.mark.benchmark(group="edit_file")
def test_bench_edit_replace_all(benchmark, tmp_path):
    f = tmp_path / "bench_edit_all.txt"

    def setup():
        f.write_text(" ".join(["target"] * 100))

    def run():
        _run(edit_file(str(f), "target", "replaced", replace_all=True))

    benchmark.pedantic(run, setup=setup, rounds=20)


# ---------------------------------------------------------------------------
# glob_files benchmarks
# ---------------------------------------------------------------------------

@pytest.mark.benchmark(group="glob")
def test_bench_glob_few_matches(benchmark, many_files):
    benchmark.pedantic(
        lambda: _run(glob_files("*.py", path=many_files)),
        rounds=10,
    )


@pytest.mark.benchmark(group="glob")
def test_bench_glob_recursive(benchmark, many_files):
    benchmark.pedantic(
        lambda: _run(glob_files("**/*", path=many_files)),
        rounds=10,
    )


# ---------------------------------------------------------------------------
# grep_files benchmarks
# ---------------------------------------------------------------------------

@pytest.mark.benchmark(group="grep")
def test_bench_grep_small_dir(benchmark, many_files):
    benchmark.pedantic(
        lambda: _run(grep_files("searchable", path=many_files)),
        rounds=10,
    )


@pytest.mark.benchmark(group="grep")
def test_bench_grep_with_glob_filter(benchmark, many_files):
    benchmark.pedantic(
        lambda: _run(grep_files("func_", path=many_files, glob="*.py")),
        rounds=10,
    )


# ---------------------------------------------------------------------------
# bash_execute benchmarks
# ---------------------------------------------------------------------------

@pytest.mark.benchmark(group="bash")
def test_bench_bash_echo(benchmark):
    benchmark.pedantic(lambda: _run(run_bash_execute("echo hello")), rounds=20)


@pytest.mark.benchmark(group="bash")
def test_bench_bash_with_output(benchmark, many_files):
    benchmark.pedantic(
        lambda: _run(run_bash_execute(f"ls -la {many_files}")),
        rounds=20,
    )
