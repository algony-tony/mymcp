"""Periodic merge cycle: read new events, ask LLM to fold them into the overview."""

import json
import logging
import platform
import socket
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from mymcp.observability import instruments
from mymcp.observability.tracing import get_tracer
from mymcp.recorder.events import AuditEvent, EventTailer
from mymcp.recorder.llm.base import LLMClient, Message
from mymcp.recorder.overview import (
    OverviewStore,
    apply_section_updates,
    render_recent_changes,
)
from mymcp.recorder.prompts import MERGE_SYSTEM_PROMPT, merge_user_prompt

_tracer = get_tracer(__name__)

log = logging.getLogger("mymcp.recorder")

# Floor for adaptive batch shrinking. Below this the per-call overhead
# dominates and a backlog would take too many cycles to drain.
_MIN_BATCH = 5

# JSON Schema for the merge output. Adapters use it for structured-output
# enforcement (OpenAI Structured Outputs; Anthropic forced tool_use).
MERGE_OUTPUT_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["new_changelog_lines", "section_updates"],
    "properties": {
        "new_changelog_lines": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "One-line entries to append to changelog.md. Format:"
                ' "YYYY-MM-DD HH:MM | <tool> | <effect, <=120 chars>"'
            ),
        },
        "section_updates": {
            "type": "object",
            "additionalProperties": {"type": "string"},
            "description": (
                "Section-name -> full new content. Sections omitted are"
                " preserved unchanged. Do not include 'Recent Changes'."
            ),
        },
    },
}


@dataclass
class MergeResult:
    events_consumed: int
    skipped_reason: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0


class _MergeFailure(Exception):
    """Internal carrier for the failure reason → metric label."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


class MergeCycle:
    """Drains pending events and asks an LLM to update the overview + changelog.

    Atomic semantics: the overview is rewritten via OverviewStore.write_overview
    (tmp + os.replace); changelog lines are appended; cursor is committed last.
    If the LLM returns a bad response or any step fails, the cursor stays put
    so the same events will be re-tried next cycle (at-least-once).
    """

    def __init__(
        self,
        *,
        client: LLMClient,
        tailer: EventTailer,
        store: OverviewStore,
        max_events_per_cycle: int = 50,
        require_bootstrap: bool = False,
        max_tokens: int = 16384,
    ):
        self._client = client
        self._tailer = tailer
        self._store = store
        self._max = max_events_per_cycle
        self._require_bootstrap = require_bootstrap
        self._max_tokens = max_tokens
        # Effective batch size for the next cycle. Halved on each max_tokens
        # failure and restored on success — see run_once. Reasoning models bill
        # thinking against the output budget, so a large backlog can truncate
        # every cycle; re-reading the same batch would trip the circuit breaker
        # (threshold 5) before the backlog ever drained.
        self._adaptive_max = max_events_per_cycle

    def _record_outcome(self, reason: str, *, start: float) -> None:
        """Increment cycle counter AND record duration histogram for one outcome."""
        labels = {"reason": reason}
        instruments.recorder_merge_cycles.add(1, labels)
        instruments.recorder_merge_duration.record(time.perf_counter() - start, labels)

    async def run_once(self) -> MergeResult:
        start = time.perf_counter()
        with _tracer.start_as_current_span("recorder.merge_cycle") as span:
            if self._require_bootstrap and self._store.read_overview() is None:
                span.set_attribute("events.in", 0)
                self._record_outcome("bootstrap_required", start=start)
                return MergeResult(events_consumed=0, skipped_reason="bootstrap_required")

            events: list[AuditEvent] = []
            for ev in self._tailer.read_new():
                events.append(ev)
                if len(events) >= self._adaptive_max:
                    break
            span.set_attribute("events.in", len(events))
            if not events:
                self._record_outcome("no_events", start=start)
                return MergeResult(events_consumed=0, skipped_reason="no_events")

            current_overview = self._store.read_overview()
            prompt = merge_user_prompt(
                current_overview=current_overview,
                recent_changelog=self._store.read_changelog_tail(10),
                events_json=json.dumps([self._event_to_dict(e) for e in events], indent=2),
                metadata={
                    "hostname": socket.gethostname(),
                    "os": platform.platform(),
                    "now": datetime.now(UTC).isoformat(),
                },
            )

            # LLM HTTP boundary — http_error covers connection/auth/quota failures.
            try:
                resp = await self._client.call(
                    system=MERGE_SYSTEM_PROMPT,
                    messages=[Message(role="user", content=prompt)],
                    max_tokens=self._max_tokens,
                    json_schema=MERGE_OUTPUT_SCHEMA,
                )
            except Exception:
                instruments.recorder_llm_calls.add(1, {"phase": "merge", "result": "http_error"})
                self._record_outcome("llm_error", start=start)
                self._tailer.rollback()
                raise

            instruments.recorder_llm_calls.add(1, {"phase": "merge", "result": "success"})
            instruments.recorder_llm_tokens.add(
                resp.usage.input_tokens, {"phase": "merge", "direction": "input"}
            )
            instruments.recorder_llm_tokens.add(
                resp.usage.output_tokens, {"phase": "merge", "direction": "output"}
            )
            span.set_attribute("tokens.in", resp.usage.input_tokens)
            span.set_attribute("tokens.out", resp.usage.output_tokens)

            # Response-quality boundary — each failure mode gets its own reason.
            try:
                # max_tokens takes priority over JSON parse: a truncated string
                # is also unparseable, but the actionable signal is "raise the
                # token cap", not "the LLM emitted garbage".
                if resp.stop_reason == "max_tokens":
                    raise _MergeFailure(
                        "max_tokens",
                        f"LLM hit max_tokens ({self._max_tokens}); response"
                        " truncated. Raise MYMCP_RECORDER_LLM_MAX_TOKENS (must"
                        " stay under your model's output limit).",
                    )
                if not resp.tool_uses and not (resp.text or "").strip():
                    raise _MergeFailure("empty", "LLM returned empty response")
                try:
                    parsed = self._extract_payload(resp)
                except ValueError as e:
                    raise _MergeFailure("unparseable", str(e)) from e
                try:
                    self._validate_payload(parsed)
                except ValueError as e:
                    raise _MergeFailure("schema_invalid", str(e)) from e
            except _MergeFailure as f:
                if f.reason == "max_tokens":
                    self._adaptive_max = max(_MIN_BATCH, self._adaptive_max // 2)
                    span.set_attribute("events.adaptive_max", self._adaptive_max)
                self._record_outcome(f.reason, start=start)
                self._tailer.rollback()
                raise ValueError(f.message) from f.__cause__

            section_updates = dict(parsed.get("section_updates") or {})
            # Python owns Recent Changes — drop whatever the LLM put there.
            section_updates.pop("Recent Changes", None)
            changelog_lines = list(parsed.get("new_changelog_lines") or [])

            existing_tail = self._store.read_changelog_tail(10)
            effective_tail = existing_tail + changelog_lines
            section_updates["Recent Changes"] = render_recent_changes(effective_tail)

            new_overview = apply_section_updates(
                current_overview or "",
                header=self._build_header(),
                section_updates=section_updates,
            )

            # Atomic: write overview first, then append changelog, then commit cursor.
            try:
                self._store.write_overview(new_overview)
                self._store.append_changelog(changelog_lines)
            except Exception:
                self._record_outcome("apply_error", start=start)
                self._tailer.rollback()
                raise

            self._adaptive_max = self._max
            self._tailer.commit()
            self._record_outcome("success", start=start)
            log.info(
                "recorder.merge_cycle.done",
                extra={
                    "events": len(events),
                    "tokens_in": resp.usage.input_tokens,
                    "tokens_out": resp.usage.output_tokens,
                    "sections_updated": len(section_updates),
                    "changelog_lines": len(changelog_lines),
                },
            )
            return MergeResult(
                events_consumed=len(events),
                tokens_in=resp.usage.input_tokens,
                tokens_out=resp.usage.output_tokens,
            )

    @staticmethod
    def _extract_payload(resp) -> dict:
        # Anthropic structured-output path: payload arrives as tool_uses[0].input.
        if resp.tool_uses:
            data = resp.tool_uses[0].input
            if not isinstance(data, dict):
                raise ValueError("tool_use input is not a JSON object")
            return data
        # OpenAI / fallback: parse text.
        text = resp.text or ""
        if not text.strip():
            raise ValueError("LLM returned empty response")
        return MergeCycle._parse_text_json(text)

    @staticmethod
    def _parse_text_json(text: str) -> dict:
        t = text.strip()
        if t.startswith("```"):
            t = t.split("\n", 1)[1] if "\n" in t else t
            if "```" in t:
                t = t.rsplit("```", 1)[0]
            t = t.strip()
        try:
            data = json.loads(t)
        except json.JSONDecodeError as e:
            log.warning("recorder.merge_cycle.unparseable", extra={"raw_head": text[:500]})
            raise ValueError(f"LLM returned unparseable JSON: {e}") from e
        if not isinstance(data, dict):
            raise ValueError("response is not a JSON object")
        return data

    @staticmethod
    def _validate_payload(data: dict) -> None:
        su = data.get("section_updates")
        if su is not None and not isinstance(su, dict):
            raise ValueError("section_updates must be an object")
        cl = data.get("new_changelog_lines")
        if cl is not None and not isinstance(cl, list):
            raise ValueError("new_changelog_lines must be a list")

    @staticmethod
    def _build_header() -> str:
        now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        return (
            "# Server Overview\n"
            f"_Last updated: {now}_\n"
            f"_Hostname: {socket.gethostname()} | OS: {platform.platform()}_"
        )

    @staticmethod
    def _event_to_dict(e: AuditEvent) -> dict:
        return {
            "ts": e.ts,
            "tool": e.tool,
            "params": e.params,
            "output": e.output,
        }
