import sys
import importlib
import pytest


def reload_metrics():
    if "metrics" in sys.modules:
        del sys.modules["metrics"]

    # Clean up prometheus_client registry to avoid duplicates
    try:
        from prometheus_client import REGISTRY
        # Get the current collectors before reloading
        collectors_to_remove = []
        for collector in list(REGISTRY._collector_to_names.keys()):
            collector_name = getattr(collector, '_name', None)
            if collector_name and collector_name.startswith('mymcp_'):
                collectors_to_remove.append(collector)

        for collector in collectors_to_remove:
            try:
                REGISTRY.unregister(collector)
            except Exception:
                pass
    except (ImportError, AttributeError):
        pass

    return importlib.import_module("metrics")


def test_metrics_enabled_when_prometheus_installed():
    m = reload_metrics()
    assert m.ENABLED is True
    assert m.TOOL_CALLS is not None
    assert m.TOOL_DURATION is not None
    assert m.HTTP_REQUESTS is not None


def test_metrics_disabled_when_prometheus_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "prometheus_client", None)
    m = reload_metrics()
    assert m.ENABLED is False
    assert m.TOOL_CALLS is None
    assert m.TOOL_DURATION is None
    assert m.HTTP_REQUESTS is None


def test_tool_calls_has_correct_labels():
    m = reload_metrics()
    if not m.ENABLED:
        pytest.skip("prometheus_client not installed")
    m.TOOL_CALLS.labels(tool="bash_execute", role="rw", result="ok")


def test_tool_duration_has_custom_buckets():
    m = reload_metrics()
    if not m.ENABLED:
        pytest.skip("prometheus_client not installed")
    m.TOOL_DURATION.labels(tool="test_tool").observe(0.005)
    samples = m.TOOL_DURATION.collect()[0].samples
    bucket_bounds = {float(s.labels["le"]) for s in samples if s.name.endswith("_bucket")}
    assert 0.01 in bucket_bounds
    assert 30.0 in bucket_bounds
