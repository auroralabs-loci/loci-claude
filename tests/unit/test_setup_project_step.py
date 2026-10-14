"""setup.sh's last step must survive a `loci project` that resolves no project.

In a repo with no recipe the CLI answers `not_resolved`, exit 1, and the envelope
carries no `project_root`. Under `set -euo pipefail` the unguarded read of that
field ended the script with exit 1 before "Setup complete!" (AAD-7800).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent

NOT_RESOLVED = """{
  "ok": false,
  "error": {
    "message": "LOCI cannot tell which project this call is about.",
    "code": "not_resolved",
    "hints": []
  }
}"""


def _find_bash() -> str | None:
    if sys.platform == "win32":
        for cand in (
            r"C:\Program Files\Git\usr\bin\bash.exe",
            r"C:\Program Files (x86)\Git\usr\bin\bash.exe",
        ):
            if Path(cand).is_file():
                return cand
    return shutil.which("bash")


def _to_bash_path(p: Path) -> str:
    s = p.as_posix()
    m = re.match(r"^([A-Za-z]):/(.*)$", s)
    if m:
        return f"/{m.group(1).lower()}/{m.group(2)}"
    return s


def _stub(bin_dir: Path, name: str, body: str) -> None:
    f = bin_dir / name
    f.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8", newline="\n")
    f.chmod(0o755)


@pytest.mark.skipif(_find_bash() is None, reason="bash not available")
def test_setup_completes_when_loci_project_is_not_resolved(tmp_path):
    home = tmp_path / "home"
    # Staged under a plugin cache so step 6 takes plugin mode and writes no settings.json.
    plugin = home / ".claude" / "plugins" / "cache" / "loci" / "loci" / "9.9.9"
    for sub in ("setup", "lib", "hooks"):
        shutil.copytree(PLUGIN_ROOT / sub, plugin / sub,
                        ignore=shutil.ignore_patterns("__pycache__"))

    # augment_path moves the uv shim dir to the front of PATH, so these win the lookup.
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    _stub(bin_dir, "uv", "exit 0")
    _stub(bin_dir, "loci",
          f'[ "$1" = project ] || exit 0\ncat <<\'EOF\'\n{NOT_RESOLVED}\nEOF\nexit 1')

    env = {k: v for k, v in os.environ.items()
           if k not in ("UV_TOOL_BIN_DIR", "XDG_BIN_HOME", "LOCI_STATE_DIR")}
    env["HOME"] = _to_bash_path(home)

    r = subprocess.run(
        [_find_bash(), _to_bash_path(plugin / "setup" / "setup.sh")],
        env=env, capture_output=True, text=True, timeout=60, cwd=tmp_path,
    )

    assert r.returncode == 0, r.stdout + r.stderr
    assert "Setup complete!" in r.stdout
    assert "Project:" not in r.stdout
