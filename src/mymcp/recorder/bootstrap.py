"""Bootstrap agent loop: LLM probes the host and emits the initial overview.

The Bootstrapper runs an LLM agent loop using bash_probe and read_file_probe
as tools. It stops when the LLM emits end_turn (final overview), runs out of
iterations, or exceeds the token budget. Concurrent run_once() calls coalesce
via an asyncio.Lock so two callers awaiting the same bootstrap each receive
the same result.
"""

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from mymcp.observability import instruments
from mymcp.recorder.llm.base import (
    LLMClient,
    Message,
    ToolResult,
)
from mymcp.recorder.overview import OverviewStore
from mymcp.recorder.probes import (
    BASH_PROBE_TOOL,
    READ_FILE_PROBE_TOOL,
    run_bash_probe,
    run_read_file_probe,
)

log = logging.getLogger("mymcp.recorder")


BOOTSTRAP_SYSTEM_PROMPT = """You are building an initial server overview map for a Linux host.

Probe systematically using the tools provided:
- OS / distro identification
- Running services (prefer systemd queries; fall back to alternatives if not present)
- Deployed applications (look in /opt, /srv, /var/www, common runtime paths)
- Listening network ports
- Important data directories
- Unusual configurations worth flagging in "Known Quirks"

Don't enumerate exhaustively — capture the load-bearing facts only.
Output the final overview as a single Markdown document matching this skeleton:

# Server Overview
_Last updated: <now> by mymcp-recorder (bootstrap)_
_Hostname: <h> | OS: <os>_

## TL;DR
<2–3 sentences>

## Installed Services
- ...

## Deployed Applications
- ...

## Network
- ...

## Data Locations
- ...

## Recent Changes
<the recorder will add a line for the bootstrap event automatically>

## Known Quirks
- ...

When the overview is complete, respond with the final markdown only
(no tool_use, no code fences)."""


class BootstrapState(StrEnum):
    IDLE = "idle"
    SCHEDULED = "scheduled"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass
class BootstrapResult:
    state: BootstrapState
    run_id: str
    iterations: int = 0
    tokens_used: int = 0
    error: str | None = None


class Bootstrapper:
    """Runs the bootstrap agent loop. Coalesces concurrent calls via an asyncio.Lock."""

    def __init__(
        self,
        *,
        client: LLMClient,
        store: OverviewStore,
        max_iterations: int = 200,
        token_budget: int = 10_000_000,
        probe_timeout_sec: int = 30,
    ):
        self._client = client
        self._store = store
        self._max_iterations = max_iterations
        self._token_budget = token_budget
        self._probe_timeout = probe_timeout_sec
        self._lock = asyncio.Lock()
        self._state = BootstrapState.IDLE
        self._last_result: BootstrapResult | None = None

    @property
    def state(self) -> BootstrapState:
        return self._state

    @property
    def last_result(self) -> BootstrapResult | None:
        return self._last_result

    async def run_once(self) -> BootstrapResult:
        # Coalesce: if a run is already in flight, wait for it and return its result.
        if self._lock.locked():
            async with self._lock:
                if self._last_result is not None:
                    return self._last_result
        async with self._lock:
            # Could have been completed by a coalesced caller between unlock and re-lock
            if self._last_result is not None and self._state == BootstrapState.SUCCEEDED:
                # If a previous run already succeeded, re-running explicitly is allowed;
                # but coalesced followers should see the most recent successful result.
                # Recompute only if state was reset elsewhere.
                pass
            return await self._run_locked()

    async def _run_locked(self) -> BootstrapResult:
        run_id = uuid.uuid4().hex[:8]
        self._state = BootstrapState.RUNNING
        log.info("recorder.bootstrap.start", extra={"run_id": run_id})

        tools = [BASH_PROBE_TOOL, READ_FILE_PROBE_TOOL]
        messages: list[Message] = [
            Message(
                role="user",
                content=(
                    "Begin probing this Linux host. When done, output the final overview "
                    "as plain markdown (no tool_use, no code fences)."
                ),
            )
        ]
        tokens = 0
        iterations = 0
        try:
            while iterations < self._max_iterations:
                iterations += 1
                resp = await self._client.call(
                    system=BOOTSTRAP_SYSTEM_PROMPT,
                    messages=messages,
                    tools=tools,
                    max_tokens=4096,
                )
                instruments.recorder_llm_calls.add(1, {"phase": "bootstrap", "result": "success"})
                instruments.recorder_llm_tokens.add(
                    resp.usage.input_tokens, {"phase": "bootstrap", "direction": "input"}
                )
                instruments.recorder_llm_tokens.add(
                    resp.usage.output_tokens, {"phase": "bootstrap", "direction": "output"}
                )
                tokens += resp.usage_total
                if tokens > self._token_budget:
                    raise RuntimeError(
                        f"bootstrap token budget exceeded ({tokens} > {self._token_budget})"
                    )
                if resp.is_end_turn:
                    overview_md = resp.text.strip()
                    if not overview_md:
                        raise RuntimeError("LLM ended turn with empty overview")
                    self._store.write_overview(overview_md)
                    ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
                    self._store.append_changelog(
                        [f"{ts} | bootstrap | initial overview generated (run {run_id})"]
                    )
                    self._state = BootstrapState.SUCCEEDED
                    instruments.recorder_bootstrap_runs.add(1, {"result": "success"})
                    result = BootstrapResult(
                        state=BootstrapState.SUCCEEDED,
                        run_id=run_id,
                        iterations=iterations,
                        tokens_used=tokens,
                    )
                    self._last_result = result
                    log.info(
                        "recorder.bootstrap.success",
                        extra={"run_id": run_id, "iterations": iterations, "tokens": tokens},
                    )
                    return result
                # Echo assistant turn back into history (preserves tool_use ids)
                messages.append(
                    Message(
                        role="assistant",
                        content=resp.text,
                        tool_uses=list(resp.tool_uses),
                    )
                )
                tool_results: list[ToolResult] = []
                for tu in resp.tool_uses:
                    try:
                        if tu.name == "bash_probe":
                            out = await run_bash_probe(tu.input, timeout_sec=self._probe_timeout)
                            tool_results.append(
                                ToolResult(tool_use_id=tu.id, content=json.dumps(out))
                            )
                        elif tu.name == "read_file_probe":
                            out = await run_read_file_probe(tu.input)
                            tool_results.append(
                                ToolResult(tool_use_id=tu.id, content=json.dumps(out))
                            )
                        else:
                            tool_results.append(
                                ToolResult(
                                    tool_use_id=tu.id,
                                    content=f"unknown tool: {tu.name}",
                                    is_error=True,
                                )
                            )
                    except Exception as e:  # noqa: BLE001
                        tool_results.append(
                            ToolResult(tool_use_id=tu.id, content=str(e), is_error=True)
                        )
                messages.append(Message(role="user", content="", tool_results=tool_results))
            raise RuntimeError(f"bootstrap exceeded max iterations ({self._max_iterations})")
        except Exception as e:  # noqa: BLE001
            self._state = BootstrapState.FAILED
            instruments.recorder_bootstrap_runs.add(1, {"result": "failure"})
            result = BootstrapResult(
                state=BootstrapState.FAILED,
                run_id=run_id,
                iterations=iterations,
                tokens_used=tokens,
                error=str(e),
            )
            self._last_result = result
            log.warning(
                "recorder.bootstrap.failed",
                extra={"run_id": run_id, "error": str(e)},
            )
            return result
