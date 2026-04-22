import pytest
from unittest.mock import patch
import config


def test_read_version_app_dir_takes_priority(tmp_path):
    app_version_file = tmp_path / "VERSION"
    app_version_file.write_text("2.0.0\n")
    repo_version_file = tmp_path / "repo_VERSION"
    repo_version_file.write_text("1.0.0\n")
    with patch("config.APP_DIR", str(tmp_path)), \
         patch("config._VERSION_FILE", str(repo_version_file)):
        result = config._read_version()
    assert result == "2.0.0"


def test_read_version_falls_back_to_repo(tmp_path):
    repo_version_file = tmp_path / "VERSION"
    repo_version_file.write_text("1.1.0\n")
    missing_app_dir = str(tmp_path / "nonexistent")
    with patch("config.APP_DIR", missing_app_dir), \
         patch("config._VERSION_FILE", str(repo_version_file)):
        result = config._read_version()
    assert result == "1.1.0"


def test_read_version_falls_back_to_unknown(tmp_path):
    missing_app_dir = str(tmp_path / "nonexistent")
    missing_repo = str(tmp_path / "noVERSION")
    with patch("config.APP_DIR", missing_app_dir), \
         patch("config._VERSION_FILE", missing_repo):
        result = config._read_version()
    assert result == "unknown"


def test_read_version_strips_whitespace(tmp_path):
    app_version_file = tmp_path / "VERSION"
    app_version_file.write_text("  1.2.3  \n")
    with patch("config.APP_DIR", str(tmp_path)), \
         patch("config._VERSION_FILE", str(tmp_path / "nofile")):
        result = config._read_version()
    assert result == "1.2.3"


def test_app_version_is_set():
    assert isinstance(config.APP_VERSION, str)
    assert config.APP_VERSION != ""
