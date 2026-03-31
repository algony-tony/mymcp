import os

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from auth import require_auth

transfer_router = APIRouter(prefix="/files")

CHUNK_SIZE = 1024 * 1024  # 1 MB streaming chunks


@transfer_router.post("/upload")
async def upload_file(
    file: UploadFile,
    dest_path: str = Form(...),
    _: dict = Depends(require_auth),
):
    parent = os.path.dirname(os.path.abspath(dest_path))
    os.makedirs(parent, exist_ok=True)

    total = 0
    try:
        with open(dest_path, "wb") as f:
            while chunk := await file.read(CHUNK_SIZE):
                f.write(chunk)
                total += len(chunk)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    return {"path": dest_path, "size": total}


@transfer_router.get("/download")
async def download_file(
    path: str,
    _: dict = Depends(require_auth),
):
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    filename = os.path.basename(path)

    def iterfile():
        with open(path, "rb") as f:
            while chunk := f.read(CHUNK_SIZE):
                yield chunk

    return StreamingResponse(
        iterfile(),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
