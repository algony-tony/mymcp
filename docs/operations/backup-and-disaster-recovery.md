# Backup and Disaster Recovery

This runbook documents what to back up for a production mymcp deployment,
how to restore each asset, and which symptoms map to which failure mode.

> The project ships defaults and procedures. Alert rules, retention
> windows, and backup destinations are deployment-specific.

## Assets

| Asset | Default path | Why it matters | Loss impact |
|---|---|---|---|
| Token store | `/etc/mymcp/tokens.json` | Authenticated tokens (admin/rw/ro) | Admin lockout — only the startup-random admin token works until restore |
| Audit log | `/var/log/mymcp/audit.log*` | Per-call audit trail (compliance, incident response, recorder source) | Compliance gap; recorder backlog cannot be replayed |
| Recorder overview | `/var/lib/mymcp/recorder/overview/overview.md` | LLM-curated server state summary | Recorder re-bootstraps from scratch on next start (~30 minutes of LLM calls) |
| Recorder changelog | `/var/lib/mymcp/recorder/overview/changelog.md` | Per-event log of recorded changes | History gap; nothing reconstructs it |
| Recorder cursor | `/var/lib/mymcp/recorder/cursor.json` | Position in audit log | Recorder reprocesses some events on restart |
| Service config | `/etc/mymcp/.env` | Operational config (env-style file) | Service won't start with the operator's intended settings |

The token store and `.env` are both written with `0o600`. The audit log
directory and the recorder data dir are owned by the service user (see
`mymcp install-service`).

## Backup recommendation

- **Token store + `.env`:** treat as secrets. Manage via a secret store
  (Vault, AWS SSM, sops, age, etc.) or copy to an encrypted backup
  destination. Both files are small (<10 KB) and tolerate daily snapshots.
- **Audit log:** the shipped `logrotate` config rotates weekly. Ship the
  rotated files (`audit.log.1.gz`, `audit.log.2.gz`, …) off-host nightly.
  Retention should match your compliance requirement.
- **Recorder data:** rsync the overview directory (`overview.md` +
  `changelog.md`) plus `cursor.json` daily. The overview is a single
  Markdown file; the changelog grows append-only and benefits from
  incremental backup.

A minimal cron-style template (adapt destinations and ownership):

```bash
#!/bin/bash
set -euo pipefail
DEST=/srv/backup/mymcp/$(date +%F)
mkdir -p "$DEST"
cp -p /etc/mymcp/tokens.json /etc/mymcp/.env "$DEST"/
rsync -a /var/lib/mymcp/recorder/ "$DEST"/recorder/
# Audit: only rotated files (not the live one).
find /var/log/mymcp -name 'audit.log.*' -mtime -1 -exec cp -p {} "$DEST"/ \;
```

## Restore procedures

Order matters: stop the service first, restore files, then start.

### Token store

```bash
sudo systemctl stop mymcp
sudo cp /srv/backup/mymcp/<date>/tokens.json /etc/mymcp/tokens.json
sudo chmod 600 /etc/mymcp/tokens.json
sudo chown mymcp:mymcp /etc/mymcp/tokens.json
sudo systemctl start mymcp
```

### Recorder data

```bash
sudo systemctl stop mymcp
sudo cp -a /srv/backup/mymcp/<date>/recorder/. /var/lib/mymcp/recorder/
sudo chown -R mymcp:mymcp /var/lib/mymcp/recorder
sudo systemctl start mymcp
# The recorder resumes from the restored cursor — no re-bootstrap.
```

### Service config

```bash
sudo systemctl stop mymcp
sudo cp /srv/backup/mymcp/<date>/.env /etc/mymcp/.env
sudo chmod 600 /etc/mymcp/.env
sudo systemctl start mymcp
```

## Failure modes → response

| Symptom | Likely cause | Action |
|---|---|---|
| Admin endpoints all return 401 | `tokens.json` corrupted, missing, or restored to the wrong path | Restore from backup. If no backup, set a known `MYMCP_ADMIN_TOKEN` in `.env` and restart; `auth._save()` is atomic so partial-write corruption is unlikely after v2.x. |
| `mymcp_audit_write_failures_total` increasing | Audit log dir full, read-only, or rotation race | `df` the partition, check perms on `MYMCP_AUDIT_LOG_DIR`. Tool calls return `InternalError` until resolved. |
| `server_overview` says "circuit breaker open" | Recorder LLM has failed `MYMCP_RECORDER_CIRCUIT_BREAKER_THRESHOLD` consecutive times | Inspect logs (`recorder.supervisor.circuit_open`). Recovery is **event-driven** since v2.x — the next mutating tool call triggers one retry. No restart needed. |
| Recorder banner says "N events pending; merge stalled for X minutes" | Pending backlog accumulating with no successful merges | Check the LLM provider, network, API key. Once unblocked, the next event triggers a retry automatically. |
| Service won't start after upgrade | New required `MYMCP_*` setting missing | Run `mymcp doctor`; diff against current `.env.example`. |
| `audit_log_dir not writable` in startup logs | Misconfigured `MYMCP_AUDIT_LOG_DIR` or wrong fs perms | Confirm the path is writable by the service user; fix and restart. |
| Process exits seconds after start with no logs | systemd `NoNewPrivileges` interacting with a custom `ExecStart` that uses setuid | Inspect with `systemctl status mymcp` — `NoNewPrivileges` blocks setuid in children; revert the override if intentional. |

## Not covered by this guide

- Prometheus / Grafana retention (separate concern)
- OTLP trace storage retention (sink-side)
- The pipx / pip installation itself (reinstall via `pipx install algony-mymcp`)
- Backups of arbitrary user files the LLM has written via mymcp — that's
  user data, not mymcp state.
