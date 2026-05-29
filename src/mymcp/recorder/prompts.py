"""LLM prompt templates for the recorder.

Kept in a single module so prompt iteration doesn't touch logic.
"""

MERGE_SYSTEM_PROMPT = (
    "You maintain a single Markdown document describing a Linux server's current state.\n"
    "\n"
    "Goals:\n"
    "- Keep the document compact and bounded. Prefer high-signal facts over completeness.\n"
    "- Update only sections affected by the new events. Leave unrelated sections untouched.\n"
    "- Phrase changelog entries by *effect*, not by command\n"
    '  ("installed nginx", not "ran apt install -y nginx").\n'
    "- The Overview is a progressive-disclosure map — not an operation manual."
    " Skip per-file configs.\n"
    "\n"
    "Output JSON only, no commentary, matching this exact schema:\n"
    "{\n"
    '  "new_changelog_lines": ["YYYY-MM-DD HH:MM | <tool> | <effect summary, <=120 chars>",'
    " ...],\n"
    '  "updated_overview_md": "<full new overview.md content>"\n'
    "}\n"
    "\n"
    "The Overview should use this section skeleton (omit empty sections):\n"
    "- # Server Overview (with metadata line)\n"
    "- ## TL;DR\n"
    "- ## Installed Services\n"
    "- ## Deployed Applications\n"
    "- ## Network\n"
    "- ## Data Locations\n"
    "- ## Recent Changes  (last 10 entries, newest first; end with\n"
    '  "_Full changelog: ...changelog.md (use read_file)_")\n'
    "- ## Known Quirks\n"
)


def merge_user_prompt(
    *,
    current_overview: str | None,
    recent_changelog: list[str],
    events_json: str,
    metadata: dict,
) -> str:
    parts: list[str] = [
        f"Hostname: {metadata.get('hostname', 'unknown')}",
        f"OS: {metadata.get('os', 'unknown')}",
        f"Now: {metadata.get('now', 'unknown')}",
        "",
        "## Current overview.md",
        current_overview or "(none — first merge after bootstrap)",
        "",
        "## Recent changelog tail (last 10 lines, for tone consistency)",
        *(recent_changelog or ["(empty)"]),
        "",
        "## New events to fold in (JSON)",
        events_json,
        "",
        "Produce JSON per the schema in the system prompt.",
    ]
    return "\n".join(parts)
