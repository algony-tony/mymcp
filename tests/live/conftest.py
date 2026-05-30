"""Conftest for live LLM tests.

Auto-loads tests/live/.env.live (gitignored) at session start. If the file
or the API key is missing, all tests in this directory are skipped.
"""

import os
from pathlib import Path

import pytest

ENV_FILE = Path(__file__).parent / ".env.live"


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def pytest_collection_modifyitems(config, items):
    """Skip live tests if the API key isn't configured."""
    env = _parse_env_file(ENV_FILE)
    for k, v in env.items():
        os.environ.setdefault(k, v)
    have_key = bool(os.environ.get("MYMCP_RECORDER_LIVE_TEST_API_KEY"))
    if not have_key:
        skip = pytest.mark.skip(
            reason=(
                "tests/live/.env.live missing or has no MYMCP_RECORDER_LIVE_TEST_API_KEY; "
                "copy tests/live/.env.live.example and fill in a DeepSeek key."
            )
        )
        for item in items:
            if "live" in item.keywords:
                item.add_marker(skip)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def live_client():
    """Build an LLMClient from env (provider/key/model/base_url)."""
    from mymcp.recorder.llm.factory import build_llm_client

    provider = os.environ["MYMCP_RECORDER_LIVE_TEST_PROVIDER"]
    return build_llm_client(
        provider=provider,  # type: ignore[arg-type]
        api_key=os.environ["MYMCP_RECORDER_LIVE_TEST_API_KEY"],
        model=os.environ.get("MYMCP_RECORDER_LIVE_TEST_MODEL"),
        base_url=os.environ.get("MYMCP_RECORDER_LIVE_TEST_BASE_URL"),
    )
