"""Bypass HTTP routes for binary file transfer.

These endpoints authenticate via the URL ticket alone — no Bearer header.
Bytes flow as raw streams; nothing about file content enters the MCP
protocol or LLM context.
"""

from __future__ import annotations

import logging
import os
import tempfile

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, StreamingResponse

from mymcp import config
from mymcp.tools.files import check_protected_path
from mymcp.transfer import get_ticket_store

logger = logging.getLogger("mymcp")


class _SizeExceeded(Exception):
    pass


def _err(status: int, code: str, hint: str) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": code, "hint": hint}, status_code=status
    )


def _disabled_response() -> JSONResponse:
    return _err(404, "transfer_disabled", "File transfer is disabled on this server.")


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def register_transfer_routes(app: FastAPI) -> None:
    @app.put("/files/raw/{ticket_id}")
    async def upload_endpoint(ticket_id: str, request: Request):
        if not config.TRANSFER_ENABLED:
            return _disabled_response()
        store = get_ticket_store()
        ticket = store.lookup(ticket_id)
        if ticket is None:
            raw = store._tickets.get(ticket_id)
            if raw is None:
                return _err(404, "ticket_not_found", "Mint a new ticket.")
            if raw.consumed:
                return _err(410, "ticket_not_found", "Ticket already used.")
            return _err(410, "ticket_expired", "Mint a new ticket.")
        if ticket.op != "upload":
            return _err(405, "wrong_method", "This ticket requires GET.")
        return await _do_upload(ticket, request)

    @app.get("/files/raw/{ticket_id}")
    async def download_endpoint(ticket_id: str, request: Request):
        if not config.TRANSFER_ENABLED:
            return _disabled_response()
        store = get_ticket_store()
        ticket = store.lookup(ticket_id)
        if ticket is None:
            raw = store._tickets.get(ticket_id)
            if raw is None:
                return _err(404, "ticket_not_found", "Mint a new ticket.")
            if raw.consumed:
                return _err(410, "ticket_not_found", "Ticket already used.")
            return _err(410, "ticket_expired", "Mint a new ticket.")
        if ticket.op != "download":
            return _err(405, "wrong_method", "This ticket requires PUT.")
        return await _do_download(ticket, request)


async def _do_upload(ticket, request: Request):
    err = check_protected_path(ticket.path)
    if err:
        return _err(403, "path_protected", err)

    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > ticket.max_bytes:
                return _err(
                    413,
                    "size_exceeded",
                    f"Body exceeds max_bytes={ticket.max_bytes}.",
                )
        except ValueError:
            return _err(400, "bad_content_length", "Content-Length is not an integer.")

    parent = os.path.dirname(ticket.path) or "/"
    try:
        os.makedirs(parent, exist_ok=True)
    except OSError as e:
        return _err(500, "mkdir_failed", str(e))

    fd, tmp_path = tempfile.mkstemp(prefix=".mymcp-upload-", dir=parent)
    written = 0
    try:
        with os.fdopen(fd, "wb") as out:
            async for chunk in request.stream():
                if not chunk:
                    continue
                if written + len(chunk) > ticket.max_bytes:
                    raise _SizeExceeded()
                out.write(chunk)
                written += len(chunk)
        os.replace(tmp_path, ticket.path)
    except _SizeExceeded:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return _err(
            413, "size_exceeded", f"Body exceeds max_bytes={ticket.max_bytes}."
        )
    except Exception as e:  # pragma: no cover - defensive
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        logger.error("upload failed for %s: %s", ticket.path, e)
        return _err(500, "write_failed", str(e))

    get_ticket_store().consume(ticket.ticket_id)
    return JSONResponse(
        {"ok": True, "path": ticket.path, "bytes_written": written}
    )


async def _do_download(ticket, request: Request):
    err = check_protected_path(ticket.path)
    if err:
        return _err(403, "path_protected", err)
    if not os.path.isfile(ticket.path):
        return _err(404, "path_not_found", "Server file no longer exists.")

    size = os.path.getsize(ticket.path)
    filename = os.path.basename(ticket.path)
    ticket_id = ticket.ticket_id
    file_path = ticket.path

    async def iter_file():
        with open(file_path, "rb") as fh:
            while True:
                chunk = fh.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        get_ticket_store().consume(ticket_id)

    headers = {
        "content-length": str(size),
        "content-disposition": f'attachment; filename="{filename}"',
    }
    return StreamingResponse(
        iter_file(), media_type="application/octet-stream", headers=headers
    )
