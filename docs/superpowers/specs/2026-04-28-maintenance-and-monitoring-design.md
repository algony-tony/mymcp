# Maintenance and Monitoring Update Design

**Date:** 2026-04-28
**Status:** Draft
**Topic:** Fixing mutation testing, Python compatibility, and documenting Prometheus/Grafana monitoring.

## 1. Problem Statement
1. Mutation coverage is showing 0% on GitHub because the baseline test suite fails due to Python 3.10 incompatibility (`datetime.UTC`) and a bug in configuration splitting (`split("XX,XX")`).
2. Mutation testing is extremely slow (hours) in CI, leading to timeouts or stale results.
3. Prometheus and Grafana monitoring features are implemented but not documented in the main README, making them hard to discover for users.

## 2. Proposed Changes

### 2.1 Code Fixes (Python Compatibility & Bugs)
- Replace `datetime.UTC` with `datetime.timezone.utc` in `src/mymcp/auth.py` and `src/mymcp/audit.py`.
- Catch `(asyncio.TimeoutError, TimeoutError)` in `src/mymcp/tools/bash.py` and `src/mymcp/tools/files.py` to handle `asyncio.wait_for` differences across Python versions.
- Fix `src/mymcp/config.py` to use `,` instead of `XX,XX` as the delimiter for `protected_paths`.

### 2.2 Mutation Testing Optimization
- Update `.github/workflows/ci.yml` to:
    - Pre-generate `.coverage` using `pytest-cov`.
    - Run `mutmut` with the `--use-coverage` flag to skip irrelevant tests for each mutation.
    - Refine the badge update script to handle missing cache files or empty results gracefully.

### 2.3 Documentation
- Add a **Monitoring** section to `README.md` covering:
    - Enabling metrics via `MYMCP_METRICS_TOKEN`.
    - Prometheus scrape configuration example.
    - Grafana dashboard availability in `deploy/grafana/`.
- Update the **Testing** section in `README.md` to mention `--use-coverage` for faster mutation testing.

## 3. Implementation Strategy
1. Create a new branch `feat/maintenance-and-monitoring`.
2. Apply code fixes and verify with local `pytest`.
3. Update CI workflow and documentation.
4. Push and create a Pull Request using `gh`.

## 4. Verification Plan
- Run `pytest tests/` locally on Python 3.10 to ensure compatibility.
- Run `mutmut run --use-coverage` on a single file (e.g., `config.py`) to verify speed improvements.
- Check `README.md` rendering and links.
