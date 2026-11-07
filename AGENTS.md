# AGENTS.md

## Project Overview

**algony-mymcp** is a Go MCP server exposing Linux system tools over Streamable
HTTP with Bearer token auth. The Python package is a recorder-only sidecar with
zero base dependencies.

**Two-component architecture:**

- **Go core** (`go/`) — the server binary `mymcp`, all MCP tool implementations,
  HTTP transport, auth, audit, and metrics.
- **Python recorder sidecar** (`src/mymcp/`) — optional `mymcp-recorder` daemon
  that consumes `audit.log` and uses an LLM to maintain a live system overview.

## Quick Reference Commands

```bash
# --- Go core ---
cd go && go build -o /tmp/mymcp ./cmd/mymcp      # build
cd go && go test ./... && go vet ./... && gofmt -l .  # test + lint

# --- Python sidecar ---
pip install -e ".[dev]" -c requirements-dev.txt   # dev install
pytest tests/ -v --benchmark-disable              # test
ruff check . && ruff format --check . && mypy src/mymcp  # lint

# --- Regenerate Python lockfile after pyproject.toml changes ---
pip-compile --extra dev --strip-extras \
  --unsafe-package algony-mymcp --unsafe-package pip --unsafe-package setuptools \
  --output-file requirements-dev.txt pyproject.toml

# --- Compat suite (black-box, against Go server) ---
cd go && go build -o /tmp/mymcp ./cmd/mymcp
# boot /tmp/mymcp serve, then:
#   MYMCP_COMPAT_URL=http://127.0.0.1:PORT pytest tests/compat/ -v

# --- Package ---
python -m build --wheel -n
python scripts/assemble_wheel.py dist/*.whl <go-binary> menylinux2014_x86_64 platform-dist/
```

## Git Conventions

### Branching Strategy

- **`master`** — the stable mainline; all CI must pass before merge.
- **Feature / fix branches** — branch off `master`, named as
  `feat/<topic>`, `fix/<topic>`, `chore/<topic>`, `ci/<topic>`, etc.
- **Dependabot branches** — auto-created by Dependabot for dependency bumps.
- Merge back to `master` via **Pull Request** with **squash merge**.
- **Release tags** — semantic versioning on `master`: `v3.1.0`, `v3.0.0`, etc.

### Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <subject>

# Examples:
feat(install): one-liner install script + CI release assets
fix(ci): fix security-audit vulnerabilities
chore(deps): bump pytest-randomly from 4.1.0 to 5.0.0
docs(changelog): document v3.1.0 release
test(go): raise coverage 69%→80%
build: exclude Markdown from ruff check/format
```

**Types:** `feat`, `fix`, `chore`, `docs`, `test`, `ci`, `build`, `refactor`, `perf`.
**Scopes** (optional): `deps`, `ci`, `install`, `go`, `recorder`, `changelog`, etc.
Breaking changes: use `!` suffix (`feat!:`, `build!:`) or `BREAKING CHANGE:` in body.

## Testing

### Test Stack

| Component | Framework | Config |
|-----------|-----------|--------|
| Go core | `go test` | Standard Go testing; statement coverage with ratchet floor (≥78%) |
| Python sidecar | `pytest` + `anyio` (asyncio backend) | `pyproject.toml [tool.pytest]`; timeout 30s |
| Compat suite | `pytest` against live Go server | `tests/compat/`; validates tool schemas against `golden_tools.json` |
| Mutation testing | `mutmut` (Python only) | Targets `config.py`, `cursor.py`, `events.py`, `merge_cycle.py` |
| Security audit | `pip-audit` + `govulncheck` | Runs in CI `security-audit` job |

### Test Layout

```
tests/
├── conftest.py            # shared fixtures
├── recorder/              # Python recorder unit/integration tests
├── compat/                # black-box Go server compat tests
├── install/               # packaging consistency tests
├── loadtest/              # performance / load tests
├── pentest.py             # security penetration tests
├── live/                  # tests hitting real LLM APIs (marker: @pytest.mark.live)
└── test_*.py              # unit tests for config, audit, observability
```

### Test Conventions

- Async tests use `@pytest.mark.anyio`.
- Config is patched via `unittest.mock.patch.multiple("mymcp.config", ...)` or
  `monkeypatch.setenv("MYMCP_*")` followed by `mymcp.config.reset_settings_cache()`.
- No test database or external services needed for the default test suite.
- Benchmarks are disabled by default (`--benchmark-disable`); use
  `-m benchmark` to run.
- Live LLM tests require `tests/live/.env.live` and are excluded by default
  (`-m 'not live'`).

### CI Pipeline

CI runs on every push to `master` and on all pull requests:

1. **lint** — `ruff check`, `ruff format --check`, `mypy`, `shellcheck`
2. **go** — `gofmt`, `go vet`, `go test` with coverage ratchet
3. **test** — Python `pytest` with coverage
4. **compat-go** — black-box compat suite against the Go server
5. **security-audit** — `pip-audit` + `govulncheck`
6. **build** — wheel build verification
7. **mutation-smoke / mutation-full** — mutation testing (PR only)

## Code Quality

### Linting & Formatting

- **Python**: Ruff (line-length 100, target py311), MyPy (non-strict), pre-commit hooks.
- **Go**: `gofmt`, `go vet`. No additional linter configured.
- **Shell**: `shellcheck` for install scripts.
- Markdown files are excluded from Ruff (`extend-exclude = ["*.md"]`).

### Key Design Constraints

- **SOC red line**: audit write failure → `InternalError/500`. Never confirm an
  unauditable mutation.
- **Permission model**: `readTools`/`writeTools` sets; `ro` tokens → read tools
  only. The `tools/list` result is role-filtered.
- **Protected paths**: audit log dir + `MYMCP_PROTECTED_PATHS` are read+write
  protected; recorder overview dir is write-only protected. `bash_execute` is
  NOT subject to protected paths.
- **Stateless transport**: each HTTP request is independent (no session tracking).
- **Python ↔ Go contract**: the recorder and Go server share `audit.log` format,
  `MYMCP_*` env vars, `tokens.json`, `cursor.json` shape, and the `MUTATING_TOOLS`
  set. Changes to these shared interfaces require coordination across both sides.
- **Design docs**: detailed architecture and protocol designs live under `docs/design/`
  (`architecture.md`, `overview-recorder.md`, `file-transfer.md`, `security.md`).
