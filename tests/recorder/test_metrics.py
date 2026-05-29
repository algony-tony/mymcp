"""Smoke tests that recorder OTel instruments are registered."""

from mymcp.observability import instruments


def test_recorder_instruments_registered():
    assert instruments.recorder_events_consumed is not None
    assert instruments.recorder_merge_cycles is not None
    assert instruments.recorder_bootstrap_runs is not None
    assert instruments.recorder_llm_calls is not None
    assert instruments.recorder_llm_tokens is not None
    assert instruments.recorder_bash_probe_runs is not None
    assert instruments.recorder_event_loss is not None


def test_recorder_instruments_addable():
    """Counters accept .add() without raising."""
    instruments.recorder_events_consumed.add(1, {"tool": "bash_execute"})
    instruments.recorder_merge_cycles.add(1, {"result": "success"})
    instruments.recorder_llm_calls.add(1, {"phase": "merge", "result": "success"})
    instruments.recorder_llm_tokens.add(100, {"phase": "merge", "direction": "input"})
    instruments.recorder_bash_probe_runs.add(1, {"result": "success"})
