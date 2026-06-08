# Architecture Refactor Wave Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Five tightly-coupled refactors that should ship together because each one's correctness depends on the others' contracts. (1) Remove `config.__getattr__` legacy attrs + push tool defaults to call time. (2) Introduce `ToolResult` dataclasses to kill the two-shape success protocol. (3) Introduce `ToolRegistry` + `ToolSpec` so the recorder self-registers `server_overview` instead of being hard-coded in `mcp_server.py`. (4) Move file/token sync I/O off the asyncio event loop. (5) Fix transfer audit (split `transfer_upload`/`transfer_download`, real `issuer_token_id`/`issuer_role`, add to `MUTATING_TOOLS`). Companion spec: `docs/superpowers/specs/2026-06-06-project-assessment.md` (P2 #12-15, #17).

**Architecture:** Phased rollout. Phase 1 (config) is preparatory — without it, defaults in function signatures fight every other refactor. Phase 2 (ToolResult) and Phase 3 (ToolRegistry) are interlocked: ToolSpec carries the handler that returns ToolResult, so they ship together. Phase 4 (async I/O) is mechanical — wrap blocking calls with `anyio.to_thread.run_sync`. Phase 5 (transfer) finally puts the audit/recorder fix in place once `MUTATING_TOOLS` lives on `ToolSpec` instead of a global set.

**Prerequisites:** Plan A (recorder redesign) and Plan B (quick wins) should merge before this. Plan B fixes `tool_definitions.py` content; Plan A stabilizes recorder behavior — both make this refactor mechanical instead of behavior-changing.

**Tech Stack:** Python 3.11+ • dataclasses • anyio • FastAPI • OpenTelemetry • pytest + anyio.

---

## Conventions

- All commands run from the repo root: `/home/zhu/repos/mymcp`.
- Branch: `feature/architecture-refactor` off `master` (after A + B merge).
- After every code task: `ruff format <files> && ruff check <files>`.
- Each task ends with a commit. Push at the end.
- mypy must stay clean — these refactors strengthen types; failures usually mean a real bug.

---

## Task 1: Branch + baseline

- [ ] **Step 1: Branch**

```bash
git checkout master && git pull --ff-only
git checkout -b feature/architecture-refactor
```

- [ ] **Step 2: Baseline**

Run: `.venv/bin/python -m pytest tests/ --benchmark-disable && .venv/bin/mypy src/mymcp && .venv/bin/ruff check .`

Expected: all green. Stop and fix any pre-existing failure before continuing.

---

# Phase 1: Config refactor

## Task 2: Find all import-time uses of `config.X` as function defaults

**Files:**
- (Survey only)

- [ ] **Step 1: Locate**

Run:
```bash
grep -n 'def .*= config\.' src/mymcp/tools/*.py src/mymcp/*.py
grep -n 'config\.[A-Z_]\+' src/mymcp/tools/*.py src/mymcp/*.py
```

Record findings inline as a checklist:

```
src/mymcp/tools/files.py:63  read_file(... limit: int = config.READ_FILE_DEFAULT_LIMIT)
src/mymcp/tools/files.py:??  (add other hits)
src/mymcp/tools/bash.py:88   run_bash_execute(... timeout: int = config.BASH_TIMEOUT_DEFAULT_SEC)
src/mymcp/tools/bash.py:??   (add other hits)
```

This list is the worklist for Tasks 3 and 4. Do not commit anything in this task.

---

## Task 3: Refactor `tools/files.py` — defaults via `get_settings()` at call time

**Files:**
- Modify: `src/mymcp/tools/files.py`
- Test: `tests/test_files.py` (verify monkeypatch.setenv now takes effect)

- [ ] **Step 1: Write failing test**

Append to `tests/test_files.py`:

```python
def test_read_file_default_limit_uses_current_settings(tmp_path, monkeypatch):
    """Changing the env var must affect the next call — no import-time capture."""
    from mymcp.config import reset_settings_cache

    target = tmp_path / "f.txt"
    target.write_text("\n".join(f"line{i}" for i in range(100)))

    monkeypatch.setenv("MYMCP_READ_FILE_DEFAULT_LIMIT", "10")
    reset_settings_cache()

    from mymcp.tools.files import read_file
    out = read_file(path=str(target))  # no limit kwarg — should use new default
    assert out["lines_read"] == 10
```

- [ ] **Step 2: Run, expect FAIL**

Run: `.venv/bin/python -m pytest tests/test_files.py -k default_limit_uses_current_settings -v --benchmark-disable`

Expected: FAIL — default is fixed at import time (likely 2000).

- [ ] **Step 3: Refactor**

In `src/mymcp/tools/files.py`, for `read_file`:

```python
from mymcp.config import get_settings

async def read_file(
    *,
    path: str,
    offset: int = 0,
    limit: int | None = None,
) -> dict:
    s = get_settings()
    effective_limit = limit if limit is not None else s.read_file_default_limit
    # ... clamp effective_limit against s.read_file_max_limit at this point too
    ...
```

Apply the same `param: int | None = None` + `get_settings()` pattern to every other tool function in this file that was reading `config.X` at the signature. Use the worklist from Task 2.

Remove top-level `from mymcp import config` if no longer needed.

- [ ] **Step 4: Run, expect PASS + no regressions**

Run: `.venv/bin/python -m pytest tests/test_files.py -v --benchmark-disable`

Expected: green.

- [ ] **Step 5: Commit**

```bash
git add src/mymcp/tools/files.py tests/test_files.py
git commit -m "refactor(tools/files): resolve config at call time, not import time

Function-default expressions like 'limit: int = config.X' captured
the value at import. reset_settings_cache + monkeypatch.setenv were
silently no-ops in many tests. Switch to None default + get_settings()
inside the function body — settings tests now correctly take effect."
```

---

## Task 4: Refactor `tools/bash.py` — same pattern

**Files:**
- Modify: `src/mymcp/tools/bash.py`
- Test: `tests/test_bash.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_bash.py`:

```python
@pytest.mark.anyio
async def test_bash_default_timeout_uses_current_settings(monkeypatch):
    from mymcp.config import reset_settings_cache
    monkeypatch.setenv("MYMCP_BASH_TIMEOUT_SEC", "1")
    reset_settings_cache()

    from mymcp.tools.bash import run_bash_execute
    # A sleep 5 with no explicit timeout should be killed at ~1s.
    result = await run_bash_execute(command="sleep 5")
    assert result["timed_out"] is True
```

- [ ] **Step 2: Run, expect FAIL**

Run: `.venv/bin/python -m pytest tests/test_bash.py -k default_timeout_uses_current_settings -v --benchmark-disable`

Expected: FAIL — default captured at import.

- [ ] **Step 3: Refactor**

Apply the same pattern as Task 3 to `run_bash_execute` and any other tool functions in this file:

```python
from mymcp.config import get_settings

async def run_bash_execute(
    *,
    command: str,
    timeout: int | None = None,
    working_dir: str | None = None,
) -> dict:
    s = get_settings()
    effective_timeout = timeout if timeout is not None else s.bash_timeout_sec
    effective_timeout = min(effective_timeout, s.bash_timeout_max_sec)
    ...
```

- [ ] **Step 4: Run, expect PASS**

Run: `.venv/bin/python -m pytest tests/test_bash.py -v --benchmark-disable`

Expected: green.

- [ ] **Step 5: Commit**

```bash
git add src/mymcp/tools/bash.py tests/test_bash.py
git commit -m "refactor(tools/bash): resolve config at call time"
```

---

## Task 5: Remove `config.__getattr__` legacy compatibility shim

**Files:**
- Modify: `src/mymcp/config.py`
- Test: full repo run

- [ ] **Step 1: Locate the shim**

Run: `grep -n '__getattr__\|legacy' src/mymcp/config.py`

Expected: the `__getattr__` function and a `legacy_attrs` map (or similar) appear around the end of the file.

- [ ] **Step 2: Find live consumers**

Run:
```bash
grep -rn 'config\.[A-Z_][A-Z_0-9_]*' src/ tests/ | grep -v '_get_legacy_attr\|legacy_attrs'
```

Expected: After Tasks 3 + 4, there should be no remaining consumers in `src/mymcp/tools/*.py`. Hits in tests are fine — fix them too if any rely on the legacy uppercase attributes.

For any remaining hits in `src/`, refactor that file with the same pattern (`get_settings()` at call time) before continuing.

- [ ] **Step 3: Delete the shim**

In `src/mymcp/config.py`, delete:
- The `__getattr__` function at module level
- Any `legacy_attrs` / `_LEGACY_ATTRS` mapping that supported it
- Any imports they pulled in but nothing else uses

- [ ] **Step 4: Full suite + mypy**

Run:
```bash
.venv/bin/python -m pytest tests/ --benchmark-disable
.venv/bin/mypy src/mymcp
```

Expected: green. If a test still references e.g. `config.READ_FILE_DEFAULT_LIMIT`, change it to `get_settings().read_file_default_limit`.

- [ ] **Step 5: Commit**

```bash
git add src/mymcp/config.py tests/
git commit -m "refactor(config): remove __getattr__ legacy attribute shim

After tools resolve config at call time, no consumer still reads the
uppercase legacy attributes. Drops ~50 lines of import-time magic
that fought reset_settings_cache and made test-time env overrides
silently no-op in subtle ways."
```

---

# Phase 2 + 3: ToolResult + ToolRegistry (interlocked)

These two phases are committed together because every ToolSpec.handler returns a ToolResult, so neither can be merged alone.

## Task 6: Define `ToolResult` base and per-tool subclasses

**Files:**
- Create: `src/mymcp/tools/result.py`
- Test: `tests/test_tool_result.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_tool_result.py`:

```python
from mymcp.tools.result import (
    BashResult,
    FileResult,
    GenericErrorResult,
    OverviewResult,
    ToolResult,
)


def test_bash_result_audit_payload_summarises_output():
    r = BashResult(
        exit_code=0,
        timed_out=False,
        stdout="hello\n" * 1000,
        stderr="",
    )
    p = r.audit_payload()
    # Must include sha256/head/tail/size — never the full body.
    assert "stdout_sha256" in p
    assert "stdout_head" in p
    assert "stdout_tail" in p
    assert "stdout_size" in p
    # And NEVER the full content
    assert "stdout" not in p
    # ok is derived from exit_code + timed_out
    assert r.ok is True


def test_bash_result_timeout_is_not_ok():
    r = BashResult(exit_code=-1, timed_out=True, stdout="", stderr="")
    assert r.ok is False
    p = r.audit_payload()
    assert p["timed_out"] is True


def test_file_result_includes_path_and_bytes_written():
    r = FileResult(ok=True, path="/tmp/x", bytes_written=42)
    p = r.audit_payload()
    assert p["path"] == "/tmp/x"
    assert p["bytes_written"] == 42


def test_generic_error_result_carries_code_and_message():
    r = GenericErrorResult(error_code="PermissionDenied", error_message="no")
    assert r.ok is False
    assert r.to_mcp() == {"success": False, "error": "PermissionDenied", "message": "no"}
```

- [ ] **Step 2: Run, expect ImportError**

Run: `.venv/bin/python -m pytest tests/test_tool_result.py -v --benchmark-disable`

Expected: ImportError — file doesn't exist.

- [ ] **Step 3: Implement**

Create `src/mymcp/tools/result.py`:

```python
"""ToolResult — unified return shape for every MCP tool.

All tools return a ToolResult subclass. The dispatcher only sees `ok`,
`audit_payload()`, and `to_mcp()` — never per-tool field detection.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from mymcp.audit_output import summarize_output


@dataclass
class ToolResult:
    ok: bool
    error_code: str | None = None
    error_message: str | None = None

    def audit_payload(self) -> dict[str, Any]:
        """Compact, audit-safe view; never includes raw bodies."""
        return {"ok": self.ok, "error_code": self.error_code, "error_message": self.error_message}

    def to_mcp(self) -> dict[str, Any]:
        """JSON-safe shape returned to the MCP client."""
        if self.ok:
            return {"success": True}
        return {
            "success": False,
            "error": self.error_code or "InternalError",
            "message": self.error_message or "",
        }


@dataclass
class GenericErrorResult(ToolResult):
    def __post_init__(self):
        self.ok = False


@dataclass
class BashResult(ToolResult):
    exit_code: int = 0
    timed_out: bool = False
    stdout: str = ""
    stderr: str = ""

    def __post_init__(self):
        self.ok = (self.exit_code == 0) and (not self.timed_out)

    def audit_payload(self) -> dict[str, Any]:
        base = super().audit_payload()
        base.update({
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "stdout_sha256": hashlib.sha256(self.stdout.encode()).hexdigest(),
            "stdout_size": len(self.stdout),
            "stdout_head": self.stdout[:512],
            "stdout_tail": self.stdout[-512:] if len(self.stdout) > 1024 else "",
            "stderr_size": len(self.stderr),
        })
        return base

    def to_mcp(self) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


@dataclass
class FileResult(ToolResult):
    path: str = ""
    bytes_written: int = 0
    content: str = ""
    lines_read: int = 0

    def audit_payload(self) -> dict[str, Any]:
        base = super().audit_payload()
        base.update({
            "path": self.path,
            "bytes_written": self.bytes_written,
            "lines_read": self.lines_read,
        })
        return base

    def to_mcp(self) -> dict[str, Any]:
        out = {"success": self.ok, "path": self.path}
        if self.content:
            out["content"] = self.content
        if self.lines_read:
            out["lines_read"] = self.lines_read
        if self.bytes_written:
            out["bytes_written"] = self.bytes_written
        return out


@dataclass
class OverviewResult(ToolResult):
    overview: str = ""

    def to_mcp(self) -> dict[str, Any]:
        return {"success": self.ok, "overview": self.overview}

    def audit_payload(self) -> dict[str, Any]:
        base = super().audit_payload()
        base["overview_size"] = len(self.overview)
        return base
```

(Add other subclasses — `GlobResult`, `GrepResult`, `EditResult`, `TransferResult` — using the same pattern, matching the keys their current tool code returns.)

- [ ] **Step 4: Run, expect PASS**

Same as Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mymcp/tools/result.py tests/test_tool_result.py
git commit -m "feat(tools): ToolResult dataclasses

Unified return shape. audit_payload() handles redaction (no raw
bodies). to_mcp() handles client-facing JSON. ok is derived per
subclass (BashResult uses exit_code + timed_out; others use the
explicit field). Tool dispatcher no longer needs per-tool branching."
```

---

## Task 7: Convert `tools/bash.py` to return `BashResult`

**Files:**
- Modify: `src/mymcp/tools/bash.py`
- Test: `tests/test_bash.py`

- [ ] **Step 1: Update implementation**

In `src/mymcp/tools/bash.py`, change `run_bash_execute` return type from `dict` to `BashResult`. Replace the final `return {...}` (or wherever the dict is built) with:

```python
from mymcp.tools.result import BashResult

    return BashResult(
        exit_code=exit_code,
        timed_out=timed_out,
        stdout=stdout,
        stderr=stderr,
    )
```

(Adjust to match whatever variables hold these values today.)

- [ ] **Step 2: Update tests that asserted dict shape**

Run: `.venv/bin/python -m pytest tests/test_bash.py -v --benchmark-disable`

For each failure, update the assertion. Two patterns:
- Tests that did `result["exit_code"]` — change to `result.exit_code` (when calling the tool directly) or to `result["exit_code"]` (when going through the registry which calls `.to_mcp()`).

- [ ] **Step 3: Commit**

```bash
git add src/mymcp/tools/bash.py tests/test_bash.py
git commit -m "refactor(tools/bash): return BashResult"
```

---

## Task 8: Convert `tools/files.py` to return `FileResult`

**Files:**
- Modify: `src/mymcp/tools/files.py`
- Test: `tests/test_files.py`

- [ ] **Step 1: Identify return sites**

Run: `grep -n 'return {' src/mymcp/tools/files.py`

Each one is a return-shape conversion point: `read_file` → `FileResult(content=..., lines_read=..., path=...)`; `write_file` → `FileResult(bytes_written=..., path=...)`; `edit_file` → use either `FileResult` or a new `EditResult` if the schema differs materially.

- [ ] **Step 2: Apply**

Walk each return site. Example for `read_file`:

```python
from mymcp.tools.result import FileResult
...
    return FileResult(
        ok=True,
        path=path,
        content=content,
        lines_read=lines_read,
    )
```

For error paths, return `FileResult(ok=False, error_code="...", error_message="...")` or `GenericErrorResult`.

- [ ] **Step 3: Update tests**

Run: `.venv/bin/python -m pytest tests/test_files.py -v --benchmark-disable`

Fix assertion shapes as in Task 7.

- [ ] **Step 4: Commit**

```bash
git add src/mymcp/tools/files.py tests/test_files.py
git commit -m "refactor(tools/files): return FileResult"
```

---

## Task 9: Define `ToolRegistry` + `ToolSpec`

**Files:**
- Create: `src/mymcp/tool_registry.py`
- Test: `tests/test_tool_registry.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_tool_registry.py`:

```python
import pytest

from mymcp.tool_registry import ToolRegistry, ToolSpec
from mymcp.tools.result import GenericErrorResult, FileResult


def test_register_and_dispatch():
    reg = ToolRegistry()

    async def handler(args, ctx):
        return FileResult(ok=True, path=args["path"])

    reg.register(ToolSpec(
        name="echo_path",
        description="echo",
        schema={"type": "object", "properties": {"path": {"type": "string"}}},
        permission="read",
        mutates=False,
        handler=handler,
    ))
    assert "echo_path" in reg.names()


@pytest.mark.anyio
async def test_dispatch_unknown_returns_error():
    reg = ToolRegistry()
    result = await reg.dispatch("missing", {}, {})
    assert isinstance(result, GenericErrorResult)
    assert result.error_code == "UnknownTool"


def test_list_for_role_filters_by_permission():
    reg = ToolRegistry()
    async def h(args, ctx): return FileResult(ok=True)
    reg.register(ToolSpec(name="r1", description="", schema={}, permission="read", mutates=False, handler=h))
    reg.register(ToolSpec(name="w1", description="", schema={}, permission="write", mutates=True, handler=h))
    ro_tools = reg.list_for_role("ro")
    rw_tools = reg.list_for_role("rw")
    assert {t.name for t in ro_tools} == {"r1"}
    assert {t.name for t in rw_tools} == {"r1", "w1"}


def test_mutating_tools_derived_from_specs():
    reg = ToolRegistry()
    async def h(args, ctx): return FileResult(ok=True)
    reg.register(ToolSpec(name="mut", description="", schema={}, permission="write", mutates=True, handler=h))
    reg.register(ToolSpec(name="non", description="", schema={}, permission="read", mutates=False, handler=h))
    assert reg.mutating_tool_names() == {"mut"}
```

- [ ] **Step 2: Run, expect ImportError**

Run: `.venv/bin/python -m pytest tests/test_tool_registry.py -v --benchmark-disable`

Expected: ImportError.

- [ ] **Step 3: Implement**

Create `src/mymcp/tool_registry.py`:

```python
"""Tool registry — single source of truth for tool name, schema, permission,
mutation, and handler.

Core tools register themselves in mymcp.tools.__init__ at import time.
Optional subsystems (recorder) register their tools in their own __init__
when enabled, so disabled subsystems leave no traces in list_tools.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from mymcp.tools.result import GenericErrorResult, ToolResult

Permission = Literal["read", "write"]
Handler = Callable[[dict[str, Any], dict[str, Any]], Awaitable[ToolResult]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    schema: dict[str, Any]
    permission: Permission
    handler: Handler
    mutates: bool = False


class ToolRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._specs:
            raise ValueError(f"duplicate tool name: {spec.name}")
        self._specs[spec.name] = spec

    def unregister(self, name: str) -> None:
        self._specs.pop(name, None)

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def names(self) -> set[str]:
        return set(self._specs)

    def list_for_role(self, role: str) -> list[ToolSpec]:
        if role == "admin":
            return list(self._specs.values())
        if role == "rw":
            return list(self._specs.values())
        if role == "ro":
            return [s for s in self._specs.values() if s.permission == "read"]
        return []

    def mutating_tool_names(self) -> set[str]:
        return {n for n, s in self._specs.items() if s.mutates}

    async def dispatch(
        self,
        name: str,
        args: dict[str, Any],
        ctx: dict[str, Any],
    ) -> ToolResult:
        spec = self._specs.get(name)
        if spec is None:
            return GenericErrorResult(
                error_code="UnknownTool",
                error_message=f"No tool named '{name}'",
            )
        return await spec.handler(args, ctx)


TOOL_REGISTRY = ToolRegistry()
```

- [ ] **Step 4: Run, expect PASS**

Same as Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mymcp/tool_registry.py tests/test_tool_registry.py
git commit -m "feat(registry): ToolRegistry + ToolSpec

Single source of truth: name, schema, permission, mutation, handler.
READ_TOOLS / WRITE_TOOLS / MUTATING_TOOLS sets become derived views.
list_for_role filters at the registry level — no caller-side filtering."
```

---

## Task 10: Core tools self-register in `mymcp.tools.__init__`

**Files:**
- Modify: `src/mymcp/tools/__init__.py`
- Modify: `src/mymcp/tools/bash.py`, `src/mymcp/tools/files.py`, `src/mymcp/tools/transfer.py` (each exports its `ToolSpec`s)

- [ ] **Step 1: Define specs in each tool module**

In `src/mymcp/tools/bash.py`, after the handler:

```python
from mymcp.tool_definitions import BASH_EXECUTE_SCHEMA  # or wherever schema lives now
from mymcp.tool_registry import ToolSpec

async def _bash_handler(args: dict, ctx: dict):
    return await run_bash_execute(**args)

BASH_EXECUTE_SPEC = ToolSpec(
    name="bash_execute",
    description=...,  # move text from tool_definitions.py
    schema=BASH_EXECUTE_SCHEMA,
    permission="write",
    mutates=True,
    handler=_bash_handler,
)
```

(Repeat for `read_file`, `write_file`, `edit_file`, `glob`, `grep`, `prepare_upload`, `prepare_download` — each gets its own `*_SPEC` in its file.)

For permission and mutation values, use the current authoritative sets in `mcp_server.py`:
- READ: `read_file`, `glob`, `grep`, `prepare_download`
- WRITE: `bash_execute`, `write_file`, `edit_file`, `prepare_upload`
- MUTATES: anything that changes filesystem or process state — every WRITE tool

- [ ] **Step 2: Wire into `mymcp/tools/__init__.py`**

In `src/mymcp/tools/__init__.py`:

```python
from mymcp.tool_registry import TOOL_REGISTRY
from mymcp.tools.bash import BASH_EXECUTE_SPEC
from mymcp.tools.files import (
    READ_FILE_SPEC,
    WRITE_FILE_SPEC,
    EDIT_FILE_SPEC,
    GLOB_SPEC,
    GREP_SPEC,
)
from mymcp.tools.transfer import (
    PREPARE_UPLOAD_SPEC,
    PREPARE_DOWNLOAD_SPEC,
)

for _spec in (
    BASH_EXECUTE_SPEC,
    READ_FILE_SPEC,
    WRITE_FILE_SPEC,
    EDIT_FILE_SPEC,
    GLOB_SPEC,
    GREP_SPEC,
    PREPARE_UPLOAD_SPEC,
    PREPARE_DOWNLOAD_SPEC,
):
    TOOL_REGISTRY.register(_spec)
```

- [ ] **Step 3: Verify**

Run: `.venv/bin/python -c "import mymcp.tools; from mymcp.tool_registry import TOOL_REGISTRY; print(sorted(TOOL_REGISTRY.names()))"`

Expected: prints the full core tool list.

- [ ] **Step 4: Commit**

```bash
git add src/mymcp/tools/
git commit -m "feat(tools): self-register core tool specs into ToolRegistry"
```

---

## Task 11: Replace `READ_TOOLS` / `WRITE_TOOLS` / `dispatch_tool` / `list_tools` in `mcp_server.py`

**Files:**
- Modify: `src/mymcp/mcp_server.py`
- Test: `tests/test_mcp.py`, `tests/test_permissions.py`

- [ ] **Step 1: Delete the hard-coded sets and dispatcher branches**

In `src/mymcp/mcp_server.py`, delete:
- `READ_TOOLS = { ... }` and `WRITE_TOOLS = { ... }`
- The body of `dispatch_tool` that switches on `name` (including the special `server_overview` branch — Task 13 re-introduces it via recorder self-registration)
- `_recorder_supervisor` global and `set/get_recorder_supervisor` helpers (Task 13 removes the need)

- [ ] **Step 2: New thin dispatcher and lister**

```python
from mymcp.tool_registry import TOOL_REGISTRY


async def list_tools(role: str) -> list[dict]:
    """Return MCP tool descriptors visible to this role."""
    return [
        {
            "name": s.name,
            "description": s.description,
            "inputSchema": s.schema,
        }
        for s in TOOL_REGISTRY.list_for_role(role)
    ]


async def dispatch_tool(name: str, args: dict, ctx: dict) -> dict:
    """Route call to the registered handler. Returns MCP wire shape."""
    result = await TOOL_REGISTRY.dispatch(name, args, ctx)
    # audit/tracing is done by call_tool around this point
    return result.to_mcp()
```

- [ ] **Step 3: Update `call_tool` to use ToolResult**

In `mcp_server.py`, in the central `call_tool` handler, replace the two-shape detection logic with:

```python
    # Permission check
    spec = TOOL_REGISTRY.get(name)
    if spec is None:
        # unknown tool — audit as failure
        ...
    if spec.permission == "write" and role == "ro":
        # forbid; audit; return PermissionDenied
        ...

    try:
        result = await TOOL_REGISTRY.dispatch(name, args, ctx)
    except Exception as exc:
        # unexpected; wrap into GenericErrorResult and audit
        ...

    # Single audit point — no per-tool fan-out:
    log_tool_call(
        role=role,
        tool=name,
        params=_extract_params(name, args),
        result_status="success" if result.ok else "error",
        error_code=result.error_code,
        error_message=result.error_message,
        output=result.audit_payload(),
        duration_ms=...,
    )
    return result.to_mcp()
```

- [ ] **Step 4: Update tests**

Run: `.venv/bin/python -m pytest tests/test_mcp.py tests/test_permissions.py -v --benchmark-disable`

For each failure, fix assertion to match the new shape (most `result["exit_code"]` style assertions stay valid because `to_mcp()` preserves the shapes; permission checks now route through the registry).

- [ ] **Step 5: Commit**

```bash
git add src/mymcp/mcp_server.py tests/test_mcp.py tests/test_permissions.py
git commit -m "refactor(mcp): registry-backed dispatch + single audit point

Drops READ_TOOLS/WRITE_TOOLS sets, the dispatch switch, the
server_overview hard-coded branch, the _recorder_supervisor global,
and the two-shape success detection. call_tool is now ~30 lines."
```

---

## Task 12: Recorder package self-registers `server_overview` and exposes public supervisor accessors

**Files:**
- Modify: `src/mymcp/recorder/__init__.py`
- Modify: `src/mymcp/recorder/wiring.py`
- Modify: `src/mymcp/recorder/task.py` (add public methods)
- Test: `tests/recorder/test_registration.py` (new)

- [ ] **Step 1: Public accessors on supervisor**

In `src/mymcp/recorder/task.py`, add public methods so consumers don't need `getattr` chains:

```python
class RecorderSupervisor:
    ...
    def pending_count(self) -> int:
        try:
            return int(self._merge_cycle._tailer.pending_count())
        except Exception:
            return 0

    @property
    def last_merge_attempt_ts(self) -> float | None:
        return self._last_merge_attempt_ts
```

- [ ] **Step 2: Refactor `wiring.py` to use public methods**

In `src/mymcp/recorder/wiring.py`, replace `getattr(merge, "_tailer", None) ; getattr(tailer, ...)` chains with `sup.pending_count()`. Replace `getattr(sup, "_last_merge_attempt_ts", None)` with `sup.last_merge_attempt_ts`.

Also delete the `from mymcp.mcp_server import get_recorder_supervisor` lines — Task 11 removed that. Instead, accept the supervisor instance and bind it at install time:

```python
# wiring.py
def install_recorder_metrics(sup: RecorderSupervisor) -> None:
    def _circuit(): return [Observation(1 if sup.circuit_open else 0)]
    def _pending(): return [Observation(sup.pending_count())]
    def _attempt(): return [Observation(sup.last_merge_attempt_ts or 0)]
    def _success():
        ts = getattr(sup, "_last_merge_ts", None)
        return [Observation(ts if ts is not None else 0)]
    register_callback_gauge("mymcp.recorder.circuit_open", "...", _circuit)
    register_callback_gauge("mymcp.recorder.pending_events", "...", _pending)
    register_callback_gauge("mymcp.recorder.merge.last_attempt_timestamp", "...", _attempt)
    register_callback_gauge("mymcp.recorder.merge.last_success_timestamp", "...", _success)
```

(Add `last_merge_ts` as a public read accessor on the supervisor too, to remove the remaining getattr.)

- [ ] **Step 3: Register server_overview from recorder/__init__**

In `src/mymcp/recorder/__init__.py`:

```python
"""Recorder package. Public entry point: install_recorder(settings)."""

from mymcp.config import Settings
from mymcp.recorder.task import RecorderSupervisor
from mymcp.recorder.tool import server_overview_handler
from mymcp.recorder.wiring import build_supervisor, install_recorder_metrics
from mymcp.tool_registry import TOOL_REGISTRY, ToolSpec
from mymcp.tools.result import GenericErrorResult, OverviewResult


def install_recorder(settings: Settings) -> RecorderSupervisor:
    sup = build_supervisor(settings)

    async def _handler(args: dict, ctx: dict):
        status = sup.status()
        text = server_overview_handler(
            store=sup.store,
            schedule_bootstrap=lambda: sup.request_bootstrap(),
            pending_events=status.pending_events,
            last_merge_attempt_age_seconds=status.last_merge_attempt_age_seconds,
            consecutive_failures=status.consecutive_failures,
            last_error=status.last_error,
            circuit_open=status.circuit_open,
            merge_interval_sec=sup.merge_interval,
        )
        return OverviewResult(ok=True, overview=text)

    TOOL_REGISTRY.register(ToolSpec(
        name="server_overview",
        description="Returns the LLM-curated summary of recent server changes.",
        schema={"type": "object", "properties": {}, "additionalProperties": False},
        permission="read",
        mutates=False,
        handler=_handler,
    ))
    install_recorder_metrics(sup)
    return sup
```

Then in `mymcp/server.py` (or wherever startup wiring lives) — instead of calling `set_recorder_supervisor(...)`, call `from mymcp.recorder import install_recorder ; sup = install_recorder(settings)` gated on `settings.recorder_enabled`.

- [ ] **Step 4: Write a registration test**

Create `tests/recorder/test_registration.py`:

```python
def test_server_overview_not_registered_when_disabled(monkeypatch):
    """If recorder is disabled, server_overview must NOT be in the registry."""
    # Importing the recorder package itself does not register — only install_recorder does.
    from mymcp.tool_registry import TOOL_REGISTRY
    TOOL_REGISTRY.unregister("server_overview")
    assert "server_overview" not in TOOL_REGISTRY.names()


def test_install_recorder_registers_server_overview(tmp_path, monkeypatch):
    monkeypatch.setenv("MYMCP_RECORDER_ENABLED", "true")
    monkeypatch.setenv("MYMCP_RECORDER_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("MYMCP_RECORDER_LLM_API_KEY", "test")
    monkeypatch.setenv("MYMCP_RECORDER_DATA_DIR", str(tmp_path))

    from mymcp.config import Settings, reset_settings_cache
    reset_settings_cache()
    settings = Settings()

    from mymcp.recorder import install_recorder
    from mymcp.tool_registry import TOOL_REGISTRY
    TOOL_REGISTRY.unregister("server_overview")
    install_recorder(settings)
    assert "server_overview" in TOOL_REGISTRY.names()
```

- [ ] **Step 5: Run all recorder + mcp tests**

Run: `.venv/bin/python -m pytest tests/recorder tests/test_mcp.py tests/test_permissions.py -v --benchmark-disable`

Expected: green. The lifecycle (`set_recorder_supervisor` gone) means any test that called it must be updated to call `install_recorder` or to patch `TOOL_REGISTRY` directly.

- [ ] **Step 6: Commit**

```bash
git add src/mymcp/recorder/ tests/recorder/test_registration.py
git commit -m "feat(recorder): self-register server_overview via ToolRegistry

mcp_server.py no longer knows recorder exists. Disabled recorder
means server_overview is absent from list_tools — no phantom tool
returning RecorderDisabled. wiring.py uses public methods on the
supervisor instead of getattr-chain into private attributes."
```

---

# Phase 4: Async I/O off the event loop

## Task 13: File tools — `anyio.to_thread.run_sync`

**Files:**
- Modify: `src/mymcp/tools/files.py`
- Test: `tests/test_files.py` (no new assertions; regression check)

- [ ] **Step 1: Identify blocking call sites**

Run: `grep -n 'open(\|\.read\b\|\.write\b\|os.stat\|os.path' src/mymcp/tools/files.py`

- [ ] **Step 2: Wrap each in `anyio.to_thread.run_sync`**

For `read_file` (example):

```python
import anyio

async def read_file(...) -> FileResult:
    ...
    def _do_read() -> tuple[str, int]:
        with open(path) as f:
            lines = f.readlines()
            slice_ = lines[offset:offset + effective_limit]
            return "".join(slice_), len(slice_)

    content, lines_read = await anyio.to_thread.run_sync(_do_read)
    return FileResult(ok=True, path=path, content=content, lines_read=lines_read)
```

Apply the same pattern to `write_file`, `edit_file`, `glob_files` (if it does sync globbing), and the Python `_grep_python` fallback in `grep_files`.

- [ ] **Step 3: Verify**

Run: `.venv/bin/python -m pytest tests/test_files.py -v --benchmark-disable`

Expected: green.

- [ ] **Step 4: Commit**

```bash
git add src/mymcp/tools/files.py
git commit -m "perf(tools/files): blocking I/O off the asyncio event loop

Synchronous open/read/write inside async def pinned the FastAPI
worker on slow disks. anyio.to_thread.run_sync routes the blocking
syscalls through a thread pool."
```

---

## Task 14: TokenStore load/save off the event loop

**Files:**
- Modify: `src/mymcp/auth.py`

- [ ] **Step 1: Apply pattern**

If `TokenStore._load()` / `_save()` are called from request paths, wrap them with `anyio.to_thread.run_sync` at the call site. Easier: leave the sync internals as-is but make any async caller route through `await anyio.to_thread.run_sync(store._save)`.

For the in-process token middleware, lookups are pure-memory (after Task 4) so no change is needed. The only remaining disk hit is `flush()` at shutdown, which is fine sync (shutdown isn't latency-sensitive).

- [ ] **Step 2: Run full suite**

Run: `.venv/bin/python -m pytest tests/ --benchmark-disable`

Expected: green.

- [ ] **Step 3: Commit**

```bash
git add src/mymcp/auth.py
git commit -m "perf(auth): keep disk hits out of the request hot path

After Task 4 moved last_used to RAM, the only disk hits left are
load (startup) and flush (shutdown). Document and verify; no behavior change."
```

---

# Phase 5: Transfer audit fix

## Task 15: Ticket model — `issuer_token_id` + `issuer_role`

**Files:**
- Modify: `src/mymcp/transfer/tickets.py`
- Test: `tests/test_transfer_tickets.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_transfer_tickets.py`:

```python
def test_ticket_records_issuer(tmp_path):
    from mymcp.transfer.tickets import TicketStore

    s = TicketStore(path=str(tmp_path / "tickets.json"))
    tk = s.mint(
        kind="upload",
        path="/tmp/x",
        issuer_token_id="abc123",
        issuer_role="rw",
    )
    redeemed = s.consume(tk.id)
    assert redeemed.issuer_token_id == "abc123"
    assert redeemed.issuer_role == "rw"
```

- [ ] **Step 2: Run, expect FAIL** (signature mismatch).

- [ ] **Step 3: Add fields**

In `src/mymcp/transfer/tickets.py`, add `issuer_token_id: str` and `issuer_role: str` to the Ticket dataclass. Update `mint` to accept and store them; update `consume` to return them via the redeemed ticket object.

- [ ] **Step 4: Commit**

```bash
git add src/mymcp/transfer/tickets.py tests/test_transfer_tickets.py
git commit -m "feat(transfer/tickets): record issuer_token_id and issuer_role on mint"
```

---

## Task 16: `prepare_upload` / `prepare_download` mint with real issuer info

**Files:**
- Modify: `src/mymcp/tools/transfer.py`

- [ ] **Step 1: Read issuer from context**

In `src/mymcp/tools/transfer.py`, the tool handler has access to the audit context (the same one `mcp_server` sets via contextvar). Read `token_id` and `role` from it and pass to `mint()`:

```python
from mymcp.mcp_server import _current_audit_info  # or whichever public accessor exists post-refactor

async def prepare_upload(...) -> TransferResult:
    info = _current_audit_info.get()
    ticket = tickets.mint(
        kind="upload",
        path=path,
        issuer_token_id=info["token_id"],
        issuer_role=info["role"],
    )
    ...
```

(If a public accessor exists post-refactor — e.g. `get_current_audit_context()` — use that instead.)

- [ ] **Step 2: Commit**

```bash
git add src/mymcp/tools/transfer.py
git commit -m "feat(tools/transfer): mint tickets with real issuer identity"
```

---

## Task 17: Endpoints — split `transfer_upload` / `transfer_download` audit; drop fake role

**Files:**
- Modify: `src/mymcp/transfer/endpoints.py`
- Test: `tests/test_transfer_endpoints.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_transfer_endpoints.py`:

```python
@pytest.mark.anyio
async def test_upload_audit_uses_transfer_upload_tool_name(...):
    """The audited tool name must be 'transfer_upload', not the legacy 'transfer_redeem'."""
    ...
    # Inspect the audit record produced by the upload redeem path.
    records = read_audit_records()  # use whatever helper the suite has
    assert any(r["tool"] == "transfer_upload" for r in records)
    assert not any(r["tool"] == "transfer_redeem" for r in records)


@pytest.mark.anyio
async def test_upload_audit_has_no_fake_role(...):
    """role must not be one of the hardcoded 'rw'/'ro' literals — it should
    be either absent or explicitly the issuer's recorded role."""
    ...
    rec = next(r for r in read_audit_records() if r["tool"] == "transfer_upload")
    # issuer fields present
    assert "issuer_token_id" in rec
    assert "issuer_role" in rec
    # redeemer side has IP but no fabricated MCP role
    assert "redeemer_ip" in rec
    assert rec.get("actor_role") in (None, "ticket")
```

- [ ] **Step 2: Run, expect FAIL.**

- [ ] **Step 3: Update endpoint code**

In `src/mymcp/transfer/endpoints.py`, replace the `role="rw" if upload else "ro"` line and the `tool="transfer_redeem"` log call:

```python
ticket = tickets.consume(ticket_id)
audit_tool = "transfer_upload" if upload else "transfer_download"
log_tool_call(
    tool=audit_tool,
    role="ticket",          # explicit literal — not an MCP role
    result_status="success" if ok else "error",
    params={
        "ticket_id": ticket_id,
        "issuer_token_id": ticket.issuer_token_id,
        "issuer_role": ticket.issuer_role,
        "redeemer_ip": request.client.host,
        "path": ticket.path,
        "size": size,
    },
    ...
)
```

- [ ] **Step 4: Run, expect PASS**.

- [ ] **Step 5: Commit**

```bash
git add src/mymcp/transfer/endpoints.py tests/test_transfer_endpoints.py
git commit -m "fix(transfer/audit): split transfer_upload/transfer_download; drop fake role

Redeemer has no MCP role. Endpoints now log the issuer (from ticket)
and the redeemer's IP. Tool name distinguishes the direction. Old
'transfer_redeem' synthetic name is gone."
```

---

## Task 18: Mark `transfer_upload` as mutating so the recorder learns

**Files:**
- Modify: `src/mymcp/recorder/events.py`
- Test: `tests/recorder/test_events.py`

- [ ] **Step 1: Write failing test**

Append to `tests/recorder/test_events.py`:

```python
def test_transfer_upload_is_mutating():
    from mymcp.recorder.events import is_mutating_event
    assert is_mutating_event({"tool": "transfer_upload"}) is True


def test_transfer_download_is_not_mutating():
    from mymcp.recorder.events import is_mutating_event
    assert is_mutating_event({"tool": "transfer_download"}) is False
```

- [ ] **Step 2: Update `MUTATING_TOOLS`**

In `src/mymcp/recorder/events.py`, replace the hard-coded set with a registry-derived set:

```python
def _mutating_tool_names() -> set[str]:
    """Derived from the registry so subsystems contribute their own."""
    from mymcp.tool_registry import TOOL_REGISTRY
    return TOOL_REGISTRY.mutating_tool_names()


def is_mutating_event(event: dict) -> bool:
    return event.get("tool") in _mutating_tool_names()
```

The `transfer_upload` tool itself (the `prepare_upload` MCP tool) is already registered as `mutates=True` (Task 10). But the **audit event name `transfer_upload`** is the *endpoint*-side name, not the MCP tool name. Decide between two options and pick one:

**Option A (recommended):** Add an extra mutating-name override that includes endpoint-only tool names. Update `_mutating_tool_names`:

```python
_EXTRA_MUTATING = {"transfer_upload"}  # endpoint events, not MCP tools

def _mutating_tool_names() -> set[str]:
    from mymcp.tool_registry import TOOL_REGISTRY
    return TOOL_REGISTRY.mutating_tool_names() | _EXTRA_MUTATING
```

(Update Task 17's audit code so it writes `tool="transfer_upload"` for the endpoint event, which is what the test expects.)

- [ ] **Step 3: Run, expect PASS.**

- [ ] **Step 4: Commit**

```bash
git add src/mymcp/recorder/events.py tests/recorder/test_events.py
git commit -m "fix(recorder/events): transfer_upload counts as mutating

The endpoint-side audit name 'transfer_upload' was not in MUTATING_TOOLS,
so the recorder's changelog had no record of files uploaded through
the transfer endpoint — a silent blind spot. Set now derives from the
ToolRegistry plus an endpoint-event override."
```

---

## Task 19: Full regression + PR

- [ ] **Step 1: Full suite**

Run: `.venv/bin/python -m pytest tests/ -v --benchmark-disable`

Expected: green.

- [ ] **Step 2: mypy + ruff**

Run: `.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy src/mymcp`

Expected: clean. mypy strictness should *increase* — many previously-`Any` paths now type as `ToolResult`.

- [ ] **Step 3: Push and PR**

```bash
git push -u origin feature/architecture-refactor
gh pr create --title "refactor: ToolResult + ToolRegistry + config cleanup + async I/O + transfer audit" --body "$(cat <<'EOF'
## Summary

Five interlocked refactors. Each was risky alone; together they remove ~200 lines of glue and tighten typing across the dispatch path.

- **Config (Phase 1):** Tools resolve config at call time via \`get_settings()\` instead of capturing import-time defaults. \`config.__getattr__\` legacy shim removed. \`monkeypatch.setenv\` + \`reset_settings_cache\` now actually take effect.
- **ToolResult (Phase 2):** Per-tool dataclasses unify the return shape. \`audit_payload()\` handles redaction; \`to_mcp()\` handles wire JSON. The two-shape success protocol (\`success=False\` vs \`exit_code\`/\`timed_out\`) is gone from \`call_tool\`.
- **ToolRegistry (Phase 3):** \`ToolSpec\` carries name/schema/permission/mutates/handler. Core tools self-register; recorder self-registers \`server_overview\` only when enabled. \`mcp_server.py\` loses \`READ_TOOLS\`/\`WRITE_TOOLS\` sets, the \`server_overview\` switch branch, the \`_recorder_supervisor\` global, and \`set/get_recorder_supervisor\`. \`wiring.py\` loses the private-attribute getattr chains.
- **Async I/O (Phase 4):** File tool blocking syscalls wrapped in \`anyio.to_thread.run_sync\`.
- **Transfer audit (Phase 5):** Tickets record \`issuer_token_id\` / \`issuer_role\`. Endpoints write \`transfer_upload\` / \`transfer_download\` (not synthetic \`transfer_redeem\`) with the real issuer and redeemer fields. Endpoint events that mutate the filesystem are now visible to the recorder.

Spec: \`docs/superpowers/specs/2026-06-06-project-assessment.md\` (P2 #12-15, #17).

## Test plan
- [ ] Full \`pytest tests/\` green
- [ ] mypy clean (and stronger — fewer Anys)
- [ ] Manual: bring up server, hit \`server_overview\` with recorder enabled (works) and disabled (returns UnknownTool, not RecorderDisabled)
- [ ] Manual: upload through \`prepare_upload\` → curl ticket — confirm changelog now records the upload

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review checklist

- [x] P2 #12 (config refactor) → Tasks 2-5
- [x] P2 #13 (ToolResult) → Tasks 6, 7, 8
- [x] P2 #14 (ToolRegistry + recorder decoupling) → Tasks 9-12
- [x] P2 #15 (async I/O) → Tasks 13-14
- [x] P2 #17 (transfer audit) → Tasks 15-18
- [x] No "TODO" / "TBD" / "similar to Task N" — each task is self-contained
- [x] Type names consistent: `ToolResult`, `ToolSpec`, `ToolRegistry`, `TOOL_REGISTRY` everywhere
- [x] Dependencies on Plan A (recorder behavior settled) and Plan B (tool_definitions.py cleaned up) noted in header
