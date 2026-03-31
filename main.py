from contextlib import asynccontextmanager

import uvicorn
from fastapi import Depends, FastAPI, Request

import config
from auth import admin_router, get_store, require_auth
from mcp_server import server, sse_transport
from tools.transfer import transfer_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail fast if ADMIN_TOKEN not configured
    get_store()
    yield


app = FastAPI(title="Linux MCP Server", version="1.0.0", lifespan=lifespan)

app.include_router(admin_router)
app.include_router(transfer_router)


@app.get("/sse")
async def handle_sse(request: Request, _: dict = Depends(require_auth)):
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await server.run(
            streams[0],
            streams[1],
            server.create_initialization_options(),
        )


@app.post("/messages")
async def handle_messages(request: Request, _: dict = Depends(require_auth)):
    await sse_transport.handle_post_message(
        request.scope, request.receive, request._send
    )


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run("main:app", host=config.HOST, port=config.PORT, reload=False)
