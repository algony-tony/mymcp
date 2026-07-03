import httpx
import pytest

from mymcp.recorder.llm.http_common import LLM_TIMEOUT, post_json


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.anyio
async def test_post_json_returns_parsed_body():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        return httpx.Response(200, json={"ok": True})

    async with _client(handler) as c:
        data = await post_json(c, "https://api.test/v1/x", {"a": 1})
    assert data == {"ok": True}


@pytest.mark.anyio
async def test_post_json_sends_payload_as_json():
    import json

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["content-type"] == "application/json"
        assert json.loads(request.content) == {"a": 1}
        return httpx.Response(200, json={})

    async with _client(handler) as c:
        await post_json(c, "https://api.test/v1/x", {"a": 1})


@pytest.mark.anyio
async def test_post_json_raises_on_non_2xx():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "bad key"})

    async with _client(handler) as c:
        with pytest.raises(httpx.HTTPStatusError):
            await post_json(c, "https://api.test/v1/x", {})


def test_llm_timeout_has_long_read():
    # Merge calls can take minutes on slow providers.
    assert LLM_TIMEOUT.read >= 600
    assert LLM_TIMEOUT.connect <= 10
