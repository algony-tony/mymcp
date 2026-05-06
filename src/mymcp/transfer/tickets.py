"""In-memory ticket store for file transfer endpoints.

Tickets are URL-safe random IDs that grant single-use, time-limited,
path-and-size-bounded access to PUT or GET on /files/raw/{ticket}.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from typing import Literal


@dataclass
class Ticket:
    ticket_id: str
    op: Literal["upload", "download"]
    path: str
    max_bytes: int
    expires_at: float
    created_by: str
    consumed: bool = False


class TicketStore:
    """Thread-safe in-memory ticket dictionary."""

    def __init__(self) -> None:
        self._tickets: dict[str, Ticket] = {}
        self._lock = threading.Lock()

    def mint(
        self,
        *,
        op: Literal["upload", "download"],
        path: str,
        max_bytes: int,
        ttl_sec: int,
        created_by: str,
    ) -> Ticket:
        ticket_id = secrets.token_urlsafe(24)
        ticket = Ticket(
            ticket_id=ticket_id,
            op=op,
            path=path,
            max_bytes=max_bytes,
            expires_at=time.time() + ttl_sec,
            created_by=created_by,
        )
        with self._lock:
            self._tickets[ticket_id] = ticket
        return ticket

    def lookup(self, ticket_id: str) -> Ticket | None:
        with self._lock:
            t = self._tickets.get(ticket_id)
            if t is None:
                return None
            if t.consumed:
                return None
            if t.expires_at <= time.time():
                return None
            return t

    def classify(self, ticket_id: str) -> Literal["valid", "missing", "consumed", "expired"]:
        """Classify a ticket atomically under the lock.

        Use after `lookup()` returns None to distinguish why, without racing
        sweep_expired/consume.
        """
        with self._lock:
            t = self._tickets.get(ticket_id)
            if t is None:
                return "missing"
            if t.consumed:
                return "consumed"
            if t.expires_at <= time.time():
                return "expired"
            return "valid"

    def consume(self, ticket_id: str) -> bool:
        """Mark a ticket consumed. Returns False if already consumed/missing."""
        with self._lock:
            t = self._tickets.get(ticket_id)
            if t is None or t.consumed:
                return False
            t.consumed = True
            return True

    def sweep_expired(self) -> int:
        """Remove expired or consumed entries. Returns number removed."""
        now = time.time()
        with self._lock:
            stale = [tid for tid, t in self._tickets.items() if t.consumed or t.expires_at <= now]
            for tid in stale:
                del self._tickets[tid]
            return len(stale)


_store: TicketStore | None = None
_store_lock = threading.Lock()


def get_ticket_store() -> TicketStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = TicketStore()
    return _store


def reset_ticket_store() -> None:
    """Test helper. Drops the singleton."""
    global _store
    with _store_lock:
        _store = None
