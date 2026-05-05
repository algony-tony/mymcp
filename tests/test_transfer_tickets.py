import time

import pytest

from mymcp.transfer.tickets import Ticket, TicketStore


@pytest.fixture
def store():
    return TicketStore()


def test_mint_upload_returns_ticket_with_id(store):
    t = store.mint(
        op="upload",
        path="/tmp/foo.bin",
        max_bytes=1024,
        ttl_sec=60,
        created_by="rw-client",
    )
    assert isinstance(t, Ticket)
    assert t.op == "upload"
    assert t.path == "/tmp/foo.bin"
    assert t.max_bytes == 1024
    assert t.created_by == "rw-client"
    assert t.consumed is False
    assert isinstance(t.ticket_id, str) and len(t.ticket_id) >= 32
    assert t.expires_at > time.time()


def test_mint_download_ignores_max_bytes(store):
    t = store.mint(
        op="download",
        path="/etc/hostname",
        max_bytes=0,
        ttl_sec=60,
        created_by="ro-client",
    )
    assert t.op == "download"
    assert t.path == "/etc/hostname"


def test_lookup_returns_ticket(store):
    t = store.mint(op="upload", path="/tmp/x", max_bytes=1, ttl_sec=60, created_by="t")
    found = store.lookup(t.ticket_id)
    assert found is t


def test_lookup_unknown_ticket_returns_none(store):
    assert store.lookup("nope") is None


def test_two_mints_get_distinct_ids(store):
    a = store.mint(op="upload", path="/a", max_bytes=1, ttl_sec=60, created_by="t")
    b = store.mint(op="upload", path="/b", max_bytes=1, ttl_sec=60, created_by="t")
    assert a.ticket_id != b.ticket_id


def test_expired_ticket_lookup_returns_none(store, monkeypatch):
    t = store.mint(op="upload", path="/x", max_bytes=1, ttl_sec=60, created_by="t")
    real_time = time.time
    monkeypatch.setattr("mymcp.transfer.tickets.time.time", lambda: real_time() + 3600)
    assert store.lookup(t.ticket_id) is None


def test_consume_marks_ticket_consumed(store):
    t = store.mint(op="upload", path="/x", max_bytes=1, ttl_sec=60, created_by="t")
    ok = store.consume(t.ticket_id)
    assert ok is True
    assert store._tickets[t.ticket_id].consumed is True


def test_consume_already_consumed_returns_false(store):
    t = store.mint(op="upload", path="/x", max_bytes=1, ttl_sec=60, created_by="t")
    store.consume(t.ticket_id)
    assert store.consume(t.ticket_id) is False


def test_lookup_consumed_ticket_returns_none(store):
    t = store.mint(op="upload", path="/x", max_bytes=1, ttl_sec=60, created_by="t")
    store.consume(t.ticket_id)
    assert store.lookup(t.ticket_id) is None


def test_sweep_removes_expired_entries(store, monkeypatch):
    a = store.mint(op="upload", path="/a", max_bytes=1, ttl_sec=60, created_by="t")
    real_time = time.time
    monkeypatch.setattr("mymcp.transfer.tickets.time.time", lambda: real_time() + 3600)
    b = store.mint(op="upload", path="/b", max_bytes=1, ttl_sec=60, created_by="t")
    removed = store.sweep_expired()
    assert removed == 1
    assert a.ticket_id not in store._tickets
    assert b.ticket_id in store._tickets


def test_get_ticket_store_returns_same_instance():
    from mymcp.transfer import get_ticket_store, reset_ticket_store

    reset_ticket_store()
    a = get_ticket_store()
    b = get_ticket_store()
    assert a is b


def test_reset_ticket_store_returns_fresh_instance():
    from mymcp.transfer import get_ticket_store, reset_ticket_store

    a = get_ticket_store()
    reset_ticket_store()
    b = get_ticket_store()
    assert a is not b
