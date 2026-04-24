# Security Testing Design

**Date:** 2026-04-21  
**Scope:** mymcp MCP server — public-internet-facing threat model

## Background

mymcp exposes Linux system tools (file read/write, bash execution, glob, grep) over Streamable HTTP with Bearer token auth. The server may be deployed publicly. Existing tests cover auth logic, permission filtering, and protected path enforcement at the unit level, but there is no dedicated security test file and no black-box pentest tooling.

## Goals

1. Add `tests/test_security.py` — pytest-based security tests using ASGI transport (no live server needed, runs in CI)
2. Add `tests/pentest.py` — standalone black-box script that targets a live running instance

## Non-Goals

- Rate limiting / DoS protection (infrastructure concern, not application code)
- TLS configuration testing
- Threat model documentation (deferred)

---

## tests/test_security.py

Uses the same ASGI transport fixture pattern as `test_main.py` (httpx `AsyncClient` + `ASGITransport`). Requires a valid and an ro token fixture.

### Category 1: Authentication Boundary

| Test | Expected |
|------|----------|
| No Authorization header on /mcp | 401 |
| `Authorization: Bearer ` (empty token after prefix) | 401 |
| `Authorization: ` (no Bearer prefix) | 401 |
| Admin token (`adm_...`) used as user token | 401 |
| Disabled token | 401 |

### Category 2: Privilege Escalation

| Test | Expected |
|------|----------|
| ro token calls `bash_execute` | PermissionDenied in response body |
| ro token calls `write_file` | PermissionDenied |
| ro token calls `edit_file` | PermissionDenied |
| ro token list_tools response | Does not contain bash_execute, write_file, edit_file |

### Category 3: Path Traversal

Tested at the tool-function level (reuses `mock_protected_paths` fixture pattern).

The tool's security boundary is the protected-path list, not a chroot. `read_file("../../../etc/passwd")` legitimately resolves to `/etc/passwd` and is allowed — the tool is intentionally a system-management tool. Path traversal tests focus on protected-path bypass attempts only.

| Input | Expected |
|-------|----------|
| `/opt/mymcp/../mymcp/config.py` | Blocked — `realpath` resolves to `/opt/mymcp/config.py`, which is inside APP_DIR |
| Symlink in tmp pointing into protected dir | Blocked — `realpath` follows symlinks |
| Path with null byte (`/tmp/foo\x00bar`) | Returns error — Python's `open()` raises `ValueError` on embedded null bytes |
| Exact protected dir path (e.g. `/opt/mymcp`) | Blocked |

### Category 4: Information Leakage

| Test | Expected |
|------|----------|
| 401 response body | Does not contain internal file paths or stack traces |
| PermissionDenied response | Does not echo back the token value |
| InternalError response (mocked unhandled exception) | Returns generic message only, no traceback |

### Category 5: HTTP Layer

| Test | Expected |
|------|----------|
| Authorization header > 8KB | 401 or 400, server does not crash |
| Bearer token containing `\n` (newline injection) | 401, not treated as valid |
| Bearer token containing null byte | 401 |

---

## tests/pentest.py

Standalone script. No pytest dependency. Uses only `httpx` + stdlib.

### Usage

```bash
python3 tests/pentest.py --url http://localhost:8000 --token tok_xxxx
# With ro token for privilege escalation tests:
python3 tests/pentest.py --url http://localhost:8000 --token tok_xxxx --ro-token tok_yyyy
```

### Test Sequence

1. **Auth checks** — no token → 401; invalid token → 401; admin token → 401
2. **Path traversal** — `read_file /opt/mymcp/../mymcp/config.py` → must be blocked; verify no crash on null-byte path
3. **Privilege escalation** (requires `--ro-token`) — ro token calls bash_execute → PermissionDenied
4. **Information leakage** — 401 body scan for stack traces; error responses scanned for token strings
5. **Bash edge cases** — timeout=0, timeout=99999, command=""

### Output Format

```
[PASS] No token → 401
[FAIL] Path traversal read_file: expected blocked, got 200
...
Results: 11 passed, 1 failed
```

Exit code 0 = all pass. Non-zero = failures present (CI-friendly).

### Dependencies

`httpx` only (already in `requirements.txt`). No additional packages.

---

## Test Fixtures

`tests/test_security.py` will add a shared `security_client` fixture that creates both an `rw` and an `ro` token and yields an `AsyncClient`. This keeps individual tests concise.

---

## What Is NOT Tested Here

- **bash_execute command injection** — bash_execute is intentionally a raw shell executor; the security boundary is the token/role, not input sanitization. Testing "can you run `rm -rf /`" is correct behavior for an rw token holder.
- **Admin API endpoints** — already covered in `test_admin.py`.
