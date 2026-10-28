"""The plan-mode preflight reminder reaches the host (AAD-7880).

QA's two Copilot sessions (claude-sonnet-5.5, Copilot CLI 1.0.97, plugin
0.3.20) sent a `[[PLAN]]` prompt describing new C logic in the BLE project, a
recipe in place, and the model explored, asked, wrote `plan.md`, left plan mode
and edited — never loading `loci-preflight`. `loci-post-edit` ran both times,
because the post-edit hook names it on the edit itself. The session block's
auto-run rule, stated once at SessionStart, was not enough.

So `loci hook prompt-submit` now answers a plan-mode prompt in a project a
recipe governs with a `[loci] plan mode:` reminder under the `[loci] turn=`
line, and this module pins its DELIVERY through `hooks/prompt-submit-turn.sh`
with the real installed CLI — the thin hook pipes stdin to the verb unread, so
what the host receives is the verb's document, in the envelope each host reads:
under GitHub Copilot CLI the top-level `additionalContext` (the nested one is
dropped there, AAD-7783), under Claude Code the nested one.

Plan mode is read the host's way: Copilot's `[[PLAN]]` prefix on `prompt`
(probed on 1.0.98 — the hook sees the same text the model sees), Claude Code's
`permission_mode`. The CLI's own suite pins the rule's edges (each signal under
its host alone, no recipe, a failing check); this one pins what the plugin
ships. It runs against the INSTALLED `loci`, which must be at the plugin's pin
(`LOCI_CLI_VERSION` in lib/setup-steps.sh) — an older CLI emits the turn line
alone, and that is a red here, not a skip, the way every installed-CLI test in
this suite reads.
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
HOOK = PLUGIN_ROOT / "hooks" / "prompt-submit-turn.sh"
SKILL = PLUGIN_ROOT / "skills" / "loci-preflight" / "SKILL.md"

REMINDER_LEAD = "[loci] plan mode:"


def _find_bash() -> str | None:
    if sys.platform == "win32":
        for cand in (
            r"C:\Program Files\Git\usr\bin\bash.exe",
            r"C:\Program Files (x86)\Git\usr\bin\bash.exe",
        ):
            if Path(cand).is_file():
                return cand
    return shutil.which("bash")


from tests.fixtures.copilot_payloads import current as _host  # noqa: E402

pytestmark = [
    pytest.mark.skipif(_find_bash() is None or shutil.which("loci") is None,
                       reason="bash and an installed loci required"),
    pytest.mark.usefixtures("host"),
]


def _to_bash_path(p: Path) -> str:
    s = Path(p).as_posix()
    m = re.match(r"^([A-Za-z]):/(.*)$", s)
    return f"/{m.group(1).lower()}/{m.group(2)}" if m else s


def _governed(root: Path) -> Path:
    (root / ".loci").mkdir(parents=True, exist_ok=True)
    (root / ".loci" / "build.yaml").write_text("version: 1\n", encoding="utf-8")
    return root


def _run(project: Path, home: Path, claude_payload: dict) -> dict | None:
    """One prompt through the real hook and the real CLI; the host's document."""
    loci_dir = Path(shutil.which("loci")).parent
    env = {
        "PATH": f"{_to_bash_path(loci_dir)}:/usr/bin:/bin:/usr/local/bin",
        "HOME": _to_bash_path(home),
        "LOCI_STATE_DIR": _to_bash_path(home / ".loci" / "state"),
        "CLAUDE_PROJECT_DIR": _to_bash_path(project),
        "_LOCI_BOOTSTRAP": "1",
        # The CLI reads Windows locations from these when HOME is a fake.
        **{k: v for k, v in os.environ.items()
           if k in ("SYSTEMROOT", "SystemRoot", "TEMP", "TMP", "USERPROFILE",
                    "LOCALAPPDATA", "APPDATA", "PATHEXT", "COMSPEC")},
    }
    host = _host()
    payload = host.respell({"cwd": _to_bash_path(project), "prompt_id": "t-7880",
                            **claude_payload}, event="UserPromptSubmit")
    env.update(host.env(project=_to_bash_path(project)))
    host.seed(home / ".loci" / "state")
    proc = subprocess.run([_find_bash(), _to_bash_path(HOOK)], input=json.dumps(payload),
                          capture_output=True, text=True, timeout=120, env=env,
                          cwd=host.cwd(None))
    assert proc.returncode == 0, f"the hook must exit 0; got {proc.returncode}: {proc.stderr!r}"
    out = proc.stdout.strip()
    return json.loads(out) if out else None


def _plan(claude_payload: dict) -> dict:
    """The same request as the host says plan mode: Copilot's prefix on the
    prompt, Claude Code's `permission_mode`."""
    doc = dict(claude_payload)
    if _host().copilot:
        doc["prompt"] = "[[PLAN]] " + doc["prompt"]
    else:
        doc["permission_mode"] = "plan"
    return doc


def _context(out: dict) -> str:
    nested = out["hookSpecificOutput"]["additionalContext"]
    if _host().copilot:
        assert out["additionalContext"] == nested, "Copilot reads the top-level copy"
    else:
        assert "additionalContext" not in out
    return nested


PROMPT = {"prompt": "add a bounds check before the table lookup in parse_frame"}


def test_a_plan_prompt_in_a_governed_project_carries_the_reminder(tmp_path):
    project = _governed(tmp_path / "proj")
    out = _run(project, tmp_path / "home", _plan(PROMPT))
    ctx = _context(out)
    lines = ctx.split("\n")
    assert lines[0].startswith("[loci] turn="), ctx
    assert lines[1].startswith(REMINDER_LEAD), (
        f"no reminder under the turn line — is the installed loci at the plugin's "
        f"pin (lib/setup-steps.sh LOCI_CLI_VERSION)?\n{ctx}")
    assert "loci:loci-preflight" in lines[1] and "MUST" in lines[1]
    assert "before the plan is written" in lines[1]


def test_the_same_words_outside_plan_mode_get_the_turn_line_alone(tmp_path):
    project = _governed(tmp_path / "proj")
    out = _run(project, tmp_path / "home", PROMPT)
    ctx = _context(out)
    assert ctx.startswith("[loci] turn=") and "\n" not in ctx, ctx
    assert REMINDER_LEAD not in ctx


def test_a_plan_prompt_where_no_recipe_governs_gets_the_turn_line_alone(tmp_path):
    """The model is never sent to a skill that stops at `not_initialized`."""
    project = tmp_path / "proj"
    project.mkdir()
    out = _run(project, tmp_path / "home", _plan(PROMPT))
    ctx = _context(out)
    assert ctx.startswith("[loci] turn=") and REMINDER_LEAD not in ctx, ctx


def test_the_skill_gate_names_the_reminder_as_the_second_signal():
    """The reminder's lead is the skill's own word for it: a hook line the
    model has never been told about is noise, one the gate names is the cue."""
    text = SKILL.read_text(encoding="utf-8").replace("\r\n", "\n")
    gate = text[text.index("**Gate: plan mode only.**"):text.index("## Tool boundary")]
    flat = re.sub(r"\s+", " ", gate)
    assert "the prompt-submit hook says so too, in a `[loci] plan mode:` line under the turn id" in flat
    assert "invoke this skill when that line arrives, before the plan is written" in flat
    # The gate's negative half is untouched (AAD-7803's pins hold it too).
    assert "If plan mode is off, stop here, whatever the request says" in flat
