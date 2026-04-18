#!/usr/bin/env bash
# Source-guarded helper for Docker integration scenarios.

# Stub systemctl so install.sh / upgrade.sh can call daemon-reload / enable /
# start / stop inside a non-systemd container.
cat > /usr/local/bin/systemctl <<'STUB'
#!/usr/bin/env bash
echo "[mock-systemctl] $*" >&2
exit 0
STUB
chmod +x /usr/local/bin/systemctl

# Bootstrap a test repo from /src with synthetic v-test-old and v-test-new
# tags. Exports WORKDIR (test repo root at v-test-old) and REMOTE_SRC
# (separate tree at v-test-new, used as upgrade source).
#
# This avoids relying on a real prior tag (like v1.0.0) whose code predates
# the upgrade tooling.
bootstrap_test_repo() {
    WORKDIR=$(mktemp -d)
    cp -r /src "$WORKDIR/repo"
    cd "$WORKDIR/repo"
    git config user.email ci@local
    git config user.name ci
    git tag -f v-test-old HEAD
    echo "new-marker-$(date +%s)" > .new-marker
    git add .new-marker
    git commit -q -m "test: upgrade target"
    git tag -f v-test-new HEAD

    REMOTE_SRC="$WORKDIR/remote"
    cp -r "$WORKDIR/repo" "$REMOTE_SRC"
    git -C "$REMOTE_SRC" checkout -q v-test-new

    git checkout -q v-test-old
    export WORKDIR REMOTE_SRC
}
