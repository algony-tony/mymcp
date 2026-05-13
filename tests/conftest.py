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
