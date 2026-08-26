#!/usr/bin/env bash
# mymcp installer. This script ONLY gets the binary onto the machine; every
# configuration decision belongs to `mymcp init`, which it exec's at the end.
#
#   curl -fsSL https://github.com/algony-tony/mymcp/releases/latest/download/install.sh | sudo bash
#   curl -fsSL … | sudo bash -s -- --yes --port 9000
set -euo pipefail

REPO_URL="https://github.com/algony-tony/mymcp/releases"
BASE_URL="${MYMCP_INSTALL_BASE_URL:-$REPO_URL/latest/download}"
METHOD="auto"
VERSION=""
BIN_DIR="/usr/local/bin"
INIT_ARGS=()

die() { echo "install.sh: $*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --method)  METHOD="$2"; shift 2 ;;
        --version) VERSION="$2"; shift 2 ;;
        --bin-dir) BIN_DIR="$2"; shift 2 ;;
        --help)
            echo "usage: install.sh [--method auto|pipx|binary] [--version X.Y.Z] [--bin-dir DIR] [mymcp init flags…]"
            exit 0 ;;
        *) INIT_ARGS+=("$1"); shift ;;   # everything else is for `mymcp init`
    esac
done

[ "$(uname -s)" = "Linux" ] || die "mymcp only runs on Linux (found $(uname -s))"
MACHINE="$(uname -m)"
case "$MACHINE" in
    x86_64)  ARCH="amd64" ;;
    aarch64) ARCH="arm64" ;;
    *) die "unsupported architecture: $MACHINE (mymcp ships amd64 and arm64)" ;;
esac

if [ -n "$VERSION" ] && [ -z "${MYMCP_INSTALL_BASE_URL:-}" ]; then
    BASE_URL="$REPO_URL/download/v$VERSION"
fi

fetch() {  # fetch <url> <dest>
    if have curl; then
        curl -fsSL "$1" -o "$2"
    elif have wget; then
        wget -qO "$2" "$1"
    else
        die "neither curl nor wget is available"
    fi
}

install_binary() {
    local tmp asset sums want got
    tmp="$(mktemp -d)"
    # shellcheck disable=SC2064  # expand tmp now, not at trap time
    trap "rm -rf '$tmp'" EXIT
    asset="mymcp-linux-$ARCH"

    echo "==> downloading $asset"
    fetch "$BASE_URL/$asset" "$tmp/$asset" || die "download failed: $BASE_URL/$asset"
    fetch "$BASE_URL/SHA256SUMS" "$tmp/SHA256SUMS" || die "could not fetch SHA256SUMS"

    sums="$(grep " $asset\$" "$tmp/SHA256SUMS" || true)"
    [ -n "$sums" ] || die "no checksum entry for $asset in SHA256SUMS"
    want="${sums%% *}"
    got="$(sha256sum "$tmp/$asset" | cut -d' ' -f1)"
    [ "$want" = "$got" ] || die "checksum mismatch for $asset (want $want, got $got)"
    echo "==> checksum verified"

    mkdir -p "$BIN_DIR"
    install -m 0755 "$tmp/$asset" "$BIN_DIR/mymcp"
    echo "==> installed $BIN_DIR/mymcp"
    export PATH="$BIN_DIR:$PATH"
}

install_pipx() {
    echo "==> installing via pipx"
    if pipx list 2>/dev/null | grep -q algony-mymcp; then
        pipx upgrade algony-mymcp
    elif [ -n "$VERSION" ]; then
        pipx install "algony-mymcp==$VERSION"
    else
        pipx install algony-mymcp
    fi
}

case "$METHOD" in
    pipx)   have pipx || die "--method pipx but pipx is not installed"; install_pipx ;;
    binary) install_binary ;;
    auto)   if have pipx; then install_pipx; else install_binary; fi ;;
    *) die "unknown --method: $METHOD" ;;
esac

have mymcp || die "mymcp is not on PATH after install; check $BIN_DIR"

echo "==> handing off to mymcp init"
exec mymcp init "${INIT_ARGS[@]}"
