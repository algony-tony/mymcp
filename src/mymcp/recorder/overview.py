"""Atomic read/write of overview.md and append-only changelog.md.

OverviewStore owns the recorder's two output files. The overview is fully
rewritten on each merge (atomic via tmp + os.replace); the changelog is
append-only.
"""

import os
from pathlib import Path


class OverviewStore:
    def __init__(self, data_dir: Path):
        self._dir = Path(data_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._overview = self._dir / "overview.md"
        self._changelog = self._dir / "changelog.md"

    @property
    def overview_path(self) -> Path:
        return self._overview

    @property
    def changelog_path(self) -> Path:
        return self._changelog

    def write_overview(self, content: str) -> None:
        tmp = self._overview.with_suffix(self._overview.suffix + ".tmp")
        tmp.write_text(content)
        os.replace(tmp, self._overview)

    def read_overview(self) -> str | None:
        try:
            return self._overview.read_text()
        except FileNotFoundError:
            return None

    def append_changelog(self, lines: list[str]) -> None:
        if not lines:
            return
        with self._changelog.open("a") as f:
            for line in lines:
                f.write(line.rstrip("\n") + "\n")

    def read_changelog_tail(self, n: int) -> list[str]:
        if n <= 0:
            return []
        try:
            all_lines = self._changelog.read_text().splitlines()
        except FileNotFoundError:
            return []
        return all_lines[-n:]
