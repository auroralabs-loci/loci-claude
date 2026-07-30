"""One user-facing version: the plugin's. The CLI's number stays internal.

The session context used to carry two numbers — "loci version:" (plugin) and
"loci command: loci (on PATH, v<cli>)" — so when asked "what is loci's
version" the model reported both and editorialized a "mismatch" (the CLI pins
independently by design). The context must announce the plugin version as THE
version and not mention the CLI's number at all.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent


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


needs_tools = pytest.mark.skipif(
    not (_find_bash() and shutil.which("jq")), reason="bash and jq required"
)


@needs_tools
def test_context_announces_only_plugin_version(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "Makefile").write_text("all:\n\ttrue\n")
    (project / "main.c").write_text("int main(){}")
    home = tmp_path / "home"
    home.mkdir()

    env = {
        **os.environ,
        "HOME": _to_bash_path(home),
        "LOCI_STATE_DIR": _to_bash_path(home / ".loci" / "state"),
    }
    res = subprocess.run(
        [_find_bash(), _to_bash_path(PLUGIN_ROOT / "hooks" / "session-init.sh")],
        env=env, cwd=project, capture_output=True, text=True, timeout=60,
        stdin=subprocess.DEVNULL,
    )
    assert res.returncode == 0, res.stderr
    ctx = json.loads(res.stdout)["hookSpecificOutput"]["additionalContext"]

    plugin_ver = json.loads(
        (PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text()
    )["version"]
    assert f"loci version: {plugin_ver}" in ctx
    assert "report exactly this number" in ctx

    # The old format leaked the CLI's own number ("on PATH, v0.1.x") as a
    # second answer to "what version is loci" — it must not reappear.
    assert "on PATH, v" not in ctx
    if shutil.which("loci"):
        cli_ver = re.sub(
            r"[^0-9.]", "",
            subprocess.run(["loci", "--version"], capture_output=True,
                           text=True, timeout=30).stdout,
        )
        if cli_ver and cli_ver != plugin_ver:
            assert cli_ver not in ctx, (
                "CLI version leaked into the session context."
            )
