"""The pinned loci CLI version must exist on PyPI.

``lib/setup-steps.sh`` pins an exact ``loci-tools==<version>``, so shipping a
plugin release whose pin was never published leaves every user's
``uv tool install`` failing with no resolution — the plugin works, analysis
doesn't. Guard the release, not the runtime.

Deliberately NOT a monotonicity check: a new plugin version pinning an older CLI
is a legitimate rollback (see test_cli_pin_resolution.py).

Network-gated: set LOCI_TEST_NETWORK=1 (CI release job) to run it.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

import pytest


PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
SETUP_STEPS = PLUGIN_ROOT / "lib" / "setup-steps.sh"


def _pin() -> tuple[str, str]:
    text = SETUP_STEPS.read_text(encoding="utf-8")
    ver = re.search(r'^LOCI_CLI_VERSION="([^"]+)"', text, flags=re.MULTILINE)
    pkg = re.search(r'^LOCI_CLI_PACKAGE="([^"]+)"', text, flags=re.MULTILINE)
    assert ver and pkg, f"Could not find the CLI pin constants in {SETUP_STEPS}"
    return pkg.group(1), ver.group(1)


def test_pin_constants_are_wellformed():
    """Runs offline — a malformed pin breaks the sed read in the stale-hook path."""
    pkg, ver = _pin()
    assert re.fullmatch(r"[0-9]+(\.[0-9]+)*", ver), (
        f"LOCI_CLI_VERSION={ver!r} must be dotted-numeric: the authoritative-pin "
        "read in _loci_resolve_install_spec matches [0-9.] only, so anything else "
        "(a suffix, a range specifier) makes a stale hook silently fall back."
    )
    assert pkg == "loci-tools", f"unexpected package name {pkg!r}"


@pytest.mark.skipif(
    os.environ.get("LOCI_TEST_NETWORK") != "1",
    reason="network test — set LOCI_TEST_NETWORK=1",
)
def test_pinned_cli_version_is_published():
    pkg, ver = _pin()
    url = f"https://pypi.org/pypi/{pkg}/json"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.load(resp)
    except (urllib.error.URLError, TimeoutError) as exc:
        pytest.skip(f"PyPI unreachable: {exc}")

    releases = data.get("releases", {})
    assert ver in releases and releases[ver], (
        f"{pkg}=={ver} is pinned in lib/setup-steps.sh but not published on PyPI. "
        f"Publish the CLI before tagging the plugin. Latest published: "
        f"{data.get('info', {}).get('version')!r}"
    )
