#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$REPO_ROOT"

distro="${1:-debian}"
scenario="${2:-scenario_fresh_upgrade.sh}"

case "$distro" in
    debian) image_tag=mymcp-test-debian ;;
    rocky)  image_tag=mymcp-test-rocky ;;
    *) echo "Usage: $0 {debian|rocky} [scenario.sh]" >&2; exit 2 ;;
esac

docker build -t "$image_tag" -f "$SCRIPT_DIR/Dockerfile.$distro" "$SCRIPT_DIR"

docker run --rm \
    -v "$REPO_ROOT:/src:ro" \
    -v "$SCRIPT_DIR:/tests/integration:ro" \
    "$image_tag" \
    bash "/tests/integration/$scenario"

echo "OK: $distro / $scenario"
