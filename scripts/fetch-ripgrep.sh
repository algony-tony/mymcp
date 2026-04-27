#!/usr/bin/env bash
# Usage: fetch-ripgrep.sh BUNDLE_DIR
# Downloads ripgrep static linux binaries for x86_64 and aarch64
# into BUNDLE_DIR/ripgrep-{x86_64,aarch64}.
set -euo pipefail

DEST="${1:?missing BUNDLE_DIR}"
mkdir -p "$DEST"

TAG=$(curl -sI https://github.com/BurntSushi/ripgrep/releases/latest \
    | grep -i ^location: | sed 's|.*/||' | tr -d '\r\n')
echo "ripgrep tag: $TAG"

fetch() {
    local arch="$1" tarsuffix="$2"
    local out="$DEST/ripgrep-${arch}"
    local tarball="ripgrep-${TAG}-${tarsuffix}.tar.gz"
    local url="https://github.com/BurntSushi/ripgrep/releases/download/${TAG}/${tarball}"
    echo "fetching $url"
    local tmp; tmp=$(mktemp -d)
    curl -sL "$url" | tar xz -C "$tmp" --strip-components=1
    mv "$tmp/rg" "$out"
    chmod +x "$out"
    rm -rf "$tmp"
}

fetch x86_64  x86_64-unknown-linux-musl
fetch aarch64 aarch64-unknown-linux-gnu

ls -la "$DEST"/ripgrep-*
