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


def _base_path() -> str:
    """A PATH the hook can actually work on.

    The nudge gates on `jq` before it does anything, and jq is not in /usr/bin on a
    Windows checkout (chocolatey, scoop and winget all put it elsewhere). A
    hardcoded jq-less PATH made the hook exit early, so the "reaches the user"
    assertions failed while every silence assertion passed for the wrong reason.
    """
    base = "/usr/bin:/bin:/usr/local/bin"
    jq = shutil.which("jq")
    if jq:
        base = f"{_to_bash_path(Path(jq).parent)}:{base}"
    return base


def _run(project_dir: Path, *, fake_loci: str | None = None,
         home: Path | None = None, native: bool = False) -> tuple[int, dict | None]:
    """Run the hook; return (exit code, its JSON output or None if silent).

    `native=True` spells the project directory the way a real payload does \u2014 with
    backslashes on Windows \u2014 which is what the upward walk actually has to step
    up. `home` moves the `$HOME` ceiling somewhere a test can reach."""
    spelled = str(project_dir) if native else _to_bash_path(project_dir)
    env = {
        "PATH": _base_path(),
        "HOME": _to_bash_path(home) if home is not None else str(Path.home()),
        "CLAUDE_PROJECT_DIR": spelled,
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
        input=json.dumps({"cwd": spelled, "session_id": "t"}),
        capture_output=True, text=True, timeout=30, env=env,
    )
    out = proc.stdout.strip()
    return proc.returncode, (json.loads(out) if out else None)


# Where the draft lives, and where a pre-move CLI left one. `contract` writes and
# reads the new path and migrates a pre-move draft up to it on first contact —
# so a draft still at the old path is one no verb has seen, and since T14 the
# hook does not test for it: `draft show` could not report it either.
_NEW_DRAFT = ".loci/build/contract.draft.yaml"
_OLD_DRAFT = ".loci-build/contract.draft.yaml"


# A `loci` that RECORDS being run. `2>&1` is not enough: the hook redirects the
# spawn's stderr, and a test that only asserts silence passes just as well when
# the gate let the spawn through and the CLI happened to fail. The stub writes
# into the project root, which is where the hook `cd`s before calling it.
_SPY_NAME = ".loci-was-spawned"
_SPY_LOCI = 'printf x > "' + _SPY_NAME + '"; exit 1'


def _spawned(root: Path) -> bool:
    return (root / _SPY_NAME).exists()


def _draft(root: Path, rel: str = _NEW_DRAFT, body: str = "version: 1\nops: []\n") -> Path:
    """Stage a pending draft — at the one path the CLI reads, unless a test says
    otherwise to prove the other is ignored."""
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
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
    # The cheap file check must come first, because this hook runs at the end of
    # every turn. Asserted on the SPAWN, not on the silence: a stub that exits 1
    # produces silence either way, so "no output" alone cannot tell "the gate
    # held" from "the gate opened and the CLI failed".
    code, out = _run(tmp_path, fake_loci=_SPY_LOCI)
    assert code == 0 and out is None
    assert not _spawned(tmp_path), "the gate opened with no draft on disk"


def test_an_empty_build_directory_is_not_a_pending_draft(tmp_path):
    """`.loci/build/` exists in every project that has ever compiled, so a gate
    that tested the DIRECTORY rather than the file would spawn `loci` at the end
    of every turn in every one of them. (The pre-move `.loci-build/` a soak-era
    CLI left beside it is litter, and staged here as such.)"""
    (tmp_path / ".loci" / "build").mkdir(parents=True)
    (tmp_path / ".loci-build").mkdir()

    code, out = _run(tmp_path, fake_loci=_SPY_LOCI)
    assert code == 0 and out is None
    assert not _spawned(tmp_path), (
        "the gate tested the directory rather than the draft file, so every turn "
        "of every project that has ever compiled now spawns `loci`")


# ── the gate: one path ───────────────────────────────────────────────────────
#
# The draft moved to `.loci/build/contract.draft.yaml` with the rest of the
# layout, and for one soak the hook tested the old path too. Since T14 it does
# not: nothing reads a draft a pre-move CLI left under `.loci-build/` until a
# verb has migrated it up, so nudging over it first would announce a draft that
# `draft show` — the CLI the nudge then quotes — cannot see. A hook gating on the
# old path ALONE would be the worse failure, going quiet the moment a post-move
# CLI wrote a draft; both directions are pinned.

def test_a_draft_at_the_new_path_reaches_the_user(tmp_path):
    _draft(tmp_path, _NEW_DRAFT)

    code, out = _run(tmp_path, fake_loci=_envelope(2, ops=["add", "add"]))

    assert code == 0, "the draft took the hook down"
    assert out is not None, "the draft produced no nudge"
    assert "2 bounds added" in out["systemMessage"]


def test_a_legacy_draft_alone_is_not_pending_and_never_spawns_loci(tmp_path):
    _draft(tmp_path, _OLD_DRAFT)

    code, out = _run(tmp_path, fake_loci=_SPY_LOCI)

    assert code == 0 and out is None, "the hook still gates on the pre-move path"
    assert not _spawned(tmp_path), (
        "the gate opened on a draft no post-move verb has seen, so `loci` was "
        "spawned to report a draft `draft show` cannot find")


def test_both_paths_present_is_one_nudge_and_the_cli_decides_what_it_says(tmp_path):
    """Which wins when both exist with different contents is not this hook's to
    say: `contract draft show` migrates the legacy copy up on first contact and
    never overwrites a new-path draft, so the NEW one governs — and the hook
    proves it by asking rather than by reading. It gates on the new path and
    spells none below the gate, so there is nothing here to disagree with the
    CLI about, and one `systemMessage` rather than two."""
    _draft(tmp_path, _NEW_DRAFT, body="version: 1\nops: [{op: add}]\n")
    _draft(tmp_path, _OLD_DRAFT, body="version: 1\nops: [{op: disable}]\n")

    code, out = _run(tmp_path, fake_loci=_envelope(1, ops=["add"]))

    assert code == 0
    assert out is not None and set(out) == {"systemMessage"}
    assert "1 bound added" in out["systemMessage"]
    assert "retired" not in out["systemMessage"], (
        "the hook read a path itself instead of asking the CLI"
    )


def test_the_hook_names_no_draft_path_below_its_gate(tmp_path):
    """Structural, because the behavioural test above can only see the paths the
    stub is asked about. Everything after the gate must go through
    `contract draft show`; a second spelling of the draft path is a second answer
    to "which file governs", and the CLI already owns that one."""
    text = NUDGE.read_text(encoding="utf-8")
    gate = text.index("contract.draft.yaml")
    after = text[text.index("\n", text.index("|| exit 0", gate)):]
    assert "contract.draft.yaml" not in after, (
        "the hook spells a draft path after its gate"
    )
    assert "loci contract draft show" in after


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


# \u2500\u2500 the root, not just the filename \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
#
# `loci contract draft` resolves its root the way every verb does \u2014 the git top
# level, else the cwd \u2014 while `$CLAUDE_PROJECT_DIR` is the directory the session
# was LAUNCHED in. In a monorepo those are different, so a draft written from
# `repo/firmware` lands at `repo/.loci/build/` and a hook testing
# `repo/firmware/.loci/build/` is silent about it for ever.


def _repo_with_draft_at_the_top(tmp_path: Path, rel: str = _NEW_DRAFT) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    sub = root / "firmware" / "src"
    sub.mkdir(parents=True)
    _draft(root, rel)
    return root, sub


def test_a_draft_at_the_repo_root_reaches_a_session_launched_in_a_subdirectory(tmp_path):
    root, sub = _repo_with_draft_at_the_top(tmp_path)

    code, out = _run(sub, fake_loci=_envelope(1, ops=["add"]))

    assert code == 0
    assert out is not None, (
        "a pending draft at the repo root was never reported to a session "
        "launched in a subdirectory, and never will be")
    assert "1 bound added" in out["systemMessage"]


def test_the_walk_stops_at_the_repo_top_level(tmp_path):
    """A draft ABOVE the repo belongs to another project. The `.git` ceiling is
    tested after the candidate, so a draft at the top level itself is still found
    \u2014 but one outside it is not."""
    outside = tmp_path / "outside"
    outside.mkdir()
    _draft(outside, _NEW_DRAFT)
    root = outside / "repo"
    (root / ".git").mkdir(parents=True)
    sub = root / "src"
    sub.mkdir()

    code, out = _run(sub, fake_loci=_envelope(1, ops=["add"]))

    assert code == 0
    assert out is None, (
        "a draft outside the repository was reported for a project inside it: "
        f"{out}")


def test_the_cli_is_asked_from_the_sessions_own_directory(tmp_path):
    """THE rule the gate's permissiveness rests on.

    `loci contract accept` is the only command this message names, and the user
    runs it where they are. So the question "is there a draft to apply" has to be
    asked from the same directory, with the same resolution — git top level, else
    cwd — and the hook must not substitute a root of its own.

    Two earlier shapes did substitute one, and both were wrong: testing only
    `$CLAUDE_PROJECT_DIR` missed a monorepo, and climbing to wherever a draft
    happened to live produced a nag `accept` answers "nothing to accept" for, on
    every Stop, for ever. The gate may over-reach; the ANSWER may not."""
    root, sub = _repo_with_draft_at_the_top(tmp_path)
    # A `loci` that records the directory it was invoked from.
    where = tmp_path / "cwd.txt"
    stub = 'pwd > "%s"\n' % _to_bash_path(where) + _envelope(1, ops=["add"])

    code, out = _run(sub, fake_loci=stub)

    assert code == 0
    assert out is not None
    assert where.is_file(), "the CLI was never asked"
    asked = where.read_text(encoding="utf-8").strip()
    assert asked.rstrip("/").endswith("src"), (
        "the CLI was asked from a directory the hook chose rather than from the "
        f"session's own, so its answer is not the one `accept` will give: {asked}")


def test_a_draft_in_the_home_directory_opens_the_gate_and_the_cli_settles_it(tmp_path):
    """The `$HOME` ceiling is gone, deliberately.

    It was one more opinion, and a reviewer showed the comparison was inert on
    Windows anyway (`C:/Users/User` never equals `/c/Users/User`). A stray draft
    above a project now opens the gate and the CLI answers `pending: 0` — the
    right answer, arrived at by the party that owns it. This pins the cost of
    that: the hook must stay SILENT on the CLI's answer, not on its own."""
    home = tmp_path / "home"
    home.mkdir()
    _draft(home, _NEW_DRAFT)
    project = home / "work" / "proj"
    project.mkdir(parents=True)

    # The CLI, resolving from the project, finds nothing pending.
    code, out = _run(project, fake_loci=_envelope(0), home=home)

    assert code == 0
    assert out is None, f"the hook spoke over the CLI's own answer: {out}"



def test_the_walk_steps_up_a_native_windows_path(tmp_path):
    """The walk gets the payload's spelling. Git Bash resolves `C:\\Users\\...` in a
    FILE TEST but not in a parameter expansion, so a `/`-only step stopped the
    walk at the first level. Every other test here uses `_to_bash_path`, so every
    one of them would have stayed green."""
    root, sub = _repo_with_draft_at_the_top(tmp_path)

    code, out = _run(sub, fake_loci=_envelope(1, ops=["add"]), native=True)

    assert code == 0
    assert out is not None, (
        "the walk could not step up a native path, so the draft was never found")
