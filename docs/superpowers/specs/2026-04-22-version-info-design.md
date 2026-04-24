# Version Info Design

**Date:** 2026-04-22
**Scope:** Expose current deployed version via HTTP endpoint

## Background

The app currently has a hardcoded `version="1.0.0"` in `main.py` with no way for clients or operators to query the running version. The deploy system (`upgrade.sh`) supports deploying specific tags (e.g. `v1.1.0`) or HEAD. There is no authoritative record of which version is actually running.

## Goals

1. Add a `VERSION` file to the git repo as a development reference
2. `upgrade.sh` writes the deployed version to `APP_DIR/VERSION` at deploy time
3. `config.py` reads the version at startup from `APP_DIR/VERSION` (falls back to repo `VERSION`, then `"unknown"`)
4. `GET /version` endpoint returns the running version (no auth required)
5. `GET /health` includes the version field

## Non-Goals

- MCP tool or Resource for version (not needed; `bash_execute` + `curl` covers AI client use case)
- Semantic version validation
- Changelog or release notes exposure

---

## VERSION File (git-tracked)

- Path: `VERSION` (repo root)
- Format: plain semver string, no `v` prefix, single line — e.g. `1.1.1`
- Used as fallback when `APP_DIR/VERSION` does not exist (local dev, fresh clone)

## upgrade.sh Version Writing

When `upgrade.sh` deploys a specific tag (e.g. `v1.1.0`):
```bash
echo "1.1.0" > "$APP_DIR/VERSION"
```
Strip the leading `v` from the argument.

When upgrading to latest HEAD (no version argument or `latest`):
```bash
git describe --tags --always > "$APP_DIR/VERSION"
```
Output examples: `1.1.0-3-gabcdef1` (3 commits past tag), `abcdef1` (no tags).

On rollback after a failed upgrade, `upgrade.sh` restores the previous `APP_DIR/VERSION` from a backup taken before the upgrade started.

## config.py

```python
def _read_version() -> str:
    for path in [os.path.join(APP_DIR, "VERSION"), "VERSION"]:
        try:
            with open(path) as f:
                return f.read().strip()
        except OSError:
            pass
    return "unknown"

APP_VERSION: str = _read_version()
```

## HTTP Endpoints

### GET /version

No authentication required.

Response:
```json
{"version": "1.1.1"}
```

### GET /health (updated)

```json
{"status": "ok", "version": "1.1.1"}
```

## Files Changed

| Action | File | Change |
|--------|------|--------|
| Create | `VERSION` | Initial content: current version number |
| Modify | `config.py` | Add `_read_version()` and `APP_VERSION` |
| Modify | `main.py` | Add `/version` endpoint; update `/health` response |
| Modify | `deploy/upgrade.sh` | Write `APP_DIR/VERSION` on success; backup/restore on rollback |

## Testing

- Unit test: `_read_version()` reads from `APP_DIR/VERSION` when present, falls back to repo `VERSION`, falls back to `"unknown"`
- Integration test: `GET /version` returns 200 with `{"version": ...}`; `GET /health` includes `version` field
- No test for `upgrade.sh` version writing (covered by existing bats integration tests structure)
