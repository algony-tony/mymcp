import json
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path


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
            info["last_used"] = datetime.now(timezone.utc).isoformat()
            self._save()
            return dict(info)

    def create_token(self, name: str) -> str:
        token = "tok_" + secrets.token_hex(16)
        with self._lock:
            self._data["tokens"][token] = {
                "name": name,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "last_used": None,
                "enabled": True,
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
