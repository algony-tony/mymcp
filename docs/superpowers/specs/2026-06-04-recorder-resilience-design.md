# Recorder Resilience Fix Design

**Date:** 2026-06-04
**Status:** Draft (supersedes closed PR #46)
**Owner:** algony-tony
**Topic:** Hardening the llm-recorder merge cycle against LLM truncation, empty responses, permanently-stuck cursors, and provider-specific output guarantees.

Companion to [`2026-05-29-llm-recorder-design.md`](2026-05-29-llm-recorder-design.md); this document describes the production incident that motivated the fix and the design choices made in response. Closed PR #46 was withdrawn so the work could be re-done with proper plan-before-implement flow and a wider scope (Anthropic structured output, observability for the circuit breaker, Python-owned Recent Changes).

## 1. Problem Statement

On the GCP host, calling `server_overview` returned a stale 2026-04-30 overview banner-prefixed with `"⚠️ overview is 0 minutes stale: LLM returned unparseable JSON: Unterminated string starting at: line 10 column 26 (char 595)"`. The recorder had been silently failing for over a month.

Investigation on the live server (audit.log + journald) established the failure chain:

1. **`max_tokens=4096` hardcoded** in `merge_cycle.py:83` and `bootstrap.py:169`. No env knob, no provider awareness.
2. **The single successful merge** (2026-06-02 12:21:29) produced `tokens_out=3854` — within 6% of the ceiling. Every other cycle blew past it.
3. **The output schema required rewriting the whole overview** (`updated_overview_md` field). With overview at ~1.8k tokens + JSON wrapping + new changelog lines, the LLM kept getting truncated mid-string in `updated_overview_md`.
4. **`EventTailer.rollback()` on every failure** preserved the cursor, so the same poisoned batch of 758 events was retried every 300s for 35 days, burning DeepSeek API quota and producing no progress.
5. **The banner showed "0 minutes stale"** even when `stale_seconds` was `None` because `tool.py:31` fell through to `else 0` when only `last_error` was set — misleading operators into thinking the file had just updated.

The provider was DeepSeek `deepseek-v4-flash` (1M context, 384k max output) — the provider was not the constraint; the client-side cap was.

## 2. Goals & Non-Goals

**Goals**

1. Raise the per-call ceiling and make it configurable per deployment.
2. Detect known failure modes (empty response, `stop_reason=max_tokens`) before parse, with actionable errors.
3. Reduce per-cycle output volume so a single cycle never approaches the ceiling.
4. Stop retrying poisoned batches forever; fail loudly when something is fundamentally wrong.
5. Make the overview banner honest about what actually went wrong.
6. Provide structured-output guarantees for both OpenAI-style (DeepSeek) and Anthropic providers — not just OpenAI.
7. Expose circuit-breaker state as a metric so Grafana/Prometheus can alert on it.
8. Eliminate the only mandatory section rewrite (`Recent Changes`) by having Python compute it from the changelog tail.

**Non-Goals**

- Automatic recovery from circuit-open state. Restart of the service is the only reset path (intentional — keeps the operator in the loop).
- Auto-skipping past poisoned events. Skipping silently violates the recorder's at-least-once contract.
- Bootstrap protocol changes beyond `max_tokens` plumbing. Bootstrap is a one-off; section-update protocol is unnecessary.
- Shipping Prometheus alert rule YAML files. The repo currently has dashboards but no alert files; adding the infra is a separate ops PR. We ship the metric; downstream ops define the alert.

## 3. Design Decisions

### 3.1 `max_tokens` becomes a config knob, default 16384

- **Why not just raise the constant?** Different providers cap differently (Claude Haiku 4.5: 64k, Sonnet 4.6: 64k, Opus 4.8: 128k, GPT-5.x: 128k, DeepSeek v4: 384k). Anthropic verified via Models API that exceeding the model's max output is a hard validation error (HTTP 400). A single global constant inevitably breaks one provider.
- **Why 16384?** Comfortably above what merge_cycle ever needs (the successful cycle used 3854), comfortably under the smallest currently-supported model output (Haiku 64k). DeepSeek users can raise it via env if they have a reason.
- **Env:** `MYMCP_RECORDER_LLM_MAX_TOKENS=16384`.

### 3.2 Per-section updates instead of full-overview rewrite

- **Why:** Output volume was the dominant cost and the dominant failure cause. A typical event affects 1–2 sections; rewriting the other 6 each cycle is pure waste.
- **Schema (new):**
  ```json
  {
    "new_changelog_lines": ["YYYY-MM-DD HH:MM | tool | effect"],
    "section_updates": { "Section Name": "<full new body>" }
  }
  ```
  Sections omitted are preserved unchanged. Sections present are replaced wholesale. New section names get appended at the end.
- **Python owns the header.** `# Server Overview` + `_Last updated: ..._` + `_Hostname: ... | OS: ..._` are rebuilt in `merge_cycle._build_header()` so the LLM never has to spend tokens on metadata and timestamps stay accurate.
- **Helpers in `overview.py`:** `parse_sections(text) → (header, [(name, body)])` and `apply_section_updates(current, *, header, section_updates) → str`. Both pure functions, fully unit-tested.

### 3.3 Early-fail on empty / `max_tokens` responses

- **Why:** Parsing half-JSON wastes log space and produces misleading `"Unterminated string"` errors. The protocol already exposes the signal (`LLMResponse.stop_reason`).
- **Behaviour:** `merge_cycle.run_once()` raises a clear `ValueError` before reaching `_parse_response()` when:
  - `resp.text.strip() == ""` — empty response
  - `resp.stop_reason == "max_tokens"` — truncated, with a hint to raise `MYMCP_RECORDER_LLM_MAX_TOKENS`

### 3.4 Structured output for both providers via `json_schema`

The parser is a load-bearing assumption; both providers must produce valid JSON. Rather than a generic `json_mode: bool`, the call signature takes an explicit schema, which lets each adapter pick its strongest enforcement mechanism:

- **New protocol parameter:** `LLMClient.call(..., json_schema: dict | None = None)`. When set, the adapter must coerce the model to emit JSON conforming to `json_schema`. The result lands in `LLMResponse.tool_uses[0].input` for tool-use providers and in `LLMResponse.text` for response-format providers; merge_cycle handles both.
- **OpenAI / DeepSeek:** Pass through as `response_format={"type":"json_schema","json_schema":{"name":"merge_output","schema":json_schema,"strict":true}}`. This is OpenAI's Structured Outputs feature; DeepSeek's OpenAI-compat endpoint supports the `json_object` variant — if `strict` json_schema isn't accepted by the provider, fall back to `{"type":"json_object"}`. Detect via TypeError on the SDK call.
- **Anthropic:** Inject a synthetic tool `{"name":"emit_merge_output","description":"...","input_schema":json_schema}` and set `tool_choice={"type":"tool","name":"emit_merge_output"}`. Claude is then required to call this tool with conforming input. The adapter returns `LLMResponse(text="", tool_uses=[ToolUse(name="emit_merge_output", input=parsed_dict)], ...)`.
- **Backward-compat:** When `json_schema is None`, both adapters behave exactly as today. Bootstrap (which outputs markdown) continues to use the unconstrained path.

### 3.5 Python owns the `Recent Changes` section

- **Why:** `Recent Changes` is the only section that mechanically changes every cycle (because the changelog grew). Forcing the LLM to regenerate it each cycle is the largest avoidable output. Python already has the data (`OverviewStore.read_changelog_tail(10)`) and the LLM gets nothing right that Python doesn't.
- **Behaviour:** After `apply_section_updates()` folds in the LLM's `section_updates`, merge_cycle overwrites the `Recent Changes` body with `render_recent_changes(combined_tail)` — the 10 newest changelog lines, newest first, plus the `_Full changelog: changelog.md (use read_file)_` footer. Any `Recent Changes` value the LLM included in `section_updates` is discarded.
- **Atomicity:** The combined tail is computed from `existing_tail + new_changelog_lines` before writing anything, so the rendered section reflects the state that *will* exist after the cycle commits.
- **Prompt:** The merge system prompt explicitly tells the LLM not to include `Recent Changes` in `section_updates`. If it does anyway, Python silently drops it.

### 3.6 Circuit breaker, restart-only recovery

- **Why not skip-on-failure:** Silently dropping events violates the audit contract that motivated the recorder.
- **Why not auto-reset:** If 5 cycles in a row fail, something is wrong (model outage, prompt-too-large, output format drift). An operator should see it.
- **Behaviour:** `_consecutive_failures` counter, threshold `MYMCP_RECORDER_CIRCUIT_BREAKER_THRESHOLD=5`. Once tripped, supervisor still ticks (so shutdown remains responsive) but skips `merge_cycle.run_once()` entirely. Recovery: restart the service.
- **No admin reset endpoint.** Intentional: the recovery path is "fix root cause + restart", not "click a button".

### 3.7 Circuit-breaker observability

- **Why:** Without a metric, the only way to know the recorder is paused is to call `server_overview` and read the banner. That's a human-in-the-loop discovery, unsuited for an automated alert path.
- **What:** New OpenTelemetry observable gauge `mymcp_recorder_circuit_open` (value `0` or `1`), registered in `mymcp.observability.instruments` with a callback that reads `RecorderSupervisor.circuit_open` at scrape time.
- **Wiring:** The gauge callback is installed in `mymcp.recorder.wiring.build_supervisor()` after the supervisor is constructed, so the gauge has access to its state. If the recorder is disabled, the gauge is never registered (so Prometheus simply doesn't see the metric).
- **Operator path:** Out of scope to add a Prometheus alert rule file (the repo doesn't ship one today). The spec recommends an alert of the form `mymcp_recorder_circuit_open == 1 for 1m`, which downstream ops can add to their Prometheus config.

### 3.8 Banner honesty

- **Before:** `"⚠️ overview is 0 minutes stale: ..."` even when nothing was stale.
- **After:** Three distinct messages, prioritised:
  - `⛔ recorder paused after repeated merge failures; restart service to retry. Last error: ...` (circuit open)
  - `⚠️ overview is N minutes stale: ...` (actually stale, i.e. >2× merge interval)
  - `⚠️ last merge cycle failed: ...` (recent cycle errored but not yet stale)

## 4. Configuration Surface (new env vars)

| Variable | Default | Purpose |
|---|---|---|
| `MYMCP_RECORDER_LLM_MAX_TOKENS` | `16384` | Per-call output ceiling. Must stay ≤ chosen model's max output. |
| `MYMCP_RECORDER_CIRCUIT_BREAKER_THRESHOLD` | `5` | Consecutive merge failures before pausing. `0` disables. |

## 5. Migration / Deployment

- **No breaking changes to overview format** (sections are still `## H2` blocks).
- **The stuck GCP cursor self-heals on upgrade.** Larger token budget + section updates + JSON mode → next merge cycle should succeed → cursor advances past the 758 backlog naturally over the next few cycles.
- **No manual cursor.json edit required.**
- **Existing audit.log entries** parse fine; no schema change there.

## 6. Incidental Repairs

While we're in the area, fix one pre-existing issue that surfaces during this work:

- **`openai_client.py:29` `[unused-ignore]` mypy error.** The `# type: ignore[import-not-found]` is unused when the openai SDK is installed in the dev environment, but required when the `[recorder-openai]` extra isn't installed. Fix by widening to `# type: ignore[import-not-found, unused-ignore]` so the ignore is itself conditional.

## 7. Known Limitations / Follow-ups (still deferred)

- **Bootstrap still rewrites the full overview.** It's a one-off; the new `max_tokens=16384` default makes it safe in practice. If bootstrap output ever approaches the ceiling on a complex host, port the section-update protocol there too.
- **Section-name normalisation.** "TL;DR" vs "TLDR" would create duplicate sections. Not observed in practice; mention here so the next contributor sees it.
- **Per-event prompt blowup.** A single `bash_execute` with very large output still bloats the prompt. Audit-side truncation (`audit_output_bash_head/tail_bytes`) caps this at the source.
- **Prometheus alert rule file.** Repo currently ships dashboards but no alert YAML. Adding the infra is a separate ops PR; this spec documents the recommended alert expression for downstream.
