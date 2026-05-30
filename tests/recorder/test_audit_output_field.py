import json
import os

import pytest

from mymcp import audit, config


@pytest.fixture(autouse=True)
def _reset(tmp_path, monkeypatch):
    monkeypatch.setenv("MYMCP_AUDIT_ENABLED", "true")
    monkeypatch.setenv("MYMCP_AUDIT_LOG_DIR", str(tmp_path))
    config.reset_settings_cache()
    # force audit module to re-setup
    audit._setup_done = False
    audit._logger = None
    yield
    audit._setup_done = False
    audit._logger = None


def test_audit_includes_output_field(tmp_path):
    audit.log_tool_call(
        token_name="t",
        role="rw",
        ip="127.0.0.1",
        tool="bash_execute",
        params={"command": "ls"},
        result="ok",
        output={
            "stdout_head": "x",
            "stdout_tail": "",
            "stdout_truncated_bytes": 0,
            "stdout_sha256": "abc",
        },
    )
    log_path = os.path.join(str(tmp_path), "audit.log")
    log = open(log_path).read()
    entries = [json.loads(line) for line in log.splitlines() if line.strip()]
    assert entries
    assert entries[-1]["output"]["stdout_head"] == "x"


def test_audit_omits_output_when_none(tmp_path):
    audit.log_tool_call(
        token_name="t",
        role="rw",
        ip="127.0.0.1",
        tool="read_file",
        params={"file_path": "/tmp/x"},
        result="ok",
    )
    log_path = os.path.join(str(tmp_path), "audit.log")
    log = open(log_path).read()
    entries = [json.loads(line) for line in log.splitlines() if line.strip()]
    assert entries
    assert "output" not in entries[-1]
