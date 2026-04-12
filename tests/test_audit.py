import json
import os
import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def audit_config(tmp_path):
    with patch.multiple(
        "config",
        AUDIT_ENABLED=True,
        AUDIT_LOG_DIR=str(tmp_path),
        AUDIT_MAX_BYTES=1024 * 1024,
        AUDIT_BACKUP_COUNT=2,
    ):
        import audit
        audit._logger = None
        audit._setup_done = False
        yield tmp_path


def test_log_tool_call_writes_json_line(audit_config):
    import audit
    audit.log_tool_call(
        token_name="test-client",
        role="rw",
        ip="127.0.0.1",
        tool="bash_execute",
        params={"command": "ls"},
        result="success",
        duration_ms=42,
    )

    log_file = audit_config / "audit.log"
    assert log_file.exists()
    line = log_file.read_text().strip()
    record = json.loads(line)
    assert record["token_name"] == "test-client"
    assert record["role"] == "rw"
    assert record["ip"] == "127.0.0.1"
    assert record["tool"] == "bash_execute"
    assert record["params"] == {"command": "ls"}
    assert record["result"] == "success"
    assert record["duration_ms"] == 42
    assert "ts" in record
    assert "reason" not in record


def test_log_denied_includes_reason(audit_config):
    import audit
    audit.log_tool_call(
        token_name="readonly-bot",
        role="ro",
        ip="10.0.0.1",
        tool="write_file",
        params={"file_path": "/tmp/x"},
        result="denied",
        reason="ro_role",
    )

    log_file = audit_config / "audit.log"
    record = json.loads(log_file.read_text().strip())
    assert record["result"] == "denied"
    assert record["reason"] == "ro_role"
    assert "duration_ms" not in record


def test_log_error_includes_reason(audit_config):
    import audit
    audit.log_tool_call(
        token_name="client",
        role="rw",
        ip="10.0.0.1",
        tool="bash_execute",
        params={"command": "bad"},
        result="error",
        reason="TimeoutError",
    )

    log_file = audit_config / "audit.log"
    record = json.loads(log_file.read_text().strip())
    assert record["result"] == "error"
    assert record["reason"] == "TimeoutError"


def test_audit_disabled_writes_nothing(tmp_path):
    with patch.multiple(
        "config",
        AUDIT_ENABLED=False,
        AUDIT_LOG_DIR=str(tmp_path),
        AUDIT_MAX_BYTES=1024 * 1024,
        AUDIT_BACKUP_COUNT=2,
    ):
        import audit
        audit._logger = None
        audit._setup_done = False
        audit.log_tool_call(
            token_name="x",
            role="rw",
            ip="1.2.3.4",
            tool="glob",
            params={"pattern": "*"},
            result="success",
        )
        log_file = tmp_path / "audit.log"
        assert not log_file.exists()


def test_multiple_entries_are_separate_lines(audit_config):
    import audit
    for i in range(3):
        audit.log_tool_call(
            token_name=f"client-{i}",
            role="rw",
            ip="127.0.0.1",
            tool="glob",
            params={"pattern": "*"},
            result="success",
            duration_ms=i,
        )

    log_file = audit_config / "audit.log"
    lines = [l for l in log_file.read_text().strip().split("\n") if l]
    assert len(lines) == 3
    for line in lines:
        json.loads(line)
