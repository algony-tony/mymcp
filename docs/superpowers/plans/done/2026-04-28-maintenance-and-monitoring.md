# Maintenance & Monitoring Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix mutation testing bugs, optimize CI speed, and document monitoring features.

**Architecture:** Combine maintenance fixes and documentation into a single PR. Use `--use-coverage` for mutmut performance.

**Tech Stack:** Python 3.10+, mutmut, pytest, GitHub CLI.

---

### Task 1: Environment and Branch Setup

- [ ] **Step 1: Create a new branch**
Run: `git checkout -b feat/maintenance-and-monitoring`

- [ ] **Step 2: Commit the design spec**
Run: `git add docs/superpowers/specs/2026-04-28-maintenance-and-monitoring-design.md && git commit -m "docs: add design spec for maintenance and monitoring update"`

---

### Task 2: Apply Code Fixes (Mutation & Compatibility)

**Files:**
- Modify: `src/mymcp/auth.py`
- Modify: `src/mymcp/audit.py`
- Modify: `src/mymcp/config.py`
- Modify: `src/mymcp/tools/bash.py`
- Modify: `src/mymcp/tools/files.py`

- [ ] **Step 1: Fix Python 3.10 compatibility and logic bugs**
(The changes involve replacing `datetime.UTC` and broadening `TimeoutError` catching).

- [ ] **Step 2: Run all tests to verify everything passes**
Run: `python3 -m pytest tests/ -v --benchmark-disable`
Expected: 304 passed.

- [ ] **Step 3: Commit code fixes**
Run: `git add src/mymcp/ && git commit -m "fix: python compatibility, timeout handling, and config splitting bugs"`

---

### Task 3: CI Optimization & Documentation

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `README.md`

- [ ] **Step 1: Update CI workflow to use `--use-coverage`**
Update `.github/workflows/ci.yml` mutation job steps as per investigation findings.

- [ ] **Step 2: Add Monitoring section to README.md**
Add the "Monitoring" section and update "Testing" section with `--use-coverage` hint.

- [ ] **Step 3: Commit CI and documentation changes**
Run: `git add .github/workflows/ci.yml README.md && git commit -m "ci: optimize mutation testing and add monitoring docs to README"`

---

### Task 4: Push and Create PR

- [ ] **Step 1: Push the branch**
Run: `git push -u origin feat/maintenance-and-monitoring`

- [ ] **Step 2: Create Pull Request using `gh`**
Run: `gh pr create --title "Maintenance & Monitoring Update" --body "This PR fixes mutation testing (Python compatibility and config bugs), optimizes mutation testing speed in CI using --use-coverage, and adds documentation for Prometheus/Grafana monitoring."`
