import os

import pytest


@pytest.mark.anyio
async def test_edit_single(rw, scratch):
    p = os.path.join(scratch, "e.txt")
    with open(p, "w") as f:
        f.write("alpha beta alpha")
    res = await rw.call("edit_file", {"file_path": p, "old_string": "beta", "new_string": "BETA"})
    assert res["success"] is True and res["replacements"] == 1
    with open(p) as f:
        assert f.read() == "alpha BETA alpha"


@pytest.mark.anyio
async def test_edit_ambiguous(rw, scratch):
    p = os.path.join(scratch, "e.txt")
    with open(p, "w") as f:
        f.write("x x x")
    res = await rw.call("edit_file", {"file_path": p, "old_string": "x", "new_string": "y"})
    assert res["success"] is False
    assert res["error"] == "AmbiguousMatch"
    assert res["message"].startswith("old_string appears 3 times")


@pytest.mark.anyio
async def test_edit_replace_all(rw, scratch):
    p = os.path.join(scratch, "e.txt")
    with open(p, "w") as f:
        f.write("x x x")
    res = await rw.call(
        "edit_file", {"file_path": p, "old_string": "x", "new_string": "y", "replace_all": True}
    )
    assert res["success"] is True and res["replacements"] == 3
