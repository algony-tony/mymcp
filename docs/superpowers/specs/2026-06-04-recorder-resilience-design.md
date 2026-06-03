# Recorder Resilience Fix Design

**Date:** 2026-06-04
**Status:** Draft (PR #46)
**Owner:** algony-tony
**Topic:** Hardening the llm-recorder merge cycle against LLM truncation, empty responses, and permanently-stuck cursors.

Companion to [`2026-05-29-llm-recorder-design.md`](2026-05-29-llm-recorder-design.md); this document describes the production incident that motivated the fix and the design choices made in response.

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

**Non-Goals**

- Automatic recovery from circuit-open state. Restart of the service is the only reset path (intentional — keeps the operator in the loop).
- Auto-skipping past poisoned events. Skipping silently violates the recorder's at-least-once contract.
- Anthropic JSON-mode equivalence. Deferred to a follow-up that uses `tool_use` with a JSON schema.
- Bootstrap protocol changes. Bootstrap is a one-off; only the `max_tokens` knob applies.

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

### 3.4 OpenAI / DeepSeek JSON mode; Anthropic deferred

- **Why:** Even with section updates, the parser is a load-bearing assumption. OpenAI-compatible APIs offer `response_format={"type":"json_object"}` which forces valid JSON output. DeepSeek supports this.
- **Anthropic:** No `response_format` equivalent. The robust path is `tool_use` with a `json_schema`, which is a larger refactor (the merge prompt would need to become a tool invocation, response parsing changes). Deferred. The `json_mode` parameter is accepted as a no-op for interface uniformity.
- **Interface:** `LLMClient.call(..., json_mode: bool = False)` added to the Protocol.

### 3.5 Circuit breaker, restart-only recovery

- **Why not skip-on-failure:** Silently dropping events violates the audit contract that motivated the recorder.
- **Why not auto-reset:** If 5 cycles in a row fail, something is wrong (model outage, prompt-too-large, output format drift). An operator should see it.
- **Behaviour:** `_consecutive_failures` counter, threshold `MYMCP_RECORDER_CIRCUIT_BREAKER_THRESHOLD=5`. Once tripped, supervisor still ticks (so shutdown remains responsive) but skips `merge_cycle.run_once()` entirely. Recovery: restart the service.
- **No admin reset endpoint.** Intentional: the recovery path is "fix root cause + restart", not "click a button".

### 3.6 Banner honesty

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

## 6. Known Limitations / Follow-ups

- **Anthropic JSON mode.** Worth picking up if/when someone runs the recorder with Claude in production. Tracked as `recorder.llm.anthropic_client` TODO.
- **Bootstrap still rewrites the full overview.** It's a one-off; the new `max_tokens=16384` default makes it safe in practice. If bootstrap output ever approaches the ceiling on a complex host, port the section-update protocol there too.
- **Section-name normalisation.** "TL;DR" vs "TLDR" would create duplicate sections. Not observed in practice; mention here so the next contributor sees it.
- **Per-event prompt blowup.** A single `bash_execute` with very large output still bloats the prompt. Audit-side truncation (`audit_output_bash_head/tail_bytes`) caps this at the source.
- **No metric for `circuit_open`.** Should be added to `instruments.py` so Grafana can alert on it. Out of scope here.
- **`Recent Changes` section regeneration.** Python could own this directly (it has the changelog tail), eliminating the LLM's only mandatory section rewrite. Future optimisation.
