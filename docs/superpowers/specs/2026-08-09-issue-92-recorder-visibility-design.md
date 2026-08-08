# issue #92: Make a Dead Recorder Visible

**Status:** Approved (2026-08-09)
**Date:** 2026-08-09
**Owner:** algony-tony
**Tracks:** [#92](https://github.com/algony-tony/mymcp/issues/92)

## Problem

Following the README's **Upgrade** section to move a v2 deployment to v3 silently
kills the overview recorder, and nothing surfaces the failure. `server_overview`
keeps serving a frozen `overview.md` to external LLMs as current fact.

On one production host this went unnoticed for four weeks. The last successful
merge was 2026-07-13 02:08 CST; the v3.0.1 upgrade plus `systemctl restart mymcp`
happened the same day at 22:40 CST. It surfaced only on 2026-08-09 because the
overview's *content* was visibly wrong — it described an nginx hello page replaced
by an SPA weeks earlier, and reported `v2.5.0` while the binary was `v3.0.1`.

Nothing was broken. The sidecar was never started. `MYMCP_RECORDER_ENABLED=true`
was set the whole time, `cursor.json`'s inode still matched the live `audit.log`
(so this was not rotation), and running the recorder by hand worked immediately.
The `[recorder]` dependencies survived only by accident — they were base
dependencies in v2.5.0 and were left in the venv. The missing piece was purely
the sidecar systemd unit, which the upgrade path never mentions.

Two failures compound here, and the second is what set the cost:

1. **The upgrade drops the sidecar.** A 20-minute documentation omission.
2. **Nothing reports the drop.** That omission became a four-week silent outage.

### Root cause of the missing signal

The staleness signal is not new work — it is a **v3 port regression**. In v2 the
in-process handler `src/mymcp/recorder/tool.py` prefixed the overview with a
status banner under a three-level priority (circuit open → backlog stalled →
backlog with recent failure → idle means no banner). When `server_overview` moved
to the Go core, the banner did not come with it. `go/internal/tools/overview.go`
reads the file and returns it — no freshness check of any kind. `tool.py` is now
dead on the serving path; only `tests/recorder/test_server_overview_tool.py`
still imports it.

The Go core cannot read the sidecar's in-memory status: the sidecar serves no
HTTP and pushes metrics via OTLP. Any signal must be derived from disk.

## Goals & Non-Goals

**Goals**

1. A v2 → v3 upgrade that follows the README ends with a running recorder.
2. A stopped recorder is visible in `server_overview` output within one merge
   interval, to both a model reading prose and a program reading fields.
3. `server_overview`'s failure message names the actual cause.
4. A recorder recovering from a large backlog drains it instead of tripping its
   circuit breaker.

**Non-Goals**

- Having the Go core supervise, start, or restart the sidecar. It reports; the
  operator (or systemd) acts.
- A new cross-process IPC channel. Signals come from files both sides already own.
- Shipping alert rules. The project ships dashboards and documented PromQL only;
  that convention holds.
- Changing the `error` code vocabulary of `server_overview` (see A3).

## Approach

Two PRs. Stop the bleeding first, with changes that carry no test burden and can
ship immediately; then the hardening that needs real tests.

---

## PR-A — Stop the bleeding

Documentation plus two constant-level changes. No new logic.

### A1. README: a v2 → v3 upgrade subsection

`README.md:77-86` currently reads:

> Existing v2 deployments already have this unit — a `pipx upgrade` + restart
> keeps it (the `ExecStart=mymcp serve` line is unchanged; it now runs the Go
> binary).
>
> ### Upgrade
>
> ```bash
> pipx upgrade algony-mymcp
> sudo systemctl restart mymcp
> ```

The framing — *"already have this unit"*, *"keeps it"*, *"unchanged"* — actively
communicates that nothing else is required. Add a `#### From v2.x` subsection
covering:

1. `pipx inject algony-mymcp "algony-mymcp[recorder]"`
2. Install `mymcp-recorder.service` (until PR-B lands `--install-unit`, inline the
   full unit text so it can be copied without transcription)
3. `systemctl enable --now mymcp-recorder`
4. **Verification** — `cursor.json`'s `offset` advances, `overview.md`'s mtime
   moves. Both are checkable in one command and would have caught this in minutes.

Also amend the preceding paragraph: it covers the main service only; a v2
deployment that ran the recorder needs a second unit.

The correct steps existed in
`docs/superpowers/plans/2026-07-12-go-core-m3b-p3b-v3-cutover.md:528` but never
reached user-facing docs.

### A2. Fix a README/code contradiction

`README.md:224` describes `MYMCP_RECORDER_ENABLED` as: *"the Go `server_overview`
tool works regardless, returning a stub until an overview exists"*. It does not —
`overview.go:17-22` returns `success: false`. The stub is v2 Python behaviour
(`tool.py`'s `_STUB_TEMPLATE`) that the Go port did not carry over. Correct the
documentation to match the code; the `success: false` shape is pinned by compat
tests and stays.

### A3. Differentiate the failure message

`overview.go`'s single `err != nil` branch returns *"server_overview requires
`MYMCP_RECORDER_ENABLED=true`"* for every read failure: sidecar never installed,
sidecar crashed before first write, wrong `recorder_data_dir`, permission denied.
On the affected host that flag was correct the whole time, so the message sent
the operator to a dead end.

Split it:

- `os.IsNotExist` → overview not generated yet; check `systemctl status
  mymcp-recorder`.
- any other error → read failure, including the path and the underlying error.

**The `error` code stays `RecorderDisabled`.** `tests/compat/test_server_overview.py:10`
asserts it, and the entire value of this item is in the message. Renaming the code
would be a compat break bought for nothing.

### A4. Raise the default `max_tokens`

`config.py:99` defaults `recorder_llm_max_tokens` to 16384. On reasoning models
thinking tokens count toward `completion_tokens` — probing DeepSeek
`deepseek-v4-flash` with a trivial prompt returned `{"completion_tokens": 54,
"completion_tokens_details": {"reasoning_tokens": 40}}`, i.e. 74% of the budget
spent on reasoning before answering. Default to 32768 (accepted by that model,
which also accepts 65536) and note in the README config table that reasoning
tokens draw on the output budget.

### A5. Fix a stale path reference

`docs/superpowers/plans/2026-07-12-go-core-m3b-p3b-v3-cutover.md:528` points at
`mymcp/deploy/templates/`; the template now lives in
`src/mymcp/recorder/templates/`.

---

## PR-B — Hardening

### B1. Disk-derived freshness in the Go core

New file `go/internal/tools/recorderstatus.go` with a single responsibility:
derive recorder status from disk. `overview.go` keeps only assembly.

| Field | Source |
|---|---|
| `last_updated` | the `_Last updated:_` header line in `overview.md` (written by `merge_cycle.py:266-272` and `overview.py:85-91`); falls back to file mtime if absent or unparseable |
| `pending_events` | read `cursor.json`'s `offset`, scan `audit.log` from there, count events that are both mutating and successful — the same filter as `EventTailer.pending_count` (`events.py:162-191`), including its rotation branch when the committed inode no longer matches |
| `stale` | `pending_events > 0` **and** `last_updated` older than `2 × MYMCP_RECORDER_MERGE_INTERVAL_SEC` |

Two properties of that `stale` definition matter:

- **It is the conjunction, deliberately.** It is the local evaluation of the
  composite PromQL this project already recommends, and it avoids the documented
  historical false positive where an idle server with nothing to fold looked
  stale. A quiet server has `pending_events == 0` and is never flagged.
- **It needs no new configuration.** Go reads the same
  `MYMCP_RECORDER_MERGE_INTERVAL_SEC` the sidecar reads, out of the same `.env`.
  The `2×` factor matches the v2 banner's threshold so one slow cycle is not
  flagged.

Counting mutating events requires knowing which tools mutate — and the correct
set is **not** `mcpserver.writeTools`. The recorder consumes
`MUTATING_TOOLS` (`events.py:34-43`), which is six entries to `writeTools`' four:
it adds `prepare_download` (classified read-only for permissions, but it still
hands out host bytes) and `transfer_upload` (the *endpoint* audit name for a
redeemed upload ticket, which is not an MCP tool at all and so can never appear
in `writeTools`). Counting with `writeTools` would report a backlog that differs
from the one the sidecar actually drains.

So the Go side defines its own `mutatingTools` in `recorderstatus.go` as a
faithful port of `MUTATING_TOOLS`, with a comment naming the Python source and
stating why it is not `writeTools`. This also removes the import-cycle concern
entirely — `tools` never needs to reference `mcpserver`. Success is `result ∈
{"ok", "success"}`, matching `_SUCCESS_RESULTS` (`events.py:48`).

**Output.** Add `last_updated`, `pending_events`, and `stale` to the result, and
when `stale` is true prefix the overview body with a banner. Both, not either:
the fields serve programmatic consumers, and the banner guarantees a model that
reads only the prose still sees it. Banner wording follows v2
(`_⚠️ N events pending; merge stalled for M minutes_`) with the
`systemctl status mymcp-recorder` remedy appended.

### B2. Adaptive batch shrinking on `max_tokens`

`merge_cycle.py:161-167` raises on `max_tokens` and re-reads the same oversized
batch next cycle. This interacts badly with the outage it is most likely to
follow: the longer the recorder has been down, the larger the first merge's
backlog, the more reasoning it needs, and the more likely it truncates. With
`recorder_circuit_breaker_threshold=5` and a 300s interval, a recovering recorder
trips its breaker roughly 25 minutes after being started and goes dormant again —
so the fix for A1 can quietly fail to stick.

`MergeCycle` gains `self._adaptive_max`, initialised to `max_events_per_cycle`.
On a `max_tokens` failure it halves (floor 5) and still raises, so the supervisor
keeps its failure accounting and metrics unchanged. The next cycle reads the
smaller batch; a success restores the full value. The sequence 50 → 25 → 12 → 6 → 3
converges within the 5-failure breaker budget.

Metrics are untouched: `merge_cycles{reason="max_tokens"}` already names this
outcome. The effective batch size goes on the existing cycle span as an attribute.

### B3. `mymcp-recorder --install-unit`

`src/mymcp/recorder/templates/mymcp-recorder.service.in` ships with
`{service_user}` / `{working_directory}` / `{env_file}` / `{exec_start}`
placeholders, but v3 removed `install-service`, the CLI that rendered `.in`
templates. The only thing in the product that renders it is
`tests/recorder/test_sidecar_packaging.py`, using values hardcoded inside the
test. The template is packaged and formattable, and nothing tells an operator
what to substitute; recovery required hand-transcription.

Add `--install-unit`: render the template from settings plus
`shutil.which("mymcp-recorder")`, print to stdout by default, `--output PATH` to
write. Rewrite `test_sidecar_packaging.py` to exercise the real code path instead
of its own copy of the substitutions.

Separately, `[project.scripts]` (`pyproject.toml:80-81`) declares `mymcp-recorder`
unconditionally while `dependencies = []` and the real deps live in the
`[recorder]` extra, so a base `pipx install` puts a command on `PATH` that dies
with `ModuleNotFoundError: httpx`. Wrap the wiring import chain in `main()` with
`try/except ImportError` and report: run
`pipx inject algony-mymcp "algony-mymcp[recorder]"`.

### B4. Delete `src/mymcp/recorder/tool.py`

Once the banner lives in Go, this module is dead on every path — only its own
test imports it. Delete it with `_STUB_TEMPLATE`, and port the six banner cases
in `tests/recorder/test_server_overview_tool.py` to the Go `recorderstatus` table
test. Those cases encode the real priority rules, including "idle is not a
failure"; the rules are worth keeping even though the Python code implementing
them is not.

## Testing

**Go** — table-driven `recorderStatus`: no `cursor.json`; cursor behind a grown
`audit.log`; idle (`pending == 0` with a deliberately old `overview.md`, asserting
**not** stale); no `overview.md`; `audit.log` rotated so the committed inode no
longer matches; `_Last updated:_` malformed (mtime fallback). Plus assembly tests
on `overview.go` for the two error branches from A3.

**Python** — consecutive `max_tokens` failures shrink the batch and then succeed,
and success restores the full batch; `--install-unit` output parses as a systemd
unit.

**Compat** — the added fields do not affect `tests/compat/test_server_overview.py`:
compat CI runs without an `overview.md`, so it takes the disabled branch, whose
shape is unchanged. `golden_tools.json` snapshots tool *schemas*; result shape is
not in scope.

**Gates** — `go test ./... && go vet ./... && gofmt -l .`;
`pytest tests/ --benchmark-disable`;
`ruff check . && ruff format --check . && mypy src/mymcp`.

**Manual** — walk the new A1 upgrade section on ucloud end to end and confirm the
verification step reports a live recorder.

## Risks

- **Go now parses `cursor.json`.** This extends the Python ↔ Go contract, which
  so far covers `audit.log`, `MYMCP_*`, and `tokens.json`. Record it in CLAUDE.md's
  contract section so the format is not changed unilaterally.
- **Duplicated mutating-event filter.** `pending_events` reimplements
  `pending_count`'s semantics — including the `MUTATING_TOOLS` membership list —
  in Go. The set now exists in two languages and can drift; a tool added to the
  recorder's set but not to Go's would make the backlog silently under-count.
  Pin both with tests and name the counterpart file in each comment.
- **A shrinking batch drains more slowly.** Deliberate: a recorder that drains
  slowly recovers, one that trips its breaker does not.

## Delivery

Both PRs branch from `master` and merge via pull request; `master` is protected
and takes no direct commits.
