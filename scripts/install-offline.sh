#!/usr/bin/env bash
# Run from inside an unpacked mymcp-<ver>-offline-bundle/ directory.
# Installs mymcp + deps from the bundled wheels and places the matching
# ripgrep binary into /usr/local/bin.
set -euo pipefail

BUNDLE_DIR="$(cd "$(dirname "$0")" && pwd)"
WHEELS="$BUNDLE_DIR/wheels"

if [ ! -d "$WHEELS" ]; then
    echo "error: $WHEELS not found. Are you running from inside the unpacked bundle?" >&2
    exit 1
fi

PIP=${PIP:-pip}
if ! command -v "$PIP" >/dev/null 2>&1; then
    echo "error: pip not found on PATH. Install python3 + pip first." >&2
    exit 1
fi

echo "installing mymcp from local wheels..."
"$PIP" install --no-index --find-links "$WHEELS" mymcp

ARCH=$(uname -m)
case "$ARCH" in
    x86_64)  RG="$BUNDLE_DIR/ripgrep-x86_64"  ;;
    aarch64) RG="$BUNDLE_DIR/ripgrep-aarch64" ;;
    *)       echo "warning: no bundled ripgrep for arch=$ARCH" >&2 ; RG="" ;;
esac

if [ -n "$RG" ] && [ -x "$RG" ]; then
    if [ "$(id -u)" -eq 0 ]; then
        cp "$RG" /usr/local/bin/rg
        chmod +x /usr/local/bin/rg
        echo "installed ripgrep -> /usr/local/bin/rg"
    else
        echo "ripgrep binary at $RG (re-run as root to install to /usr/local/bin)"
    fi
fi

echo
echo "Done. Next steps:"
echo "  sudo mymcp install-service --yes        # production"
echo "  mymcp serve                             # dev / quick try"
