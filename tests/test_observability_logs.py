import io
import json
import logging

from mymcp.observability.logs import configure_logging
from mymcp.observability.request_id import current_request_id


def test_log_record_is_json_with_request_id():
    buf = io.StringIO()
    configure_logging(level="INFO", stream=buf)

    token = current_request_id.set("rid-test-001")
    try:
        logging.getLogger("mymcp.test").info("hello", extra={"foo": "bar"})
    finally:
        current_request_id.reset(token)

    line = buf.getvalue().strip().splitlines()[-1]
    record = json.loads(line)
    assert record["message"] == "hello"
    assert record["request_id"] == "rid-test-001"
    assert record["foo"] == "bar"
    assert record["levelname"] == "INFO"


def test_log_record_request_id_absent_when_unset():
    buf = io.StringIO()
    configure_logging(level="INFO", stream=buf)
    logging.getLogger("mymcp.test").info("no-rid")
    line = buf.getvalue().strip().splitlines()[-1]
    record = json.loads(line)
    assert record.get("request_id") is None


def test_configure_logging_idempotent():
    buf1 = io.StringIO()
    buf2 = io.StringIO()
    configure_logging(level="INFO", stream=buf1)
    configure_logging(level="INFO", stream=buf2)
    logging.getLogger("mymcp.test").info("once")
    assert "once" in buf2.getvalue()
    assert "once" not in buf1.getvalue()
