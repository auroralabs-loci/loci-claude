"""The hook that reclaims what `.loci-build` accumulates.

Phase 03. `loci build snapshot --turn` writes one tree per user turn — the
pre-edit copy of every file the turn touched, plus any baseline objects rebuilt
from them — and until now nothing removed any of it. One script on two events:
`Stop` is the one event that means "that turn is over", so retention lives
there; `SessionStart` does the same plus `--reclaim-objects`, the only stage
that deletes something a compile produced.

The interesting assertions are not "it ran". They are the ways this hook could
be worse than absent:

* **It must never exit 2.** On `Stop`, exit 2 blocks the stop and continues the
  conversation, so a hook that runs every turn would spin for ever. The CLI it
  calls exits 2 for an unknown subcommand, which is exactly what the pinned CLI
  does with `build reap` — i.e. the dangerous case is the DEFAULT case today.
* **It must resolve the project root the way the WRITER does.**
  `pre-edit-hook.sh` calls `build snapshot` with no `--project-root`, so captures
  land under the session's own directory — the payload's `cwd`. An earlier
  version walked that up to the git top level, which is a different directory
  whenever a session runs in a subdirectory of a repo: the snapshot wrote
  `repo/firmware/.loci-build` while the reap swept `repo/.loci-build`, and
  retention never ran for that project, silently, for ever. Every test here runs
  the hook from somewhere that is NOT the project root; with the two equal,
  "anchored to the payload" and "resolved against the shell" are the same path
  and nothing could tell them apart.
* **It must pass `--turn`, joined.** A `Stop` fires when the main agent stops,
  which is not the same as "nothing is running"; the flag keeps this turn's
  Before alive for a background task still reading it. `--turn=<v>` rather than
  `--turn <v>` because `prompt_id` is undocumented, and a value starting with `-`
  makes argparse exit 2 — indistinguishable here from "no such verb", which
  would stop retention everywhere and look exactly like the pre-release no-op.
* **It must NOT retry without `--turn` on exit 2.** That is the edge hooks'
  pattern and it is wrong here: there, exit 2 means "this CLI lacks the flag";
  here it means the CLI lacks the whole verb, so a retry only drops the one
  guarantee the flag buys.

Arguments are logged RS-separated, never as `"$*"`: `"$*"` joins on a space, so
an unquoted `--project-root My Project` renders identically to a quoted one and
the space test passes with the quoting deleted.
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
HOOK = PLUGIN_ROOT / "hooks" / "turn-reap.sh"

_RS = "\x1e"


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


# The payload carries a NATIVE path — the captured probe shows
# `"cwd":"C:\Playground\loci-claude-tests\probe-hooks"` — and the hook passes it
# through unchanged, which is what makes it agree with the writer. A `/c/...`
# spelling would not: Python on Windows reads that as a rooted path on the
# CURRENT DRIVE, i.e. `C:\c\...`, so the reap would sweep a directory that does
# not exist while the captures piled up in the one that does.
def _to_bash_path(p: Path) -> str:
    s = Path(p).as_posix()
    m = re.match(r"^([A-Za-z]):/(.*)$", s)
    return f"/{m.group(1).lower()}/{m.group(2)}" if m else s


def _base_path() -> str:
    """A PATH the hook can work on. It gates on `command -v jq || exit 0`, and jq
    is not in /usr/bin on a Windows checkout — a hardcoded PATH would make every
    assertion below vacuous."""
    base = "/usr/bin:/bin:/usr/local/bin"
    jq = shutil.which("jq")
    if jq:
        base = f"{_to_bash_path(Path(jq).parent)}:{base}"
    return base


_STUB = r"""#!/usr/bin/env bash
{ sep=''; for a in "$@"; do printf '%s%s' "$sep" "$a"; sep=$'\x1e'; done; printf '\n'; } >> "ARGS_LOG"
STUB_BODY
"""


class Result:
    def __init__(self, proc, args_log: Path):
        self.code = proc.returncode
        self.out = proc.stdout
        self.stderr = proc.stderr
        # `.split("\n")`, NOT `.splitlines()`: Python treats RS as a line
        # boundary, which shreds every logged call at its own separator.
        log = args_log.read_text(encoding="utf-8") if args_log.is_file() else ""
        self.calls: list[list[str]] = [
            line.split(_RS) for line in log.split("\n") if line
        ]

    @property
    def reaps(self) -> list[list[str]]:
        return [c for c in self.calls if c[:2] == ["build", "reap"]]

    def flag(self, name: str, call: int = 0) -> str | None:
        """The value of `--name=value`.

        The joined form is the one the hook must use: `prompt_id` is
        undocumented, and argparse reads a separate value beginning with `-` as
        the next OPTION and exits 2 — which is indistinguishable here from "this
        CLI has no `build reap`", so the whole reap would silently stop running.
        """
        prefix = f"{name}="
        for token in self.reaps[call]:
            if token.startswith(prefix):
                return token[len(prefix):]
        return None

    def has(self, name: str, call: int = 0) -> bool:
        return any(t == name or t.startswith(f"{name}=")
                   for t in self.reaps[call])


def _project(tmp_path: Path, name: str = "proj") -> Path:
    """A project with a `.loci-build`, which is the hook's cheap gate."""
    root = tmp_path / name
    (root / ".loci-build" / "turns").mkdir(parents=True, exist_ok=True)
    return root


def _run(tmp_path: Path, root: Path | None, *, payload: dict | None = None,
         body: str = "exit 0", loci: bool = True,
         project_dir_env: bool = False, event: str = "Stop") -> Result:
    home = tmp_path / "home"
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    args_log = home / "args.log"
    if loci:
        stub = bin_dir / "loci"
        stub.write_text(
            _STUB.replace("ARGS_LOG", _to_bash_path(args_log))
                 .replace("STUB_BODY", body),
            encoding="utf-8")
        stub.chmod(0o755)

    # The hook runs from a directory that is NOT the project root, deliberately:
    # with the two equal, "anchored to the payload" and "resolved against the
    # shell's CWD" are the same path and nothing here could tell them apart.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(parents=True, exist_ok=True)

    env = {
        "PATH": f"{_to_bash_path(bin_dir)}:{_base_path()}",
        "HOME": _to_bash_path(home),
    }
    if root is not None and project_dir_env:
        env["CLAUDE_PROJECT_DIR"] = str(root)

    doc = payload if payload is not None else {
        "hook_event_name": event,
        "prompt_id": "5e1b8673-df09-42d3-a338-c13726ff8d32",
        "cwd": str(root) if root else "",
        "stop_hook_active": False,
    }
    proc = subprocess.run(
        [_find_bash(), _to_bash_path(HOOK)],
        input=json.dumps(doc), capture_output=True, text=True, timeout=30,
        cwd=str(elsewhere), env=env,
    )
    assert proc.returncode == 0, (
        f"a Stop hook must always exit 0 — a non-zero exit blocks the stop and "
        f"continues the conversation. got {proc.returncode}, "
        f"stderr={proc.stderr!r}")
    return Result(proc, args_log)


# ── the call it makes ────────────────────────────────────────────────────────

def test_the_turn_is_reaped_against_the_payloads_project_root(tmp_path):
    root = _project(tmp_path)

    res = _run(tmp_path, root)

    assert len(res.reaps) == 1
    assert res.flag("--project-root") == str(root)
    assert res.flag("--turn") == "5e1b8673-df09-42d3-a338-c13726ff8d32"


def test_the_project_root_survives_a_space_in_its_path(tmp_path):
    """The whole reason arguments are logged RS-separated. Unquoted, this arrives
    as two argv entries and the reap runs against a directory that does not
    exist — silently, since everything here is redirected."""
    root = _project(tmp_path, "My Project")

    res = _run(tmp_path, root)

    assert res.flag("--project-root") == str(root)


def test_the_root_is_the_payloads_cwd_not_the_git_top_level(tmp_path):
    """It must agree with the WRITER. `pre-edit-hook.sh` calls `build snapshot`
    with no `--project-root`, so captures land under `Path.cwd()` — the session's
    own directory, which is what the payload's `cwd` carries.

    An earlier version walked that up to the git top level, which is a different
    directory whenever a session runs in a subdirectory of a repo: the snapshot
    wrote `repo/firmware/.loci-build` while the reap swept `repo/.loci-build`,
    the cheap gate found nothing, and retention never ran for that project —
    silently, for ever."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True,
                   capture_output=True)
    root = _project(repo, "firmware")          # the session's own directory

    res = _run(tmp_path, root)

    assert len(res.reaps) == 1
    assert res.flag("--project-root") == str(root)


def test_the_environment_is_only_a_fallback(tmp_path):
    """`CLAUDE_PROJECT_DIR` is consulted when the payload carries no `cwd` —
    a shape the payload probe has not seen, but the hook must not depend on."""
    root = _project(tmp_path)

    res = _run(tmp_path, root, project_dir_env=True,
               payload={"hook_event_name": "Stop", "prompt_id": "abc"})

    assert len(res.reaps) == 1
    assert res.flag("--project-root") == str(root)


# ── the ways it must stay quiet ──────────────────────────────────────────────

def test_a_project_that_has_never_run_loci_spawns_nothing(tmp_path):
    """This fires at the end of every single turn in every session. No
    `.loci-build`, no spawn."""
    root = tmp_path / "bare"
    root.mkdir()

    res = _run(tmp_path, root)

    assert res.reaps == []
    assert res.out == ""


def test_an_absent_cli_is_not_an_error(tmp_path):
    root = _project(tmp_path)

    res = _run(tmp_path, root, loci=False)

    assert res.calls == []


def test_the_hook_prints_nothing_even_when_the_reap_talks(tmp_path):
    """Plain stdout on `Stop` goes to the debug log, and a reap has nothing to
    tell the user anyway. A hook that leaked the envelope would put JSON where
    Claude Code expects a hook-output document."""
    root = _project(tmp_path)

    res = _run(tmp_path, root,
               body='echo \'{"ok":true,"data":{"removed_total":3}}\'; exit 0')

    assert res.out == ""


# ── the pinned CLI, which is the CLI that runs ───────────────────────────────

def test_a_cli_without_the_verb_exits_2_and_the_hook_still_exits_0(tmp_path):
    """The pin is an exact `==` on a release that has no `build reap` at all, so
    argparse's exit 2 is the DEFAULT outcome today, not an edge case. `_run`
    asserts the hook's own exit code, which is the thing that would block the
    stop and spin the conversation."""
    root = _project(tmp_path)

    res = _run(tmp_path, root,
               body='echo "loci build: error: invalid choice: reap" >&2; exit 2')

    assert len(res.reaps) == 1


def test_exit_2_is_not_retried_without_the_turn(tmp_path):
    """The edge hooks retry without `--turn` on exit 2, because there it means
    "this CLI has the verb but not the flag". Here it means the verb is missing
    entirely — so a retry cannot succeed, and the only thing it could achieve is
    running a reap that no longer protects this turn's tree."""
    root = _project(tmp_path)

    res = _run(tmp_path, root,
               body='echo "invalid choice: reap" >&2; exit 2')

    assert len(res.reaps) == 1
    assert res.has("--turn")


def test_a_payload_with_no_prompt_id_still_reaps(tmp_path):
    """`prompt_id` is undocumented and was established by probing real sessions.
    If it ever goes missing the retention must still run — losing the protection
    for one turn is a recompile; losing retention is unbounded growth."""
    root = _project(tmp_path)

    res = _run(tmp_path, root, payload={
        "hook_event_name": "Stop", "cwd": str(root)})

    assert len(res.reaps) == 1
    assert not res.has("--turn")
    assert res.flag("--project-root") == str(root)


def test_the_per_turn_reap_never_reclaims_objects(tmp_path):
    """`--reclaim-objects` is the one stage that deletes something a compile
    produced, and it belongs to the once-per-session caller. Running it every
    turn would put the riskiest stage on the hottest path."""
    root = _project(tmp_path)

    res = _run(tmp_path, root)

    assert not res.has("--reclaim-objects")


# ── registration ─────────────────────────────────────────────────────────────

def test_the_hook_is_registered_on_stop(tmp_path):
    """A hook file nothing invokes is the shape 06a shipped: a working mechanism
    and no caller."""
    doc = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text(
        encoding="utf-8"))
    commands = [h["command"]
                for entry in doc["hooks"]["Stop"] for h in entry["hooks"]]

    assert any("turn-reap.sh" in c for c in commands)


def test_session_start_reclaims_objects_and_names_no_turn(tmp_path):
    """The other caller, driven rather than grepped. A previous version of this
    test read `session-init.sh` as prose and asserted on the flags in the line it
    found — which stayed green when the CALL was deleted, i.e. it could not see
    the one failure it existed to catch.

    SessionStart is the once-per-session caller, so it is the one that passes
    `--reclaim-objects`; and it has no turn to protect, which is fine because a
    tree written moments ago is kept by the CLI's in-flight window."""
    root = _project(tmp_path)

    res = _run(tmp_path, root, event="SessionStart")

    assert len(res.reaps) == 1
    assert res.has("--reclaim-objects")
    assert not res.has("--turn")
    assert res.flag("--project-root") == str(root)


def test_the_hook_is_registered_on_session_start_with_no_matcher(tmp_path):
    """`session-init.sh` is registered with `"matcher": "startup"`, so it does
    not run on `resume`, `clear` or `compact`. The reclamation stage lived there
    and was therefore unreachable for anyone living in `claude --continue`; this
    entry must carry no matcher at all."""
    doc = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text(
        encoding="utf-8"))
    entries = [e for e in doc["hooks"]["SessionStart"]
               if any("turn-reap.sh" in h["command"] for h in e["hooks"])]

    assert len(entries) == 1
    assert "matcher" not in entries[0]


def test_session_init_no_longer_reaps_on_its_own(tmp_path):
    """Two callers spawning the same verb on one event would double the work and
    let the two disagree about the project root, which is how they diverged in
    the first place."""
    text = (PLUGIN_ROOT / "hooks" / "session-init.sh").read_text(encoding="utf-8")
    invocations = [line for line in text.splitlines()
                   if "loci build reap" in line]

    assert invocations == []


def test_flags_are_passed_joined_so_a_dash_value_cannot_be_read_as_an_option(
        tmp_path):
    """`prompt_id` is undocumented — established by probing real sessions. A
    value beginning with `-` makes argparse answer "expected one argument" and
    exit 2, which is indistinguishable here from "this CLI has no `build reap`":
    retention would stop for every project and look exactly like the pre-release
    no-op. The joined form has no such reading."""
    root = _project(tmp_path)

    res = _run(tmp_path, root, payload={
        "hook_event_name": "Stop", "prompt_id": "-abc123", "cwd": str(root)})

    assert res.flag("--turn") == "-abc123"
    assert "--turn" not in res.reaps[0]          # never the separate spelling
