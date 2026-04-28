import json
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel


class TokenStore:
    def __init__(self, path: str, admin_token: str):
        self.path = Path(path)
        self.admin_token = admin_token
        self._lock = threading.Lock()
        self._data: dict = {"tokens": {}, "admin_token": admin_token}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            with open(self.path) as f:
                self._data = json.load(f)
            self._data["admin_token"] = self.admin_token
            # Backward compat: add default role to tokens missing it
            for info in self._data.get("tokens", {}).values():
                if "role" not in info:
                    info["role"] = "rw"
        else:
            self._data = {"tokens": {}, "admin_token": self.admin_token}
            self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self._data, f, indent=2)

    def validate(self, token: str) -> dict | None:
        """Returns token info dict if valid and enabled, else None."""
        with self._lock:
            info = self._data["tokens"].get(token)
            if info is None or not info.get("enabled", False):
                return None
            info["last_used"] = datetime.now(timezone.utc).isoformat()  # noqa: UP017
            self._save()
            return dict(info)

    def create_token(self, name: str, role: str = "ro") -> str:
        if role not in ("ro", "rw"):
            raise ValueError(f"Invalid role: {role!r}. Must be 'ro' or 'rw'.")
        token = "tok_" + secrets.token_hex(16)
        with self._lock:
            self._data["tokens"][token] = {
                "name": name,
                "created_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
                "last_used": None,
                "enabled": True,
                "role": role,
            }
            self._save()
        return token

    def revoke_token(self, token: str) -> bool:
        """Returns True if token existed and was removed, False otherwise."""
        with self._lock:
            if token not in self._data["tokens"]:
                return False
            del self._data["tokens"][token]
            self._save()
            return True

    def list_tokens(self) -> dict:
        with self._lock:
            return dict(self._data["tokens"])


# ---------------------------------------------------------------------------
# FastAPI dependency / singleton
# ---------------------------------------------------------------------------

_store: "TokenStore | None" = None


def get_store() -> "TokenStore":
    """FastAPI dependency — returns the singleton TokenStore.
    Override in tests via app.dependency_overrides[get_store]."""
    global _store
    if _store is None:
        from mymcp import config

        if not config.ADMIN_TOKEN:
            raise RuntimeError("MYMCP_ADMIN_TOKEN environment variable is required")
        _store = TokenStore(config.TOKEN_FILE, config.ADMIN_TOKEN)
    return _store


async def require_auth(
    request: Request,
    store: "TokenStore" = Depends(get_store),
) -> dict:
    """FastAPI dependency — validates user Bearer token. Returns token info."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    token = auth[7:]
    info = store.validate(token)
    if info is None:
        raise HTTPException(status_code=401, detail="Invalid or disabled token")
    return info


async def require_admin(
    request: Request,
    store: "TokenStore" = Depends(get_store),
) -> None:
    """FastAPI dependency — validates admin Bearer token."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    token = auth[7:]
    if token != store.admin_token:
        raise HTTPException(status_code=403, detail="Admin token required")


# ---------------------------------------------------------------------------
# Admin router
# ---------------------------------------------------------------------------


class _CreateTokenRequest(BaseModel):
    name: str
    role: str = "ro"


admin_router = APIRouter(
    prefix="/admin",
    dependencies=[Depends(require_admin)],
)


@admin_router.post("/tokens")
async def create_token(
    body: _CreateTokenRequest,
    store: "TokenStore" = Depends(get_store),
):
    try:
        token = store.create_token(body.name, role=body.role)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"token": token, "name": body.name, "role": body.role}


@admin_router.delete("/tokens/{token}")
async def revoke_token(token: str, store: "TokenStore" = Depends(get_store)):
    found = store.revoke_token(token)
    if not found:
        raise HTTPException(status_code=404, detail="Token not found")
    return {"revoked": token}


@admin_router.get("/tokens")
async def list_tokens(store: "TokenStore" = Depends(get_store)):
    return store.list_tokens()
