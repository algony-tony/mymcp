import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

import config
import metrics
from auth import admin_router, get_store
from mcp_server import server, session_manager, _current_audit_info


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
            if error:
                await error(scope, receive, send)
                return

            # Extract client IP
            client = scope.get("client")
            ip = client[0] if client else "unknown"

            # Set contextvar for MCP tool handlers
            cv_token = _current_audit_info.set({
                "token_name": token_info.get("name", "unknown"),
                "role": token_info.get("role", "rw"),
                "ip": ip,
            })
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_store()
    async with session_manager.run():
        yield


app = FastAPI(title="Linux MCP Server", version=config.APP_VERSION, lifespan=lifespan)

app.add_middleware(McpAuthMiddleware)
app.add_middleware(MetricsMiddleware)

app.include_router(admin_router)


@app.get("/health")
async def health():
    return {"status": "ok", "version": config.APP_VERSION}


@app.get("/version")
async def version():
    return {"version": config.APP_VERSION}


@app.get("/metrics")
async def get_metrics(request: Request):
    if not metrics.ENABLED:
        return JSONResponse(
            {"detail": "Metrics disabled: prometheus_client not installed"},
            status_code=503,
        )
    if not config.METRICS_TOKEN:
        return JSONResponse(
            {"detail": "Metrics disabled: MCP_METRICS_TOKEN not configured"},
            status_code=503,
        )
    auth_header = request.headers.get("authorization", "")
    if auth_header != f"Bearer {config.METRICS_TOKEN}":
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    return Response(content=metrics.generate_latest(), media_type=metrics.CONTENT_TYPE_LATEST)


if __name__ == "__main__":
    uvicorn.run("main:app", host=config.HOST, port=config.PORT, reload=False)
