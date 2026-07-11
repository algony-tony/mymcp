"""Black-box compatibility suite. Aim it at a live server:

    MYMCP_COMPAT_URL=http://127.0.0.1:8765 \
    MYMCP_COMPAT_RW_TOKEN=tok_... MYMCP_COMPAT_RO_TOKEN=tok_... \
    MYMCP_COMPAT_TMP=/tmp/compat-scratch \
    pytest tests/compat/ -v

The suite never imports server internals — except tool_definitions, used as
the golden source for schema comparison.
"""

import json
import os

import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

BASE_URL = os.environ.get("MYMCP_COMPAT_URL", "http://127.0.0.1:8765")
RW_TOKEN = os.environ.get("MYMCP_COMPAT_RW_TOKEN", "")
RO_TOKEN = os.environ.get("MYMCP_COMPAT_RO_TOKEN", "")
# Scratch dir that BOTH the test process and the server can read/write.
TMP = os.environ.get("MYMCP_COMPAT_TMP", "/tmp/mymcp-compat")
METRICS_TOKEN = os.environ.get("MYMCP_COMPAT_METRICS_TOKEN", "")
AUDIT_DIR = os.environ.get("MYMCP_COMPAT_AUDIT_DIR", "")
ADMIN_TOKEN = os.environ.get("MYMCP_COMPAT_ADMIN_TOKEN", "")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def scratch(request):
    import shutil

    base = os.path.join(TMP, request.node.name)
    shutil.rmtree(base, ignore_errors=True)
    os.makedirs(base, exist_ok=True)
    yield base
    shutil.rmtree(base, ignore_errors=True)


class Client:
    """One-shot MCP calls over streamable HTTP with a Bearer token."""

    def __init__(self, token: str):
        self.token = token

    async def list_tools(self):
        async with streamablehttp_client(
            f"{BASE_URL}/mcp", headers={"Authorization": f"Bearer {self.token}"}
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return (await session.list_tools()).tools

    async def call(self, name: str, args: dict) -> dict:
        async with streamablehttp_client(
            f"{BASE_URL}/mcp", headers={"Authorization": f"Bearer {self.token}"}
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(name, args)
                assert result.content and result.content[0].type == "text"
                return json.loads(result.content[0].text)


@pytest.fixture
def rw() -> Client:
    if not RW_TOKEN:
        pytest.skip("MYMCP_COMPAT_RW_TOKEN not set")
    return Client(RW_TOKEN)


@pytest.fixture
def ro() -> Client:
    if not RO_TOKEN:
        pytest.skip("MYMCP_COMPAT_RO_TOKEN not set")
    return Client(RO_TOKEN)


@pytest.fixture
def metrics_token() -> str:
    if not METRICS_TOKEN:
        pytest.skip("MYMCP_COMPAT_METRICS_TOKEN not set")
    return METRICS_TOKEN


@pytest.fixture
def audit_dir() -> str:
    if not AUDIT_DIR:
        pytest.skip("MYMCP_COMPAT_AUDIT_DIR not set")
    return AUDIT_DIR


@pytest.fixture
def admin_token() -> str:
    if not ADMIN_TOKEN:
        pytest.skip("MYMCP_COMPAT_ADMIN_TOKEN not set")
    return ADMIN_TOKEN


def pytest_collection_modifyitems(config, items):
    """Skip all compat tests when MYMCP_COMPAT_URL is not explicitly set."""
    if os.environ.get("MYMCP_COMPAT_URL"):
        return  # URL explicitly provided — run the suite
    skip_marker = pytest.mark.skip(
        reason="MYMCP_COMPAT_URL not set; set it to a live server URL to run the compat suite"
    )
    for item in items:
        if "compat" in str(item.fspath):
            item.add_marker(skip_marker)
