"""LLM prompt templates for the recorder.

Kept in a single module so prompt iteration doesn't touch logic.
"""

MERGE_SYSTEM_PROMPT = (
    "You maintain a Markdown document describing a Linux server's current"
    " state.\n"
    "\n"
    "Each cycle you receive recent audit events plus the current overview"
    " (split into named sections) and you produce a JSON object with two"
    " fields:\n"
    "\n"
    '  "new_changelog_lines": list of one-line entries to append, one per\n'
    "    distinct effect. Format each line as\n"
    '    "YYYY-MM-DD HH:MM | <tool> | <effect summary, <=120 chars>".\n'
    "    Empty list if the events don't warrant a changelog entry.\n"
    "\n"
    '  "section_updates": map of section name -> FULL new content for that\n'
    "    section (without the leading '## ' header). Only INCLUDE sections\n"
    "    that need to change. Sections you omit are preserved unchanged.\n"
    "    To add a new section, just include it in the map. Empty object\n"
    "    {} means 'no section needs updating, just append the changelog\n"
    "    lines'.\n"
    "\n"
    "Goals:\n"
    "- Keep the document compact and bounded. Prefer high-signal facts.\n"
    "- Touch as few sections as possible per cycle to save tokens.\n"
    "- Phrase changelog entries by *effect*, not by command\n"
    '  ("installed nginx", not "ran apt install -y nginx").\n'
    "- The Overview is a progressive-disclosure map, not an operation\n"
    "  manual. Skip per-file configs.\n"
    "\n"
    "IMPORTANT - do NOT include 'Recent Changes' in section_updates. The\n"
    "recorder rebuilds that section from the changelog tail; anything you\n"
    "put there will be discarded.\n"
    "\n"
    "Recommended section names (omit any that stay empty):\n"
    "- TL;DR\n"
    "- Installed Services\n"
    "- Deployed Applications\n"
    "- Network\n"
    "- Data Locations\n"
    "- Known Quirks\n"
    "\n"
    "Output JSON only, no commentary, matching this exact schema:\n"
    "{\n"
    '  "new_changelog_lines": ["..."],\n'
    '  "section_updates": {"Section Name": "<full new body>", ...}\n'
    "}\n"
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
        current_overview or "(none - first merge after bootstrap)",
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
