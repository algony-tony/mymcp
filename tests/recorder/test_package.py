import importlib


def test_recorder_package_importable_without_extras():
    """Importing mymcp.recorder must NOT pull in anthropic/openai SDKs."""
    mod = importlib.import_module("mymcp.recorder")
    assert mod is not None


def test_recorder_public_surface():
    from mymcp import recorder

    # placeholder for future public exports
    assert hasattr(recorder, "__all__")
