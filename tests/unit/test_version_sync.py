"""Plugin version must match in pyproject.toml, plugin.json and uv.lock.

The manifest is what the Claude Code marketplace reads and what session-init.sh
uses to detect upgrades; if it drifts from the Python package version, the
marketplace and package disagree and upgrade detection breaks.

`uv.lock` joined this check late, and the reason it is here is what it drifted:
it recorded 0.1.130 from that release through 0.1.131, 0.1.132, 0.1.133 and
0.1.134, because this file looked at two of the three places a version lives and
nothing looked at the third. The stakes are lower than the manifest's — the
plugin is a `virtual` project, so nothing reads the lock's own version field and
nothing broke — but a tracked file stating a version the repo left behind four
releases ago is a claim that has to be re-checked by hand every time someone
wonders whether it matters. It cost exactly that during the 0.1.134 train. The
check is two lines; the drift is now impossible instead of merely harmless.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
PYPROJECT = PLUGIN_ROOT / "pyproject.toml"
PLUGIN_MANIFEST = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
UV_LOCK = PLUGIN_ROOT / "uv.lock"


def _pyproject_version() -> str:
    """Extract `version = "..."` from the top-level `[project]` table.

    Hand-rolled instead of `tomllib` so the test runs identically on
    Python 3.10/3.11 wheels too; the regex is anchored to the
    `[project]` table to avoid matching a `version =` line in some
    other table (e.g. a tool-specific subtable).
    """
    text = PYPROJECT.read_text(encoding="utf-8")
    m = re.search(
        r"\[project\][^\[]*?^\s*version\s*=\s*\"([^\"]+)\"",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert m, f"Could not find version in [project] table of {PYPROJECT}"
    return m.group(1)


def _plugin_manifest_version() -> str:
    data = json.loads(PLUGIN_MANIFEST.read_text(encoding="utf-8"))
    version = data.get("version")
    assert isinstance(version, str) and version, (
        f"plugin.json is missing a string `version` field: {PLUGIN_MANIFEST}"
    )
    return version


def _uv_lock_version() -> str:
    """The `loci` entry's version in `uv.lock`.

    Anchored to `name = "loci"` exactly: the lock also holds
    `loci-service-asmslicer`, and a looser pattern would happily read the
    version of whichever package sorted first.
    """
    text = UV_LOCK.read_text(encoding="utf-8")
    m = re.search(r'^name = "loci"\s*\nversion = "([^"]+)"', text,
                  flags=re.MULTILINE)
    assert m, f"Could not find the `loci` package entry in {UV_LOCK}"
    return m.group(1)


def test_the_lock_records_the_version_the_project_declares():
    py = _pyproject_version()
    locked = _uv_lock_version()
    assert py == locked, (
        f"Version drift between pyproject.toml ({py}) and uv.lock ({locked}). "
        f"`uv lock` re-records it; this is the check that was missing while the "
        f"lock sat at 0.1.130 for four releases."
    )


def test_pyproject_and_plugin_manifest_versions_match():
    py = _pyproject_version()
    manifest = _plugin_manifest_version()
    assert py == manifest, (
        f"Version drift between pyproject.toml ({py}) and "
        f"{PLUGIN_MANIFEST.relative_to(PLUGIN_ROOT)} ({manifest}). "
        "Every release must bump both files in lockstep — the manifest "
        "is what the Claude Code marketplace reads and what "
        "session-init.sh uses to detect upgrades."
    )
