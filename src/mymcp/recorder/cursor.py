"""Persistent cursor for tracking position in the audit log.

The cursor is saved atomically (write tmp + os.replace) so a crash during
save cannot produce a half-written cursor file.
"""

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class Cursor:
    file: str | None = None
    inode: int | None = None
    offset: int = 0

    @classmethod
    def load(cls, path: Path) -> "Cursor":
        try:
            data = json.loads(Path(path).read_text())
            return cls(
                file=data.get("file"),
                inode=data.get("inode"),
                offset=int(data.get("offset", 0)),
            )
        except (FileNotFoundError, json.JSONDecodeError, ValueError, TypeError):
            return cls()

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(asdict(self)))
        os.replace(tmp, path)
