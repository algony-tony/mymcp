# Recorder LLM Clients Without Vendor SDKs

**Status:** Draft (awaiting review)
**Date:** 2026-07-04
**Owner:** algony-tony

## Problem

On a low-memory VPS the deployed mymcp process weighs ~43 MB RSS plus ~72 MB
swapped out. Measured on that machine (pipx venv, Python 3.14):

| Import set                        | Peak RSS |
| --------------------------------- | -------- |
| bare interpreter                  | 8 MB     |
| core stack (mcp, fastapi, uvicorn, httpx, otel) | 61 MB |
| core stack **+ openai SDK**       | 74 MB    |

The openai SDK costs **~13 MB of marginal RSS (~30% of the running
footprint)** while the recorder uses exactly one method on it:
`chat.completions.create`, non-streaming. The anthropic SDK adapter is
equally shallow: one `messages.create` call. Both SDKs are pulled in solely
to issue a single JSON POST per merge cycle — something `httpx`, already a
core dependency, does natively.

## Goals & Non-Goals

**Goals**

1. Replace both SDK adapters with direct-HTTP clients built on `httpx`,
   implementing the existing `LLMClient` protocol (`recorder/llm/base.py`,
   unchanged).
2. Zero configuration changes: all `MYMCP_RECORDER_*` env vars and the
   provider values `anthropic` / `openai` keep working as-is.
3. Recorder works with the base install — no extras needed. The
   `recorder*` extras remain defined (empty) so existing install commands
   don't break.
4. Preserve per-provider structured-output behavior:
   - OpenAI-compatible: strict `json_schema` response_format first, fall
     back to `json_object` on HTTP 400 (DeepSeek et al.).
   - Anthropic: forced `tool_choice` on an injected `emit_merge_output`
     tool.
5. Metrics semantics unchanged (`recorder_llm_calls_total`,
   `recorder_llm_tokens_total`, cycle reasons).

**Non-Goals**

- Streaming, vision, prompt caching, batch — out of scope, as before.
- Client-level automatic retries. The SDKs retried transient failures
  (429/5xx) twice by default; the direct clients do **not** retry.
  Recorder resilience already lives at the cycle level (circuit breaker,
  event-driven retry), which is the right layer for a background task.
  This is an accepted behavior change.
- A fallback/escape-hatch to the SDK adapters. They are deleted outright.
- The Go rewrite (see final section) — separate effort, separate spec.

## Design

### New modules

**`recorder/llm/http_common.py`** — one shared helper:

- `post_json(client, url, headers, payload, timeout) -> dict` — POST,
  `raise_for_status()`, return parsed JSON body.
- Timeout: `httpx.Timeout(connect=5, read=600, write=30, pool=5)`. Merge
  calls can legitimately take minutes on slow providers; the read timeout
  matches the SDKs' generous defaults rather than httpx's 5 s default.
- No custom exception types. `httpx.HTTPStatusError`, `httpx.ConnectError`
  etc. propagate as-is; `merge_cycle.py` already catches broad `Exception`
  at the LLM boundary (merge_cycle.py:140) and classifies it as
  `http_error` / `llm_error`.

**`recorder/llm/openai_compat.py`** — `OpenAICompatClient`:

- POST `{base_url or "https://api.openai.com/v1"}/chat/completions` with
  `Authorization: Bearer {api_key}`.
- Message/tool translation copied from the current adapter (same
  `_to_sdk_messages` / `_to_sdk_tool` shapes, now plain dicts end to end).
- `json_schema` handling: send strict
  `response_format={"type": "json_schema", ...}` first; on
  `HTTPStatusError` with status 400, retry once with
  `{"type": "json_object"}`. This mirrors the current
  `except (TypeError, BadRequestError)` logic.
- Response mapping identical: finish_reason map
  (`stop→end_turn`, `tool_calls→tool_use`, `length→max_tokens`),
  tool_call arguments parsed with `json.loads`, malformed arguments → `{}`.
- `DEFAULT_MODEL = "gpt-4o"` (unchanged).

**`recorder/llm/anthropic_http.py`** — `AnthropicHTTPClient`:

- POST `{base_url or "https://api.anthropic.com"}/v1/messages` with headers
  `x-api-key: {api_key}`, `anthropic-version: 2023-06-01`.
- Message block translation copied from the current adapter (tool_result /
  tool_use / plain-text branches).
- `json_schema` handling: inject the `emit_merge_output` tool and force it
  via `tool_choice`, exactly as today.
- Response mapping: concatenate `text` blocks, collect `tool_use` blocks,
  pass through `stop_reason` and usage.
- `DEFAULT_MODEL = "claude-sonnet-4-6"` (unchanged).

Both clients own an `httpx.AsyncClient` created in `__init__` and expose
`async def aclose()` for tests. In production the client lives for the
process lifetime — same as the SDK clients today, which are never
explicitly closed either. No `wiring.py` changes.

### Changes

- **`recorder/llm/factory.py`** — import the two new classes instead of the
  SDK adapters. Provider selection logic, API-key resolution
  (`MYMCP_RECORDER_LLM_API_KEY` → `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`),
  and error messages unchanged.
- **`pyproject.toml`** — `recorder-anthropic`, `recorder-openai`, and
  `recorder` extras become empty lists with a comment explaining they are
  kept for install-command compatibility. Regenerate
  `requirements-dev.txt`.
- **Docs** — CLAUDE.md and README lose the "install with `[recorder]`"
  caveat; recorder is enabled by env var alone.

### Deletions

- `recorder/llm/openai_client.py`
- `recorder/llm/anthropic_client.py`
- Their SDK-mocking tests.

## Error Handling

| Failure                          | Behavior                                            |
| -------------------------------- | --------------------------------------------------- |
| Connection refused / DNS / TLS   | httpx exception → cycle records `llm_error`, metric `http_error` |
| Non-2xx (auth, quota, 5xx)       | `HTTPStatusError` → same path                        |
| HTTP 400 on strict json_schema (openai-compat only) | one retry with `json_object`; a second 400 propagates |
| Read timeout                     | `httpx.ReadTimeout` → same `llm_error` path          |
| Malformed JSON body in 2xx       | `json.JSONDecodeError` → same path                   |

No behavior change for the merge cycle, circuit breaker, or metrics — the
exception boundary and classification stay where they are.

## Testing

- Rewrite the two adapter test modules against `httpx.MockTransport`
  (no network, no SDK stubs):
  - request payload shape per branch (plain text, tool_uses, tool_results,
    tools, json_schema strict + fallback / forced tool_choice)
  - response mapping (text, tool_calls with malformed arguments,
    finish_reason map, usage)
  - HTTP 400 → json_object fallback fires exactly once (openai-compat)
  - non-2xx and timeout propagate
- Factory tests: unchanged provider/API-key cases still pass; assert the
  new client types are returned.
- Existing merge-cycle and bootstrap tests are untouched — they stub
  `LLMClient` at the protocol level.

## Expected Outcome

- ucloud instance RSS: ~43 MB → **~30 MB**; swap pressure drops
  proportionally.
- Install size shrinks (openai SDK + jiter + distro no longer on disk).
- Two fewer third-party dependency trees to track for CVEs and Dependabot
  churn.

## Phase 2 (Separate Effort): Go Rewrite Feasibility

Recorded here as a conclusion, not a design.

- **Motivation**: the Python floor is the interpreter + core stack
  (~61 MB peak import, ~35-40 MB steady RSS after this spec lands). The
  stated long-term target — 10-20 MB RSS on cheap VPSes — is unreachable
  in Python.
- **Verdict**: a full-server rewrite in **Go** is feasible and is the only
  rewrite shape worth doing. Rewriting individual modules while keeping
  the Python server saves almost nothing (the interpreter and FastAPI/mcp
  stack dominate).
- **Go over Rust**: official `modelcontextprotocol/go-sdk` exists; static
  single-binary distribution; goroutines map cleanly onto the
  subprocess-tracking model in `tools/bash.py`; expected 10-15 MB RSS.
  Rust would save a further ~5 MB at several times the development and
  maintenance cost — not worth it for this project.
- **Scope when it happens**: core only (6 tools, auth, audit, metrics,
  file transfer). The recorder stays a Python sidecar — LLM/prompt logic
  gains nothing from Go, and after this spec its footprint is just the
  core-Python stack it already shares.
- **Trigger**: revisit when there is a concrete plan to deploy to more
  low-spec machines. Until then, this spec's ~30 MB is acceptable for the
  single ucloud instance.
