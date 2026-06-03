"""Atomic read/write of overview.md and append-only changelog.md.

OverviewStore owns the recorder's two output files. The overview is rewritten
in place on each merge (atomic via tmp + os.replace); the changelog is
append-only.

Section helpers (parse_sections / apply_section_updates) support incremental
merges: the LLM emits only the sections that changed, and Python folds them
into the existing overview without rewriting unchanged sections.
"""

import os
from pathlib import Path


def parse_sections(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Split overview markdown into (header_block, [(section_name, body)]).

    The header block is everything before the first '## ' line (typically
    '# Title' plus a metadata block). Section bodies are the content between
    a '## name' line and the next '## ' line (exclusive), with surrounding
    blank lines stripped. Returned in original order so callers can preserve
    layout when reassembling.
    """
    lines = text.split("\n")
    header_lines: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    current_name: str | None = None
    current_body: list[str] = []
    for line in lines:
        if line.startswith("## "):
            if current_name is not None:
                sections.append((current_name, current_body))
            current_name = line[3:].strip()
            current_body = []
        elif current_name is None:
            header_lines.append(line)
        else:
            current_body.append(line)
    if current_name is not None:
        sections.append((current_name, current_body))
    header = "\n".join(header_lines).rstrip("\n")
    out = [(name, "\n".join(body).strip("\n")) for name, body in sections]
    return header, out


def apply_section_updates(
    current: str,
    *,
    header: str | None,
    section_updates: dict[str, str],
) -> str:
    """Fold per-section updates into an existing overview, preserving order.

    Sections present in section_updates have their bodies replaced; new
    section names are appended at the end. header (if not None) replaces the
    pre-first-section block; pass None to keep the existing header. Sections
    not mentioned in section_updates keep their existing bodies.
    """
    existing_header, existing = parse_sections(current)
    body_map = dict(existing)
    order = [name for name, _ in existing]
    for name, body in section_updates.items():
        if name not in body_map:
            order.append(name)
        body_map[name] = body.strip("\n")

    parts: list[str] = []
    final_header = header if header is not None else existing_header
    if final_header:
        parts.append(final_header.rstrip("\n"))
    for name in order:
        parts.append(f"## {name}\n{body_map[name]}".rstrip("\n"))
    return "\n\n".join(parts).rstrip("\n") + "\n"


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
