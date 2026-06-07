"""Hypothesis property tests for transfer ticket invariants.

The ticket store enforces two important guarantees:

1. Single-use: consume() returns True at most once per ticket.
2. TTL: lookup() refuses to return a ticket past expires_at.

Hand-written examples test these for a handful of cases; here we fuzz
around random mint/consume/expiry sequences.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from mymcp.transfer.tickets import TicketStore


def _store() -> TicketStore:
    return TicketStore()


@given(n_tickets=st.integers(min_value=1, max_value=20))
@settings(max_examples=50, deadline=None)
def test_consume_succeeds_exactly_once_per_ticket(n_tickets):
    """For N mints: each consume() returns True on first call, False after."""
    store = _store()
    ids = []
    for i in range(n_tickets):
        t = store.mint(
            op="upload",
            path=f"/tmp/x{i}",
            max_bytes=1,
            ttl_sec=60,
            created_by="t",
        )
        ids.append(t.ticket_id)

    # First consume succeeds exactly once per ticket.
    for tid in ids:
        assert store.consume(tid) is True

    # Subsequent consumes always fail — single-use invariant.
    for tid in ids:
        assert store.consume(tid) is False


@given(ttl=st.integers(min_value=1, max_value=10))
@settings(max_examples=30, deadline=None)
def test_lookup_refuses_after_ttl(ttl):
    """Past expires_at, lookup() returns None and classify() returns 'expired'.

    Uses a manual monkeypatch context — hypothesis can't reset a
    function-scoped fixture between generated inputs.
    """
    import pytest as _pytest

    from mymcp.transfer import tickets as tickets_mod

    current = [1_000_000.0]
    with _pytest.MonkeyPatch.context() as mp:
        mp.setattr(tickets_mod.time, "time", lambda: current[0])

        store = _store()
        t = store.mint(
            op="download",
            path="/tmp/x",
            max_bytes=1,
            ttl_sec=ttl,
            created_by="t",
        )
        # Within TTL: lookup returns the ticket.
        assert store.lookup(t.ticket_id) is not None
        assert store.classify(t.ticket_id) == "valid"

        # Past TTL: lookup refuses; classify reports 'expired'.
        current[0] += ttl + 1.0
        assert store.lookup(t.ticket_id) is None
        assert store.classify(t.ticket_id) == "expired"


@given(
    actions=st.lists(
        st.sampled_from(["mint", "consume", "lookup"]),
        min_size=1,
        max_size=30,
    )
)
@settings(max_examples=50, deadline=None)
def test_no_action_sequence_corrupts_the_store(actions):
    """Random mint/consume/lookup mixes must not raise or leak state.

    After any sequence: every successfully-consumed ticket is also no longer
    lookup-able. The store remains usable for further mints.
    """
    store = _store()
    minted_ids: list[str] = []
    consumed_ids: set[str] = set()

    for action in actions:
        if action == "mint":
            t = store.mint(
                op="upload",
                path="/tmp/x",
                max_bytes=1,
                ttl_sec=60,
                created_by="t",
            )
            minted_ids.append(t.ticket_id)
        elif action == "consume" and minted_ids:
            # Try consuming each currently-known id; record successes.
            for tid in minted_ids:
                if store.consume(tid):
                    consumed_ids.add(tid)
        elif action == "lookup" and minted_ids:
            for tid in minted_ids:
                got = store.lookup(tid)
                if tid in consumed_ids:
                    assert got is None, "consumed ticket must not lookup"

    # Store still mints after arbitrary churn.
    fresh = store.mint(
        op="download",
        path="/tmp/fresh",
        max_bytes=1,
        ttl_sec=60,
        created_by="t",
    )
    assert store.lookup(fresh.ticket_id) is not None
