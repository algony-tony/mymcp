# Quick Wins Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the cheap, high-ROI fixes from the assessment: stale `.env.example` (wrong prefix), CHANGELOG drift, CI coverage + security gates, tool description quality, test flakiness plumbing, plans archive, "Why no Dockerfile" rationale. Each task is small and ships independently. Companion spec: `docs/superpowers/specs/2026-06-06-project-assessment.md` (P0 #1-4, P1 #9, P1 #11, P3 #20, P3 #21).

**Architecture:** No architecture change. Pure hygiene: env example regen, docs backfill, CI step additions, JSON schema polish, dev-dependency additions, filesystem reorg.

**Tech Stack:** Python 3.11+ • pytest plugins (timeout, randomly) • pip-audit • GitHub Actions • Markdown.

---

## Conventions

- All commands run from the repo root: `/home/zhu/repos/mymcp`.
- Branch: `feature/quick-wins` off `master`.
- After every code task: `ruff format <files> && ruff check <files>`.
- Each task ends with a commit. Push at the end.

---

## Task 1: Branch

- [ ] **Step 1: Create**

```bash
git checkout master && git pull --ff-only
git checkout -b feature/quick-wins
```

---

## Task 2: Rewrite `.env.example` with correct `MYMCP_*` prefix

**Files:**
- Modify: `.env.example` (full rewrite)

The file uses the legacy `MCP_*` prefix; the code reads `MYMCP_*`. Anyone copying it gets a silently-ignored config.

- [ ] **Step 1: Inspect current**

Run: `cat .env.example`

- [ ] **Step 2: Replace contents**

Overwrite `.env.example` with:

```bash
# mymcp configuration (env vars also accepted directly).
# All keys use the MYMCP_ prefix. See README "Configuration" for the full list.

# --- Server ---
MYMCP_HOST=127.0.0.1
MYMCP_PORT=8080
MYMCP_LOG_LEVEL=INFO

# --- Auth ---
# Leave unset to auto-generate a random admin token printed to stderr at startup.
# MYMCP_ADMIN_TOKEN=

# --- Paths ---
MYMCP_AUDIT_LOG_DIR=/var/log/mymcp
MYMCP_TOKEN_STORE_PATH=/etc/mymcp/tokens.json
# Extra paths that file tools may not read/write/edit (audit log dir is always protected).
# MYMCP_PROTECTED_PATHS=/etc/shadow,/root/.ssh

# --- Tool limits ---
# MYMCP_READ_FILE_DEFAULT_LIMIT=2000
# MYMCP_READ_FILE_MAX_LIMIT=50000
# MYMCP_BASH_TIMEOUT_SEC=120
# MYMCP_BASH_TIMEOUT_MAX_SEC=600

# --- Process management ---
# MYMCP_SHUTDOWN_GRACE_SEC=10

# --- Observability ---
# MYMCP_METRICS_TOKEN=  # if set, /metrics requires Bearer <this>
# OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318

# --- Recorder (optional; install with: pip install algony-mymcp[recorder]) ---
# MYMCP_RECORDER_ENABLED=false
# MYMCP_RECORDER_DATA_DIR=/var/lib/mymcp/recorder
# MYMCP_RECORDER_LLM_PROVIDER=anthropic
# MYMCP_RECORDER_LLM_MODEL=claude-sonnet-4-6
# MYMCP_RECORDER_LLM_API_KEY=
# MYMCP_RECORDER_LLM_BASE_URL=
# MYMCP_RECORDER_LLM_MAX_TOKENS=16384
# MYMCP_RECORDER_MERGE_INTERVAL_SEC=300
# MYMCP_RECORDER_MAX_EVENTS_PER_CYCLE=200
# MYMCP_RECORDER_CIRCUIT_BREAKER_THRESHOLD=5
# MYMCP_RECORDER_BOOTSTRAP_MAX_ITERATIONS=20
# MYMCP_RECORDER_BOOTSTRAP_TOKEN_BUDGET=200000
# MYMCP_RECORDER_BOOTSTRAP_PROBE_TIMEOUT_SEC=30
```

- [ ] **Step 3: Sanity check against current Settings**

Run: `.venv/bin/python -c "from mymcp.config import Settings; print('\n'.join(sorted(Settings.model_fields)))"`

Confirm every field listed above exists in `Settings` (allow for variable subset — comment out anything that doesn't exist; don't invent fields).

- [ ] **Step 4: Commit**

```bash
git add .env.example
git commit -m "fix(env-example): use MYMCP_ prefix and document recorder knobs

The legacy MCP_ prefix was silently ignored after v2.0.0. Operators
copying .env.example got a server that fell back to defaults for
every setting. Also adds MYMCP_RECORDER_LLM_MAX_TOKENS and
MYMCP_RECORDER_CIRCUIT_BREAKER_THRESHOLD which were undocumented."
```

---

## Task 3: Backfill CHANGELOG

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Gather PR details**

Run:
```bash
git log --oneline --since='2026-05-15' master
gh pr view 43 --json title,body,number
gh pr view 45 --json title,body,number
gh pr view 47 --json title,body,number
gh pr view 48 --json title,body,number
```

- [ ] **Step 2: Insert new section under `[Unreleased]`**

In `CHANGELOG.md`, immediately under `## [Unreleased]`, add (adjust titles to match real PR titles):

```markdown
### Added
- (#48) Recorder reason labels on merge_cycle metrics; new `Recorder Health` row in Grafana dashboards; SLO gauges (`mymcp_recorder_circuit_open`, `mymcp_recorder_merge_last_success_timestamp`).
- (#43) 57 mutation-killer tests for audit/dispatch/bash/files paths.

### Changed
- (#47) Recorder resilience overhaul — circuit breaker, structured JSON output from LLM, prioritized banner in `server_overview`.
- (#45) Tool definitions split out of `mcp_server.py` into `tool_definitions.py`.
```

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): backfill #43, #45, #47, #48 under [Unreleased]"
```

---

## Task 4: CI coverage floor

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Measure current coverage as a baseline**

Run: `.venv/bin/python -m pytest tests/ --cov=mymcp --cov-report=term --benchmark-disable 2>&1 | tail -20`

Note the total coverage percentage (e.g. 91%).

- [ ] **Step 2: Pick the floor**

Set floor 3-5 points below current to absorb minor fluctuation. If current is 91, use 85 (a healthy margin without making the floor meaningless). Record the chosen value here as `COV_FLOOR`.

- [ ] **Step 3: Edit ci.yml**

Find the pytest invocation in `.github/workflows/ci.yml` that runs with `--cov`. Append `--cov-fail-under=<COV_FLOOR>` to it. For example, if the line reads:

```yaml
      - run: pytest tests/ --cov=mymcp --cov-report=xml --benchmark-disable
```

change to:

```yaml
      - run: pytest tests/ --cov=mymcp --cov-report=xml --cov-fail-under=85 --benchmark-disable
```

(Use the actual floor you chose.)

- [ ] **Step 4: Smoke-run the command locally**

Run: `.venv/bin/python -m pytest tests/ --cov=mymcp --cov-report=term --cov-fail-under=85 --benchmark-disable 2>&1 | tail -5`

Expected: exit 0 (coverage ≥ floor).

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: enforce coverage floor with --cov-fail-under

Coverage was being measured and badged but never gated. PRs could
silently reduce coverage. Floor is set comfortably below current to
absorb churn while catching real regressions."
```

---

## Task 5: CI dependency vulnerability scan

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Add pip-audit step**

In `.github/workflows/ci.yml`, in the lint+test job (alongside ruff/mypy/pytest), add a step:

```yaml
      - name: pip-audit
        run: |
          pip install pip-audit
          pip-audit --strict --requirement requirements-dev.txt
```

(Run on the same Python version matrix step; doesn't need to fan out across all versions if budget matters — pin to one job.)

- [ ] **Step 2: Local dry-run**

Run: `pip install pip-audit && pip-audit --strict --requirement requirements-dev.txt`

If any vulnerability is reported, decide before committing: bump the dep (preferred) or document a temporary `--ignore-vuln ID` with reason. Do not commit a permanently-suppressed scan.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add pip-audit scan against requirements-dev.txt

Dependabot bumps versions but does not surface known CVEs. pip-audit
fails the build on any OSV/GHSA hit, matching the project's general
'gate on quality, don't just report' posture."
```

---

## Task 6: README — add missing recorder knobs to Configuration table

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Locate the Recorder configuration table**

Run: `grep -n "Recorder" README.md | head -10`

Find the Configuration table section that lists recorder env vars.

- [ ] **Step 2: Insert two rows**

In the Recorder Configuration table, add (matching the table's existing column shape):

```markdown
| `MYMCP_RECORDER_LLM_MAX_TOKENS` | `16384` | Per-call output ceiling for the recorder's LLM. Must stay ≤ the chosen model's max_output_tokens. Larger values let the recorder cover more sections per cycle but cost more per call. |
| `MYMCP_RECORDER_CIRCUIT_BREAKER_THRESHOLD` | `5` | Consecutive merge failures before the breaker opens. After this plan ships, recovery is event-driven (a new event triggers a single retry; success clears the breaker). Set to `0` to disable the breaker. |
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs(readme): document RECORDER_LLM_MAX_TOKENS and CIRCUIT_BREAKER_THRESHOLD"
```

---

## Task 7: `tool_definitions.py` — fix `read_file` description and limit

**Files:**
- Modify: `src/mymcp/tool_definitions.py`

- [ ] **Step 1: Inspect**

Run: `grep -n -A 5 'read_file' src/mymcp/tool_definitions.py | head -40`

Confirm `description` mentions "max 10000" or similar stale limit.

- [ ] **Step 2: Update**

In `src/mymcp/tool_definitions.py`, for the `read_file` tool:

- In `description`, replace any phrase like "up to 10000 lines" with "up to MYMCP_READ_FILE_MAX_LIMIT lines (default 50000)".
- In the `inputSchema.properties.limit` block, set `"maximum": 50000` (or remove the explicit max if the runtime clamps — but keep `"minimum": 1`).
- Add `"additionalProperties": false` to the `inputSchema` object.

- [ ] **Step 3: Run schema/dispatch tests**

Run: `.venv/bin/python -m pytest tests/test_mcp.py tests/test_files.py -v --benchmark-disable`

Expected: green. (No tests should hardcode the old 10000 number.)

- [ ] **Step 4: Commit**

```bash
git add src/mymcp/tool_definitions.py
git commit -m "fix(tools): correct read_file limit in tool description

LLMs see this description, not the README. The stale 10000 cap
caused models to artificially split reads. Aligns with actual
MYMCP_READ_FILE_MAX_LIMIT (default 50000) and adds
additionalProperties: false for stricter validation."
```

---

## Task 8: `tool_definitions.py` — bash_execute warnings and timeout convention

**Files:**
- Modify: `src/mymcp/tool_definitions.py`

- [ ] **Step 1: Locate bash_execute definition**

Run: `grep -n -A 15 'bash_execute' src/mymcp/tool_definitions.py | head -30`

- [ ] **Step 2: Update description**

Append to the existing `description` (keep prior content, add):

```
WARNING: bash_execute is NOT subject to MYMCP_PROTECTED_PATHS. It can read or
modify any path the service user has access to (including audit logs and
tokens.json). Use ro tokens for untrusted clients.

Defaults: working_dir="/" if omitted; timeout 120s (max MYMCP_BASH_TIMEOUT_MAX_SEC,
default 600s). On timeout, exit_code is -1 and timed_out is true.
```

Add `"additionalProperties": false` to the `inputSchema`.

- [ ] **Step 3: Commit**

```bash
git add src/mymcp/tool_definitions.py
git commit -m "docs(tools): bash_execute description — protected-path bypass + timeout"
```

---

## Task 9: `tool_definitions.py` — transfer tool workflow notes + additionalProperties

**Files:**
- Modify: `src/mymcp/tool_definitions.py`

- [ ] **Step 1: Update prepare_upload description**

Append to the existing `description`:

```
Workflow: this tool returns a one-shot ticket URL. The CLIENT must then upload
the file via:  curl -F "file=@/local/path" <ticket_url>
The MCP server does not pull from the client. Tickets are single-use and
expire (see MYMCP_TICKET_TTL_SEC).
```

Add `"additionalProperties": false` to the input schema.

- [ ] **Step 2: Same for prepare_download**

Append:

```
Workflow: returns a one-shot ticket URL. The CLIENT must fetch via:
  curl -o /local/path <ticket_url>
The MCP server does not push to the client.
```

Add `"additionalProperties": false`.

- [ ] **Step 3: Apply `additionalProperties: false` to remaining tools**

For each of: `write_file`, `edit_file`, `glob`, `grep`, `server_overview` — add `"additionalProperties": false` to the input schema if not already present. Skip any that already have it.

- [ ] **Step 4: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v --benchmark-disable`

Expected: green. Any test that sends an unknown property should still pass — if any test now fails, it was relying on lax schema and needs updating.

- [ ] **Step 5: Commit**

```bash
git add src/mymcp/tool_definitions.py
git commit -m "fix(tools): strict input schemas + transfer workflow notes

additionalProperties:false on all 9 tools catches LLM-side typos at
schema layer instead of at runtime. Transfer descriptions now spell
out the curl-on-client workflow that the README explains but the
LLM-facing schema previously omitted."
```

---

## Task 10: Add `pytest-timeout` and `pytest-randomly` to dev deps

**Files:**
- Modify: `pyproject.toml`
- Modify: `requirements-dev.txt` (regenerated)

- [ ] **Step 1: Edit pyproject.toml**

In `pyproject.toml`, under `[project.optional-dependencies]` `dev = [...]`, add:

```toml
    "pytest-timeout",
    "pytest-randomly",
```

- [ ] **Step 2: Regenerate lockfile**

Run:
```bash
pip-compile --extra dev --strip-extras \
  --unsafe-package algony-mymcp --unsafe-package pip --unsafe-package setuptools \
  --output-file requirements-dev.txt pyproject.toml
```

- [ ] **Step 3: Install**

Run: `.venv/bin/pip install -e ".[dev]" -c requirements-dev.txt`

- [ ] **Step 4: Configure default timeout**

In `pyproject.toml`, under `[tool.pytest.ini_options]`, add:

```toml
timeout = 30
timeout_method = "thread"
```

- [ ] **Step 5: Smoke-run with randomization**

Run: `.venv/bin/python -m pytest tests/ -v --benchmark-disable -p randomly 2>&1 | tail -10`

Expected: green. If anything fails due to ordering, the failure is a real test-pollution bug — file it and fix in a separate PR (do not gate this commit on it). Document the failure in the commit body if any.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml requirements-dev.txt
git commit -m "test: add pytest-timeout (30s) and pytest-randomly

Caps the time a single test can hang in CI (was unbounded, default
GH Actions job ceiling 6h). Randomization surfaces inter-test
pollution from module-level singletons (auth._store, audit._logger).
Any failures found by randomization should be fixed separately."
```

---

## Task 11: Archive completed plans

**Files:**
- Create: `docs/superpowers/plans/done/` directory
- Move: completed plans (April 2026 and any one-off execution scripts)

- [ ] **Step 1: List candidates**

Run: `ls docs/superpowers/plans/2026-04-*.md`

These are completed work (linux-mcp-server, audit-and-permissions, etc.). Identify any from May that are also done.

- [ ] **Step 2: Move**

```bash
mkdir -p docs/superpowers/plans/done
git mv docs/superpowers/plans/2026-04-01-linux-mcp-server.md docs/superpowers/plans/done/
# repeat for each completed plan; do NOT move:
#   - any plan from June 2026
#   - any plan whose work is still in-flight
```

- [ ] **Step 3: Add a README to the done dir**

Create `docs/superpowers/plans/done/README.md`:

```markdown
# Completed plans (archive)

This directory holds plan documents whose implementation has shipped.
They are kept for historical and audit reasons. For active and proposed
work, see the parent `plans/` directory.
```

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/plans/
git commit -m "docs: archive completed plans under plans/done/

Active plans dir was 16 files / ~800 KB, indistinguishable from
in-flight work. Moves April plans and other shipped work; keeps
June+ plans in the top level."
```

---

## Task 12: README — "Why we don't ship a Dockerfile"

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add section**

In `README.md`, near the install/deploy section, add:

```markdown
## Why we don't ship a Dockerfile

mymcp's purpose is to let an LLM operate the host Linux system: install
packages, edit config under /etc, manage processes, read/write files in
/home. Running mymcp inside a container defeats this: by default the
container sees only its own filesystem and PID namespace, so the LLM
can only operate the container itself.

To run mymcp in a container and actually control the host you would
need at minimum `--privileged --pid=host --net=host -v /:/host` and
some convention that the LLM operates on `/host`. At that point the
container provides no isolation; it's an awkward installer.

If you really want a container (e.g. as a sidecar managing another
containerized service), it is ~10 lines:

```dockerfile
FROM python:3.13-slim
RUN pip install algony-mymcp
EXPOSE 8080
ENV MYMCP_HOST=0.0.0.0 MYMCP_PORT=8080
CMD ["mymcp", "serve"]
```

Our recommended deployment is `pipx install algony-mymcp` + the
shipped systemd unit (`mymcp install-service`), which gives the
service direct host access matching the product's purpose.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs(readme): explain why no Dockerfile is shipped

Containerizing mymcp surrenders its core value (host control) without
any real security benefit. Document the rationale so this question
doesn't keep coming up."
```

---

## Task 13: Push and open PR

- [ ] **Step 1: Push**

```bash
git push -u origin feature/quick-wins
```

- [ ] **Step 2: Open PR**

```bash
gh pr create --title "chore: quick-wins (env-example, CHANGELOG, CI gates, tool docs, test plumbing)" --body "$(cat <<'EOF'
## Summary

- Fix `.env.example` — was using stale `MCP_` prefix, silently broke operator copy-paste.
- CHANGELOG backfill for #43 / #45 / #47 / #48.
- CI: `--cov-fail-under=<floor>` and `pip-audit` step.
- Tool descriptions: correct `read_file` limit, document `bash_execute` protected-path bypass and timeout convention, document transfer curl-on-client workflow, `additionalProperties: false` on all input schemas.
- Dev deps: `pytest-timeout` (30s default) and `pytest-randomly` to surface flake/pollution.
- Archive completed plans under `docs/superpowers/plans/done/`.
- README: "Why no Dockerfile" rationale.

Spec: `docs/superpowers/specs/2026-06-06-project-assessment.md` (P0 #1-4, P1 #9, P1 #11, P3 #20, P3 #21).

## Test plan
- [ ] CI green (coverage gate + pip-audit + randomized order)
- [ ] Manually verify `.env.example` keys all match `Settings` fields
- [ ] `mymcp install-service` still works (no service-template change here)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review checklist

- [x] Spec items P0 #1-4 → Tasks 2, 3, 4, 6 (all four mapped)
- [x] P1 #9 (tool description quality) → Tasks 7, 8, 9
- [x] P1 #11 (pytest-timeout + randomly) → Task 10
- [x] P3 #20 (plans archive) → Task 11
- [x] P3 #21 (Why no Dockerfile) → Task 12
- [x] No "TODO" / "TBD" / "similar to Task N"
- [x] Every code block is self-contained
