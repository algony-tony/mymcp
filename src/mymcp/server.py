"""FastAPI app factory for mymcp. No module-level side effects."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from mymcp import config, metrics
from mymcp.auth import admin_router, get_store
from mymcp.mcp_server import _current_audit_info, server, session_manager  # noqa: F401


def _validate_token(request: Request) -> tuple[JSONResponse | None, dict | None]:
    """Validate bearer token. Returns (error_response, token_info)."""
    store = get_store()
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return JSONResponse({"detail": "Missing Bearer token"}, status_code=401), None
    token = auth[7:]
    info = store.validate(token)
    if info is None:
        return JSONResponse({"detail": "Invalid or disabled token"}, status_code=401), None
    return None, info


class McpAuthMiddleware:
    """Intercepts /mcp to validate Bearer token, then delegates
    to StreamableHTTPSessionManager as raw ASGI."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "http" and scope.get("path", "") == "/mcp":
            request = Request(scope, receive, send)
            error, token_info = _validate_token(request)
            if error or token_info is None:
                if error:
                    await error(scope, receive, send)
                return

            client = scope.get("client")
            ip = client[0] if client else "unknown"

            cv_token = _current_audit_info.set(
                {
                    "token_name": token_info.get("name", "unknown"),
                    "role": token_info.get("role", "rw"),
                    "ip": ip,
                }
            )
            try:
                await session_manager.handle_request(scope, receive, send)
            finally:
                _current_audit_info.reset(cv_token)
            return
        await self.app(scope, receive, send)


class MetricsMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http" or not metrics.ENABLED:
            await self.app(scope, receive, send)
            return
        status_code = 500

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            metrics.HTTP_REQUESTS.labels(
                path=scope.get("path", ""),
                method=scope.get("method", ""),
                status=str(status_code),
            ).inc()


def create_app() -> FastAPI:
    """Build and return the FastAPI application.

    Side-effect-free: all configuration is read here, not at import time.
    """

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        get_store()
        async with session_manager.run():
            yield

    import mymcp

    app = FastAPI(title="Linux MCP Server", version=mymcp.__version__, lifespan=lifespan)

    app.add_middleware(McpAuthMiddleware)
    app.add_middleware(MetricsMiddleware)

    app.include_router(admin_router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": mymcp.__version__}

    @app.get("/version")
    async def version() -> dict[str, str]:
        return {"version": mymcp.__version__}

    @app.get("/metrics")
    async def get_metrics(request: Request):
        if not metrics.ENABLED:
            return JSONResponse(
                {"detail": "Metrics disabled: prometheus_client not installed"},
                status_code=503,
            )
        if not config.METRICS_TOKEN:
            return JSONResponse(
                {"detail": "Metrics disabled: MYMCP_METRICS_TOKEN not configured"},
                status_code=503,
            )
        auth_header = request.headers.get("authorization", "")
        if auth_header != f"Bearer {config.METRICS_TOKEN}":
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return Response(
            content=metrics.generate_latest(),
            media_type=metrics.CONTENT_TYPE_LATEST,
        )

    return app
