import os

import httpx
import pytest

BASE_URL = os.environ.get("MYMCP_COMPAT_URL", "http://127.0.0.1:8765")


def _abs(url: str) -> str:
    return url if url.startswith("http") else BASE_URL + url


@pytest.mark.anyio
async def test_upload_then_download_round_trip(rw, scratch):
    dst = os.path.join(scratch, "up.bin")
    up = await rw.call("prepare_upload", {"dest_path": dst})
    assert up["success"] is True and up["method"] == "PUT"
    r = httpx.put(_abs(up["url"]), content=b"hello-bytes")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert r.json()["bytes_written"] == 11
    with open(dst, "rb") as f:
        assert f.read() == b"hello-bytes"

    dn = await rw.call("prepare_download", {"src_path": dst})
    assert dn["success"] is True and dn["method"] == "GET"
    r = httpx.get(_abs(dn["url"]))
    assert r.status_code == 200 and r.content == b"hello-bytes"


@pytest.mark.anyio
async def test_ticket_single_use(rw, scratch):
    dst = os.path.join(scratch, "once.bin")
    up = await rw.call("prepare_upload", {"dest_path": dst})
    url = _abs(up["url"])
    assert httpx.put(url, content=b"a").status_code == 200
    assert httpx.put(url, content=b"b").status_code in (404, 410)


@pytest.mark.anyio
async def test_upload_size_exceeded(rw, scratch):
    dst = os.path.join(scratch, "big.bin")
    up = await rw.call("prepare_upload", {"dest_path": dst, "max_bytes": 3})
    r = httpx.put(_abs(up["url"]), content=b"toolong")
    assert r.status_code == 413
    assert r.json()["error"] == "size_exceeded"


@pytest.mark.anyio
async def test_ro_cannot_prepare_upload(ro, scratch):
    res = await ro.call("prepare_upload", {"dest_path": os.path.join(scratch, "x")})
    assert res["error"] == "PermissionDenied"


@pytest.mark.anyio
async def test_ro_can_prepare_download(ro, scratch):
    p = os.path.join(scratch, "readable.txt")
    with open(p, "w") as f:
        f.write("hi")
    res = await ro.call("prepare_download", {"src_path": p})
    assert res["success"] is True
