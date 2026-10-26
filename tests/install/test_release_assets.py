"""The asset names install.sh fetches and the ones CI uploads must agree.

A rename on either side turns the headline install command into a 404, which
no unit test elsewhere would catch.
"""

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "release.yml"
SCRIPT = REPO / "scripts" / "install.sh"


def _release_files() -> str:
    data = yaml.safe_load(WORKFLOW.read_text())
    blobs = []
    for job in data["jobs"].values():
        for step in job.get("steps", []):
            if "action-gh-release" in str(step.get("uses", "")):
                blobs.append(str(step.get("with", {}).get("files", "")))
    assert blobs, "no action-gh-release step found"
    return "\n".join(blobs)


def test_raw_binaries_are_uploaded():
    files = _release_files()
    for name in ("mymcp-linux-amd64", "mymcp-linux-arm64"):
        assert name in files, f"{name} is built by CI but never uploaded"


def test_checksums_and_installer_are_uploaded():
    files = _release_files()
    assert "SHA256SUMS" in files, "install.sh refuses to install without checksums"
    assert "install.sh" in files, "the one-liner fetches install.sh from the release"


def test_install_sh_asset_names_match_the_workflow():
    script = SCRIPT.read_text()
    asset = re.search(r'asset="mymcp-linux-\$ARCH"', script)
    assert asset, "install.sh no longer builds the expected asset name"
    for arch in ("amd64", "arm64"):
        assert f"mymcp-linux-{arch}" in _release_files()
