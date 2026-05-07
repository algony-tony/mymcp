"""ASGI middleware that ensures every request has an X-Request-ID."""

from __future__ import annotations

import uuid
from contextvars import ContextVar

from starlette.types import ASGIApp, Receive, Scope, Send

current_request_id: ContextVar[str | None] = ContextVar("current_request_id", default=None)

_HEADER_NAME = b"x-request-id"


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        rid = self._extract_or_generate(scope)
        token = current_request_id.set(rid)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((_HEADER_NAME, rid.encode("ascii")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            current_request_id.reset(token)

    @staticmethod
    def _extract_or_generate(scope: Scope) -> str:
        for name, value in scope.get("headers", []):
            if name == _HEADER_NAME:
                try:
                    return value.decode("ascii")
                except UnicodeDecodeError:
                    break
        return str(uuid.uuid4())
