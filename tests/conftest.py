import pytest


@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    return request.param


@pytest.fixture(autouse=True)
def _reset_observability():
    from mymcp.observability import reset_for_tests

    reset_for_tests()
    yield
    reset_for_tests()


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    """Isolate the global ``get_settings()`` lru_cache between tests.

    Several tests poison the cache by setting ``MYMCP_*`` env vars and calling
    ``importlib.reload(mymcp.config)`` (e.g. ``test_transfer_settings_env_override``).
    monkeypatch restores the env vars on teardown, but the cached ``Settings``
    object survives, so a randomly-ordered run could leak a non-default value
    (e.g. ``transfer_enabled=False``) into an unrelated test and fail it
    (404 transfer_disabled). Clearing at setup *and* teardown guarantees each
    test reads the live environment. Imported inside the fixture so it survives
    module reloads. Mirrors the observability / ticket-store reset fixtures.
    """
    import mymcp.config as cfg

    cfg.reset_settings_cache()
    yield
    cfg.reset_settings_cache()
