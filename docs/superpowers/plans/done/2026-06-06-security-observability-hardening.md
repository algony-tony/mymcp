# Security & Observability Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply the four hardening fixes from the assessment: (1) systemd `NoNewPrivileges=true` + opt-in stronger isolation as comments, (2) atomic write for `tokens.json` + move `last_used` off the hot path, (3) normalize the `path` label in `MetricsMiddleware` to prevent cardinality explosion, (4) add an `audit_write_failures` dashboard panel + recommended PromQL. Companion spec: `docs/superpowers/specs/2026-06-06-project-assessment.md` (P1 #5-8).

**Architecture:** No new abstractions. Three tightly-scoped code changes (`mymcp.service.in`, `auth.py`, `server.py`) plus one dashboard JSON edit and one docs update. None of these touch the recorder, transfer endpoints, or tool dispatch — fully independent from Plan A and Plan D.

**Tech Stack:** Python 3.11+ • systemd unit syntax • FastAPI middleware • Grafana JSON.

---

## Conventions

- All commands run from the repo root: `/home/zhu/repos/mymcp`.
- Branch: `feature/security-obs-hardening` off `master`.
- After every code task: `ruff format <files> && ruff check <files>`.
- Each task ends with a commit. Push at the end.

---

## Task 1: Branch

- [ ] **Step 1: Create**

```bash
git checkout master && git pull --ff-only
git checkout -b feature/security-obs-hardening
```

---

## Task 2: systemd unit — `NoNewPrivileges=true` (always-on) + commented opt-in directives

**Files:**
- Modify: `src/mymcp/deploy/templates/mymcp.service.in`
- Test: `tests/test_deploy_setup.py` (or wherever unit-template tests live)

The mymcp product purpose is to let an LLM operate the host. Strong sandboxing (`ProtectSystem=strict`, `ProtectHome=true`, etc.) breaks that. `NoNewPrivileges=true` blocks setuid escalation while leaving normal operations intact — that one we ship enabled. The rest are commented with a one-line explanation so high-security deployments can opt in.

- [ ] **Step 1: Write failing test**

Append to `tests/test_deploy_setup.py`:

```python
def test_service_template_has_no_new_privileges(tmp_path):
    from importlib.resources import files
    template = (files("mymcp.deploy.templates") / "mymcp.service.in").read_text()
    assert "NoNewPrivileges=true" in template
    assert "[Service]" in template
    # Opt-in hardening lines must be present but commented:
    for directive in (
        "ProtectSystem",
        "ProtectHome",
        "PrivateTmp",
        "CapabilityBoundingSet",
        "RestrictAddressFamilies",
    ):
        # Match either '# DIRECTIVE=' or '#DIRECTIVE='
        assert any(
            f"#{prefix}{directive}=" in template
            for prefix in ("", " ")
        ), f"opt-in directive {directive} should appear as a comment in the unit template"
```

- [ ] **Step 2: Run, expect FAIL**

Run: `.venv/bin/python -m pytest tests/test_deploy_setup.py -k no_new_privileges -v --benchmark-disable`

Expected: FAIL (none of the directives are present).

- [ ] **Step 3: Edit the unit template**

In `src/mymcp/deploy/templates/mymcp.service.in`, add inside the `[Service]` block (after existing `ExecStart=` and related lines):

```
# --- Security hardening ---
# Always on: prevents children from gaining capabilities via setuid binaries (no sudo etc.).
# Does NOT restrict which files mymcp itself can read/write.
NoNewPrivileges=true

# Opt-in stronger isolation. These limit mymcp's view of the host filesystem,
# capabilities and network. BY DESIGN mymcp is meant to operate the host, so
# enabling these will break common LLM workflows (apt install, editing /etc,
# touching files under /home). Enable only if you accept that trade-off.
#
# ProtectSystem=strict
# ProtectHome=true
# PrivateTmp=true
# ReadWritePaths=/var/log/mymcp /var/lib/mymcp /etc/mymcp
# CapabilityBoundingSet=
# RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
```

- [ ] **Step 4: Run, expect PASS**

Same as Step 2. Expected: PASS.

- [ ] **Step 5: Verify rendered service parses (lint via systemd-analyze if available)**

If `systemd-analyze` is available in the dev environment, run:

```bash
.venv/bin/python -c "from mymcp.deploy.setup import _render_template; print(_render_template())" > /tmp/mymcp.service
systemd-analyze verify /tmp/mymcp.service 2>&1 || echo "systemd-analyze not available, skip"
```

Expected: either no output (valid) or the "not available" message — don't gate on this.

- [ ] **Step 6: Commit**

```bash
git add src/mymcp/deploy/templates/mymcp.service.in tests/test_deploy_setup.py
git commit -m "feat(deploy): systemd NoNewPrivileges + commented opt-in isolation

NoNewPrivileges=true is safe to ship enabled (blocks setuid escalation
only). Stronger isolation directives would conflict with mymcp's purpose
(letting an LLM operate the host) so they ship commented with explanation."
```

---

## Task 3: TokenStore — atomic write

**Files:**
- Modify: `src/mymcp/auth.py`
- Test: `tests/test_auth.py`

`TokenStore._save()` currently writes the JSON in-place. A crash mid-write corrupts the file, locking out admin until manual repair.

- [ ] **Step 1: Write failing test**

Append to `tests/test_auth.py`:

```python
def test_save_is_atomic(tmp_path, monkeypatch):
    """If write fails partway, the original file must remain intact."""
    from mymcp.auth import TokenStore

    path = tmp_path / "tokens.json"
    store = TokenStore(path=str(path))
    store.mint("rw", "first")
    store._save()  # initial good state
    original = path.read_text()

    # Monkeypatch Path.replace to simulate failure after the tmp write.
    real_replace = type(path).replace

    def fail(self, target):
        raise OSError("simulated crash")

    monkeypatch.setattr(type(path), "replace", fail)

    store.mint("rw", "second")
    with pytest.raises(OSError):
        store._save()

    # File must still be the original — not partial.
    assert path.read_text() == original
    # No leftover .tmp file
    leftovers = list(tmp_path.glob("tokens.json.tmp*"))
    assert leftovers == []
```

- [ ] **Step 2: Run, expect FAIL**

Run: `.venv/bin/python -m pytest tests/test_auth.py -k save_is_atomic -v --benchmark-disable`

Expected: FAIL — current implementation writes in place.

- [ ] **Step 3: Implement atomic save**

In `src/mymcp/auth.py`, modify `TokenStore._save()`:

```python
    def _save(self) -> None:
        path = Path(self._path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps({"tokens": [t.to_dict() for t in self._data]}, indent=2)
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            tmp.write_text(data)
            os.chmod(tmp, 0o600)
            tmp.replace(path)  # atomic on POSIX
        except Exception:
            with contextlib.suppress(FileNotFoundError):
                tmp.unlink()
            raise
```

Make sure `import contextlib` and `import os` are present.

- [ ] **Step 4: Run, expect PASS**

Same as Step 2. Expected: PASS.

- [ ] **Step 5: Run full auth test suite**

Run: `.venv/bin/python -m pytest tests/test_auth.py -v --benchmark-disable`

Expected: green.

- [ ] **Step 6: Commit**

```bash
git add src/mymcp/auth.py tests/test_auth.py
git commit -m "fix(auth): atomic save for tokens.json

Previous implementation wrote in place; a crash mid-write could
corrupt the token store, locking out admin. tempfile + os.replace
gives POSIX-atomic semantics. Cleans up the .tmp on failure paths."
```

---

## Task 4: TokenStore — drop the per-validate `last_used` write

**Files:**
- Modify: `src/mymcp/auth.py`
- Test: `tests/test_auth.py`

Currently every successful `validate()` updates `last_used` and rewrites the whole token JSON. Every MCP request takes this hit. Move `last_used` to memory; flush at shutdown only.

- [ ] **Step 1: Write failing test**

Append to `tests/test_auth.py`:

```python
def test_validate_does_not_write_to_disk(tmp_path, monkeypatch):
    """Each successful validate() must not touch the token file."""
    from mymcp.auth import TokenStore

    path = tmp_path / "tokens.json"
    store = TokenStore(path=str(path))
    tok = store.mint("rw", "label")
    store._save()  # baseline

    mtime = path.stat().st_mtime_ns
    for _ in range(20):
        ok = store.validate(tok)
        assert ok
    # mtime must be unchanged — validate must not rewrite the file.
    assert path.stat().st_mtime_ns == mtime


def test_last_used_persists_after_explicit_flush(tmp_path):
    from mymcp.auth import TokenStore

    path = tmp_path / "tokens.json"
    store = TokenStore(path=str(path))
    tok = store.mint("rw", "label")
    store._save()
    assert store.validate(tok)
    # Token now has a last_used timestamp in memory but file is stale.
    store.flush()  # new public method
    reloaded = TokenStore(path=str(path))
    reloaded._load()
    # Find the token and verify its last_used is now non-null
    found = next((t for t in reloaded._data if t.value == tok), None)
    assert found is not None
    assert found.last_used is not None
```

- [ ] **Step 2: Run, expect FAIL**

Run: `.venv/bin/python -m pytest tests/test_auth.py -k 'validate_does_not_write or last_used_persists' -v --benchmark-disable`

Expected: FAIL (validate still calls `_save`; `flush` method does not exist).

- [ ] **Step 3: Implement**

In `src/mymcp/auth.py`, in `TokenStore.validate`, remove the `self._save()` call that follows the `last_used` update. The `last_used` field should still update on the in-memory `Token` object, but the file write is removed from the hot path.

Add a public `flush()` method:

```python
    def flush(self) -> None:
        """Persist in-memory state (e.g. updated last_used) to disk.
        Called at shutdown via FastAPI lifespan; explicitly callable from tests.
        Locked to avoid concurrent _save during shutdown.
        """
        with self._lock:
            self._save()
```

Wire `flush()` into the FastAPI lifespan shutdown in `src/mymcp/server.py`. Find where the lifespan context's teardown runs and add:

```python
        from mymcp.auth import _store as _auth_store
        if _auth_store is not None:
            with contextlib.suppress(Exception):
                _auth_store.flush()
```

(If a lifespan handler doesn't already exist, add one wrapping the existing app construction; defer to existing patterns in `server.py`.)

- [ ] **Step 4: Run, expect PASS**

Run: same as Step 2. Expected: both tests pass.

- [ ] **Step 5: Run the full auth and server suites**

Run:
```bash
.venv/bin/python -m pytest tests/test_auth.py tests/test_server_factory.py tests/test_integration.py -v --benchmark-disable
```

Expected: green.

- [ ] **Step 6: Commit**

```bash
git add src/mymcp/auth.py src/mymcp/server.py tests/test_auth.py
git commit -m "perf(auth): in-memory last_used; flush on shutdown only

Every MCP request was rewriting tokens.json. Throughput ceiling was
'JSON serialise + fsync the whole token DB'. Move last_used to RAM;
flush() runs on lifespan shutdown so the disk copy isn't permanently
stale across restarts."
```

---

## Task 5: `MetricsMiddleware` — normalize `path` label to route template

**Files:**
- Modify: `src/mymcp/server.py`
- Test: `tests/test_metrics.py`

The current label is the raw URL path. Today the route set is small and bounded. Once a dynamic route like `/files/raw/{ticket_id}` ships, every distinct ticket becomes a label value — unbounded Prometheus cardinality.

- [ ] **Step 1: Write failing test**

Append to `tests/test_metrics.py`:

```python
def test_metric_path_label_uses_route_template(monkeypatch):
    """Dynamic-path routes must group under one label value."""
    from prometheus_client import REGISTRY
    from starlette.testclient import TestClient

    # Build the app and add a dynamic route to exercise normalization.
    from mymcp.server import create_app
    app = create_app()

    @app.get("/synthetic/{item_id}")
    async def handler(item_id: str):
        return {"ok": True}

    client = TestClient(app)
    for i in range(5):
        client.get(f"/synthetic/{i}")

    # Collect; check that label `path` equals the template, not the URL.
    samples = []
    for fam in REGISTRY.collect():
        if fam.name == "mymcp_http_requests":
            for s in fam.samples:
                if "path" in s.labels:
                    samples.append(s.labels["path"])

    # Must contain the templated form
    assert "/synthetic/{item_id}" in samples
    # Must NOT contain any of the literal IDs
    for i in range(5):
        assert f"/synthetic/{i}" not in samples
```

- [ ] **Step 2: Run, expect FAIL**

Run: `.venv/bin/python -m pytest tests/test_metrics.py -k path_label_uses_route_template -v --benchmark-disable`

Expected: FAIL — current middleware writes raw `scope["path"]`.

- [ ] **Step 3: Implement**

In `src/mymcp/server.py`, in `MetricsMiddleware.__call__` (or wherever the `path` label is constructed), replace:

```python
        path = scope["path"]
```

with:

```python
        route = scope.get("route")
        if route is not None and getattr(route, "path", None):
            path = route.path  # templated form, e.g. "/files/raw/{ticket_id}"
        else:
            path = scope.get("path", "<unmatched>")
```

The `route` object is set by Starlette during route matching; at middleware time it's available in the scope dictionary for matched paths.

- [ ] **Step 4: Run, expect PASS**

Same as Step 2. Expected: PASS.

- [ ] **Step 5: Verify existing metrics tests still green**

Run: `.venv/bin/python -m pytest tests/test_metrics.py tests/test_metrics_saturation.py -v --benchmark-disable`

Expected: green.

- [ ] **Step 6: Commit**

```bash
git add src/mymcp/server.py tests/test_metrics.py
git commit -m "fix(metrics): use route template for path label

scope['path'] gives raw URL — fine today but cardinality explodes on
the first dynamic route (e.g. /files/raw/{ticket_id}). scope['route']
exposes the templated form. Unmatched paths get a stable sentinel."
```

---

## Task 6: Audit-write-failures dashboard panel

**Files:**
- Modify: `deploy/grafana/mymcp-dashboard.json` (or whichever JSON owns the audit/observability row)

`mymcp_audit_write_failures_total` exists as a counter but no panel surfaces it. Silent audit loss is a SOC red line.

- [ ] **Step 1: Locate the audit/observability row**

Run: `grep -n 'audit' deploy/grafana/*.json | head -10`

If there's no audit row, add the panel into the existing "Observability" row.

- [ ] **Step 2: Add panel**

Insert a panel object alongside existing ones:

```json
{
  "title": "Audit write failures (silent data loss)",
  "type": "timeseries",
  "description": "Non-zero values mean audit log writes are being rejected (disk full, permission denied, rotation race). Tool calls return InternalError but the operator is otherwise blind. Recommended alert (not shipped with project): rate(mymcp_audit_write_failures_total[5m]) > 0 for 5m.",
  "targets": [
    {
      "expr": "rate(mymcp_audit_write_failures_total[5m])",
      "legendFormat": "failures/s",
      "refId": "A"
    },
    {
      "expr": "mymcp_audit_write_failures_total",
      "legendFormat": "cumulative",
      "refId": "B"
    }
  ]
}
```

- [ ] **Step 3: Validate JSON**

Run: `python -c "import json; json.load(open('deploy/grafana/mymcp-dashboard.json'))"`

Expected: no error.

- [ ] **Step 4: Commit**

```bash
git add deploy/grafana/mymcp-dashboard.json
git commit -m "feat(grafana): audit-write-failures panel

Silent audit loss had no visualisation. Panel pairs the per-second
rate with cumulative count and a panel description containing a
recommended PromQL alert (alert rules are deployment-specific and
not shipped with the project)."
```

---

## Task 7: Recommend `audit_write_failures` alert in docs

**Files:**
- Modify: `CLAUDE.md` (recorder/observability section)
- Modify: `README.md` (observability section)

- [ ] **Step 1: CLAUDE.md**

In the observability section of `CLAUDE.md`, after the existing metrics list, add:

```markdown
### `mymcp_audit_write_failures_total`

Counter. Incremented whenever the audit log writer fails (disk full,
permission, rotation race). Tool calls in this state return InternalError
to the client. The project does not ship alert rules; recommended PromQL
for operators:

```
rate(mymcp_audit_write_failures_total[5m]) > 0
```
```

- [ ] **Step 2: README.md**

In the README's observability section, near the recorder metric table, add a row (or a small subsection):

```markdown
### Audit log integrity

| Metric | Type | Description |
|---|---|---|
| `mymcp_audit_write_failures_total` | counter | Audit log writes rejected (disk full, perms). |

Recommended PromQL (alert rules are deployment-specific and not shipped):

```
rate(mymcp_audit_write_failures_total[5m]) > 0
```
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "docs: recommend PromQL for mymcp_audit_write_failures_total

Documents the metric, what it means, and a baseline query. Alert
rules are not shipped — they're deployment-specific."
```

---

## Task 8: Full regression + PR

- [ ] **Step 1: Full suite**

Run: `.venv/bin/python -m pytest tests/ -v --benchmark-disable`

Expected: green.

- [ ] **Step 2: Lint + types**

Run: `.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy src/mymcp`

Expected: clean.

- [ ] **Step 3: Push and open PR**

```bash
git push -u origin feature/security-obs-hardening
gh pr create --title "feat: security & observability hardening (systemd, token store, path label, audit panel)" --body "$(cat <<'EOF'
## Summary

- **systemd:** Enable \`NoNewPrivileges=true\` by default. Ship \`ProtectSystem\` / \`ProtectHome\` / \`PrivateTmp\` / \`ReadWritePaths\` / \`CapabilityBoundingSet\` / \`RestrictAddressFamilies\` as commented opt-in (they conflict with mymcp's "operate the host" purpose).
- **TokenStore:** Atomic save via tempfile + \`os.replace\`. Move \`last_used\` updates to memory and flush on shutdown — every MCP request previously rewrote the whole token JSON.
- **MetricsMiddleware:** Use Starlette \`route.path\` (templated form) for the \`path\` label instead of the raw URL — prevents cardinality explosion on dynamic routes.
- **Grafana:** Add an audit-write-failures panel. Document recommended PromQL for the counter in CLAUDE.md and README. (Project does not ship alert rules; recipes are operator-side.)

Spec: \`docs/superpowers/specs/2026-06-06-project-assessment.md\` (P1 #5-8).

## Test plan
- [ ] \`pytest tests/test_auth.py\` green; \`tokens.json\` mtime unchanged across 20 validates
- [ ] \`pytest tests/test_metrics.py\` green; dynamic-path test confirms templated label
- [ ] \`systemd-analyze verify\` happy with rendered unit (or skipped if unavailable)
- [ ] Grafana dashboard JSON parses

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review checklist

- [x] P1 #5 systemd hardening → Task 2 (NoNewPrivileges on; rest as opt-in comments per assessment decision)
- [x] P1 #6 TokenStore atomic + in-memory last_used → Tasks 3, 4
- [x] P1 #7 path label normalization → Task 5
- [x] P1 #8 audit_write_failures panel + recommended queries → Tasks 6, 7
- [x] No "TODO" / "TBD" / "similar to Task N"
- [x] No alert rule files added — only dashboards and docs
