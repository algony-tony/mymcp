import pytest


@pytest.mark.anyio
async def test_server_overview_disabled_shape(rw):
    # Compat CI runs the recorder disabled / no overview.md, so both the Python
    # and Go servers return the RecorderDisabled shape.
    res = await rw.call("server_overview", {})
    assert res["success"] is False
    assert res["error"] == "RecorderDisabled"


@pytest.mark.anyio
async def test_server_overview_visible_to_ro(ro):
    res = await ro.call("server_overview", {})
    assert res["error"] == "RecorderDisabled"
