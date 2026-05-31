import json
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def audit_config(tmp_path):
    with patch.multiple(
        "mymcp.config",
        AUDIT_ENABLED=True,
        AUDIT_LOG_DIR=str(tmp_path),
        AUDIT_MAX_BYTES=1024 * 1024,
        AUDIT_BACKUP_COUNT=2,
    ):
        from mymcp import audit

        audit._logger = None
        audit._setup_done = False
        yield tmp_path


def test_log_tool_call_writes_json_line(audit_config):
    from mymcp import audit

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
    from mymcp import audit

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
    from mymcp import audit

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


def test_log_error_includes_error_code_and_message(audit_config):
    from mymcp import audit

    audit.log_tool_call(
        token_name="client",
        role="ro",
        ip="10.0.0.1",
        tool="read_file",
        params={"file_path": "/protected/file"},
        result="error",
        error_code="ProtectedPath",
        error_message="Access denied: path is within protected directory /opt/mymcp",
        duration_ms=0,
    )

    log_file = audit_config / "audit.log"
    record = json.loads(log_file.read_text().strip())
    assert record["result"] == "error"
    assert record["error_code"] == "ProtectedPath"
    assert record["error_message"].startswith("Access denied")
    assert record["duration_ms"] == 0
    assert "reason" not in record


def test_audit_disabled_writes_nothing(tmp_path):
    with patch.multiple(
        "mymcp.config",
        AUDIT_ENABLED=False,
        AUDIT_LOG_DIR=str(tmp_path),
        AUDIT_MAX_BYTES=1024 * 1024,
        AUDIT_BACKUP_COUNT=2,
    ):
        from mymcp import audit

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
    from mymcp import audit

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


def test_ts_field_is_iso_utc_with_timezone(audit_config):
    """ts must be ISO-8601 UTC ('+00:00' or 'Z'), proving timezone.utc was used."""
    from mymcp import audit

    audit.log_tool_call(
        token_name="t",
        role="rw",
        ip="1.1.1.1",
        tool="glob",
        params={},
        result="success",
    )
    record = json.loads((audit_config / "audit.log").read_text().strip())
    ts = record["ts"]
    assert ts.endswith("+00:00") or ts.endswith("Z"), f"ts not UTC: {ts!r}"
    assert "T" in ts


def test_output_field_present_when_passed(audit_config):
    """output kwarg must land in the record verbatim."""
    from mymcp import audit

    payload = {"stdout_head": "hi", "stdout_tail": "bye", "exit_code": 0}
    audit.log_tool_call(
        token_name="t",
        role="rw",
        ip="1.1.1.1",
        tool="bash_execute",
        params={"command": "echo"},
        result="ok",
        output=payload,
    )
    record = json.loads((audit_config / "audit.log").read_text().strip())
    assert record["output"] == payload


def test_output_field_absent_when_none(audit_config):
    """output=None must NOT appear in record (kills `is None` → `is not None`)."""
    from mymcp import audit

    audit.log_tool_call(
        token_name="t",
        role="rw",
        ip="1.1.1.1",
        tool="glob",
        params={},
        result="ok",
        output=None,
    )
    record = json.loads((audit_config / "audit.log").read_text().strip())
    assert "output" not in record


def test_error_message_absent_when_none(audit_config):
    """error_message=None must NOT add the key."""
    from mymcp import audit

    audit.log_tool_call(
        token_name="t",
        role="rw",
        ip="1.1.1.1",
        tool="glob",
        params={},
        result="ok",
        error_code=None,
        error_message=None,
    )
    record = json.loads((audit_config / "audit.log").read_text().strip())
    assert "error_code" not in record
    assert "error_message" not in record


def test_duration_ms_zero_is_recorded(audit_config):
    """duration_ms=0 must still be logged (kills `is not None` → truthy mutation)."""
    from mymcp import audit

    audit.log_tool_call(
        token_name="t",
        role="rw",
        ip="1.1.1.1",
        tool="glob",
        params={},
        result="ok",
        duration_ms=0,
    )
    record = json.loads((audit_config / "audit.log").read_text().strip())
    assert record["duration_ms"] == 0


def test_request_id_propagated_into_record(audit_config):
    """When request_id contextvar is set, audit entry includes it (32 hex)."""
    from mymcp import audit
    from mymcp.observability.request_id import current_request_id

    token = current_request_id.set("abc-123-xyz")
    try:
        audit.log_tool_call(
            token_name="t",
            role="rw",
            ip="1.1.1.1",
            tool="glob",
            params={},
            result="ok",
        )
    finally:
        current_request_id.reset(token)

    record = json.loads((audit_config / "audit.log").read_text().strip())
    assert record["request_id"] == "abc-123-xyz"


def test_request_id_absent_when_unset(audit_config):
    from mymcp import audit
    from mymcp.observability.request_id import current_request_id

    token = current_request_id.set(None)
    try:
        audit.log_tool_call(
            token_name="t",
            role="rw",
            ip="1.1.1.1",
            tool="glob",
            params={},
            result="ok",
        )
    finally:
        current_request_id.reset(token)

    record = json.loads((audit_config / "audit.log").read_text().strip())
    assert "request_id" not in record


def test_audit_log_is_info_level(audit_config):
    """The audit logger writes at INFO. Kills mutations changing the level."""
    import logging

    from mymcp import audit

    # Trigger setup
    audit.log_tool_call(
        token_name="t",
        role="rw",
        ip="1.1.1.1",
        tool="glob",
        params={},
        result="ok",
    )
    logger = logging.getLogger("mymcp.audit")
    assert logger.level == logging.INFO
    assert logger.propagate is False


def test_audit_setup_creates_log_dir(audit_config, tmp_path):
    """_setup must create the log dir if missing (kills exist_ok mutations)."""
    from unittest.mock import patch

    sub = tmp_path / "sub" / "audit"
    assert not sub.exists()

    with patch.multiple(
        "mymcp.config",
        AUDIT_ENABLED=True,
        AUDIT_LOG_DIR=str(sub),
        AUDIT_MAX_BYTES=1024,
        AUDIT_BACKUP_COUNT=1,
    ):
        from mymcp import audit

        audit._logger = None
        audit._setup_done = False
        audit.log_tool_call(
            token_name="t",
            role="rw",
            ip="1.1.1.1",
            tool="glob",
            params={},
            result="ok",
        )
    assert sub.is_dir()
    assert (sub / "audit.log").exists()


def test_audit_write_failure_increments_metric(audit_config):
    """Exception inside json.dumps → audit_write_failures counter +1, exception re-raised."""
    from unittest.mock import patch

    import pytest

    from mymcp import audit

    audit.log_tool_call(  # warm setup
        token_name="t",
        role="rw",
        ip="1.1.1.1",
        tool="glob",
        params={},
        result="ok",
    )

    captured = []

    class _Counter:
        def add(self, n):
            captured.append(n)

    with (
        patch("mymcp.observability.instruments.audit_write_failures", _Counter()),
        patch.object(audit._logger, "info", side_effect=RuntimeError("disk full")),
        pytest.raises(RuntimeError),
    ):
        audit.log_tool_call(
            token_name="t",
            role="rw",
            ip="1.1.1.1",
            tool="glob",
            params={},
            result="ok",
        )
    assert captured == [1]
