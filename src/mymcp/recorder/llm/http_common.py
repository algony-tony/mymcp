"""Shared HTTP plumbing for the direct LLM clients.

The recorder issues one non-streaming JSON POST per merge cycle. httpx
(already a core dependency) covers this natively — the vendor SDKs cost
~13 MB RSS for that single call. No client-level retries: recorder
resilience lives at the cycle level (circuit breaker, event-driven retry).
"""

import httpx

# Merge calls can legitimately take minutes on slow providers; match the
# SDKs' generous read timeout rather than httpx's 5s default.
LLM_TIMEOUT = httpx.Timeout(connect=5.0, read=600.0, write=30.0, pool=5.0)


async def post_json(client: httpx.AsyncClient, url: str, payload: dict) -> dict:
    """POST payload as JSON, raise on non-2xx, return the parsed body."""
    resp = await client.post(url, json=payload)
    resp.raise_for_status()
    return resp.json()
