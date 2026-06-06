from mymcp.recorder.overview import (
    OverviewStore,
    apply_section_updates,
    parse_sections,
    render_recent_changes,
)


def test_write_overview_atomic(tmp_path):
    s = OverviewStore(tmp_path)
    s.write_overview("# Server Overview\n\nbody\n")
    written = (tmp_path / "overview.md").read_text()
    # Original content + injected _Last updated_ marker.
    assert "# Server Overview" in written
    assert "body" in written
    assert "_Last updated:" in written
    assert not list(tmp_path.glob("*.tmp"))


def test_read_overview_missing(tmp_path):
    s = OverviewStore(tmp_path)
    assert s.read_overview() is None


def test_read_overview_present(tmp_path):
    """write/read round-trip — content survives plus the stamp."""
    s = OverviewStore(tmp_path)
    s.write_overview("hello")
    out = s.read_overview()
    assert out is not None
    assert "hello" in out


def test_write_overview_stamps_last_updated_after_h1(tmp_path):
    """Stamp goes right after the H1 line, before any body."""
    s = OverviewStore(tmp_path)
    s.write_overview("# Server Overview\n\nintro line\n\n## TL;DR\nyes\n")
    text = (tmp_path / "overview.md").read_text()
    lines = text.splitlines()
    # First line is the H1; the stamp should appear within the next 3 lines.
    assert lines[0] == "# Server Overview"
    head_block = "\n".join(lines[:5])
    assert "_Last updated: " in head_block
    # Ordering: H1, blank, marker, blank, then body resumes
    assert "intro line" in text


def test_write_overview_replaces_existing_last_updated(tmp_path):
    """Stamping is idempotent — old marker is replaced, not duplicated."""
    s = OverviewStore(tmp_path)
    seeded = (
        "# Server Overview\n\n"
        "_Last updated: 2020-01-01T00:00:00Z_\n\n"
        "body\n"
    )
    s.write_overview(seeded)
    text = (tmp_path / "overview.md").read_text()
    assert text.count("_Last updated:") == 1
    assert "2020-01-01" not in text


def test_write_overview_stamps_iso_8601_utc(tmp_path):
    """Marker uses a parseable ISO 8601 timestamp ending with Z."""
    import re

    s = OverviewStore(tmp_path)
    s.write_overview("# X\nbody\n")
    text = (tmp_path / "overview.md").read_text()
    m = re.search(r"_Last updated: (\S+)_", text)
    assert m is not None
    ts = m.group(1)
    # Must look like 2026-06-06T01:23:45Z
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", ts), ts


def test_write_overview_with_no_h1_prepends_marker(tmp_path):
    """If the LLM produces no H1, prepend the marker so it's still visible."""
    s = OverviewStore(tmp_path)
    s.write_overview("just body\nmore body\n")
    text = (tmp_path / "overview.md").read_text()
    assert text.startswith("_Last updated:")
    assert "just body" in text


def test_append_changelog_creates_file(tmp_path):
    s = OverviewStore(tmp_path)
    s.append_changelog(["2026-05-29 10:00 | bash_execute | installed x"])
    text = (tmp_path / "changelog.md").read_text()
    assert text == "2026-05-29 10:00 | bash_execute | installed x\n"


def test_append_changelog_appends(tmp_path):
    s = OverviewStore(tmp_path)
    s.append_changelog(["line 1"])
    s.append_changelog(["line 2", "line 3"])
    text = (tmp_path / "changelog.md").read_text()
    assert text == "line 1\nline 2\nline 3\n"


def test_append_changelog_empty_list_is_noop(tmp_path):
    s = OverviewStore(tmp_path)
    s.append_changelog([])
    assert not (tmp_path / "changelog.md").exists()


def test_append_changelog_strips_trailing_newlines(tmp_path):
    s = OverviewStore(tmp_path)
    s.append_changelog(["line\n", "next\n\n"])
    text = (tmp_path / "changelog.md").read_text()
    assert text == "line\nnext\n"


def test_read_changelog_tail(tmp_path):
    s = OverviewStore(tmp_path)
    lines = [f"2026-05-29 10:{i:02d} | write_file | line {i}" for i in range(20)]
    s.append_changelog(lines)
    tail = s.read_changelog_tail(5)
    assert len(tail) == 5
    assert tail[-1].endswith("line 19")


def test_read_changelog_tail_missing(tmp_path):
    s = OverviewStore(tmp_path)
    assert s.read_changelog_tail(5) == []


def test_read_changelog_tail_zero(tmp_path):
    s = OverviewStore(tmp_path)
    s.append_changelog(["x"])
    assert s.read_changelog_tail(0) == []


def test_paths_exposed(tmp_path):
    s = OverviewStore(tmp_path)
    assert s.overview_path == tmp_path / "overview.md"
    assert s.changelog_path == tmp_path / "changelog.md"


def test_data_dir_created_lazily(tmp_path):
    target = tmp_path / "nested" / "recorder"
    s = OverviewStore(target)
    assert target.exists()
    s.write_overview("hi")
    assert (target / "overview.md").exists()


def test_parse_sections_splits_at_h2_headers():
    text = (
        "# Server Overview\n"
        "_meta_\n"
        "\n"
        "## TL;DR\n"
        "Short summary.\n"
        "\n"
        "## Installed Services\n"
        "- nginx\n"
        "- redis\n"
    )
    header, sections = parse_sections(text)
    assert "# Server Overview" in header and "_meta_" in header
    assert [name for name, _ in sections] == ["TL;DR", "Installed Services"]
    assert sections[0][1] == "Short summary."
    assert "nginx" in sections[1][1] and "redis" in sections[1][1]


def test_parse_sections_no_header_block():
    header, sections = parse_sections("## Only Section\nbody\n")
    assert header == ""
    assert sections == [("Only Section", "body")]


def test_parse_sections_empty_input():
    assert parse_sections("") == ("", [])


def test_apply_section_updates_replaces_only_listed_sections():
    current = "# H\n_m_\n\n## TL;DR\nKeep me.\n\n## Known Quirks\n- preserve\n"
    result = apply_section_updates(current, header=None, section_updates={"TL;DR": "Updated."})
    assert "Keep me." not in result
    assert "Updated." in result
    assert "preserve" in result
    assert "_m_" in result  # header preserved when header=None


def test_apply_section_updates_appends_new_sections_at_end():
    current = "# H\n\n## A\nfoo\n"
    result = apply_section_updates(current, header=None, section_updates={"B": "bar"})
    assert result.index("## A") < result.index("## B")
    assert "foo" in result and "bar" in result


def test_apply_section_updates_overrides_header_when_given():
    current = "# Old\n_old_\n\n## TL;DR\nx\n"
    result = apply_section_updates(current, header="# New\n_new_", section_updates={})
    assert "Old" not in result
    assert "New" in result and "_new_" in result
    assert "## TL;DR" in result and "x" in result


def test_render_recent_changes_newest_first():
    tail = [
        "2026-06-01 10:00 | bash_execute | installed nginx",
        "2026-06-02 11:00 | write_file | wrote /etc/foo",
        "2026-06-03 12:00 | bash_execute | restarted nginx",
    ]
    out = render_recent_changes(tail)
    lines = out.splitlines()
    assert lines[0] == "- 2026-06-03 12:00 | bash_execute | restarted nginx"
    assert lines[1] == "- 2026-06-02 11:00 | write_file | wrote /etc/foo"
    assert lines[2] == "- 2026-06-01 10:00 | bash_execute | installed nginx"
    assert lines[-1] == "_Full changelog: changelog.md (use read_file)_"


def test_render_recent_changes_empty():
    out = render_recent_changes([])
    assert "_Full changelog:" in out
    assert not any(line.startswith("- ") for line in out.splitlines())


def test_render_recent_changes_caps_at_10():
    tail = [f"2026-06-{i:02d} 10:00 | bash_execute | event {i}" for i in range(1, 16)]
    out = render_recent_changes(tail)
    bullet_lines = [line for line in out.splitlines() if line.startswith("- ")]
    assert len(bullet_lines) == 10
    assert "event 15" in bullet_lines[0]
