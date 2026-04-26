import os
import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def mock_protected_paths(tmp_path):
    app_dir = str(tmp_path / "mymcp")
    audit_dir = str(tmp_path / "audit")
    os.makedirs(app_dir)
    os.makedirs(audit_dir)
    with patch("mymcp.config.get_protected_paths", return_value=[app_dir, audit_dir]):
        yield app_dir, audit_dir


def test_check_protected_path_blocks_app_dir(mock_protected_paths):
    from mymcp.tools.files import check_protected_path
    app_dir, _ = mock_protected_paths
    err = check_protected_path(f"{app_dir}/config.py")
    assert err is not None
    assert "protected" in err.lower()


def test_check_protected_path_blocks_audit_dir(mock_protected_paths):
    from mymcp.tools.files import check_protected_path
    _, audit_dir = mock_protected_paths
    err = check_protected_path(f"{audit_dir}/audit.log")
    assert err is not None


def test_check_protected_path_allows_normal_path(mock_protected_paths):
    from mymcp.tools.files import check_protected_path
    err = check_protected_path("/tmp/somefile.txt")
    assert err is None


def test_check_protected_path_blocks_symlink(mock_protected_paths, tmp_path):
    from mymcp.tools.files import check_protected_path
    app_dir, _ = mock_protected_paths
    secret = os.path.join(app_dir, ".env")
    with open(secret, "w") as f:
        f.write("SECRET=x")
    link = str(tmp_path / "sneaky_link")
    os.symlink(secret, link)
    err = check_protected_path(link)
    assert err is not None


def test_check_protected_path_exact_dir(mock_protected_paths):
    from mymcp.tools.files import check_protected_path
    app_dir, _ = mock_protected_paths
    err = check_protected_path(app_dir)
    assert err is not None


@pytest.mark.anyio
async def test_read_file_rejects_protected_path(mock_protected_paths):
    from mymcp.tools.files import read_file
    app_dir, _ = mock_protected_paths
    secret = os.path.join(app_dir, ".env")
    with open(secret, "w") as f:
        f.write("TOKEN=secret")
    result = await read_file(secret)
    assert result["success"] is False
    assert "protected" in result["error"].lower() or "protected" in result["message"].lower()


@pytest.mark.anyio
async def test_write_file_rejects_protected_path(mock_protected_paths):
    from mymcp.tools.files import write_file
    app_dir, _ = mock_protected_paths
    result = await write_file(os.path.join(app_dir, "hack.py"), "evil code")
    assert result["success"] is False


@pytest.mark.anyio
async def test_edit_file_rejects_protected_path(mock_protected_paths):
    from mymcp.tools.files import edit_file
    app_dir, _ = mock_protected_paths
    target = os.path.join(app_dir, "config.py")
    with open(target, "w") as f:
        f.write("original")
    result = await edit_file(target, "original", "hacked")
    assert result["success"] is False


@pytest.mark.anyio
async def test_glob_filters_protected_paths(mock_protected_paths, tmp_path):
    from mymcp.tools.files import glob_files
    app_dir, _ = mock_protected_paths
    with open(os.path.join(app_dir, "secret.py"), "w") as f:
        f.write("")
    normal_dir = str(tmp_path / "normal")
    os.makedirs(normal_dir)
    with open(os.path.join(normal_dir, "ok.py"), "w") as f:
        f.write("")

    result = await glob_files("**/*.py", str(tmp_path))
    file_list = result["files"]
    assert any("ok.py" in f for f in file_list)
    assert not any("secret.py" in f for f in file_list)


@pytest.mark.anyio
async def test_grep_filters_protected_paths(mock_protected_paths, tmp_path):
    from mymcp.tools.files import grep_files
    app_dir, _ = mock_protected_paths
    with open(os.path.join(app_dir, "secret.py"), "w") as f:
        f.write("FINDME_SECRET")
    normal_dir = str(tmp_path / "normal")
    os.makedirs(normal_dir)
    with open(os.path.join(normal_dir, "ok.py"), "w") as f:
        f.write("FINDME_NORMAL")

    result = await grep_files("FINDME", str(tmp_path))
    assert "FINDME_NORMAL" in result["results"]
    assert "FINDME_SECRET" not in result["results"]
