from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

import config
from auth import admin_router, get_store
from mcp_server import server, session_manager
from tools.transfer import transfer_router


def _validate_token(request: Request) -> JSONResponse | None:
    """Validate bearer token. Returns error JSONResponse or None if valid."""
    store = get_store()
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return JSONResponse({"detail": "Missing Bearer token"}, status_code=401)
    token = auth[7:]
    info = store.validate(token)
    if info is None:
        return JSONResponse({"detail": "Invalid or disabled token"}, status_code=401)
    return None


class McpAuthMiddleware:
    """Intercepts /mcp to validate Bearer token, then delegates
    to StreamableHTTPSessionManager as raw ASGI."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "http" and scope.get("path", "") == "/mcp":
            request = Request(scope, receive, send)
            error = _validate_token(request)
            if error:
                await error(scope, receive, send)
                return
            await session_manager.handle_request(scope, receive, send)
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
app.include_router(transfer_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run("main:app", host=config.HOST, port=config.PORT, reload=False)
