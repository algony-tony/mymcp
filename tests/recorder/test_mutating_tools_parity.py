"""Cross-language contract test: the Go core's hand-ported tool sets must
match the Python recorder's sets they were ported from.

go/internal/tools/recorderstatus.go defines `mutatingTools` and
`successResults` as a hand-port of `MUTATING_TOOLS` / `_SUCCESS_RESULTS` in
mymcp.recorder.events, used to compute `pending_events` for `server_overview`
(issue #92). Both files carry "keep in sync" comments, but nothing enforced
it — a future PR could add a 7th mutating tool on the Python side (with
Python's own tests updated and green) and forget the Go side, and CI would
stay green while `pending_events`/`stale` silently under-count.

This does not belong in tests/compat/: that suite's conftest.py skips the
whole directory via pytest_collection_modifyitems unless MYMCP_COMPAT_URL
points at a live server, and this check needs neither Go source compiled nor
a server running — just both source trees, which are always present in this
repo's checkout.

Parsing approach: the Go literals are `map[string]bool{"key": true, ...}`.
Rather than a full Go parser (overkill, and one more thing to keep in sync),
a regex isolates each var's `{...}` body (non-greedy, so it stops at the
first `}` — safe here since these literals have no nested braces) and then
pulls every double-quoted string out of that body. Since Go bool literals
(`true`/`false`) are bare identifiers, not string literals, every quoted
string inside the body is necessarily a key. This survives gofmt
reformatting (whitespace/newlines/alignment/trailing commas) because it
never depends on layout, only on `"...":` tokens existing somewhere between
the braces.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GO_SOURCE = REPO_ROOT / "go" / "internal" / "tools" / "recorderstatus.go"


def _extract_go_map_keys(source: str, var_name: str) -> set[str]:
    """Pull the string keys out of `var <var_name> = map[string]bool{...}`.

    Fails loudly (AssertionError, not None/empty) if the variable can't be
    found or its body yields no keys — a parser that silently returns
    nothing on a refactor is worse than no test at all.
    """
    pattern = rf"var\s+{re.escape(var_name)}\s*=\s*map\[string\]bool\{{(.*?)\}}"
    match = re.search(pattern, source, re.DOTALL)
    if match is None:
        raise AssertionError(
            f"could not find `var {var_name} = map[string]bool{{...}}` in "
            f"{GO_SOURCE} — has recorderstatus.go been refactored (renamed, "
            "restructured, moved to a different literal shape)? This test's "
            "parser must be updated to match, not silenced or skipped."
        )
    keys = set(re.findall(r'"([^"]+)"', match.group(1)))
    if not keys:
        raise AssertionError(
            f"parsed zero keys for `{var_name}` out of {GO_SOURCE} — the regex "
            "matched the variable but found no quoted strings inside it, which "
            "means the parser is broken, not that the set is legitimately empty "
            "(both known sets are non-empty)."
        )
    return keys


def _read_go_source() -> str:
    if not GO_SOURCE.is_file():
        raise AssertionError(
            f"{GO_SOURCE} does not exist. recorderstatus.go is part of this "
            "repo (not a build artifact) — a missing file here is a real "
            "failure (e.g. the file was moved/renamed), not a reason to skip "
            "this contract check."
        )
    return GO_SOURCE.read_text(encoding="utf-8")


def test_mutating_tools_match_between_go_and_python():
    from mymcp.recorder.events import MUTATING_TOOLS

    go_tools = _extract_go_map_keys(_read_go_source(), "mutatingTools")
    py_tools = set(MUTATING_TOOLS)

    only_in_python = py_tools - go_tools
    only_in_go = go_tools - py_tools
    assert not only_in_python and not only_in_go, (
        "mutatingTools (go/internal/tools/recorderstatus.go) and "
        "MUTATING_TOOLS (src/mymcp/recorder/events.py) have drifted apart.\n"
        f"  only in Python MUTATING_TOOLS: {sorted(only_in_python) or '(none)'}\n"
        f"  only in Go mutatingTools:      {sorted(only_in_go) or '(none)'}\n"
        "A tool present in one set but not the other makes server_overview's "
        "pending_events/stale silently wrong on whichever side is missing it."
    )


def test_success_results_match_between_go_and_python():
    from mymcp.recorder.events import _SUCCESS_RESULTS

    go_results = _extract_go_map_keys(_read_go_source(), "successResults")
    py_results = set(_SUCCESS_RESULTS)

    only_in_python = py_results - go_results
    only_in_go = go_results - py_results
    assert not only_in_python and not only_in_go, (
        "successResults (go/internal/tools/recorderstatus.go) and "
        "_SUCCESS_RESULTS (src/mymcp/recorder/events.py) have drifted apart.\n"
        f"  only in Python _SUCCESS_RESULTS: {sorted(only_in_python) or '(none)'}\n"
        f"  only in Go successResults:       {sorted(only_in_go) or '(none)'}\n"
    )
