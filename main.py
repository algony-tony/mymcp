from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

import config
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_store()
    async with session_manager.run():
        yield


app = FastAPI(title="Linux MCP Server", version="1.0.0", lifespan=lifespan)

app.add_middleware(McpAuthMiddleware)

app.include_router(admin_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run("main:app", host=config.HOST, port=config.PORT, reload=False)
