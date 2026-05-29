from mymcp.recorder.overview import OverviewStore


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
