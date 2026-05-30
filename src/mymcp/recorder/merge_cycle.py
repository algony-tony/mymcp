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
from mymcp.recorder.overview import OverviewStore
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
    ):
        self._client = client
        self._tailer = tailer
        self._store = store
        self._max = max_events_per_cycle
        self._require_bootstrap = require_bootstrap

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

            prompt = merge_user_prompt(
                current_overview=self._store.read_overview(),
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
                    max_tokens=4096,
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
                parsed = self._parse_response(resp.text)
                # Overview write first (atomic), then changelog, then cursor.
                self._store.write_overview(parsed["updated_overview_md"])
                self._store.append_changelog(parsed.get("new_changelog_lines", []))
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
                },
            )
            return MergeResult(
                events_consumed=len(events),
                tokens_in=resp.usage.input_tokens,
                tokens_out=resp.usage.output_tokens,
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
        if not isinstance(data, dict) or not isinstance(data.get("updated_overview_md"), str):
            raise ValueError("response missing updated_overview_md")
        return data
