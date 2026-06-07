"""Hypothesis property tests for check_protected_path.

Path protection is a security boundary — anything that lands under a
protected directory after resolution (traversal, symlink, encoding) must
be blocked. These tests fuzz around the obvious bypass shapes.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from hypothesis import HealthCheck, assume, given, settings, strategies as st

# Path segments — strip control chars and chars we know break filesystems.
_safe_segment = st.text(
    alphabet=st.characters(
        blacklist_characters="/\x00\n\r",
        blacklist_categories=("Cs", "Cc"),
    ),
    min_size=1,
    max_size=10,
)


def _reset_runtime_protected() -> None:
    """Drop runtime registry between examples — the file tools module keeps
    a process-wide list that other tests may have written to."""
    from mymcp.tools import files as files_mod

    files_mod._runtime_protected.clear()


@given(
    segments=st.lists(_safe_segment, min_size=1, max_size=4),
    inject_traversal=st.booleans(),
)
@settings(
    max_examples=80,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_resolved_paths_under_protected_dir_are_blocked(
    tmp_path_factory, segments, inject_traversal
):
    """Any path that, after realpath, lives under the protected dir must be
    blocked — including when ``../`` segments are sprinkled in."""
    from mymcp.tools.files import check_protected_path, register_protected_path

    _reset_runtime_protected()
    base = tmp_path_factory.mktemp("base")
    protected = base / "secret"
    protected.mkdir()
    register_protected_path(str(protected), modes={"read", "write"})

    # Build candidate: start inside protected, optionally inject ../ then go back.
    parts = list(segments)
    if inject_traversal:
        parts = [*parts, ".."]
    candidate = protected.joinpath(*parts)

    blocked = check_protected_path(str(candidate), mode="read")

    # Ground truth: did the resolved candidate end up under `protected`?
    resolved = Path(os.path.realpath(str(candidate)))
    try:
        resolved.relative_to(protected)
        should_block = True
    except ValueError:
        should_block = False

    if should_block:
        assert blocked is not None, (
            f"resolved path {resolved} lives under {protected} but was not blocked"
        )
    else:
        # Anything that resolved outside protected MUST NOT be blocked here.
        assert blocked is None


@given(name=_safe_segment)
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_symlink_pointing_into_protected_is_blocked(tmp_path_factory, name):
    """A symlink whose target is inside the protected dir must be blocked
    even when the link itself lives outside."""
    if sys.platform == "win32":
        pytest.skip("symlink semantics differ on Windows")

    from mymcp.tools.files import check_protected_path, register_protected_path

    _reset_runtime_protected()
    base = tmp_path_factory.mktemp("base")
    protected = base / "secret"
    protected.mkdir()
    target = protected / "real.txt"
    target.write_text("x")

    link = base / f"innocent-{name}"
    assume(not link.exists())  # avoid name collisions across examples
    try:
        os.symlink(str(target), str(link))
    except OSError:
        pytest.skip("cannot create symlinks in this environment")

    register_protected_path(str(protected), modes={"read", "write"})

    # Direct access is blocked.
    assert check_protected_path(str(target), mode="read") is not None
    # Symlink whose target resolves under protected must also be blocked.
    assert check_protected_path(str(link), mode="read") is not None


@given(name=_safe_segment)
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_per_mode_filter_respected(tmp_path_factory, name):
    """A path registered as write-only-blocked must allow reads."""
    from mymcp.tools.files import check_protected_path, register_protected_path

    _reset_runtime_protected()
    base = tmp_path_factory.mktemp("base")
    protected = base / "writeguard"
    protected.mkdir()
    file_path = protected / f"f-{name}.bin"
    file_path.write_bytes(b"x")

    register_protected_path(str(protected), modes={"write"})  # write only

    # Read must pass.
    assert check_protected_path(str(file_path), mode="read") is None
    # Write must block.
    assert check_protected_path(str(file_path), mode="write") is not None
