"""The Stop-hook nudge for an unapplied contract draft.

A drafted bound the user never applied is a requirement nobody set. The skill can
only mention it in the turn that drafted it, so this hook re-raises it from the
draft *file* every turn until `contract accept` consumes it or `draft clear`
deletes it.

Three things are load-bearing, and each is a way the hook could be worse than
absent:

* **It must never exit 2.** On `Stop` that blocks the stop and continues the
  conversation, so a pending draft would spin forever.
* **The message must ride in `systemMessage`.** Plain stdout on `Stop` goes to the
  debug log, where a nudge nobody sees is the same as no nudge.
* **Silence on the common turn.** No draft means no output and no `loci` spawn —
  this runs at the end of every single turn.
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
NUDGE = PLUGIN_ROOT / "hooks" / "draft-pending-nudge.sh"


def _find_bash() -> str | None:
    if sys.platform == "win32":
        for cand in (
            r"C:\Program Files\Git\usr\bin\bash.exe",
            r"C:\Program Files (x86)\Git\usr\bin\bash.exe",
        ):
            if Path(cand).is_file():
                return cand
    return shutil.which("bash")


pytestmark = pytest.mark.skipif(
    _find_bash() is None or shutil.which("jq") is None,
    reason="bash and jq required",
)


def _to_bash_path(p: Path) -> str:
    s = Path(p).as_posix()
    m = re.match(r"^([A-Za-z]):/(.*)$", s)
    return f"/{m.group(1).lower()}/{m.group(2)}" if m else s


def _run(project_dir: Path, *, fake_loci: str | None = None) -> tuple[int, dict | None]:
    """Run the hook; return (exit code, its JSON output or None if silent)."""
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(Path.home()),
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
        [_find_bash(), _to_bash_path(NUDGE)],
        input=json.dumps({"cwd": _to_bash_path(project_dir), "session_id": "t"}),
        capture_output=True, text=True, timeout=30, env=env,
    )
    out = proc.stdout.strip()
    return proc.returncode, (json.loads(out) if out else None)


def _draft(root: Path) -> Path:
    d = root / ".loci-build"
    d.mkdir(parents=True, exist_ok=True)
    path = d / "contract.draft.yaml"
    path.write_text("version: 1\nops: []\n", encoding="utf-8")
    return path


def _envelope(pending: int, stale: bool = False, ops: list[str] | None = None) -> str:
    """A `draft show` envelope. `ops` omitted means an older CLI that does not
    report the op kinds — the hook must then say how many, never what they do."""
    data: dict = {"pending": pending, "stale": stale}
    if ops is not None:
        data["ops"] = [{"op": op, "index": i} for i, op in enumerate(ops)]
    return f"cat <<'EOF'\n{json.dumps({'ok': True, 'data': data})}\nEOF"


# ── silence, which is the common case ───────────────────────────────────────

def test_no_draft_is_silent_and_never_spawns_loci(tmp_path):
    # A `loci` that fails the test if called at all: the cheap file check must
    # come first, because this hook runs at the end of every turn.
    code, out = _run(tmp_path, fake_loci="echo CALLED >&2; exit 1")
    assert code == 0 and out is None


def test_an_empty_draft_says_nothing(tmp_path):
    _draft(tmp_path)
    code, out = _run(tmp_path, fake_loci=_envelope(0))
    assert code == 0 and out is None


def test_a_failing_loci_is_silent_rather_than_noisy(tmp_path):
    _draft(tmp_path)
    code, out = _run(tmp_path, fake_loci='echo "{\\"ok\\":false}"')
    assert code == 0 and out is None


def test_absent_loci_is_silent(tmp_path):
    _draft(tmp_path)
    code, out = _run(tmp_path)  # no stub on PATH
    assert code == 0 and out is None


# ── the nudge ───────────────────────────────────────────────────────────────

def test_pending_draft_reaches_the_user_via_system_message(tmp_path):
    _draft(tmp_path)
    code, out = _run(tmp_path, fake_loci=_envelope(2, ops=["add", "add"]))
    assert code == 0
    # `systemMessage` is the only field a Stop hook shows the user. If this ever
    # becomes plain stdout or additionalContext, the nudge is invisible again.
    assert set(out) == {"systemMessage"}
    msg = out["systemMessage"]
    assert "2 bounds added" in msg
    assert "! loci contract accept" in msg


# ── it must say what the draft DOES, not how many ops it has ────────────────
#
# Counting ops and calling them "entries" told the user something was added while
# `accept` was about to retire a bound. The verb is the whole point of the line.

def test_two_disable_ops_are_not_announced_as_additions(tmp_path):
    _draft(tmp_path)
    _, out = _run(tmp_path, fake_loci=_envelope(2, ops=["disable", "disable"]))
    msg = out["systemMessage"]
    assert "2 bounds retired" in msg
    assert "added" not in msg
    assert "entries" not in msg


@pytest.mark.parametrize("ops,expected", [
    (["add"], "1 bound added"),
    (["edit"], "1 bound changed"),
    (["disable"], "1 bound retired"),
    (["enable"], "1 bound restored"),
    (["add", "add"], "2 bounds added"),
    # The noun rides on the first clause only; every kind keeps its own verb.
    (["add", "disable", "disable"], "1 bound added, 2 retired"),
    (["add", "edit", "disable", "enable"], "1 bound added, 1 changed, 1 retired, 1 restored"),
])
def test_each_op_kind_gets_its_own_verb(tmp_path, ops, expected):
    _draft(tmp_path)
    _, out = _run(tmp_path, fake_loci=_envelope(len(ops), ops=ops))
    assert expected in out["systemMessage"]


def test_an_older_cli_without_op_kinds_falls_back_to_a_bare_count(tmp_path):
    """No `ops` in the envelope means the hook cannot know the verbs. A bare count
    is honest; guessing "added" is the defect this whole group exists for."""
    _draft(tmp_path)
    _, out = _run(tmp_path, fake_loci=_envelope(2))
    msg = out["systemMessage"]
    assert "2 changes" in msg
    for wrong in ("bound", "added", "retired", "entries"):
        assert wrong not in msg
    _, out = _run(tmp_path, fake_loci=_envelope(1))
    assert "1 change" in out["systemMessage"]


def test_an_unknown_op_kind_falls_back_rather_than_undercount(tmp_path):
    """A kind added to the CLI after this hook was written must not silently drop
    out of the summary — `1 bound added` next to a pending count of 2 is worse than
    saying `2 changes`."""
    _draft(tmp_path)
    _, out = _run(tmp_path, fake_loci=_envelope(2, ops=["add", "reorder"]))
    msg = out["systemMessage"]
    assert "2 changes" in msg
    assert "added" not in msg


def test_a_stale_draft_says_it_cannot_be_applied(tmp_path):
    # Handing the user `accept` here would send them at a command that refuses.
    _draft(tmp_path)
    _, out = _run(tmp_path, fake_loci=_envelope(1, stale=True))
    msg = out["systemMessage"]
    assert "re-draft" in msg
    assert "! loci contract accept" not in msg


# ── the loop hazard ─────────────────────────────────────────────────────────

def test_a_malformed_ops_field_falls_back_instead_of_breaking(tmp_path):
    # The summary is computed in jq; a shape it cannot walk must not take the
    # nudge down with it, and must not emit half a sentence.
    _draft(tmp_path)
    code, out = _run(tmp_path, fake_loci=(
        'cat <<\'EOF\'\n{"ok":true,"data":{"pending":2,"stale":false,"ops":"nope"}}\nEOF'))
    assert code == 0
    assert "2 changes" in out["systemMessage"]
    assert "! loci contract accept" in out["systemMessage"]


@pytest.mark.parametrize("loci", [
    None,
    "exit 1",
    "echo not-json",
    _envelope(3),
    _envelope(3, stale=True),
    _envelope(3, ops=["add", "disable", "enable"]),
    "echo '{\"ok\":true,\"data\":{\"pending\":1,\"ops\":\"nope\"}}'",
    "echo '{\"ok\":true,\"data\":{}}'",
    "kill -TERM $$",
])
def test_never_exits_two_whatever_happens(tmp_path, loci):
    # Exit 2 on Stop blocks the stop and continues the conversation; with a draft
    # that stays pending, that is an infinite loop. No input may produce it.
    _draft(tmp_path)
    code, _ = _run(tmp_path, fake_loci=loci)
    assert code == 0, f"must exit 0, got {code}"
