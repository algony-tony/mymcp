#!/usr/bin/env bash
# Source-guarded helper for Docker integration scenarios.
# Installs a stub systemctl on PATH so install.sh / upgrade.sh can call
# daemon-reload / enable / start / stop inside a non-systemd container.
cat > /usr/local/bin/systemctl <<'STUB'
#!/usr/bin/env bash
echo "[mock-systemctl] $*" >&2
exit 0
STUB
chmod +x /usr/local/bin/systemctl
