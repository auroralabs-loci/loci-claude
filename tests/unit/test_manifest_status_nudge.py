"""The Stop-hook turn-end manifest check.

`hooks/manifest-status-nudge.sh` is deliberately thin: it fails open when `loci`
is missing (with a one-line notice, not silence — see .local/docs/open-
questions.md "Hooks without jq") and otherwise `exec`s straight into
`loci analyse status --turn --hook-json`, which does the actual reading. So this
test fakes `loci` itself rather than any state on disk.

Two things are load-bearing, shared with `draft-pending-nudge.sh`:
* **It must never exit 2.** On `Stop` that blocks the stop and continues the
  conversation, an infinite loop for a hook that runs every turn.
* **Silence on the clean turn.** A fully patched run must print nothing.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
NUDGE = PLUGIN_ROOT / "hooks" / "manifest-status-nudge.sh"


def _find_bash() -> str | None:
    if sys.platform == "win32":
        for cand in (
            r"C:\Program Files\Git\usr\bin\bash.exe",
            r"C:\Program Files (x86)\Git\usr\bin\bash.exe",
        ):
            if Path(cand).is_file():
                return cand
    return shutil.which("bash")


pytestmark = pytest.mark.skipif(_find_bash() is None, reason="bash required")


def _to_bash_path(p: Path) -> str:
    s = Path(p).as_posix()
    m = re.match(r"^([A-Za-z]):/(.*)$", s)
    return f"/{m.group(1).lower()}/{m.group(2)}" if m else s


def _run(project_dir: Path, *, fake_loci: str | None = None) -> tuple[int, dict | None]:
    """Run the hook; return (exit code, its JSON output or None if silent).

    HOME is an empty scratch directory, not the real one: the hook appends
    `$HOME/.local/bin` to PATH (same as `draft-pending-nudge.sh`), and this
    machine has a real, older `loci` installed there — which would otherwise
    make the "absent loci" case impossible to reach.
    """
    home = project_dir / "_fakehome"
    home.mkdir(exist_ok=True)
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(home),
        "CLAUDE_PROJECT_DIR": _to_bash_path(project_dir),
    }
    if fake_loci is not None:
        bin_dir = project_dir / "_fakebin"
        bin_dir.mkdir(exist_ok=True)
        stub = bin_dir / "loci"
        stub.write_text(f"#!/usr/bin/env bash\n{fake_loci}\n", encoding="utf-8")
        stub.chmod(0o755)
        env["PATH"] = f"{_to_bash_path(bin_dir)}:{env['PATH']}"

    # `encoding="utf-8"`: the hook's contract is UTF-8 (`PYTHONIOENCODING=utf-8`,
    # the `systemMessage` carries an em-dash), and `text=True` alone decodes with
    # the locale codec -- cp1252 on Windows, where `—` arrived as `â€”` and this
    # test was red on every Windows clone.
    proc = subprocess.run(
        [_find_bash(), _to_bash_path(NUDGE)],
        input=json.dumps({"cwd": _to_bash_path(project_dir), "prompt_id": "t-1"}),
        capture_output=True, text=True, encoding="utf-8", timeout=30, env=env,
    )
    out = proc.stdout.strip()
    if not out:
        return proc.returncode, None
    try:
        return proc.returncode, json.loads(out)
    except json.JSONDecodeError:
        return proc.returncode, out


# ── silence, which is the common case (a fully patched turn) ────────────────

def test_a_clean_turn_is_silent(tmp_path):
    # A `loci` that reads its own stdin and finds nothing to report — the CLI
    # is the one deciding "nothing outstanding", not this script.
    code, out = _run(tmp_path, fake_loci="cat >/dev/null; exit 0")
    assert code == 0 and out is None


# ── the missing-`loci` case: fail-open, but visible ──────────────────────────

def test_absent_loci_fails_open_with_a_one_line_notice(tmp_path):
    code, out = _run(tmp_path)  # no stub on PATH
    assert code == 0
    assert set(out) == {"systemMessage"}
    assert "loci" in out["systemMessage"].lower()
    assert "not found" in out["systemMessage"].lower()


# ── the outstanding case: the CLI's systemMessage rides straight through ────

def test_an_outstanding_manifest_reaches_the_user(tmp_path):
    msg = "LOCI: turn-end check — prepared, never measured: m-abc123"
    code, out = _run(tmp_path, fake_loci=(
        f'cat >/dev/null; printf %s\'\\n\' \'{{"systemMessage":"{msg}"}}\''))
    assert code == 0
    assert out == {"systemMessage": msg}


# ── stdin passes through unmangled (no jq, no consuming `cat` in between) ───

def test_the_harness_payload_reaches_loci_on_stdin(tmp_path):
    seen = tmp_path / "seen.json"
    code, out = _run(tmp_path, fake_loci=f'cat > {_to_bash_path(seen)}')
    assert code == 0 and out is None
    assert json.loads(seen.read_text(encoding="utf-8"))["prompt_id"] == "t-1"


# ── the loop hazard ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("loci", [
    None,
    "exit 1",
    "exit 2",
    "cat >/dev/null; echo not-json",
    "kill -TERM $$",
])
def test_never_exits_two_whatever_happens(tmp_path, loci):
    code, _ = _run(tmp_path, fake_loci=loci)
    assert code == 0, f"must exit 0, got {code}"
