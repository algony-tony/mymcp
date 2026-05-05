"""MCP tools that mint signed URLs for binary / large file transfer.

The tools return JSON-ready dicts. Actual byte transfer happens on the
/files/raw/{ticket} endpoint and never enters the LLM context.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from mymcp import config
from mymcp.tools.files import check_protected_path
from mymcp.transfer import get_ticket_store


def _public_base_url() -> str:
    """Return public base URL with no trailing slash. Empty string means use request Host."""
    return str(config.PUBLIC_BASE_URL).rstrip("/")


def _build_url(ticket_id: str) -> str:
    base = _public_base_url()
    if not base:
        # Relative URL. Caller must combine with the server's reachable host.
        # Set MYMCP_PUBLIC_BASE_URL to return absolute URLs (required behind
        # reverse proxies that rewrite Host).
        return f"/files/raw/{ticket_id}"
    return f"{base}/files/raw/{ticket_id}"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: UP017


async def prepare_upload(
    *,
    dest_path: str,
    max_bytes: int | None = None,
    expires_in: int | None = None,
    overwrite: bool = True,
    token_name: str = "unknown",
) -> dict:
    if not config.TRANSFER_ENABLED:
        return {
            "success": False,
            "error": "TransferDisabled",
            "message": "File transfer feature is disabled on this server.",
        }
    if not os.path.isabs(dest_path):
        return {
            "success": False,
            "error": "InvalidPath",
            "message": "dest_path must be an absolute path.",
        }
    err = check_protected_path(dest_path)
    if err:
        return {"success": False, "error": "ProtectedPath", "message": err}
    if not overwrite and os.path.exists(dest_path):
        return {
            "success": False,
            "error": "FileExists",
            "message": f"{dest_path} already exists and overwrite=False.",
        }

    cap = config.TRANSFER_MAX_BYTES
    requested = cap if max_bytes is None else int(max_bytes)
    if requested <= 0:
        return {
            "success": False,
            "error": "InvalidMaxBytes",
            "message": "max_bytes must be positive.",
        }
    effective_max = min(requested, cap)

    ttl_default = config.TRANSFER_DEFAULT_TTL_SEC
    ttl_max = config.TRANSFER_MAX_TTL_SEC
    requested_ttl = ttl_default if expires_in is None else int(expires_in)
    if requested_ttl <= 0:
        return {
            "success": False,
            "error": "InvalidExpiresIn",
            "message": "expires_in must be positive.",
        }
    effective_ttl = min(requested_ttl, ttl_max)

    ticket = get_ticket_store().mint(
        op="upload",
        path=dest_path,
        max_bytes=effective_max,
        ttl_sec=effective_ttl,
        created_by=token_name,
    )
    url = _build_url(ticket.ticket_id)
    return {
        "success": True,
        "url": url,
        "method": "PUT",
        "ticket": ticket.ticket_id,
        "expires_in": effective_ttl,
        "expires_at": _iso(ticket.expires_at),
        "max_bytes": effective_max,
        "dest_path": dest_path,
        "curl_example": f"curl -fsS -T /local/path/to/file '{url}'",
        "instructions": (
            "Run the curl above from the MCP client's local shell. "
            "The file's raw bytes go in the request body. On success the "
            'server returns {"ok": true, "path": "...", "bytes_written": N}.'
        ),
        "on_error": (
            "If the URL returns 4xx, read the JSON error.hint field. "
            "Tickets are single-use; do not retry the same URL — "
            "call prepare_upload again to mint a fresh one."
        ),
    }


async def prepare_download(
    *,
    src_path: str,
    expires_in: int | None = None,
    token_name: str = "unknown",
) -> dict:
    if not config.TRANSFER_ENABLED:
        return {
            "success": False,
            "error": "TransferDisabled",
            "message": "File transfer feature is disabled on this server.",
        }
    if not os.path.isabs(src_path):
        return {
            "success": False,
            "error": "InvalidPath",
            "message": "src_path must be an absolute path.",
        }
    err = check_protected_path(src_path)
    if err:
        return {"success": False, "error": "ProtectedPath", "message": err}
    if not os.path.exists(src_path):
        return {
            "success": False,
            "error": "FileNotFound",
            "message": f"{src_path} does not exist.",
        }
    if not os.path.isfile(src_path):
        return {
            "success": False,
            "error": "NotARegularFile",
            "message": f"{src_path} is not a regular file.",
        }

    ttl_default = config.TRANSFER_DEFAULT_TTL_SEC
    ttl_max = config.TRANSFER_MAX_TTL_SEC
    requested_ttl = ttl_default if expires_in is None else int(expires_in)
    if requested_ttl <= 0:
        return {
            "success": False,
            "error": "InvalidExpiresIn",
            "message": "expires_in must be positive.",
        }
    effective_ttl = min(requested_ttl, ttl_max)

    size = os.path.getsize(src_path)
    ticket = get_ticket_store().mint(
        op="download",
        path=src_path,
        max_bytes=size,
        ttl_sec=effective_ttl,
        created_by=token_name,
    )
    url = _build_url(ticket.ticket_id)
    return {
        "success": True,
        "url": url,
        "method": "GET",
        "ticket": ticket.ticket_id,
        "expires_in": effective_ttl,
        "expires_at": _iso(ticket.expires_at),
        "size": size,
        "src_path": src_path,
        "curl_example": f"curl -fsS '{url}' -o /local/path/{os.path.basename(src_path)}",
        "instructions": (
            "Run the curl above from the MCP client's local shell. "
            "Bytes stream back as the response body."
        ),
        "on_error": (
            "If the URL returns 4xx, read the JSON error.hint field. "
            "Tickets are single-use; mint a new one with prepare_download if needed."
        ),
    }
