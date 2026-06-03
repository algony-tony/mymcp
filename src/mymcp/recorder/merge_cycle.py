"""Periodic merge cycle: read new events, ask LLM to fold them into the overview."""

import json
import logging
import platform
import socket
from dataclasses import dataclass
from datetime import UTC, datetime

from mymcp.observability import instruments
from mymcp.observability.tracing import get_tracer
from mymcp.recorder.events import AuditEvent, EventTailer
from mymcp.recorder.llm.base import LLMClient, Message
from mymcp.recorder.overview import OverviewStore, apply_section_updates
from mymcp.recorder.prompts import MERGE_SYSTEM_PROMPT, merge_user_prompt

_tracer = get_tracer(__name__)

log = logging.getLogger("mymcp.recorder")


@dataclass
class MergeResult:
    events_consumed: int
    skipped_reason: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0


class MergeCycle:
    """Drains pending events and asks an LLM to update the overview + changelog.

    Atomic semantics: the overview is rewritten via OverviewStore.write_overview
    (tmp + os.replace); changelog lines are appended; cursor is committed last.
    If the LLM returns unparseable JSON or any step fails, the cursor stays put
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

    async def run_once(self) -> MergeResult:
        with _tracer.start_as_current_span("recorder.merge_cycle") as span:
            if self._require_bootstrap and self._store.read_overview() is None:
                span.set_attribute("events.in", 0)
                return MergeResult(events_consumed=0, skipped_reason="bootstrap_required")

            events: list[AuditEvent] = []
            for ev in self._tailer.read_new():
                events.append(ev)
                if len(events) >= self._max:
                    break
            span.set_attribute("events.in", len(events))
            if not events:
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
            try:
                resp = await self._client.call(
                    system=MERGE_SYSTEM_PROMPT,
                    messages=[Message(role="user", content=prompt)],
                    max_tokens=self._max_tokens,
                    json_mode=True,
                )
                instruments.recorder_llm_calls.add(1, {"phase": "merge", "result": "success"})
                instruments.recorder_llm_tokens.add(
                    resp.usage.input_tokens, {"phase": "merge", "direction": "input"}
                )
                instruments.recorder_llm_tokens.add(
                    resp.usage.output_tokens, {"phase": "merge", "direction": "output"}
                )
                span.set_attribute("tokens.in", resp.usage.input_tokens)
                span.set_attribute("tokens.out", resp.usage.output_tokens)
                # Early-fail on truncated / empty responses to avoid wasting
                # work parsing half-JSON we already know is broken.
                if resp.stop_reason == "max_tokens":
                    raise ValueError(
                        f"LLM hit max_tokens ({self._max_tokens}); response truncated."
                        " Raise MYMCP_RECORDER_LLM_MAX_TOKENS (must stay under your model's"
                        " output limit)."
                    )
                if not resp.text.strip():
                    raise ValueError("LLM returned empty response")
                parsed = self._parse_response(resp.text)
                section_updates = parsed.get("section_updates", {}) or {}
                changelog_lines = parsed.get("new_changelog_lines", []) or []
                new_overview = apply_section_updates(
                    current_overview or "",
                    header=self._build_header(),
                    section_updates=section_updates,
                )
                # Overview write first (atomic), then changelog, then cursor.
                self._store.write_overview(new_overview)
                self._store.append_changelog(changelog_lines)
            except Exception:
                instruments.recorder_merge_cycles.add(1, {"result": "failure"})
                self._tailer.rollback()
                raise
            self._tailer.commit()
            instruments.recorder_merge_cycles.add(1, {"result": "success"})
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
    def _build_header() -> str:
        # Python owns the metadata header so the LLM never has to spend
        # tokens on it and timestamps stay accurate.
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

    @staticmethod
    def _parse_response(text: str) -> dict:
        t = text.strip()
        # tolerate ```json ... ``` fencing
        if t.startswith("```"):
            # strip first fence line
            t = t.split("\n", 1)[1] if "\n" in t else t
            # strip trailing fence
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
        section_updates = data.get("section_updates")
        if section_updates is not None and not isinstance(section_updates, dict):
            raise ValueError("section_updates must be an object")
        changelog_lines = data.get("new_changelog_lines")
        if changelog_lines is not None and not isinstance(changelog_lines, list):
            raise ValueError("new_changelog_lines must be a list")
        return data
