from mymcp.recorder.cursor import Cursor


def test_load_missing_returns_default(tmp_path):
    c = Cursor.load(tmp_path / "cursor.json")
    assert c.file is None
    assert c.inode is None
    assert c.offset == 0


def test_save_then_load_roundtrip(tmp_path):
    p = tmp_path / "cursor.json"
    Cursor(file="audit.log", inode=42, offset=1024).save(p)
    c = Cursor.load(p)
    assert (c.file, c.inode, c.offset) == ("audit.log", 42, 1024)


def test_atomic_save_no_partial(tmp_path):
    p = tmp_path / "cursor.json"
    Cursor(file="audit.log", inode=1, offset=0).save(p)
    # tmp file is cleaned up by os.replace
    assert not (tmp_path / "cursor.json.tmp").exists()
    # main file exists with content
    assert p.exists()


def test_corrupt_cursor_returns_default(tmp_path):
    p = tmp_path / "cursor.json"
    p.write_text("{not json")
    c = Cursor.load(p)
    assert c.file is None
    assert c.offset == 0


def test_save_creates_parent_dir(tmp_path):
    p = tmp_path / "nested" / "deeper" / "cursor.json"
    Cursor(file="x", inode=1, offset=5).save(p)
    assert p.exists()
