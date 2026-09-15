"""The UserPromptSubmit hook that stamps the current turn's id.

`hooks/prompt-submit-turn.sh` is deliberately thin, same shape as
`manifest-status-nudge.sh`: it fails open with a one-line notice when `loci` is
missing, and otherwise pipes stdin straight into `loci hook prompt-submit`,
which does the actual JSON parsing and atomic stamp. So this test fakes `loci`
itself rather than any state on disk.

Two things are load-bearing:
* **It must never exit 2.** On `UserPromptSubmit` a nonzero/blocking exit
  interferes with the user's prompt.
* **stdin passes through unmangled** — no jq, no consuming `cat` in between.
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
HOOK = PLUGIN_ROOT / "hooks" / "prompt-submit-turn.sh"


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


def _run(project_dir: Path, *, fake_loci: str | None = None,
          prompt_id: str = "t-1") -> tuple[int, dict | None]:
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

    proc = subprocess.run(
        [_find_bash(), _to_bash_path(HOOK)],
        input=json.dumps({"cwd": _to_bash_path(project_dir), "prompt_id": prompt_id}),
        capture_output=True, text=True, timeout=30, env=env,
    )
    out = proc.stdout.strip()
    if not out:
        return proc.returncode, None
    try:
        return proc.returncode, json.loads(out)
    except json.JSONDecodeError:
        return proc.returncode, out


# ── the missing-`loci` case: fail-open, but visible ──────────────────────────

def test_absent_loci_fails_open_with_a_one_line_notice(tmp_path):
    code, out = _run(tmp_path)  # no stub on PATH
    assert code == 0
    assert set(out) == {"systemMessage"}
    assert "loci" in out["systemMessage"].lower()
    assert "not found" in out["systemMessage"].lower()


# ── the happy path: the CLI's hook document rides straight through ──────────

def test_the_verbs_output_reaches_the_harness_unmangled(tmp_path):
    doc = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                  "additionalContext": "[loci] turn=t-1"}}
    code, out = _run(tmp_path, fake_loci=(
        f"cat >/dev/null; printf '%s\\n' '{json.dumps(doc)}'"))
    assert code == 0
    assert out == doc


# ── stdin passes through unmangled (no jq, no consuming `cat` in between) ───

def test_the_harness_payload_reaches_loci_on_stdin(tmp_path):
    seen = tmp_path / "seen.json"
    code, out = _run(tmp_path, fake_loci=f"cat > {_to_bash_path(seen)}",
                      prompt_id="t-9")
    assert code == 0 and out is None
    assert json.loads(seen.read_text(encoding="utf-8"))["prompt_id"] == "t-9"


# ── the loop hazard: on UserPromptSubmit a blocking exit is worse than Stop ─

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
