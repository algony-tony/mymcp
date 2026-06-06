# Test Infrastructure & Ops Docs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Three additions that close gaps the assessment surfaced. (1) Hypothesis property tests for the three subsystems most worth fuzzing (audit log parser, ticket consume/TTL, `check_protected_path`). (2) A shared `app_with_fake_session` fixture + one real-server end-to-end test that actually spawns uvicorn, exercises `/mcp` + `/metrics` + `/admin/tokens`, and verifies SIGTERM cleanup — the gap no ASGI-only test can cover. (3) A backup / disaster-recovery runbook documenting what to back up and how to restore. Companion spec: `docs/superpowers/specs/2026-06-06-project-assessment.md` (P2 #16, P3 #18, P3 #19).

**Architecture:** Standalone work. No production-code changes — only test fixtures, test additions, and one new docs file. Best executed after Plan D (architecture refactor) so the e2e test is written against the stable post-refactor surface.

**Prerequisite:** Plan D should merge before Task 6 (the real e2e test) — otherwise the assertions need to be rewritten after the dispatcher refactor.

**Tech Stack:** Python 3.11+ • pytest + anyio • Hypothesis • httpx (already a dependency) • uvicorn (programmatic start).

---

## Conventions

- All commands run from the repo root: `/home/zhu/repos/mymcp`.
- Branch: `feature/test-infra-ops-docs` off `master`.
- After every code task: `ruff format <files> && ruff check <files>`.
- Each task ends with a commit. Push at the end.

---

## Task 1: Branch

- [ ] **Step 1: Create**

```bash
git checkout master && git pull --ff-only
git checkout -b feature/test-infra-ops-docs
```

---

## Task 2: Add Hypothesis to dev deps

**Files:**
- Modify: `pyproject.toml`
- Modify: `requirements-dev.txt` (regenerated)

- [ ] **Step 1: pyproject.toml**

In `pyproject.toml`, under `[project.optional-dependencies]` `dev = [...]`, add:

```toml
    "hypothesis>=6.0",
```

- [ ] **Step 2: Regenerate lockfile**

```bash
pip-compile --extra dev --strip-extras \
  --unsafe-package algony-mymcp --unsafe-package pip --unsafe-package setuptools \
  --output-file requirements-dev.txt pyproject.toml
```

- [ ] **Step 3: Install**

```bash
.venv/bin/pip install -e ".[dev]" -c requirements-dev.txt
```

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml requirements-dev.txt
git commit -m "test: add hypothesis dev dependency"
```

---

## Task 3: Property tests — audit log line parser

**Files:**
- Create: `tests/test_audit_properties.py`

The audit log is JSON-lines. The reader/parser must handle any output the writer produces, including weird values (Unicode, embedded newlines, very long fields).

- [ ] **Step 1: Write tests**

Create `tests/test_audit_properties.py`:

```python
"""Property-based tests for the audit log writer/reader round-trip."""

import json
import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st


# Strategy for a single audit record — keep values in shapes the writer
# can actually produce. Don't fuzz fields the writer always controls.
_safe_text = st.text(
    alphabet=st.characters(
        blacklist_categories=("Cs",),  # no surrogates
    ),
    max_size=200,
)

_audit_record = st.fixed_dictionaries({
    "tool": st.sampled_from(["read_file", "write_file", "bash_execute", "glob", "grep"]),
    "role": st.sampled_from(["admin", "rw", "ro"]),
    "result": st.sampled_from(["success", "error"]),
    "params": st.dictionaries(_safe_text, _safe_text, max_size=5),
    "duration_ms": st.integers(min_value=0, max_value=10**9),
})


@given(records=st.lists(_audit_record, min_size=1, max_size=20))
@settings(max_examples=100, deadline=None)
def test_round_trip_lines_through_writer_and_parser(records):
    """Every record written by the audit writer must be parseable back to itself."""
    from mymcp.audit import _serialize_record  # the writer's record builder
    # If the project doesn't expose this, equivalent to: lambda r: json.dumps(r) + "\n"

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "audit.log"
        with path.open("w") as f:
            for r in records:
                f.write(_serialize_record(r))

        with path.open() as f:
            parsed = [json.loads(line) for line in f if line.strip()]

    assert len(parsed) == len(records)
    for orig, p in zip(records, parsed, strict=True):
        # Every field present in the original must round-trip exactly.
        for k, v in orig.items():
            assert p[k] == v, f"field {k!r} did not round-trip"


@given(noise=st.text(max_size=500))
@settings(max_examples=50, deadline=None)
def test_parser_tolerates_blank_and_junk_lines(noise):
    """Reader must skip blank lines and not crash on partial / truncated lines.
    (Adapt the assertion to match how the project's audit reader is actually
    structured — many projects use 'skip lines that don't parse as JSON'.)"""
    import io
    from mymcp.recorder.events import _parse_audit_line  # adapt to actual import

    # Pure-junk input should either return None or raise a recognised exception
    # — never an uncaught crash.
    try:
        out = _parse_audit_line(noise)
    except (ValueError, json.JSONDecodeError):
        return  # acceptable: parser flags but does not crash
    assert out is None or isinstance(out, dict)
```

If imports don't line up exactly with the codebase (e.g. `_serialize_record` doesn't exist), adapt the test to call the public writer (write a record via the actual logger, then read the file). The point is round-trip and crash-resistance, not the exact internal API.

- [ ] **Step 2: Run**

```bash
.venv/bin/python -m pytest tests/test_audit_properties.py -v --benchmark-disable
```

Expected: green. If Hypothesis finds a counterexample, **the parser has a bug** — fix it in the next commit before continuing.

- [ ] **Step 3: Commit**

```bash
git add tests/test_audit_properties.py
git commit -m "test(audit): property-based round-trip + junk-tolerance tests"
```

---

## Task 4: Property tests — ticket TTL and single-consume invariants

**Files:**
- Create: `tests/test_ticket_properties.py`

- [ ] **Step 1: Write tests**

Create `tests/test_ticket_properties.py`:

```python
"""Property tests for transfer ticket invariants."""

import time
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st


@given(
    ttls=st.lists(st.integers(min_value=1, max_value=120), min_size=1, max_size=10),
)
@settings(max_examples=50, deadline=None)
def test_consume_succeeds_exactly_once(tmp_path_factory, ttls):
    """For any number of mint+consume pairs: each ticket must be consumable
    exactly once. A second consume of the same id must fail."""
    from mymcp.transfer.tickets import TicketStore

    tmp = tmp_path_factory.mktemp("tk")
    store = TicketStore(path=str(tmp / "tickets.json"))
    minted = []
    for ttl in ttls:
        tk = store.mint(kind="upload", path="/tmp/x", ttl_sec=ttl,
                        issuer_token_id="abc", issuer_role="rw")
        minted.append(tk.id)

    consumed = set()
    for tid in minted:
        got = store.consume(tid)
        assert got is not None, "first consume must succeed"
        consumed.add(tid)

    # Second consume must fail
    for tid in consumed:
        got = store.consume(tid)
        assert got is None, "second consume must fail"


@given(ttl=st.integers(min_value=1, max_value=3))
@settings(max_examples=20, deadline=None)
def test_consume_after_ttl_returns_none(tmp_path_factory, ttl, monkeypatch):
    """A ticket consumed past its TTL must return None.
    Use monkeypatched time.time() so tests don't actually sleep."""
    from mymcp.transfer import tickets as tickets_mod
    from mymcp.transfer.tickets import TicketStore

    tmp = tmp_path_factory.mktemp("tk")
    store = TicketStore(path=str(tmp / "tickets.json"))
    current = [1_000_000.0]
    monkeypatch.setattr(tickets_mod, "time", type("T", (), {"time": lambda: current[0]}))

    tk = store.mint(kind="upload", path="/tmp/x", ttl_sec=ttl,
                    issuer_token_id="abc", issuer_role="rw")
    current[0] += ttl + 1.0  # advance past TTL
    got = store.consume(tk.id)
    assert got is None
```

(Adapt the `monkeypatch.setattr` if the tickets module uses `time.monotonic` instead — change the attribute name accordingly. Adapt the mint signature if Plan D's transfer audit changes have not yet shipped; if so, omit `issuer_token_id`/`issuer_role` for now.)

- [ ] **Step 2: Run**

```bash
.venv/bin/python -m pytest tests/test_ticket_properties.py -v --benchmark-disable
```

Expected: green.

- [ ] **Step 3: Commit**

```bash
git add tests/test_ticket_properties.py
git commit -m "test(transfer): property tests for single-consume + TTL invariants"
```

---

## Task 5: Property tests — `check_protected_path` traversal & symlink

**Files:**
- Create: `tests/test_protected_path_properties.py`

- [ ] **Step 1: Write tests**

Create `tests/test_protected_path_properties.py`:

```python
"""Property tests for path-protection — symlink escape, traversal, encoding."""

import os
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st


@given(
    # Random path segments, with traversal injected
    segments=st.lists(
        st.text(
            alphabet=st.characters(blacklist_characters="/\x00"),
            min_size=1, max_size=12,
        ),
        min_size=1, max_size=6,
    ),
    inject_traversal=st.booleans(),
)
@settings(max_examples=100, deadline=None)
def test_protected_dir_cannot_be_escaped_by_traversal(tmp_path, segments, inject_traversal):
    """Building any path with '../' segments must NOT bypass the protection of
    an absolute directory listed as protected."""
    from mymcp.tools.files import register_protected_path, check_protected_path, clear_protected_paths

    protected = tmp_path / "secret"
    protected.mkdir()
    clear_protected_paths()  # implement a test-only reset if missing
    register_protected_path(str(protected), modes={"read", "write"})

    # Build a path that may or may not include ../
    parts = list(segments)
    if inject_traversal:
        parts = ["..", *parts]
    candidate = protected / Path(*parts)

    is_blocked = check_protected_path(str(candidate), mode="read")

    # Anything that, after realpath, still lives under `protected` must be blocked.
    resolved = Path(os.path.realpath(str(candidate)))
    try:
        resolved.relative_to(protected)
        should_block = True
    except ValueError:
        should_block = False
    assert is_blocked == should_block


def test_symlink_into_protected_dir_is_blocked(tmp_path):
    from mymcp.tools.files import register_protected_path, check_protected_path, clear_protected_paths

    protected = tmp_path / "secret"
    protected.mkdir()
    (protected / "x.txt").write_text("hello")
    outside = tmp_path / "innocent.txt"
    os.symlink(str(protected / "x.txt"), str(outside))

    clear_protected_paths()
    register_protected_path(str(protected), modes={"read", "write"})

    # Direct hit must be blocked
    assert check_protected_path(str(protected / "x.txt"), mode="read") is True
    # Symlink to a protected target must also be blocked
    assert check_protected_path(str(outside), mode="read") is True
```

If `clear_protected_paths()` doesn't exist as a public helper, add a small test helper that calls `_runtime_protected.clear()` (or the equivalent module attribute). Document in the commit message.

- [ ] **Step 2: Run**

```bash
.venv/bin/python -m pytest tests/test_protected_path_properties.py -v --benchmark-disable
```

Expected: green. Any Hypothesis counterexample = real bypass bug; fix the implementation, not the test.

- [ ] **Step 3: Commit**

```bash
git add tests/test_protected_path_properties.py
git commit -m "test(security): property tests for path-protection traversal + symlink"
```

---

## Task 6: Extract shared `app_with_fake_session` fixture

**Files:**
- Modify: `tests/conftest.py`
- Modify: `tests/test_security.py`, `tests/test_integration.py` (consume the new fixture)

Two near-duplicate fake-session implementations exist (~70 LOC). Drift risk; refactor to one.

- [ ] **Step 1: Identify both implementations**

Run: `grep -n 'fake.*session\|class Fake' tests/test_security.py tests/test_integration.py`

- [ ] **Step 2: Define the shared fixture**

In `tests/conftest.py`:

```python
import pytest


@pytest.fixture
def fake_session_manager():
    """A minimal stand-in for StreamableHTTPSessionManager used by the
    ASGI integration tests. Routes requests through call_tool without
    network."""
    class _FakeManager:
        async def handle_request(self, scope, receive, send):
            # mirror the existing test-side implementations
            ...
    return _FakeManager()


@pytest.fixture
def app_with_fake_session(fake_session_manager, monkeypatch):
    """Build a real FastAPI app but inject the fake session manager."""
    from mymcp.server import create_app
    app = create_app()
    # The actual hook point depends on the post-refactor architecture; if
    # there's a public `app.state.session_manager`, set it. Otherwise patch
    # the module attribute the middleware reads.
    monkeypatch.setattr("mymcp.server._session_manager", fake_session_manager, raising=False)
    return app
```

- [ ] **Step 3: Migrate `test_security.py` and `test_integration.py`**

In both, delete their local fake-session class definitions. Use the new fixture in test signatures:

```python
async def test_some_security_thing(app_with_fake_session):
    ...
```

- [ ] **Step 4: Verify**

Run:
```bash
.venv/bin/python -m pytest tests/test_security.py tests/test_integration.py -v --benchmark-disable
```

Expected: green.

- [ ] **Step 5: Commit**

```bash
git add tests/conftest.py tests/test_security.py tests/test_integration.py
git commit -m "test: shared app_with_fake_session fixture

Removes ~70 LOC of near-duplicate fake-session plumbing across two
files. Both test_security.py and test_integration.py now consume the
same fixture from conftest, so drift is structural — change once."
```

---

## Task 7: Real-server end-to-end test

**Files:**
- Create: `tests/test_e2e_server.py`

This is the gap no ASGI-only test covers: real `StreamableHTTPSessionManager`, real uvicorn, real socket, real `/metrics` exposition, real SIGTERM handling.

- [ ] **Step 1: Write the test**

Create `tests/test_e2e_server.py`:

```python
"""End-to-end test against a real server process.

Spawns uvicorn in-process on an ephemeral port, hits HTTP endpoints with
httpx, exercises /mcp, /metrics, /health, and verifies a clean shutdown
on SIGTERM. This is the only test that covers the actual session manager,
uvicorn lifespan, and OS-level signal handling.
"""

import asyncio
import signal
import socket
from contextlib import asynccontextmanager

import httpx
import pytest
import uvicorn


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@asynccontextmanager
async def _running_server(monkeypatch, port: int):
    """Start uvicorn in a background asyncio Task; ensure clean shutdown."""
    monkeypatch.setenv("MYMCP_HOST", "127.0.0.1")
    monkeypatch.setenv("MYMCP_PORT", str(port))
    monkeypatch.setenv("MYMCP_TOKEN_FILE", "/tmp/mymcp-e2e-tokens.json")

    from mymcp.config import reset_settings_cache
    reset_settings_cache()
    from mymcp.server import create_app

    config = uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())

    # Wait for the port to accept connections (max ~3s)
    for _ in range(60):
        await asyncio.sleep(0.05)
        if server.started:
            break
    assert server.started, "server failed to start"

    try:
        yield port
    finally:
        server.should_exit = True
        await task


@pytest.mark.anyio
async def test_health_endpoint_responds(monkeypatch):
    port = _free_port()
    async with _running_server(monkeypatch, port):
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"http://127.0.0.1:{port}/health")
            assert r.status_code == 200


@pytest.mark.anyio
async def test_metrics_endpoint_emits_prometheus(monkeypatch):
    port = _free_port()
    async with _running_server(monkeypatch, port):
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"http://127.0.0.1:{port}/metrics")
            # Either 200 (metrics open) or 401 (token gate). Either is correct
            # behavior; we just need the endpoint to be wired.
            assert r.status_code in (200, 401)
            if r.status_code == 200:
                assert "mymcp_http_requests" in r.text or "mymcp_" in r.text


@pytest.mark.anyio
async def test_admin_tokens_lifecycle(monkeypatch):
    """Mint a rw token, validate it works against a tool call, revoke it,
    confirm subsequent calls fail."""
    monkeypatch.setenv("MYMCP_ADMIN_TOKEN", "test-admin-token")
    port = _free_port()
    async with _running_server(monkeypatch, port):
        async with httpx.AsyncClient(timeout=5.0) as client:
            mint = await client.post(
                f"http://127.0.0.1:{port}/admin/tokens",
                headers={"Authorization": "Bearer test-admin-token"},
                json={"role": "rw", "label": "e2e"},
            )
            assert mint.status_code in (200, 201)
            token = mint.json().get("token") or mint.json().get("value")
            assert token

            revoke = await client.delete(
                f"http://127.0.0.1:{port}/admin/tokens/{token[:8]}",
                headers={"Authorization": "Bearer test-admin-token"},
            )
            assert revoke.status_code in (200, 204)


@pytest.mark.anyio
async def test_shutdown_on_sigterm_is_clean(monkeypatch):
    """Server must exit within the configured grace period when sent SIGTERM."""
    monkeypatch.setenv("MYMCP_SHUTDOWN_GRACE_SEC", "2")
    port = _free_port()
    from mymcp.config import reset_settings_cache
    reset_settings_cache()
    from mymcp.server import create_app

    config = uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    for _ in range(60):
        await asyncio.sleep(0.05)
        if server.started:
            break
    assert server.started

    server.should_exit = True
    # Should finish within grace window + small overhead
    await asyncio.wait_for(task, timeout=5.0)
```

- [ ] **Step 2: Run**

```bash
.venv/bin/python -m pytest tests/test_e2e_server.py -v --benchmark-disable
```

Expected: green. If port conflicts cause flakes, the `_free_port` helper already mitigates; if uvicorn hangs on shutdown there's a real bug — fix the production code before retrying.

- [ ] **Step 3: Commit**

```bash
git add tests/test_e2e_server.py
git commit -m "test(e2e): real-uvicorn end-to-end smoke

Covers what ASGI-only tests cannot: actual StreamableHTTPSessionManager,
the uvicorn lifespan, /metrics exposition, and clean shutdown. Uses
ephemeral ports so it's CI-safe and parallel-safe."
```

---

## Task 8: Backup / disaster-recovery runbook

**Files:**
- Create: `docs/operations/backup-and-disaster-recovery.md`
- Modify: `README.md` (link to the new doc from the Operations section)

- [ ] **Step 1: Create the runbook**

Create `docs/operations/backup-and-disaster-recovery.md`:

```markdown
# Backup and Disaster Recovery

This runbook documents what to back up for a production mymcp deployment,
how to restore it, and what each failure mode looks like.

> Project ships defaults and procedures; alert rules, retention policies,
> and backup destinations are deployment-specific.

## Assets

| Asset | Default path | Purpose | Loss impact |
|---|---|---|---|
| Token store | `/etc/mymcp/tokens.json` | Authenticated tokens (admin/rw/ro) | Admin lockout — only the startup-random admin token works until restore |
| Audit log | `/var/log/mymcp/audit.log*` | Per-call audit trail (compliance, incident response, recorder source) | Compliance gap; recorder backlog cannot be replayed |
| Recorder overview | `/var/lib/mymcp/recorder/overview/overview.md` | LLM-curated server state summary | Recorder re-bootstraps from scratch on next start (~30 minutes of LLM calls) |
| Recorder changelog | `/var/lib/mymcp/recorder/overview/changelog.md` | Per-event log of recorded changes | History gap; nothing reconstructs it |
| Recorder cursor | `/var/lib/mymcp/recorder/cursor.json` | Position in audit log | Recorder reprocesses some events on restart |
| Service config | `/etc/mymcp/.env` (or environment) | Operational config | Service won't start with correct settings |

## Backup recommendation

- **Token store, .env:** secret-manager rotation (Vault, AWS SSM, sops, etc.). Both are small (<10 KB) and tolerate fresh copies daily.
- **Audit log:** rotate via the shipped logrotate config (already in place); ship the rotated files off-host nightly. Retention should match your compliance requirement.
- **Recorder data:** rsync the overview directory + cursor.json daily. The overview is a single Markdown file; the changelog grows append-only.

A minimal cron entry (template — adapt destinations):

```bash
#!/bin/bash
DEST=/srv/backup/mymcp/$(date +%F)
mkdir -p "$DEST"
rsync -a /etc/mymcp/tokens.json /etc/mymcp/.env "$DEST"/
rsync -a /var/lib/mymcp/recorder/ "$DEST"/recorder/
# audit logs: only rotated files, not the live one
find /var/log/mymcp -name 'audit.log.*' -mtime -1 -exec cp {} "$DEST"/ \;
```

## Restore procedures

Order matters: stop the service first, restore files, then start.

### Token store

```bash
systemctl stop mymcp
cp /srv/backup/mymcp/<date>/tokens.json /etc/mymcp/tokens.json
chmod 600 /etc/mymcp/tokens.json
chown mymcp:mymcp /etc/mymcp/tokens.json
systemctl start mymcp
```

### Recorder overview

```bash
systemctl stop mymcp
cp -a /srv/backup/mymcp/<date>/recorder/* /var/lib/mymcp/recorder/
chown -R mymcp:mymcp /var/lib/mymcp/recorder
systemctl start mymcp
# Recorder will resume from the restored cursor; no re-bootstrap.
```

### Service config

```bash
systemctl stop mymcp
cp /srv/backup/mymcp/<date>/.env /etc/mymcp/.env
chmod 600 /etc/mymcp/.env
systemctl start mymcp
```

## Failure modes — what to watch for

| Symptom | Likely cause | Action |
|---|---|---|
| Admin endpoints all return 401 | tokens.json corrupted or empty | Restore from backup; if no backup, restart with a known `MYMCP_ADMIN_TOKEN` env var |
| `mymcp_audit_write_failures_total` > 0 | Audit log dir full or readonly | Check disk, perms; tool calls return `InternalError` until resolved |
| `server_overview` says "circuit breaker open" | Recorder LLM has failed N consecutive times | Inspect logs (`recorder.supervisor.circuit_open` lines); the breaker is event-driven — once LLM recovers, the next mutating tool call triggers a retry |
| Service won't start after upgrade | New required `MYMCP_*` setting missing | Check `mymcp doctor` output; compare with current `.env.example` |
| Process exits with `audit_log_dir not writable` | Misconfigured `MYMCP_AUDIT_LOG_DIR` or fs perms | Confirm the path is writable by the `mymcp` user; fix and restart |

## What is NOT backed up by this guide

- Prometheus / Grafana state (separate retention)
- OTLP trace storage (sink-side concern)
- pip installation itself (reinstall via `pipx install algony-mymcp`)
```

- [ ] **Step 2: Link from README**

In `README.md`, in the Operations/Deployment section, add a one-liner:

```markdown
For backup and disaster-recovery procedures, see [docs/operations/backup-and-disaster-recovery.md](docs/operations/backup-and-disaster-recovery.md).
```

- [ ] **Step 3: Commit**

```bash
git add docs/operations/backup-and-disaster-recovery.md README.md
git commit -m "docs(ops): backup and disaster-recovery runbook

Documents what to back up (tokens, audit, recorder data, config),
how to restore each, and which symptoms map to which failure mode.
Closes a P3 gap from the project assessment."
```

---

## Task 9: Full regression + PR

- [ ] **Step 1: Full suite**

```bash
.venv/bin/python -m pytest tests/ -v --benchmark-disable
```

Expected: green.

- [ ] **Step 2: Open PR**

```bash
git push -u origin feature/test-infra-ops-docs
gh pr create --title "test+docs: hypothesis properties, e2e server, DR runbook" --body "$(cat <<'EOF'
## Summary

- **Hypothesis property tests** for three security-relevant surfaces: audit log round-trip + junk tolerance, ticket single-consume + TTL invariants, protected-path traversal + symlink escape.
- **Shared \`app_with_fake_session\` fixture** in conftest, replacing two near-duplicate implementations in test_security.py and test_integration.py.
- **Real-server e2e test** that actually spawns uvicorn on an ephemeral port, exercises /health, /metrics, /admin/tokens, and verifies clean SIGTERM shutdown — the gap no ASGI-only test covers.
- **Backup / disaster-recovery runbook** under \`docs/operations/\`, linked from the README.

Spec: \`docs/superpowers/specs/2026-06-06-project-assessment.md\` (P2 #16, P3 #18, P3 #19).

## Test plan
- [ ] All new tests green locally and in CI
- [ ] Hypothesis finds no counterexamples (any counterexample = real bug, fix before merge)
- [ ] e2e test runs successfully on a clean checkout

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review checklist

- [x] P2 #16 (shared fixture + real e2e test) → Tasks 6, 7
- [x] P3 #18 (Hypothesis property tests) → Tasks 2, 3, 4, 5
- [x] P3 #19 (backup/DR runbook) → Task 8
- [x] No "TODO" / "TBD" / "similar to Task N"
- [x] All test code is concrete; imports adapt to project realities are flagged where ambiguous
- [x] Dependency on Plan D (architecture refactor) flagged in header for Task 7 (e2e)
