import os
import time

import pytest


@pytest.mark.anyio
async def test_recursive_glob_mtime_desc(rw, scratch):
    root = os.path.join(scratch, "globtest")
    os.makedirs(os.path.join(root, "sub"), exist_ok=True)
    older = os.path.join(root, "old.mark")
    newer = os.path.join(root, "sub", "new.mark")
    for p in (older, newer):
        with open(p, "w") as f:
            f.write("x")
    past = time.time() - 3600
    os.utime(older, (past, past))
    res = await rw.call("glob", {"pattern": "**/*.mark", "path": root})
    assert res["count"] == 2
    assert res["truncated"] is False
    assert res["files"][0].endswith("new.mark")
    assert res["files"][1].endswith("old.mark")


@pytest.mark.anyio
async def test_glob_no_match(rw, scratch):
    res = await rw.call("glob", {"pattern": "*.definitely-not-here", "path": scratch})
    assert res["count"] == 0
    assert res["files"] == []
    assert res["truncated"] is False
