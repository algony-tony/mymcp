"""Pure-function tests for deploy/setup.py builders."""
import json


def test_build_env_dict_minimal():
    from mymcp.deploy.setup import build_env_dict
    d = build_env_dict(
        host="0.0.0.0", port=8765, admin_token="adm",
        metrics_token="", token_file="/etc/mymcp/tokens.json",
        audit_enabled=True, audit_log_dir="/var/log/mymcp",
    )
    assert d["MYMCP_HOST"] == "0.0.0.0"
    assert d["MYMCP_PORT"] == "8765"
    assert d["MYMCP_ADMIN_TOKEN"] == "adm"
    assert d["MYMCP_TOKEN_FILE"] == "/etc/mymcp/tokens.json"
    assert d["MYMCP_AUDIT_ENABLED"] == "true"
    assert d["MYMCP_AUDIT_LOG_DIR"] == "/var/log/mymcp"
    assert "MYMCP_METRICS_TOKEN" in d
    assert d["MYMCP_METRICS_TOKEN"] == ""


def test_build_env_dict_audit_disabled_lowercased():
    from mymcp.deploy.setup import build_env_dict
    d = build_env_dict(
        host="0.0.0.0", port=8765, admin_token="adm", metrics_token="m",
        token_file="/etc/mymcp/tokens.json",
        audit_enabled=False, audit_log_dir="/var/log/mymcp",
    )
    assert d["MYMCP_AUDIT_ENABLED"] == "false"


def test_format_env_file_round_trips_dict():
    from mymcp.deploy.setup import format_env_file
    text = format_env_file({"A": "1", "B": "two", "C": ""})
    lines = text.strip().splitlines()
    assert "A=1" in lines
    assert "B=two" in lines
    assert "C=" in lines


def test_write_env_file_sets_mode_600(tmp_path):
    from mymcp.deploy.setup import write_env_file
    target = tmp_path / "new.env"
    write_env_file(target, {"X": "1"})
    assert target.exists()
    assert (target.stat().st_mode & 0o777) == 0o600


def test_write_empty_token_store(tmp_path):
    from mymcp.deploy.setup import write_empty_token_store
    target = tmp_path / "tokens.json"
    write_empty_token_store(target, admin_token="adm")
    body = json.loads(target.read_text())
    assert body == {"tokens": {}, "admin_token": "adm"}
    assert (target.stat().st_mode & 0o777) == 0o600


def test_make_token_returns_prefixed_hex():
    from mymcp.deploy.setup import make_token
    t = make_token()
    assert t.startswith("tok_")
    assert len(t) == len("tok_") + 32  # 16 bytes hex


def test_update_env_file_merges_keys(tmp_path):
    from mymcp.deploy.setup import update_env_file, write_env_file
    p = tmp_path / "env"
    write_env_file(p, {"A": "1", "B": "2"})
    update_env_file(p, {"B": "two", "C": "3"})
    text = p.read_text()
    assert "A=1" in text
    assert "B=two" in text
    assert "C=3" in text


def test_update_env_file_creates_if_missing(tmp_path):
    from mymcp.deploy.setup import update_env_file
    p = tmp_path / "new.env"
    update_env_file(p, {"K": "v"})
    assert p.read_text().strip() == "K=v"
