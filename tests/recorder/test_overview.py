from mymcp.recorder.overview import OverviewStore, apply_section_updates, parse_sections


def test_write_overview_atomic(tmp_path):
    s = OverviewStore(tmp_path)
    s.write_overview("# Server Overview\n\nbody\n")
    assert (tmp_path / "overview.md").read_text() == "# Server Overview\n\nbody\n"
    assert not list(tmp_path.glob("*.tmp"))


def test_read_overview_missing(tmp_path):
    s = OverviewStore(tmp_path)
    assert s.read_overview() is None


def test_read_overview_present(tmp_path):
    s = OverviewStore(tmp_path)
    s.write_overview("hello")
    assert s.read_overview() == "hello"


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
    assert "nginx" in sections[1][1]


def test_parse_sections_no_header_block():
    text = "## Only Section\nbody\n"
    header, sections = parse_sections(text)
    assert header == ""
    assert sections == [("Only Section", "body")]


def test_apply_section_updates_preserves_unrelated_sections():
    current = "# Server Overview\n_meta_\n\n## TL;DR\nKeep me.\n\n## Known Quirks\n- preserve\n"
    result = apply_section_updates(
        current,
        header=None,
        section_updates={"TL;DR": "Updated summary."},
    )
    assert "Keep me." not in result
    assert "Updated summary." in result
    assert "preserve" in result
    # Header preserved when header=None
    assert "_meta_" in result


def test_apply_section_updates_appends_new_section_at_end():
    current = "# H\n\n## A\nfoo\n"
    result = apply_section_updates(
        current,
        header=None,
        section_updates={"B": "bar"},
    )
    a_idx = result.index("## A")
    b_idx = result.index("## B")
    assert a_idx < b_idx
    assert "foo" in result and "bar" in result


def test_apply_section_updates_can_override_header():
    current = "# Old Header\n_old_\n\n## TL;DR\nx\n"
    result = apply_section_updates(
        current,
        header="# New Header\n_new_",
        section_updates={},
    )
    assert "Old Header" not in result
    assert "New Header" in result
    assert "_new_" in result
    # Existing sections still preserved.
    assert "## TL;DR" in result and "x" in result


def test_apply_section_updates_no_op_when_updates_empty():
    current = "# H\n\n## A\nfoo\n"
    result = apply_section_updates(current, header=None, section_updates={})
    # Roundtrip should preserve section content (whitespace normalized).
    assert "## A" in result
    assert "foo" in result
