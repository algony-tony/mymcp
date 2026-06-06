"""Atomic read/write of overview.md and append-only changelog.md.

OverviewStore owns the recorder's two output files. The overview is rewritten
in place on each merge (atomic via tmp + os.replace); the changelog is
append-only.

Section helpers (parse_sections / apply_section_updates) support incremental
merges: the LLM emits only the sections that changed, and Python folds them
into the existing overview without rewriting unchanged sections.
"""

import os
import re
from datetime import UTC, datetime
from pathlib import Path

# Stamp the overview with the time of the most recent merge so the timestamp
# is visible in the file itself — across restarts, in offline copies, and to
# consumers reading the markdown directly without scraping Prometheus.
_LAST_UPDATED_RE = re.compile(r"^_Last updated: [^_]+_\s*$", re.MULTILINE)


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


def _stamp_last_updated(content: str) -> str:
    """Inject a `_Last updated: ISO8601_` line into overview content.

    Placed immediately after the first H1 if present, otherwise at the top.
    Idempotent — replaces any prior `_Last updated:_` marker.
    """
    now = datetime.now(tz=UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    marker = f"_Last updated: {now}_"
    # Strip any prior marker(s), normalising trailing newlines.
    stripped = _LAST_UPDATED_RE.sub("", content)
    # Collapse blank-line runs left behind by the strip — keep one blank.
    stripped = re.sub(r"\n{3,}", "\n\n", stripped)

    lines = stripped.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.startswith("# "):
            # Insert marker right after the H1 line.
            tail = "".join(lines[i + 1 :]).lstrip("\n")
            return "".join(lines[: i + 1]) + "\n" + marker + "\n\n" + tail
    # No H1 — prepend.
    return marker + "\n\n" + stripped.lstrip("\n")


def render_recent_changes(changelog_tail: list[str], *, limit: int = 10) -> str:
    """Render the 'Recent Changes' section body from changelog lines.

    changelog_tail is expected in file order (oldest first). Output is
    newest-first, capped at ``limit`` entries, with a trailing pointer to
    the full changelog.
    """
    newest_first = list(reversed(changelog_tail))[:limit]
    bullets = [f"- {line}" for line in newest_first]
    footer = "_Full changelog: changelog.md (use read_file)_"
    if bullets:
        return "\n".join(bullets) + "\n" + footer
    return footer


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
        stamped = _stamp_last_updated(content)
        tmp = self._overview.with_suffix(self._overview.suffix + ".tmp")
        tmp.write_text(stamped)
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
