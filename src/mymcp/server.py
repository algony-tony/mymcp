"""FastAPI app factory for mymcp. No module-level side effects."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from mymcp import config
from mymcp.auth import admin_router, get_store
from mymcp.config import get_settings
from mymcp.mcp_server import _current_audit_info, server, session_manager  # noqa: F401
from mymcp.observability import instruments, setup_observability
from mymcp.observability.request_id import RequestIdMiddleware
from mymcp.transfer.endpoints import register_transfer_routes

logger = logging.getLogger("mymcp.server")


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
        if scope["type"] != "http":
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
            instruments.http_requests.add(
                1,
                {
                    "path": _path_label(scope),
                    "method": scope.get("method", ""),
                    "status": str(status_code),
                },
            )


def _path_label(scope: Scope) -> str:
    """Bounded `path` label for HTTP metrics.

    Returns the matched route's template (e.g. ``/files/raw/{ticket_id}``)
    when Starlette has populated ``scope["route"]``. Otherwise — including
    every unmatched 404 — returns the literal sentinel ``<unmatched>``. This
    is what keeps cardinality bounded against scanners hammering distinct
    URLs (``/wp-login.php``, ``/.git/config``, …); using the raw path here
    would create one label value per probe URL.
    """
    route = scope.get("route")
    if route is None:
        return "<unmatched>"
    path = getattr(route, "path", None) or getattr(route, "path_format", None)
    if path:
        return str(path)
    return "<unmatched>"


def create_app() -> FastAPI:
    """Build and return the FastAPI application.

    Side-effect-free: all configuration is read here, not at import time.
    """

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        get_store()
        settings = get_settings()
        recorder_task: asyncio.Task | None = None
        supervisor = None
        if settings.recorder_enabled:
            try:
                from mymcp import mcp_server as _mcp
                from mymcp.recorder import admin as recorder_admin
                from mymcp.recorder.wiring import build_supervisor

                supervisor = build_supervisor(settings)
                recorder_admin.set_supervisor(supervisor)
                _mcp.set_recorder_supervisor(supervisor)
                recorder_task = asyncio.create_task(supervisor.run())
                logger.info("recorder: started")
            except Exception as e:
                logger.error("recorder: failed to start: %s", e)
        async with session_manager.run():
            try:
                yield
            finally:
                if supervisor is not None:
                    supervisor.shutdown()
                if recorder_task is not None:
                    try:
                        await asyncio.wait_for(recorder_task, timeout=10)
                    except TimeoutError:
                        recorder_task.cancel()
                # Persist soft observability state (last_used per token) to
                # the token file. last_used is updated in-memory on every
                # validate; flushing here keeps the disk copy honest across
                # restarts without the per-request fsync cost.
                try:
                    get_store().flush()
                except Exception as e:  # noqa: BLE001
                    logger.warning("auth: flush token store failed: %s", e)

    import mymcp

    app = FastAPI(title="Linux MCP Server", version=mymcp.__version__, lifespan=lifespan)

    setup_observability(app, service_name="mymcp", service_version=mymcp.__version__)

    app.add_middleware(McpAuthMiddleware)
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(RequestIdMiddleware)  # added last → runs first

    app.include_router(admin_router)
    from mymcp.recorder import admin as recorder_admin

    app.include_router(recorder_admin.router)
    register_transfer_routes(app)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": mymcp.__version__}

    @app.get("/version")
    async def version() -> dict[str, str]:
        return {"version": mymcp.__version__}

    @app.get("/metrics")
    async def get_metrics(request: Request):
        if not config.METRICS_TOKEN:
            return JSONResponse(
                {"detail": "Metrics disabled: MYMCP_METRICS_TOKEN not configured"},
                status_code=503,
            )
        auth_header = request.headers.get("authorization", "")
        if auth_header != f"Bearer {config.METRICS_TOKEN}":
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app
