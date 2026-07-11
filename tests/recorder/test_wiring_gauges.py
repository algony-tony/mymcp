import mymcp.recorder.wiring as wiring


class _FakeTailer:
    @staticmethod
    def pending_count():
        return 7


class _FakeMerge:
    _tailer = _FakeTailer


class _FakeSup:
    circuit_open = True
    _merge_cycle = _FakeMerge
    last_merge_ts_unix = 111
    last_merge_attempt_ts_unix = 222


def test_gauges_read_local_supervisor(monkeypatch):
    monkeypatch.setattr(wiring, "_active_supervisor", _FakeSup())
    assert wiring._observe_circuit_open()[0].value == 1
    assert wiring._observe_pending_events()[0].value == 7
    assert wiring._observe_last_success_ts()[0].value == 111
    assert wiring._observe_last_attempt_ts()[0].value == 222


def test_gauges_zero_when_no_supervisor(monkeypatch):
    monkeypatch.setattr(wiring, "_active_supervisor", None)
    assert wiring._observe_circuit_open()[0].value == 0
    assert wiring._observe_pending_events()[0].value == 0


def test_wiring_does_not_import_mcp_server():
    import inspect

    src = inspect.getsource(wiring)
    assert "from mymcp.mcp_server" not in src, "recorder wiring must not import the Python core"
    assert "import mymcp.mcp_server" not in src, "recorder wiring must not import the Python core"


def test_set_active_supervisor_publishes_reference():
    sentinel = object()
    wiring.set_active_supervisor(sentinel)
    try:
        assert wiring._active_supervisor is sentinel
    finally:
        wiring.set_active_supervisor(None)
