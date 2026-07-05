import os

import pytest


@pytest.fixture
def grep_root(scratch):
    root = os.path.join(scratch, "greptest")
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "app.log"), "w") as f:
        f.write("ERROR boom\nok\nerror quiet\n")
    return root


@pytest.mark.anyio
async def test_content_mode(rw, grep_root):
    res = await rw.call("grep", {"pattern": "error", "path": grep_root})
    assert res["match_count"] == 1
    assert "app.log:3:error quiet" in res["results"]


@pytest.mark.anyio
async def test_case_insensitive_and_files_mode(rw, grep_root):
    res = await rw.call(
        "grep",
        {"pattern": "error", "path": grep_root, "case_insensitive": True, "output_mode": "files"},
    )
    assert res["match_count"] == 1
    assert res["results"].endswith("app.log")


@pytest.mark.anyio
async def test_count_mode_loose(rw, grep_root):
    # rg emits "path:2", the fallbacks emit "path: 2" — assert loosely.
    res = await rw.call(
        "grep",
        {"pattern": "error", "path": grep_root, "case_insensitive": True, "output_mode": "count"},
    )
    line = res["results"].strip()
    assert line.replace(" ", "").endswith("app.log:2")


@pytest.mark.anyio
async def test_truncation_marker(rw, scratch):
    root = os.path.join(scratch, "trunctest")
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "many.txt"), "w") as f:
        f.write("hit\n" * 10)
    res = await rw.call("grep", {"pattern": "hit", "path": root, "max_results": 3})
    assert res["match_count"] == 10
    assert "[TRUNCATED: 7 more matches not shown]" in res["results"]
