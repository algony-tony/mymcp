# File Transfer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add binary / large-file upload and download to mymcp via two MCP tools that mint one-time signed URLs and two FastAPI bypass endpoints that stream raw bytes — file content never enters the LLM context.

**Architecture:** Two new MCP tools (`prepare_upload`, `prepare_download`) hand back a single-use ticket. Two new HTTP endpoints (`PUT/GET /files/raw/{ticket}`) authenticate via the ticket alone — no Bearer header needed. Tickets live in an in-memory store keyed by URL-safe random IDs, scoped to a path, byte-cap, and 5-minute TTL. All operations reuse `check_protected_path` and append to the existing audit log.

**Tech Stack:** FastAPI + Starlette streaming responses, pydantic-settings for config, `secrets.token_urlsafe` for tickets, `pytest` + `anyio` (asyncio backend), `httpx.ASGITransport` for endpoint tests — same patterns as the existing test suite.

**Spec:** `docs/superpowers/specs/2026-05-04-file-transfer-design.md`

**Branch:** `feat/file-transfer-design` (already created during brainstorming)

---

## File Structure

**Created:**
- `src/mymcp/transfer/__init__.py` — package marker, re-exports `TicketStore`, `get_ticket_store`
- `src/mymcp/transfer/tickets.py` — `Ticket` dataclass + `TicketStore` (mint / lookup / consume / sweep)
- `src/mymcp/transfer/endpoints.py` — `register_transfer_routes(app)` adds `PUT/GET /files/raw/{ticket}`
- `src/mymcp/tools/transfer.py` — `prepare_upload`, `prepare_download` async functions returning JSON-ready dicts
- `tests/test_transfer_tickets.py`
- `tests/test_transfer_tools.py`
- `tests/test_transfer_endpoints.py`
- `tests/test_transfer_integration.py`

**Modified:**
- `src/mymcp/config.py` — add settings + `_LEGACY_ATTRS` entries
- `src/mymcp/mcp_server.py` — extend `READ_TOOLS` / `WRITE_TOOLS` / `_TOOL_DEFS` / `dispatch_tool`
- `src/mymcp/server.py` — call `register_transfer_routes(app)`, ensure `McpAuthMiddleware` skips `/files/raw/`
- `CHANGELOG.md` — entry under `[Unreleased]`

**TDD discipline:** every task writes the failing test first, runs it to confirm it fails, then implements minimally and re-runs. Commit after each task. Use `pytest -v --benchmark-disable -x` for fast feedback.

---

## Task 1: Add transfer config settings

**Files:**
- Modify: `src/mymcp/config.py`
- Test: `tests/test_config_settings.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config_settings.py` (append, do not replace existing tests):

```python
def test_transfer_settings_defaults(monkeypatch):
    for var in (
        "MYMCP_TRANSFER_ENABLED",
        "MYMCP_TRANSFER_MAX_BYTES",
        "MYMCP_TRANSFER_DEFAULT_TTL_SEC",
        "MYMCP_TRANSFER_MAX_TTL_SEC",
        "MYMCP_PUBLIC_BASE_URL",
    ):
        monkeypatch.delenv(var, raising=False)
    from mymcp import config

    config.reset_settings_cache()
    s = config.get_settings()
    assert s.transfer_enabled is True
    assert s.transfer_max_bytes == 2 * 1024 * 1024 * 1024
    assert s.transfer_default_ttl_sec == 300
    assert s.transfer_max_ttl_sec == 900
    assert s.public_base_url == ""


def test_transfer_settings_env_override(monkeypatch):
    monkeypatch.setenv("MYMCP_TRANSFER_ENABLED", "false")
    monkeypatch.setenv("MYMCP_TRANSFER_MAX_BYTES", "5242880")
    monkeypatch.setenv("MYMCP_TRANSFER_DEFAULT_TTL_SEC", "60")
    monkeypatch.setenv("MYMCP_TRANSFER_MAX_TTL_SEC", "120")
    monkeypatch.setenv("MYMCP_PUBLIC_BASE_URL", "https://mcp.example.com")
    from mymcp import config

    config.reset_settings_cache()
    s = config.get_settings()
    assert s.transfer_enabled is False
    assert s.transfer_max_bytes == 5_242_880
    assert s.transfer_default_ttl_sec == 60
    assert s.transfer_max_ttl_sec == 120
    assert s.public_base_url == "https://mcp.example.com"
    assert config.TRANSFER_ENABLED is False
    assert config.TRANSFER_MAX_BYTES == 5_242_880
    assert config.PUBLIC_BASE_URL == "https://mcp.example.com"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_config_settings.py::test_transfer_settings_defaults tests/test_config_settings.py::test_transfer_settings_env_override -v --benchmark-disable`

Expected: FAIL with `AttributeError` for `transfer_enabled` / `TRANSFER_ENABLED` etc.

- [ ] **Step 3: Implement the settings**

In `src/mymcp/config.py`, inside `class Settings`, append after the `protected_paths` field:

```python
    # File transfer (binary / large file)
    transfer_enabled: bool = Field(default=True)
    transfer_max_bytes: int = Field(default=2 * 1024 * 1024 * 1024)
    transfer_default_ttl_sec: int = Field(default=300)
    transfer_max_ttl_sec: int = Field(default=900)
    public_base_url: str = Field(default="")
```

In the same file, extend `_LEGACY_ATTRS` with these entries (keep alphabetical-ish, append at the end before the closing `}`):

```python
    "TRANSFER_ENABLED": "transfer_enabled",
    "TRANSFER_MAX_BYTES": "transfer_max_bytes",
    "TRANSFER_DEFAULT_TTL_SEC": "transfer_default_ttl_sec",
    "TRANSFER_MAX_TTL_SEC": "transfer_max_ttl_sec",
    "PUBLIC_BASE_URL": "public_base_url",
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_config_settings.py -v --benchmark-disable`

Expected: all tests PASS, including the two new ones.

- [ ] **Step 5: Commit**

```bash
git add src/mymcp/config.py tests/test_config_settings.py
git commit -m "feat(config): add file transfer settings"
```

---

## Task 2: TicketStore — mint and lookup

**Files:**
- Create: `src/mymcp/transfer/__init__.py`
- Create: `src/mymcp/transfer/tickets.py`
- Test: `tests/test_transfer_tickets.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_transfer_tickets.py`:

```python
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
    t = store.mint(
        op="upload", path="/tmp/x", max_bytes=1, ttl_sec=60, created_by="t"
    )
    found = store.lookup(t.ticket_id)
    assert found is t


def test_lookup_unknown_ticket_returns_none(store):
    assert store.lookup("nope") is None


def test_two_mints_get_distinct_ids(store):
    a = store.mint(op="upload", path="/a", max_bytes=1, ttl_sec=60, created_by="t")
    b = store.mint(op="upload", path="/b", max_bytes=1, ttl_sec=60, created_by="t")
    assert a.ticket_id != b.ticket_id
```

Create `src/mymcp/transfer/__init__.py` as an empty file (will re-export later) — but **do not implement `tickets.py` yet**.

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_transfer_tickets.py -v --benchmark-disable`

Expected: FAIL with `ModuleNotFoundError: No module named 'mymcp.transfer.tickets'`.

- [ ] **Step 3: Implement TicketStore (mint + lookup only)**

Create `src/mymcp/transfer/tickets.py`:

```python
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
            return self._tickets.get(ticket_id)
```

Update `src/mymcp/transfer/__init__.py`:

```python
"""File transfer support: tickets, tools, and bypass HTTP endpoints."""

from mymcp.transfer.tickets import Ticket, TicketStore

__all__ = ["Ticket", "TicketStore"]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_transfer_tickets.py -v --benchmark-disable`

Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mymcp/transfer/__init__.py src/mymcp/transfer/tickets.py tests/test_transfer_tickets.py
git commit -m "feat(transfer): add TicketStore with mint and lookup"
```

---

## Task 3: TicketStore — expiry, consume, and sweep

**Files:**
- Modify: `src/mymcp/transfer/tickets.py`
- Test: `tests/test_transfer_tickets.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_transfer_tickets.py`:

```python
def test_expired_ticket_lookup_returns_none(store, monkeypatch):
    t = store.mint(
        op="upload", path="/x", max_bytes=1, ttl_sec=60, created_by="t"
    )
    real_time = time.time
    monkeypatch.setattr(
        "mymcp.transfer.tickets.time.time", lambda: real_time() + 3600
    )
    assert store.lookup(t.ticket_id) is None


def test_consume_marks_ticket_consumed(store):
    t = store.mint(
        op="upload", path="/x", max_bytes=1, ttl_sec=60, created_by="t"
    )
    ok = store.consume(t.ticket_id)
    assert ok is True
    assert store._tickets[t.ticket_id].consumed is True


def test_consume_already_consumed_returns_false(store):
    t = store.mint(
        op="upload", path="/x", max_bytes=1, ttl_sec=60, created_by="t"
    )
    store.consume(t.ticket_id)
    assert store.consume(t.ticket_id) is False


def test_lookup_consumed_ticket_returns_none(store):
    t = store.mint(
        op="upload", path="/x", max_bytes=1, ttl_sec=60, created_by="t"
    )
    store.consume(t.ticket_id)
    assert store.lookup(t.ticket_id) is None


def test_sweep_removes_expired_entries(store, monkeypatch):
    a = store.mint(op="upload", path="/a", max_bytes=1, ttl_sec=60, created_by="t")
    real_time = time.time
    monkeypatch.setattr(
        "mymcp.transfer.tickets.time.time", lambda: real_time() + 3600
    )
    b = store.mint(op="upload", path="/b", max_bytes=1, ttl_sec=60, created_by="t")
    removed = store.sweep_expired()
    assert removed == 1
    assert a.ticket_id not in store._tickets
    assert b.ticket_id in store._tickets
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_transfer_tickets.py -v --benchmark-disable`

Expected: 5 new tests FAIL — `consume` / `sweep_expired` undefined and `lookup` does not check expiry.

- [ ] **Step 3: Extend TicketStore**

In `src/mymcp/transfer/tickets.py`, replace the `lookup` method and add `consume` and `sweep_expired`:

```python
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

    def consume(self, ticket_id: str) -> bool:
        """Mark a ticket consumed. Returns False if already consumed/missing."""
        with self._lock:
            t = self._tickets.get(ticket_id)
            if t is None or t.consumed:
                return False
            t.consumed = True
            return True

    def sweep_expired(self) -> int:
        """Remove expired entries. Returns number removed."""
        now = time.time()
        with self._lock:
            stale = [tid for tid, t in self._tickets.items() if t.expires_at <= now]
            for tid in stale:
                del self._tickets[tid]
            return len(stale)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_transfer_tickets.py -v --benchmark-disable`

Expected: all 10 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mymcp/transfer/tickets.py tests/test_transfer_tickets.py
git commit -m "feat(transfer): add ticket expiry, consume, and sweep"
```

---

## Task 4: TicketStore singleton accessor

**Files:**
- Modify: `src/mymcp/transfer/__init__.py`
- Modify: `src/mymcp/transfer/tickets.py`
- Test: `tests/test_transfer_tickets.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_transfer_tickets.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_transfer_tickets.py -v --benchmark-disable`

Expected: FAIL with `ImportError: cannot import name 'get_ticket_store'`.

- [ ] **Step 3: Add singleton accessor**

Append to `src/mymcp/transfer/tickets.py`:

```python
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
```

Replace `src/mymcp/transfer/__init__.py` with:

```python
"""File transfer support: tickets, tools, and bypass HTTP endpoints."""

from mymcp.transfer.tickets import (
    Ticket,
    TicketStore,
    get_ticket_store,
    reset_ticket_store,
)

__all__ = ["Ticket", "TicketStore", "get_ticket_store", "reset_ticket_store"]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_transfer_tickets.py -v --benchmark-disable`

Expected: all 12 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mymcp/transfer/tickets.py src/mymcp/transfer/__init__.py tests/test_transfer_tickets.py
git commit -m "feat(transfer): add module-level ticket store singleton"
```

---

## Task 5: prepare_upload tool — happy path

**Files:**
- Create: `src/mymcp/tools/transfer.py`
- Test: `tests/test_transfer_tools.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_transfer_tools.py`:

```python
import pytest

from mymcp.transfer import reset_ticket_store
from mymcp.tools.transfer import prepare_upload


@pytest.fixture(autouse=True)
def _reset():
    reset_ticket_store()
    yield
    reset_ticket_store()


@pytest.mark.anyio
async def test_prepare_upload_returns_url_and_ticket(tmp_path, monkeypatch):
    monkeypatch.setattr("mymcp.tools.transfer._public_base_url", lambda: "https://srv.example.com")
    dest = str(tmp_path / "foo.bin")
    result = await prepare_upload(
        dest_path=dest,
        max_bytes=1024,
        expires_in=120,
        token_name="rw-client",
    )
    assert result["success"] is True
    assert result["method"] == "PUT"
    assert result["url"].startswith("https://srv.example.com/files/raw/")
    assert result["dest_path"] == dest
    assert result["max_bytes"] == 1024
    assert result["expires_in"] == 120
    assert "expires_at" in result
    assert "curl_example" in result
    assert "instructions" in result
    assert "ticket" in result
    assert result["url"].endswith(result["ticket"])
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_transfer_tools.py -v --benchmark-disable`

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement prepare_upload**

Create `src/mymcp/tools/transfer.py`:

```python
"""MCP tools that mint signed URLs for binary / large file transfer.

The tools return JSON-ready dicts. Actual byte transfer happens on the
/files/raw/{ticket} endpoint and never enters the LLM context.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from mymcp import config
from mymcp.tools.files import check_protected_path
from mymcp.transfer import get_ticket_store


def _public_base_url() -> str:
    """Return public base URL with no trailing slash. Empty string means use request Host."""
    return config.PUBLIC_BASE_URL.rstrip("/")


def _build_url(ticket_id: str) -> str:
    base = _public_base_url()
    if not base:
        return f"/files/raw/{ticket_id}"
    return f"{base}/files/raw/{ticket_id}"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def prepare_upload(
    *,
    dest_path: str,
    max_bytes: int | None = None,
    expires_in: int | None = None,
    overwrite: bool = True,
    token_name: str = "unknown",
) -> dict:
    if not config.TRANSFER_ENABLED:
        return {
            "success": False,
            "error": "TransferDisabled",
            "message": "File transfer feature is disabled on this server.",
        }
    if not os.path.isabs(dest_path):
        return {
            "success": False,
            "error": "InvalidPath",
            "message": "dest_path must be an absolute path.",
        }
    err = check_protected_path(dest_path)
    if err:
        return {"success": False, "error": "ProtectedPath", "message": err}
    if not overwrite and os.path.exists(dest_path):
        return {
            "success": False,
            "error": "FileExists",
            "message": f"{dest_path} already exists and overwrite=False.",
        }

    cap = config.TRANSFER_MAX_BYTES
    requested = cap if max_bytes is None else int(max_bytes)
    if requested <= 0:
        return {
            "success": False,
            "error": "InvalidMaxBytes",
            "message": "max_bytes must be positive.",
        }
    effective_max = min(requested, cap)

    ttl_default = config.TRANSFER_DEFAULT_TTL_SEC
    ttl_max = config.TRANSFER_MAX_TTL_SEC
    requested_ttl = ttl_default if expires_in is None else int(expires_in)
    if requested_ttl <= 0:
        return {
            "success": False,
            "error": "InvalidExpiresIn",
            "message": "expires_in must be positive.",
        }
    effective_ttl = min(requested_ttl, ttl_max)

    ticket = get_ticket_store().mint(
        op="upload",
        path=dest_path,
        max_bytes=effective_max,
        ttl_sec=effective_ttl,
        created_by=token_name,
    )
    url = _build_url(ticket.ticket_id)
    return {
        "success": True,
        "url": url,
        "method": "PUT",
        "ticket": ticket.ticket_id,
        "expires_in": effective_ttl,
        "expires_at": _iso(ticket.expires_at),
        "max_bytes": effective_max,
        "dest_path": dest_path,
        "curl_example": f"curl -fsS -T /local/path/to/file '{url}'",
        "instructions": (
            "Run the curl above from the MCP client's local shell. "
            "The file's raw bytes go in the request body. On success the "
            'server returns {"ok": true, "path": "...", "bytes_written": N}.'
        ),
        "on_error": (
            "If the URL returns 4xx, read the JSON error.hint field. "
            "Tickets are single-use; do not retry the same URL — "
            "call prepare_upload again to mint a fresh one."
        ),
    }
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_transfer_tools.py -v --benchmark-disable`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mymcp/tools/transfer.py tests/test_transfer_tools.py
git commit -m "feat(transfer): add prepare_upload tool"
```

---

## Task 6: prepare_upload — error paths

**Files:**
- Modify: `tests/test_transfer_tools.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_transfer_tools.py`:

```python
@pytest.mark.anyio
async def test_prepare_upload_rejects_relative_path():
    r = await prepare_upload(dest_path="relative/path", token_name="t")
    assert r["success"] is False
    assert r["error"] == "InvalidPath"


@pytest.mark.anyio
async def test_prepare_upload_rejects_protected_path(monkeypatch):
    monkeypatch.setattr("mymcp.config.PROTECTED_PATHS", ["/etc"])
    r = await prepare_upload(dest_path="/etc/passwd", token_name="t")
    assert r["success"] is False
    assert r["error"] == "ProtectedPath"


@pytest.mark.anyio
async def test_prepare_upload_disabled(monkeypatch):
    monkeypatch.setenv("MYMCP_TRANSFER_ENABLED", "false")
    from mymcp import config

    config.reset_settings_cache()
    try:
        r = await prepare_upload(dest_path="/tmp/x", token_name="t")
        assert r["success"] is False
        assert r["error"] == "TransferDisabled"
    finally:
        monkeypatch.delenv("MYMCP_TRANSFER_ENABLED", raising=False)
        config.reset_settings_cache()


@pytest.mark.anyio
async def test_prepare_upload_clamps_max_bytes_and_ttl(tmp_path, monkeypatch):
    monkeypatch.setenv("MYMCP_TRANSFER_MAX_BYTES", "1024")
    monkeypatch.setenv("MYMCP_TRANSFER_MAX_TTL_SEC", "60")
    from mymcp import config

    config.reset_settings_cache()
    try:
        r = await prepare_upload(
            dest_path=str(tmp_path / "f"),
            max_bytes=10**9,
            expires_in=10**6,
            token_name="t",
        )
        assert r["success"] is True
        assert r["max_bytes"] == 1024
        assert r["expires_in"] == 60
    finally:
        monkeypatch.delenv("MYMCP_TRANSFER_MAX_BYTES", raising=False)
        monkeypatch.delenv("MYMCP_TRANSFER_MAX_TTL_SEC", raising=False)
        config.reset_settings_cache()


@pytest.mark.anyio
async def test_prepare_upload_overwrite_false_rejects_existing(tmp_path):
    p = tmp_path / "exists.bin"
    p.write_bytes(b"x")
    r = await prepare_upload(dest_path=str(p), overwrite=False, token_name="t")
    assert r["success"] is False
    assert r["error"] == "FileExists"


@pytest.mark.anyio
async def test_prepare_upload_invalid_max_bytes(tmp_path):
    r = await prepare_upload(dest_path=str(tmp_path / "f"), max_bytes=0, token_name="t")
    assert r["success"] is False
    assert r["error"] == "InvalidMaxBytes"
```

- [ ] **Step 2: Run the tests to verify they pass (or fail with details)**

Run: `pytest tests/test_transfer_tools.py -v --benchmark-disable`

Expected: all 7 tests PASS — Task 5 already implemented these branches; this task verifies them.

If any FAIL, fix the implementation in `src/mymcp/tools/transfer.py` to match the assertions before committing.

- [ ] **Step 3: Commit**

```bash
git add tests/test_transfer_tools.py
git commit -m "test(transfer): cover prepare_upload error paths"
```

---

## Task 7: prepare_download tool

**Files:**
- Modify: `src/mymcp/tools/transfer.py`
- Modify: `tests/test_transfer_tools.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_transfer_tools.py`:

```python
from mymcp.tools.transfer import prepare_download  # noqa: E402


@pytest.mark.anyio
async def test_prepare_download_returns_url_and_size(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "mymcp.tools.transfer._public_base_url", lambda: "https://srv.example.com"
    )
    src = tmp_path / "thing.bin"
    src.write_bytes(b"hello-bytes")
    r = await prepare_download(src_path=str(src), expires_in=60, token_name="ro")
    assert r["success"] is True
    assert r["method"] == "GET"
    assert r["src_path"] == str(src)
    assert r["size"] == len(b"hello-bytes")
    assert r["url"].startswith("https://srv.example.com/files/raw/")
    assert "curl_example" in r and "-o " in r["curl_example"]


@pytest.mark.anyio
async def test_prepare_download_missing_file(tmp_path):
    r = await prepare_download(src_path=str(tmp_path / "nope"), token_name="t")
    assert r["success"] is False
    assert r["error"] == "FileNotFound"


@pytest.mark.anyio
async def test_prepare_download_directory_rejected(tmp_path):
    r = await prepare_download(src_path=str(tmp_path), token_name="t")
    assert r["success"] is False
    assert r["error"] == "NotARegularFile"


@pytest.mark.anyio
async def test_prepare_download_protected_path(monkeypatch):
    monkeypatch.setattr("mymcp.config.PROTECTED_PATHS", ["/etc"])
    r = await prepare_download(src_path="/etc/shadow", token_name="t")
    assert r["success"] is False
    assert r["error"] == "ProtectedPath"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_transfer_tools.py -v --benchmark-disable`

Expected: 4 new tests FAIL — `prepare_download` undefined.

- [ ] **Step 3: Implement prepare_download**

Append to `src/mymcp/tools/transfer.py`:

```python
async def prepare_download(
    *,
    src_path: str,
    expires_in: int | None = None,
    token_name: str = "unknown",
) -> dict:
    if not config.TRANSFER_ENABLED:
        return {
            "success": False,
            "error": "TransferDisabled",
            "message": "File transfer feature is disabled on this server.",
        }
    if not os.path.isabs(src_path):
        return {
            "success": False,
            "error": "InvalidPath",
            "message": "src_path must be an absolute path.",
        }
    err = check_protected_path(src_path)
    if err:
        return {"success": False, "error": "ProtectedPath", "message": err}
    if not os.path.exists(src_path):
        return {
            "success": False,
            "error": "FileNotFound",
            "message": f"{src_path} does not exist.",
        }
    if not os.path.isfile(src_path):
        return {
            "success": False,
            "error": "NotARegularFile",
            "message": f"{src_path} is not a regular file.",
        }

    ttl_default = config.TRANSFER_DEFAULT_TTL_SEC
    ttl_max = config.TRANSFER_MAX_TTL_SEC
    requested_ttl = ttl_default if expires_in is None else int(expires_in)
    if requested_ttl <= 0:
        return {
            "success": False,
            "error": "InvalidExpiresIn",
            "message": "expires_in must be positive.",
        }
    effective_ttl = min(requested_ttl, ttl_max)

    size = os.path.getsize(src_path)
    ticket = get_ticket_store().mint(
        op="download",
        path=src_path,
        max_bytes=size,
        ttl_sec=effective_ttl,
        created_by=token_name,
    )
    url = _build_url(ticket.ticket_id)
    return {
        "success": True,
        "url": url,
        "method": "GET",
        "ticket": ticket.ticket_id,
        "expires_in": effective_ttl,
        "expires_at": _iso(ticket.expires_at),
        "size": size,
        "src_path": src_path,
        "curl_example": f"curl -fsS '{url}' -o /local/path/{os.path.basename(src_path)}",
        "instructions": (
            "Run the curl above from the MCP client's local shell. "
            "Bytes stream back as the response body."
        ),
        "on_error": (
            "If the URL returns 4xx, read the JSON error.hint field. "
            "Tickets are single-use; mint a new one with prepare_download if needed."
        ),
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_transfer_tools.py -v --benchmark-disable`

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mymcp/tools/transfer.py tests/test_transfer_tools.py
git commit -m "feat(transfer): add prepare_download tool"
```

---

## Task 8: Wire tools into MCP server

**Files:**
- Modify: `src/mymcp/mcp_server.py`
- Test: `tests/test_mcp.py` (append) or new `tests/test_transfer_dispatch.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_transfer_dispatch.py`:

```python
import json

import pytest

from mymcp.mcp_server import (
    READ_TOOLS,
    WRITE_TOOLS,
    _TOOL_DEFS,
    check_tool_permission,
    dispatch_tool,
)
from mymcp.transfer import reset_ticket_store


@pytest.fixture(autouse=True)
def _reset():
    reset_ticket_store()
    yield
    reset_ticket_store()


def test_transfer_tools_registered():
    assert "prepare_upload" in _TOOL_DEFS
    assert "prepare_download" in _TOOL_DEFS
    assert "prepare_upload" in WRITE_TOOLS
    assert "prepare_download" in READ_TOOLS


def test_prepare_upload_descriptions_minimal():
    """Tool descriptions are loaded into every client session — keep them short."""
    assert len(_TOOL_DEFS["prepare_upload"].description) < 120
    assert len(_TOOL_DEFS["prepare_download"].description) < 120


def test_ro_role_cannot_call_prepare_upload():
    err = check_tool_permission("prepare_upload", "ro")
    assert err is not None
    assert "rw" in err


def test_ro_role_can_call_prepare_download():
    assert check_tool_permission("prepare_download", "ro") is None


@pytest.mark.anyio
async def test_dispatch_prepare_upload(tmp_path):
    out = await dispatch_tool(
        "prepare_upload", {"dest_path": str(tmp_path / "x.bin"), "max_bytes": 100}
    )
    data = json.loads(out)
    assert data["success"] is True
    assert data["method"] == "PUT"


@pytest.mark.anyio
async def test_dispatch_prepare_download(tmp_path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"abc")
    out = await dispatch_tool("prepare_download", {"src_path": str(p)})
    data = json.loads(out)
    assert data["success"] is True
    assert data["method"] == "GET"
    assert data["size"] == 3
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_transfer_dispatch.py -v --benchmark-disable`

Expected: FAIL — tools not registered.

- [ ] **Step 3: Wire tools into mcp_server.py**

In `src/mymcp/mcp_server.py`:

a) Import them at the top alongside other tool imports:

```python
from mymcp.tools.transfer import prepare_download, prepare_upload
```

b) Extend the role sets:

```python
READ_TOOLS: set[str] = {"read_file", "glob", "grep", "prepare_download"}
WRITE_TOOLS: set[str] = {
    "bash_execute", "write_file", "edit_file", "prepare_upload",
}
```

c) Inside `_build_tool_definitions()`, add two entries to the returned dict — keep descriptions to one short sentence each:

```python
        "prepare_upload": types.Tool(
            name="prepare_upload",
            description="Mint a signed URL for uploading bytes to a server path.",
            inputSchema={
                "type": "object",
                "properties": {
                    "dest_path": {
                        "type": "string",
                        "description": "Absolute server path to write to",
                    },
                    "max_bytes": {
                        "type": "integer",
                        "description": "Reject upload above this many bytes",
                    },
                    "expires_in": {
                        "type": "integer",
                        "description": "Ticket TTL seconds (default 300)",
                    },
                    "overwrite": {
                        "type": "boolean",
                        "description": "If false, refuse when dest_path exists (default true)",
                    },
                },
                "required": ["dest_path"],
            },
        ),
        "prepare_download": types.Tool(
            name="prepare_download",
            description="Mint a signed URL for downloading bytes from a server path.",
            inputSchema={
                "type": "object",
                "properties": {
                    "src_path": {
                        "type": "string",
                        "description": "Absolute server path to read from",
                    },
                    "expires_in": {
                        "type": "integer",
                        "description": "Ticket TTL seconds (default 300)",
                    },
                },
                "required": ["src_path"],
            },
        ),
```

d) Extend `dispatch_tool` with two new branches (place them after `edit_file` branch and before `glob`):

```python
    elif name == "prepare_upload":
        info = _current_audit_info.get()
        result = await prepare_upload(
            dest_path=args["dest_path"],
            max_bytes=args.get("max_bytes"),
            expires_in=args.get("expires_in"),
            overwrite=args.get("overwrite", True),
            token_name=info.get("token_name", "unknown"),
        )
    elif name == "prepare_download":
        info = _current_audit_info.get()
        result = await prepare_download(
            src_path=args["src_path"],
            expires_in=args.get("expires_in"),
            token_name=info.get("token_name", "unknown"),
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_transfer_dispatch.py tests/test_mcp.py -v --benchmark-disable`

Expected: all PASS, no regressions in `test_mcp.py`.

- [ ] **Step 5: Commit**

```bash
git add src/mymcp/mcp_server.py tests/test_transfer_dispatch.py
git commit -m "feat(transfer): register prepare_upload/download as MCP tools"
```

---

## Task 9: Endpoint scaffolding and 4xx error responses

**Files:**
- Create: `src/mymcp/transfer/endpoints.py`
- Test: `tests/test_transfer_endpoints.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_transfer_endpoints.py`:

```python
import json

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from mymcp.transfer import get_ticket_store, reset_ticket_store
from mymcp.transfer.endpoints import register_transfer_routes


@pytest.fixture(autouse=True)
def _reset():
    reset_ticket_store()
    yield
    reset_ticket_store()


@pytest.fixture
def app():
    app = FastAPI()
    register_transfer_routes(app)
    return app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.anyio
async def test_unknown_ticket_returns_404(client):
    r = await client.get("/files/raw/does-not-exist")
    assert r.status_code == 404
    body = r.json()
    assert body["ok"] is False
    assert body["error"] == "ticket_not_found"
    assert "hint" in body


@pytest.mark.anyio
async def test_get_on_upload_ticket_returns_405(client):
    t = get_ticket_store().mint(
        op="upload", path="/tmp/x", max_bytes=100, ttl_sec=60, created_by="t"
    )
    r = await client.get(f"/files/raw/{t.ticket_id}")
    assert r.status_code == 405
    body = r.json()
    assert body["error"] == "wrong_method"


@pytest.mark.anyio
async def test_put_on_download_ticket_returns_405(client):
    t = get_ticket_store().mint(
        op="download", path="/tmp/x", max_bytes=0, ttl_sec=60, created_by="t"
    )
    r = await client.put(f"/files/raw/{t.ticket_id}", content=b"x")
    assert r.status_code == 405


@pytest.mark.anyio
async def test_consumed_ticket_returns_410(client, tmp_path):
    f = tmp_path / "f.bin"
    f.write_bytes(b"hi")
    t = get_ticket_store().mint(
        op="download", path=str(f), max_bytes=0, ttl_sec=60, created_by="t"
    )
    get_ticket_store().consume(t.ticket_id)
    r = await client.get(f"/files/raw/{t.ticket_id}")
    assert r.status_code == 410
    assert r.json()["error"] == "ticket_not_found"  # consumed lookups return None


@pytest.mark.anyio
async def test_transfer_disabled_returns_404(client, monkeypatch):
    monkeypatch.setenv("MYMCP_TRANSFER_ENABLED", "false")
    from mymcp import config

    config.reset_settings_cache()
    try:
        t = get_ticket_store().mint(
            op="upload", path="/tmp/x", max_bytes=10, ttl_sec=60, created_by="t"
        )
        r = await client.put(f"/files/raw/{t.ticket_id}", content=b"x")
        assert r.status_code == 404
        assert r.json()["error"] == "transfer_disabled"
    finally:
        monkeypatch.delenv("MYMCP_TRANSFER_ENABLED", raising=False)
        config.reset_settings_cache()
```

Note about the `consumed` test: a consumed ticket fails the `lookup` predicate and so falls into the same "not found" branch. We accept that the same status/error is returned as for an unknown id — there is no security value in distinguishing the two for the caller. The audit log records the actual outcome.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_transfer_endpoints.py -v --benchmark-disable`

Expected: FAIL with `ModuleNotFoundError` or missing `register_transfer_routes`.

- [ ] **Step 3: Implement endpoint scaffolding**

Create `src/mymcp/transfer/endpoints.py`:

```python
"""Bypass HTTP routes for binary file transfer.

These endpoints authenticate via the URL ticket alone — no Bearer header.
Bytes flow as raw streams; nothing about file content enters the MCP
protocol or LLM context.
"""

from __future__ import annotations

import logging
import os
import tempfile

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, StreamingResponse

from mymcp import config
from mymcp.tools.files import check_protected_path
from mymcp.transfer import get_ticket_store

logger = logging.getLogger("mymcp")


def _err(status: int, code: str, hint: str) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": code, "hint": hint}, status_code=status
    )


def _disabled_response() -> JSONResponse:
    return _err(404, "transfer_disabled", "File transfer is disabled on this server.")


def register_transfer_routes(app: FastAPI) -> None:
    @app.put("/files/raw/{ticket_id}")
    async def upload_endpoint(ticket_id: str, request: Request):
        if not config.TRANSFER_ENABLED:
            return _disabled_response()
        store = get_ticket_store()
        ticket = store.lookup(ticket_id)
        if ticket is None:
            # Could be unknown, expired, or consumed; status differs.
            raw = store._tickets.get(ticket_id)  # type: ignore[attr-defined]
            if raw is None:
                return _err(404, "ticket_not_found", "Mint a new ticket.")
            if raw.consumed:
                return _err(410, "ticket_not_found", "Ticket already used.")
            return _err(410, "ticket_expired", "Mint a new ticket.")
        if ticket.op != "upload":
            return _err(405, "wrong_method", "This ticket requires GET.")

        return await _do_upload(ticket, request)

    @app.get("/files/raw/{ticket_id}")
    async def download_endpoint(ticket_id: str):
        if not config.TRANSFER_ENABLED:
            return _disabled_response()
        store = get_ticket_store()
        ticket = store.lookup(ticket_id)
        if ticket is None:
            raw = store._tickets.get(ticket_id)  # type: ignore[attr-defined]
            if raw is None:
                return _err(404, "ticket_not_found", "Mint a new ticket.")
            if raw.consumed:
                return _err(410, "ticket_not_found", "Ticket already used.")
            return _err(410, "ticket_expired", "Mint a new ticket.")
        if ticket.op != "download":
            return _err(405, "wrong_method", "This ticket requires PUT.")

        return await _do_download(ticket)


async def _do_upload(ticket, request: Request):
    return _err(501, "not_implemented", "Upload streaming not yet wired.")


async def _do_download(ticket):
    return _err(501, "not_implemented", "Download streaming not yet wired.")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_transfer_endpoints.py -v --benchmark-disable`

Expected: all 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mymcp/transfer/endpoints.py tests/test_transfer_endpoints.py
git commit -m "feat(transfer): scaffold /files/raw endpoints with 4xx handling"
```

---

## Task 10: Upload streaming with size cap and atomic replace

**Files:**
- Modify: `src/mymcp/transfer/endpoints.py`
- Modify: `tests/test_transfer_endpoints.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_transfer_endpoints.py`:

```python
@pytest.mark.anyio
async def test_upload_happy_path_atomic_write(client, tmp_path):
    dest = tmp_path / "uploaded.bin"
    payload = b"\x00\x01\x02hello-binary\xff" * 100
    t = get_ticket_store().mint(
        op="upload", path=str(dest), max_bytes=10_000, ttl_sec=60, created_by="t"
    )
    r = await client.put(f"/files/raw/{t.ticket_id}", content=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["bytes_written"] == len(payload)
    assert body["path"] == str(dest)
    assert dest.read_bytes() == payload
    # Ticket marked consumed
    assert get_ticket_store().consume(t.ticket_id) is False


@pytest.mark.anyio
async def test_upload_content_length_too_large_returns_413(client, tmp_path):
    dest = tmp_path / "x.bin"
    t = get_ticket_store().mint(
        op="upload", path=str(dest), max_bytes=10, ttl_sec=60, created_by="t"
    )
    payload = b"x" * 100
    r = await client.put(f"/files/raw/{t.ticket_id}", content=payload)
    assert r.status_code == 413
    assert r.json()["error"] == "size_exceeded"
    assert not dest.exists()


@pytest.mark.anyio
async def test_upload_streaming_truncation_returns_413(client, tmp_path):
    """Caller lies in Content-Length and sends more bytes mid-stream."""
    dest = tmp_path / "x.bin"
    t = get_ticket_store().mint(
        op="upload", path=str(dest), max_bytes=10, ttl_sec=60, created_by="t"
    )

    async def gen():
        yield b"x" * 5
        yield b"x" * 50  # exceeds cap mid-stream

    headers = {"transfer-encoding": "chunked"}
    r = await client.put(f"/files/raw/{t.ticket_id}", content=gen(), headers=headers)
    assert r.status_code == 413
    assert not dest.exists()


@pytest.mark.anyio
async def test_upload_protected_path_at_redeem(client, tmp_path, monkeypatch):
    dest = tmp_path / "x.bin"
    t = get_ticket_store().mint(
        op="upload", path=str(dest), max_bytes=100, ttl_sec=60, created_by="t"
    )
    # Reconfigure protected paths AFTER mint, BEFORE redeem.
    monkeypatch.setattr("mymcp.config.PROTECTED_PATHS", [str(tmp_path)])
    r = await client.put(f"/files/raw/{t.ticket_id}", content=b"x")
    assert r.status_code == 403
    assert r.json()["error"] == "path_protected"
    assert not dest.exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_transfer_endpoints.py -v --benchmark-disable`

Expected: 4 new FAIL with status 501.

- [ ] **Step 3: Implement upload streaming**

Replace `_do_upload` in `src/mymcp/transfer/endpoints.py`:

```python
async def _do_upload(ticket, request: Request):
    err = check_protected_path(ticket.path)
    if err:
        return _err(403, "path_protected", err)

    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > ticket.max_bytes:
                return _err(
                    413,
                    "size_exceeded",
                    f"Body exceeds max_bytes={ticket.max_bytes}.",
                )
        except ValueError:
            return _err(400, "bad_content_length", "Content-Length is not an integer.")

    parent = os.path.dirname(ticket.path) or "/"
    try:
        os.makedirs(parent, exist_ok=True)
    except OSError as e:
        return _err(500, "mkdir_failed", str(e))

    fd, tmp_path = tempfile.mkstemp(prefix=".mymcp-upload-", dir=parent)
    written = 0
    try:
        with os.fdopen(fd, "wb") as out:
            async for chunk in request.stream():
                if not chunk:
                    continue
                if written + len(chunk) > ticket.max_bytes:
                    raise _SizeExceeded()
                out.write(chunk)
                written += len(chunk)
        os.replace(tmp_path, ticket.path)
    except _SizeExceeded:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return _err(
            413, "size_exceeded", f"Body exceeds max_bytes={ticket.max_bytes}."
        )
    except Exception as e:  # pragma: no cover - defensive
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        logger.error("upload failed for %s: %s", ticket.path, e)
        return _err(500, "write_failed", str(e))

    get_ticket_store().consume(ticket.ticket_id)
    return JSONResponse(
        {"ok": True, "path": ticket.path, "bytes_written": written}
    )


class _SizeExceeded(Exception):
    pass
```

(The `_SizeExceeded` class and the `tempfile`/`os` imports must already be at the top of the file from Task 9 — `os` and `tempfile` are already imported.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_transfer_endpoints.py -v --benchmark-disable`

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mymcp/transfer/endpoints.py tests/test_transfer_endpoints.py
git commit -m "feat(transfer): implement upload streaming with size cap"
```

---

## Task 11: Download streaming

**Files:**
- Modify: `src/mymcp/transfer/endpoints.py`
- Modify: `tests/test_transfer_endpoints.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_transfer_endpoints.py`:

```python
@pytest.mark.anyio
async def test_download_happy_path(client, tmp_path):
    src = tmp_path / "f.bin"
    payload = b"\x00\x01\x02" * 1000
    src.write_bytes(payload)
    t = get_ticket_store().mint(
        op="download", path=str(src), max_bytes=0, ttl_sec=60, created_by="t"
    )
    r = await client.get(f"/files/raw/{t.ticket_id}")
    assert r.status_code == 200
    assert r.content == payload
    assert r.headers["content-type"].startswith("application/octet-stream")
    assert "f.bin" in r.headers.get("content-disposition", "")
    # Consumed after success
    r2 = await client.get(f"/files/raw/{t.ticket_id}")
    assert r2.status_code in (404, 410)


@pytest.mark.anyio
async def test_download_missing_file_returns_404(client, tmp_path):
    src = tmp_path / "gone.bin"
    src.write_bytes(b"x")
    t = get_ticket_store().mint(
        op="download", path=str(src), max_bytes=0, ttl_sec=60, created_by="t"
    )
    src.unlink()
    r = await client.get(f"/files/raw/{t.ticket_id}")
    assert r.status_code == 404
    assert r.json()["error"] == "path_not_found"


@pytest.mark.anyio
async def test_download_protected_at_redeem(client, tmp_path, monkeypatch):
    src = tmp_path / "f.bin"
    src.write_bytes(b"x")
    t = get_ticket_store().mint(
        op="download", path=str(src), max_bytes=0, ttl_sec=60, created_by="t"
    )
    monkeypatch.setattr("mymcp.config.PROTECTED_PATHS", [str(tmp_path)])
    r = await client.get(f"/files/raw/{t.ticket_id}")
    assert r.status_code == 403
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_transfer_endpoints.py -v --benchmark-disable`

Expected: 3 new FAIL with 501.

- [ ] **Step 3: Implement download streaming**

Replace `_do_download` in `src/mymcp/transfer/endpoints.py`:

```python
async def _do_download(ticket):
    err = check_protected_path(ticket.path)
    if err:
        return _err(403, "path_protected", err)
    if not os.path.isfile(ticket.path):
        return _err(404, "path_not_found", "Server file no longer exists.")

    size = os.path.getsize(ticket.path)
    filename = os.path.basename(ticket.path)

    async def iter_file():
        with open(ticket.path, "rb") as fh:
            while True:
                chunk = fh.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        get_ticket_store().consume(ticket.ticket_id)

    headers = {
        "content-length": str(size),
        "content-disposition": f'attachment; filename="{filename}"',
    }
    return StreamingResponse(
        iter_file(), media_type="application/octet-stream", headers=headers
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_transfer_endpoints.py -v --benchmark-disable`

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mymcp/transfer/endpoints.py tests/test_transfer_endpoints.py
git commit -m "feat(transfer): implement download streaming with consumption"
```

---

## Task 12: Audit logging for redeem events

**Files:**
- Modify: `src/mymcp/transfer/endpoints.py`
- Modify: `tests/test_transfer_endpoints.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_transfer_endpoints.py`:

```python
@pytest.mark.anyio
async def test_upload_writes_audit_entry(client, tmp_path, monkeypatch):
    audit_dir = tmp_path / "audit"
    monkeypatch.setenv("MYMCP_AUDIT_ENABLED", "true")
    monkeypatch.setenv("MYMCP_AUDIT_LOG_DIR", str(audit_dir))
    from mymcp import audit, config

    config.reset_settings_cache()
    audit._setup_done = False
    audit._logger = None
    try:
        dest = tmp_path / "f.bin"
        t = get_ticket_store().mint(
            op="upload", path=str(dest), max_bytes=100, ttl_sec=60, created_by="rwc"
        )
        r = await client.put(f"/files/raw/{t.ticket_id}", content=b"hello")
        assert r.status_code == 200
        log_path = audit_dir / "audit.log"
        assert log_path.exists()
        text = log_path.read_text()
        assert "transfer_redeem" in text
        assert "rwc" in text
        assert "5" in text  # bytes_written
    finally:
        monkeypatch.delenv("MYMCP_AUDIT_ENABLED", raising=False)
        monkeypatch.delenv("MYMCP_AUDIT_LOG_DIR", raising=False)
        config.reset_settings_cache()
        audit._setup_done = False
        audit._logger = None


@pytest.mark.anyio
async def test_failed_upload_writes_error_audit(client, tmp_path, monkeypatch):
    audit_dir = tmp_path / "audit"
    monkeypatch.setenv("MYMCP_AUDIT_ENABLED", "true")
    monkeypatch.setenv("MYMCP_AUDIT_LOG_DIR", str(audit_dir))
    from mymcp import audit, config

    config.reset_settings_cache()
    audit._setup_done = False
    audit._logger = None
    try:
        dest = tmp_path / "f.bin"
        t = get_ticket_store().mint(
            op="upload", path=str(dest), max_bytes=2, ttl_sec=60, created_by="t"
        )
        r = await client.put(f"/files/raw/{t.ticket_id}", content=b"too-big")
        assert r.status_code == 413
        text = (audit_dir / "audit.log").read_text()
        assert "size_exceeded" in text
    finally:
        monkeypatch.delenv("MYMCP_AUDIT_ENABLED", raising=False)
        monkeypatch.delenv("MYMCP_AUDIT_LOG_DIR", raising=False)
        config.reset_settings_cache()
        audit._setup_done = False
        audit._logger = None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_transfer_endpoints.py -v --benchmark-disable`

Expected: 2 new FAIL — no audit emission yet.

- [ ] **Step 3: Add audit emission**

In `src/mymcp/transfer/endpoints.py`, add an import at the top:

```python
from mymcp.audit import log_tool_call
```

Add a helper function near `_err`:

```python
def _audit(ticket, *, success: bool, bytes_count: int, error_code: str | None,
           client_ip: str) -> None:
    log_tool_call(
        token_name=ticket.created_by,
        role="rw" if ticket.op == "upload" else "ro",
        ip=client_ip,
        tool="transfer_redeem",
        params={"op": ticket.op, "path": ticket.path, "ticket": ticket.ticket_id[:8]},
        result="ok" if success else "error",
        error_code=error_code,
        error_message=None if success else error_code,
        duration_ms=None,
        extra={"bytes": bytes_count},
    )
```

Wait — check the existing `log_tool_call` signature first:

```bash
grep -n "def log_tool_call" src/mymcp/audit.py
```

Adapt the call to match the actual parameters of `log_tool_call`. If the signature does not accept `extra=`, pass `bytes_count` via the `params` dict instead. **Read `src/mymcp/audit.py` and match the signature exactly before writing the helper.**

In `_do_upload`, capture `client_ip = request.client.host if request.client else "unknown"` near the top. After `os.replace(...)` succeeds, call:

```python
_audit(ticket, success=True, bytes_count=written, error_code=None, client_ip=client_ip)
```

In each error path inside `_do_upload` (size_exceeded, mkdir_failed, write_failed, path_protected, bad_content_length), call `_audit(...)` with `success=False` and the matching `error_code`.

In `_do_download`, capture client IP via the request (signature change: `_do_download(ticket, request)` — update both the route handler and the function). Call `_audit(...)` after successful streaming completes (inside `iter_file` after the read loop) and on the protected/missing-file errors.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_transfer_endpoints.py -v --benchmark-disable`

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mymcp/transfer/endpoints.py tests/test_transfer_endpoints.py
git commit -m "feat(transfer): emit audit log entries on redeem"
```

---

## Task 13: Wire endpoints into FastAPI app and bypass middleware

**Files:**
- Modify: `src/mymcp/server.py`
- Test: `tests/test_server_factory.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_server_factory.py`:

```python
@pytest.mark.anyio
async def test_files_raw_route_does_not_require_bearer_token():
    """The bypass endpoint authenticates by ticket; no Bearer header should be needed."""
    from httpx import ASGITransport, AsyncClient

    from mymcp.server import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.get("/files/raw/nonexistent-ticket")
    # 404 from ticket lookup, NOT 401 from auth middleware
    assert r.status_code == 404
    body = r.json()
    assert body.get("error") == "ticket_not_found"


@pytest.mark.anyio
async def test_mcp_route_still_requires_bearer_token():
    from httpx import ASGITransport, AsyncClient

    from mymcp.server import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/mcp", json={})
    assert r.status_code == 401
```

(Pick whichever import style matches the existing tests in this file. Use `pytest.mark.anyio` if other tests in the file already use it.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_server_factory.py -v --benchmark-disable`

Expected: `test_files_raw_route_does_not_require_bearer_token` FAILs with 404 returning a different shape OR being intercepted by middleware (401).

- [ ] **Step 3: Wire endpoints into create_app**

In `src/mymcp/server.py`:

a) At the top, add the import:

```python
from mymcp.transfer.endpoints import register_transfer_routes
```

b) In `McpAuthMiddleware.__call__`, the path check should already only intercept exactly `/mcp`. Confirm it does NOT match `/files/raw/...`. If it does (e.g., `startswith`), tighten it to `== "/mcp"`. **Read the current middleware before modifying.**

c) In `create_app()`, after `app.include_router(admin_router)`, add:

```python
    register_transfer_routes(app)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_server_factory.py tests/test_security.py -v --benchmark-disable`

Expected: all PASS, including no regressions in `test_security.py`.

- [ ] **Step 5: Commit**

```bash
git add src/mymcp/server.py tests/test_server_factory.py
git commit -m "feat(transfer): mount /files/raw routes on FastAPI app"
```

---

## Task 14: URL building uses request Host when public_base_url empty

**Files:**
- Modify: `src/mymcp/tools/transfer.py`
- Test: `tests/test_transfer_tools.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_transfer_tools.py`:

```python
@pytest.mark.anyio
async def test_prepare_upload_relative_url_when_no_public_base(tmp_path, monkeypatch):
    monkeypatch.setenv("MYMCP_PUBLIC_BASE_URL", "")
    from mymcp import config

    config.reset_settings_cache()
    try:
        r = await prepare_upload(
            dest_path=str(tmp_path / "x"), max_bytes=10, token_name="t"
        )
        assert r["url"].startswith("/files/raw/")
        assert r["curl_example"].startswith("curl -fsS -T ")
    finally:
        monkeypatch.delenv("MYMCP_PUBLIC_BASE_URL", raising=False)
        config.reset_settings_cache()
```

- [ ] **Step 2: Run the test**

Run: `pytest tests/test_transfer_tools.py::test_prepare_upload_relative_url_when_no_public_base -v --benchmark-disable`

Expected: PASS — Task 5 already returns relative URL when base is empty. This task documents/locks that behavior. If FAIL, the test exposed a bug in `_build_url`; fix it.

- [ ] **Step 3: Document the limitation**

Add a comment in `_build_url` in `src/mymcp/tools/transfer.py`:

```python
def _build_url(ticket_id: str) -> str:
    base = _public_base_url()
    if not base:
        # Relative URL. Caller must combine with the server's reachable host.
        # Set MYMCP_PUBLIC_BASE_URL to return absolute URLs (required behind
        # reverse proxies that rewrite Host).
        return f"/files/raw/{ticket_id}"
    return f"{base}/files/raw/{ticket_id}"
```

- [ ] **Step 4: Re-run the test**

Run: `pytest tests/test_transfer_tools.py -v --benchmark-disable`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mymcp/tools/transfer.py tests/test_transfer_tools.py
git commit -m "test(transfer): lock relative URL behavior when public_base_url unset"
```

---

## Task 15: End-to-end integration test

**Files:**
- Create: `tests/test_transfer_integration.py`

- [ ] **Step 1: Write the test**

Create `tests/test_transfer_integration.py`:

```python
"""End-to-end: mint via prepare_upload, PUT bytes, file appears on disk;
mint via prepare_download, GET, bytes match."""

import json
import os

import pytest
from httpx import ASGITransport, AsyncClient

from mymcp.transfer import reset_ticket_store


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch, tmp_path):
    reset_ticket_store()
    monkeypatch.setenv("MYMCP_PUBLIC_BASE_URL", "")
    monkeypatch.setenv("MYMCP_TOKEN_FILE", str(tmp_path / "tokens.json"))
    monkeypatch.setenv("MYMCP_ADMIN_TOKEN", "adm-test")
    from mymcp import config

    config.reset_settings_cache()
    yield
    reset_ticket_store()
    config.reset_settings_cache()


@pytest.fixture
async def client():
    from mymcp.server import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        yield ac


@pytest.mark.anyio
async def test_full_upload_and_download_roundtrip(client, tmp_path):
    # Use prepare_upload directly (skip MCP layer to keep test focused on HTTP path).
    from mymcp.tools.transfer import prepare_download, prepare_upload

    dest = tmp_path / "round.bin"
    payload = os.urandom(50_000)

    upload_info = await prepare_upload(
        dest_path=str(dest),
        max_bytes=100_000,
        token_name="rwc",
    )
    assert upload_info["success"] is True
    r = await client.put(upload_info["url"], content=payload)
    assert r.status_code == 200, r.text
    assert dest.read_bytes() == payload

    # Download it back.
    dl_info = await prepare_download(src_path=str(dest), token_name="rwc")
    assert dl_info["success"] is True
    r = await client.get(dl_info["url"])
    assert r.status_code == 200
    assert r.content == payload


@pytest.mark.anyio
async def test_upload_url_is_single_use(client, tmp_path):
    from mymcp.tools.transfer import prepare_upload

    dest = tmp_path / "single.bin"
    info = await prepare_upload(dest_path=str(dest), max_bytes=10, token_name="t")
    r1 = await client.put(info["url"], content=b"hi")
    assert r1.status_code == 200
    r2 = await client.put(info["url"], content=b"hi")
    assert r2.status_code in (404, 410)
```

- [ ] **Step 2: Run the test**

Run: `pytest tests/test_transfer_integration.py -v --benchmark-disable`

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_transfer_integration.py
git commit -m "test(transfer): end-to-end upload/download integration"
```

---

## Task 16: Lint, full suite, changelog

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Run lint and type-check**

Run:
```bash
ruff check . && ruff format --check . && mypy src/mymcp
```

Expected: clean. If `ruff format --check` complains, run `ruff format .` and re-stage.

- [ ] **Step 2: Run the full test suite**

Run: `pytest tests/ -v --benchmark-disable`

Expected: all tests PASS, no regressions in pre-existing tests.

- [ ] **Step 3: Update CHANGELOG.md**

Open `CHANGELOG.md` and add under `[Unreleased]` (or create that section if missing):

```markdown
### Added
- File transfer for binary and large files via two new MCP tools
  (`prepare_upload`, `prepare_download`) and bypass HTTP endpoints
  (`PUT /files/raw/{ticket}`, `GET /files/raw/{ticket}`). File bytes
  never enter the LLM context. One-time signed tickets with 5-minute
  default TTL; configurable via `MYMCP_TRANSFER_*` env vars. See
  `docs/superpowers/specs/2026-05-04-file-transfer-design.md`.
```

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: changelog entry for file transfer"
```

---

## Task 17: Push and open PR

- [ ] **Step 1: Push the branch**

```bash
git push
```

- [ ] **Step 2: Open the pull request**

```bash
gh pr create --title "feat: file transfer via signed-URL bypass endpoints" --body "$(cat <<'EOF'
## Summary

- Add `prepare_upload` / `prepare_download` MCP tools that mint one-time signed URLs
- Add `PUT /files/raw/{ticket}` and `GET /files/raw/{ticket}` bypass endpoints — file bytes never enter the LLM context
- Reuse existing protected-path enforcement and audit logging
- Configurable via `MYMCP_TRANSFER_*` env vars (default 2 GB cap, 5-min TTL)

Spec: `docs/superpowers/specs/2026-05-04-file-transfer-design.md`
Plan: `docs/superpowers/plans/2026-05-05-file-transfer.md`

## Test plan

- [x] Unit tests for `TicketStore` (mint, lookup, expiry, consume, sweep)
- [x] Tool tests for `prepare_upload` / `prepare_download` (happy + every error branch)
- [x] Endpoint tests via `httpx.ASGITransport` (4xx error matrix, streaming size cap, atomic replace, audit emission)
- [x] End-to-end roundtrip (mint → PUT → file on disk → mint → GET → bytes match)
- [x] `ruff check`, `ruff format --check`, `mypy src/mymcp`, full `pytest` suite

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review

**1. Spec coverage**

| Spec section | Task(s) |
|---|---|
| Bypass HTTP endpoints | 9, 10, 11, 13 |
| MCP tools (`prepare_upload`, `prepare_download`) | 5, 6, 7, 8 |
| Ticket store (mint/lookup/consume/sweep) | 2, 3, 4 |
| `check_protected_path` at mint and at redeem | 6, 7, 10, 11 |
| Configuration (`MYMCP_TRANSFER_*`, `PUBLIC_BASE_URL`) | 1, 14 |
| Auth: tickets only, bypass middleware | 13 |
| Audit logging on mint and redeem | 8 (mint via existing `log_tool_call` in `call_tool`), 12 (redeem) |
| Error matrix (404/405/410/413/403) | 9, 10, 11 |
| `read_file` / `write_file` deliberately unchanged | (no task — verified by absence) |
| End-to-end integration test | 15 |
| CHANGELOG | 16 |

All spec items have a task.

**2. Placeholder scan**

No "TBD", no "implement later", no "similar to Task N". Task 12 has one explicit "read the existing signature first" instruction because `log_tool_call`'s exact kwargs depend on the version of `audit.py` at execution time — that is intentional and the agent has clear actions to take.

**3. Type / name consistency**

- `Ticket` dataclass fields used consistently across tasks 2, 3, 4 and endpoint tasks 9, 10, 11, 12.
- `get_ticket_store()` / `reset_ticket_store()` introduced in task 4 and used in all later tasks.
- `prepare_upload` returns dict with keys `success`, `url`, `method`, `ticket`, `expires_in`, `expires_at`, `max_bytes`, `dest_path`, `curl_example`, `instructions`, `on_error` — matches spec §"Return JSON".
- `prepare_download` symmetric with `src_path` and `size`.
- Endpoint error JSON shape `{"ok": false, "error": "...", "hint": "..."}` consistent across tasks 9–12.
- HTTP status codes consistent with spec error table.

Plan is internally consistent.

---

Plan complete and saved to `docs/superpowers/plans/2026-05-05-file-transfer.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
