"""The pre-edit hook must capture the turn's baseline exactly once.

It runs before every Edit/Write to a C/C++/Rust source and freezes the LOCI-built
`.o` as `.o.prev`, which is the "Before" column of every post-edit report. Until now
it had no tests, and that is how this shipped:

    the freeze was unconditional, and the post-edit skill recompiles the `.o` in
    between edits — so the SECOND edit of a user turn froze the FIRST edit's output
    and labelled it "pre-edit". A three-edit turn reported the delta between edits 2
    and 3; a turn that edited and then reverted reported a regression that
    `loci elf diff` on the true pair says does not exist.

The fix passes `prompt_id` as `--turn`, making the capture first-write-wins for that
turn. `prompt_id` is on every hook payload, identical across a turn, distinct
between turns, and still the PARENT turn's id inside a subagent — all verified by
instrumenting real sessions, since the payload is undocumented.

Two invariants:

* **Always exit 0.** A non-zero exit from a `PreToolUse` hook is a tool failure.
* **Degrade, never drop.** A `loci` predating `--turn` rejects it. The hook must
  retry without the flag — restoring the old overwrite-every-time behaviour, which
  is worse but is what those installs already had — rather than skip the snapshot
  and leave the turn with no baseline at all.
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
HOOK = PLUGIN_ROOT / "hooks" / "pre-edit-hook.sh"

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


def _to_bash_path(p: Path) -> str:
    s = Path(p).as_posix()
    m = re.match(r"^([A-Za-z]):/(.*)$", s)
    return f"/{m.group(1).lower()}/{m.group(2)}" if m else s


def _base_path() -> str:
    """A PATH the hook can work on — it gates on `command -v jq || exit 0`, and jq is
    not in /usr/bin on a Windows checkout, so a hardcoded PATH makes every assertion
    here vacuous."""
    base = "/usr/bin:/bin:/usr/local/bin"
    jq = shutil.which("jq")
    if jq:
        base = f"{_to_bash_path(Path(jq).parent)}:{base}"
    return base


def opt(call: list[str], name: str) -> str | None:
    """The value of `--name` in one recorded argv, in either spelling.

    Both forms are read because the hook uses both deliberately — `--source <path>`
    separate, everything whose content the hook does not control joined — and a
    helper that understood only one would silently answer None for the other, which
    reads exactly like "the flag is not there"."""
    for i, arg in enumerate(call):
        if arg == name:
            return call[i + 1] if i + 1 < len(call) else ""
        if arg.startswith(name + "="):
            return arg[len(name) + 1:]
    return None


class Result:
    def __init__(self, proc, calls: list[list[str]]):
        self.code = proc.returncode
        self.out = proc.stdout
        self.stderr = proc.stderr
        self.calls = calls          # one argv LIST per `loci` invocation

    @property
    def snapshots(self) -> list[list[str]]:
        return [c for c in self.calls if c[:2] == ["build", "snapshot"]]

    @property
    def scans(self) -> list[list[str]]:
        return [c for c in self.calls if c[:1] == ["scan"]]

    def turn_of(self, i: int = 0) -> str | None:
        return opt(self.snapshots[i], "--turn")


def _run(home: Path, payload: dict, *, stub: str, expect_zero: bool = True,
         env_extra: dict | None = None) -> Result:
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    # Always present, so the hook's state-directory resolution lands inside the
    # sandbox. Its fallback is the plugin's own `state/`, and a test that reached
    # THAT would be reading the developer's real project-context files — a suite
    # that passes or fails on what happens to be on the machine running it.
    (home / ".loci" / "state").mkdir(parents=True, exist_ok=True)
    args_log = home / "args.log"
    # One record separator after every ARGUMENT, one group separator after every
    # CALL. `"$*"` joins on a space, so `--project-root "/My Src"` and an unquoted
    # `--project-root /My Src` render identically — the space test would pass with
    # the quoting deleted, which is precisely what it exists to catch. Read back as
    # BYTES and split explicitly: Python's `str.splitlines()` treats \x1c, \x1d and
    # \x1e as line boundaries, so any line-oriented parse shreds this at its own
    # separators.
    (bin_dir / "loci").write_text(
        "#!/usr/bin/env bash\n"
        f'{{ for a in "$@"; do printf "%s\\036" "$a"; done; printf "\\035"; }} '
        f'>> "{_to_bash_path(args_log)}"\n'
        f"{stub}\n",
        encoding="utf-8",
    )
    (bin_dir / "loci").chmod(0o755)

    env = {
        "PATH": f"{_to_bash_path(bin_dir)}:{_base_path()}",
        "HOME": _to_bash_path(home),
        "CLAUDE_PROJECT_DIR": _to_bash_path(home),
    }
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [_find_bash(), _to_bash_path(HOOK)],
        input=json.dumps(payload), capture_output=True, text=True, timeout=30,
        env=env,
    )
    if expect_zero:
        assert proc.returncode == 0, (
            f"the pre-edit hook must always exit 0; got {proc.returncode}. "
            f"stderr={proc.stderr!r}"
        )
    calls: list[list[str]] = []
    if args_log.is_file():
        for chunk in args_log.read_bytes().split(b"\x1d"):
            if not chunk:
                continue
            calls.append([a.decode("utf-8") for a in chunk.split(b"\x1e")[:-1]])
    return Result(proc, calls)


# `scan` must still answer (the hook reads .data.report from it); `snapshot` is the
# call under test.
_OK = "echo '{\"ok\":true,\"data\":{\"report\":\"\",\"snapshotted\":true}}'"
# A `loci` predating --turn: argparse rejects the flag and exits 2.
_NO_TURN_FLAG = (
    'if [[ "$*" == *--turn* ]]; then\n'
    '  echo "loci: error: unrecognized arguments: --turn" >&2; exit 2\n'
    "fi\n" + _OK
)
# The pinned CLI: it has neither of the flags this branch adds, and rejects the
# whole call the moment it sees either. This is the generation production runs.
_NO_NEW_FLAGS = (
    'if [[ "$*" == *--turn* || "$*" == *--loci-target* || "$*" == *--content-kind* ]]; then\n'
    '  echo "loci: error: unrecognized arguments" >&2; exit 2\n'
    "fi\n" + _OK
)

_PROMPT_ID = "b52ae369-e1ba-4823-9c6e-3d51b9e0166e"


def _edit(path: str, *, prompt_id: str | None = _PROMPT_ID, agent: str | None = None,
          cwd: str | None = None) -> dict:
    payload: dict = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Edit",
        "tool_input": {"file_path": path, "old_string": "y", "new_string": "  acc += 1;"},
    }
    if prompt_id is not None:
        payload["prompt_id"] = prompt_id
    if agent is not None:
        payload["agent_id"] = agent
        payload["agent_type"] = "general-purpose"
    if cwd is not None:
        payload["cwd"] = cwd
    return payload


def _writer_key(project_root: Path) -> str:
    """The state-file key `session-init.sh` would produce for this project.

    Computed by calling the plugin's OWN `hash_cwd` exactly as that writer calls it
    — no argument, from inside the project directory, i.e. off the process's cwd and
    the Git Bash `/c/...` spelling.

    The hook under test calls the same function with the payload's NATIVE `C:\\...`
    path instead. That both land on one file is the property being tested, and it is
    why the key is not hardcoded here: a fixture that named the file itself would
    pass whatever spelling the hook happened to use."""
    script = (
        'PLUGIN_DIR="$1"; STATE_DIR="$2"; export LOCI_STATE_DIR="$STATE_DIR"; '
        '. "$PLUGIN_DIR/lib/setup-steps.sh" || exit 1; cd "$3" || exit 1; hash_cwd'
    )
    proc = subprocess.run(
        [_find_bash(), "-c", script, "bash",
         _to_bash_path(PLUGIN_ROOT), "/tmp/unused", _to_bash_path(project_root)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, f"hash_cwd failed: {proc.stderr!r}"
    key = proc.stdout.strip()
    assert key, f"hash_cwd produced no key: {proc.stdout!r} {proc.stderr!r}"
    return key


def _context(home: Path, project_root: Path, loci_target: str = "armv7e-m") -> Path:
    """A keyed project-context file, as `session-init.sh` writes it.

    `project_root` inside the document is stored the way that writer stores it —
    `$(pwd)` from Git Bash, i.e. the `/c/...` spelling. The hook no longer reads that
    field to find the file (it names the file directly), but it stays accurate so the
    fixture keeps describing a real state file rather than a convenient one."""
    state = home / ".loci" / "state"
    state.mkdir(parents=True, exist_ok=True)
    ctx = state / f"project-context-{_writer_key(project_root)}.json"
    ctx.write_text(json.dumps({
        "detection_status": "ok",
        "project_root": _to_bash_path(project_root),
        "loci_target": loci_target,
        "compiler": "arm-none-eabi-gcc",
    }), encoding="utf-8")
    return ctx


# ── the regression ───────────────────────────────────────────────────────────

def test_the_turn_id_is_passed_to_the_snapshot(tmp_path):
    """THE regression. Without `--turn` the capture is unconditional, so the turn's
    second edit overwrites the baseline with the first edit's output."""
    r = _run(tmp_path, _edit("/p/blink.c"), stub=_OK)
    assert r.snapshots, f"a snapshot must be attempted; calls={r.calls!r}"
    assert r.turn_of(0) == _PROMPT_ID, f"got {r.snapshots[0]!r}"


def test_an_edit_inside_a_subagent_uses_the_parent_turn(tmp_path):
    """Verified against a live session: both edit hooks fire inside a subagent and
    `prompt_id` stays the PARENT turn's. So a subagent's edits share the turn's
    baseline, which is what a per-turn baseline wants — a subagent fan-out must not
    fragment it."""
    r = _run(tmp_path, _edit("/p/blink.c", agent="a3b74c248f211b1f1"), stub=_OK)
    assert r.turn_of(0) == _PROMPT_ID


def test_two_edits_of_one_turn_send_the_same_turn_id(tmp_path):
    """The hook is stateless; first-write-wins lives in the CLI. What the hook owes
    is the SAME id both times — a fresh id per edit would defeat it entirely."""
    first = _run(tmp_path, _edit("/p/blink.c"), stub=_OK)
    second = _run(tmp_path, _edit("/p/blink.c"), stub=_OK)
    assert first.turn_of(0) == second.turn_of(0) == _PROMPT_ID


# ── degrade, never drop ─────────────────────────────────────────────────────

def test_an_older_cli_still_gets_a_snapshot_without_the_flag(tmp_path):
    """--turn needs a CLI newer than the pin, and the pin installs an exact `==`
    spec, so an older `loci` is normal. Dropping the snapshot would leave no baseline
    at all, which is worse than the overwrite behaviour those installs already have."""
    r = _run(tmp_path, _edit("/p/blink.c"), stub=_NO_TURN_FLAG)
    assert len(r.snapshots) == 2, f"expected a flagless retry; snapshots={r.snapshots!r}"
    assert r.turn_of(0) == _PROMPT_ID
    assert r.turn_of(1) is None, "the retry must drop --turn entirely"


def test_the_retry_drops_every_unreleased_flag_at_once(tmp_path):
    """The fallback used to drop `--turn` and nothing else. Adding a second
    unreleased flag to the same call would then have made an older CLI exit 2
    TWICE and capture nothing at all — worse than the degrade-don't-drop contract
    it is supposed to have. So the retry is the base call, not the previous one
    minus a flag."""
    proj = tmp_path / "proj"
    proj.mkdir()
    _context(tmp_path, proj, "armv7e-m")
    r = _run(tmp_path, _edit("/p/blink.c", cwd=str(proj)), stub=_NO_NEW_FLAGS)
    assert len(r.snapshots) == 2, f"snapshots={r.snapshots!r}"
    first, retry = r.snapshots
    assert opt(first, "--turn") == _PROMPT_ID
    assert opt(first, "--loci-target") == "armv7e-m"
    assert opt(retry, "--turn") is None
    assert opt(retry, "--loci-target") is None
    # …and the retry must still name the source and the root, or the degrade has
    # quietly become a different call.
    assert opt(retry, "--source") == "/p/blink.c"
    assert opt(retry, "--project-root") == str(proj)


def test_the_state_dir_override_is_honoured(tmp_path):
    """`ensure-loci-cli.sh` and `session-init.sh` both export `LOCI_STATE_DIR`, and
    the context file this hook reads is the one THEY wrote. Resolving it differently
    here would read an empty directory and silently drop the target on every install
    that overrides it."""
    proj = tmp_path / "proj"
    elsewhere = tmp_path / "elsewhere"
    proj.mkdir()
    elsewhere.mkdir()
    ctx = _context(tmp_path, proj, "tc399")
    shutil.move(str(ctx), str(elsewhere / ctx.name))
    r = _run(tmp_path, _edit("/p/blink.c", cwd=str(proj)), stub=_OK,
             env_extra={"LOCI_STATE_DIR": _to_bash_path(elsewhere)})
    assert opt(r.snapshots[0], "--loci-target") == "tc399"


def test_a_payload_with_no_prompt_id_still_snapshots(tmp_path):
    """`prompt_id` is undocumented. If it ever disappears the hook must fall back to
    the turn-less call, not skip the capture."""
    r = _run(tmp_path, _edit("/p/blink.c", prompt_id=None), stub=_OK)
    assert len(r.snapshots) == 1
    assert r.turn_of(0) is None


def test_exactly_one_snapshot_on_the_happy_path(tmp_path):
    """The whole fix rests on the flagged call being the only one. A spurious
    flagless second call overwrites the baseline it just captured — and nothing
    pinned the count, so a retry firing unconditionally passed the entire suite."""
    r = _run(tmp_path, _edit("/p/blink.c"), stub=_OK)
    assert len(r.snapshots) == 1, f"snapshots={r.snapshots!r}"


@pytest.mark.parametrize("stub,label", [
    ("exit 1", "generic failure"),
    ("exit 127", "not runnable"),
    ("exit 3", "auth_required"),
    ("exit 4", "quota_exceeded"),
])
def test_only_a_usage_error_drops_the_flag(tmp_path, stub, label):
    """Exit 2 means "I do not know that flag" — the one case where retrying without
    `--turn` is right. Any other failure must NOT escalate to the overwrite path:
    that is the defect, and with everything redirected to /dev/null it would happen
    silently and stay for the rest of the turn."""
    r = _run(tmp_path, _edit("/p/blink.c"), stub=stub)
    assert r.code == 0
    assert len(r.snapshots) == 1, (
        f"{label} (exit from `{stub}`) must not trigger the flagless retry; "
        f"snapshots={r.snapshots!r}"
    )
    assert r.turn_of(0) == _PROMPT_ID


@pytest.mark.parametrize("stub", ["echo 'not json'", "echo ''"])
def test_a_nonsense_but_successful_cli_is_accepted(tmp_path, stub):
    """Exit 0 means the call was understood; the hook does not read its output."""
    r = _run(tmp_path, _edit("/p/blink.c"), stub=stub)
    assert r.code == 0
    assert len(r.snapshots) == 1


def test_a_write_payload_is_snapshotted_too(tmp_path):
    """Every fixture here was an Edit, so the Write branch — which reads
    `tool_input.content` rather than `new_string` — was never exercised by the first
    tests this hook has ever had."""
    r = _run(tmp_path, {
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "prompt_id": _PROMPT_ID,
        "tool_input": {"file_path": "/p/newfile.c", "content": "int g(void){return 1;}"},
    }, stub=_OK)
    assert r.snapshots, f"calls={r.calls!r}"
    assert r.turn_of(0) == _PROMPT_ID


# ── scope ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ext", [".c", ".cc", ".cpp", ".cxx", ".c++", ".rs",
                                 ".h", ".hpp", ".hxx", ".h++", ".hh",
                                 ".inc", ".ipp", ".tcc", ".S", ".s"])
def test_every_source_extension_is_snapshotted(tmp_path, ext):
    """Headers included, and this list must not be NARROWER than the CLI's
    `_SNAPSHOT_SOURCE_EXTS`.

    That is a correctness rule rather than tidiness. `build snapshot` now captures a
    pre-edit COPY of every extension it lists, and `build compile --baseline`
    rebuilds a header edit's Before out of those copies. A file this hook filters out
    is one that is never captured — and the reconstruction then reads it at its
    current, edited content while every other check passes, producing an object that
    mixes pre-edit and post-edit sources and is published as a clean Before.

    `.inc` is ordinary embedded C, `.ipp`/`.tcc` are C++ template bodies, and `.S` is
    preprocessed assembly that really does read headers. `.s` is not preprocessed,
    but a case-insensitive filesystem cannot tell the two names apart."""
    r = _run(tmp_path, _edit(f"/p/api{ext}"), stub=_OK)
    assert r.snapshots, f"{ext} must reach the CLI"


@pytest.mark.parametrize("path", ["/p/notes.md", "/p/build.py", "/p/Makefile", "/p/x"])
def test_a_non_source_path_never_invokes_loci(tmp_path, path):
    r = _run(tmp_path, _edit(path), stub=_OK)
    assert r.calls == [], f"{path} must be filtered in the hook; got {r.calls!r}"


def test_the_pre_scan_still_runs(tmp_path):
    """The snapshot is not the hook's only job — it also asks `loci scan` for the
    call-graph pre-scan. Pinned so a change to the snapshot path cannot quietly
    remove it."""
    r = _run(tmp_path, _edit("/p/blink.c"), stub=_OK)
    assert r.scans, f"calls={r.calls!r}"


@pytest.mark.parametrize("path", ["/p/.claude/plans/draft.c", "/p/.claude/settings.json.c",
                                  "/p/.claude/settings.local.json.rs"])
def test_a_plan_or_settings_file_is_skipped(tmp_path, path):
    """The skip `post-edit-hook.sh` has had since phase 04, and this one did not. The
    two hooks are a pair: a file one acts on while the other ignores it is a state
    neither was designed for — the pre-edit side would capture a baseline and stamp
    the turn for a file the post-edit side then refuses to measure."""
    r = _run(tmp_path, _edit(path), stub=_OK)
    assert r.calls == [], f"{path} must be filtered in the hook; got {r.calls!r}"


def test_an_ordinary_source_under_a_dot_claude_sibling_still_runs(tmp_path):
    """The other half — the skip is scoped to `.claude/plans/` and `.claude/settings*`
    and must not swallow a project that happens to have `claude` in a path
    component."""
    r = _run(tmp_path, _edit("/p/claude/plans/blink.c"), stub=_OK)
    assert r.snapshots, f"calls={r.calls!r}"


# ── the project root is stated, not guessed ─────────────────────────────────

def test_the_payloads_cwd_is_passed_as_the_project_root(tmp_path):
    """`build snapshot` resolves its root from `--project-root` or `Path.cwd()`.
    Passing neither left every reader guessing the same way — `turn-reap.sh` swept
    a directory it had to reproduce the writer's guess to find, and a session
    running in a subdirectory of a repo made the two disagree."""
    r = _run(tmp_path, _edit("/p/blink.c", cwd=r"C:\proj\firmware"), stub=_OK)
    assert opt(r.snapshots[0], "--project-root") == r"C:\proj\firmware"


def test_the_native_path_is_passed_through_unchanged(tmp_path):
    """A `/c/...` conversion reaches Python on Windows as a rooted path on the
    CURRENT DRIVE (`C:\\c\\...`), which is a different directory that usually does
    not exist. Same rule as `turn-reap.sh`."""
    r = _run(tmp_path, _edit("/p/blink.c", cwd=r"C:\proj"), stub=_OK)
    root = opt(r.snapshots[0], "--project-root")
    assert not root.startswith("/c/"), root


def test_a_project_root_with_a_space_survives_as_one_argument(tmp_path):
    """The reason this file's stub records argument BOUNDARIES: `"$*"` joins on a
    space, so an unquoted expansion renders identically to a quoted one and the
    space test passes with the quoting deleted."""
    r = _run(tmp_path, _edit("/p/blink.c", cwd=r"C:\My Projects\fw"), stub=_OK)
    assert opt(r.snapshots[0], "--project-root") == r"C:\My Projects\fw"


def test_no_cwd_in_the_payload_omits_the_flag_rather_than_inventing_one(tmp_path):
    """Omitting it gives the CLI `Path.cwd()`, which is the behaviour every install
    already has. Passing an empty string would resolve to the CLI's cwd too — but
    via `Path("")`, which is `.` only by accident, and it would make `--project-root`
    look answered to anything reading the argv."""
    payload = _edit("/p/blink.c")
    payload.pop("cwd", None)
    r = _run(tmp_path, payload, stub=_OK,
             env_extra={"CLAUDE_PROJECT_DIR": ""})
    assert opt(r.snapshots[0], "--project-root") is None, r.snapshots[0]


# ── which target's object gets frozen ───────────────────────────────────────

def test_the_recorded_loci_target_is_passed(tmp_path):
    """Without it `_canonical_object` ranks every target directory. That keeps a
    turn's baseline stable but cannot know which target this session builds, so on a
    project built for two the baseline can land beside the object the next compile
    does not write and the report degrades to absolute-only."""
    proj = tmp_path / "proj"
    proj.mkdir()
    _context(tmp_path, proj, "armv7e-m")
    r = _run(tmp_path, _edit("/p/blink.c", cwd=str(proj)), stub=_OK)
    assert opt(r.snapshots[0], "--loci-target") == "armv7e-m"


@pytest.mark.parametrize("recorded", ["unknown", "null", ""])
def test_an_unresolved_target_is_not_passed(tmp_path, recorded):
    """`--loci-target` is argparse `choices`-constrained, so a value detection never
    resolved is a USAGE error — and the retry drops `--turn` along with it, trading
    the turn's whole first-write-wins guarantee for nothing."""
    proj = tmp_path / "proj"
    proj.mkdir()
    _context(tmp_path, proj, recorded)
    r = _run(tmp_path, _edit("/p/blink.c", cwd=str(proj)), stub=_OK)
    assert opt(r.snapshots[0], "--loci-target") is None, r.snapshots[0]
    assert r.turn_of(0) == _PROMPT_ID, "the turn id must survive regardless"


def test_another_projects_context_is_not_read(tmp_path):
    """The state directory holds one file per project. Keying on the recorded
    `project_root` rather than on "the first file that parses" is what stops a
    sibling project's target being stamped onto this one's baseline."""
    proj = tmp_path / "proj"
    other = tmp_path / "other"
    proj.mkdir()
    other.mkdir()
    _context(tmp_path, other, "tc399")
    r = _run(tmp_path, _edit("/p/blink.c", cwd=str(proj)), stub=_OK)
    assert opt(r.snapshots[0], "--loci-target") is None, r.snapshots[0]


def test_no_context_file_at_all_still_snapshots(tmp_path):
    """session-init may never have run — a plugin installed mid-session, or a
    `claude --continue` that missed the `startup` matcher. The target is an
    improvement, never a precondition."""
    proj = tmp_path / "proj"
    proj.mkdir()
    r = _run(tmp_path, _edit("/p/blink.c", cwd=str(proj)), stub=_OK)
    assert len(r.snapshots) == 1
    assert r.turn_of(0) == _PROMPT_ID


# ── the pre-scan says what it looked at ─────────────────────────────────────

def test_an_edits_replacement_text_is_sent_as_a_fragment(tmp_path):
    """The Edit tool passes `new_string` — the replacement text alone. Sent without
    a kind, `loci scan` applied its `file` default, and the call-graph checks are
    ABSENCE tests: "no early-return base case" was asserted about a function on the
    evidence of the lines that replaced part of it."""
    r = _run(tmp_path, _edit("/p/blink.c"), stub=_OK)
    assert opt(r.scans[0], "--content-kind") == "fragment"


def test_a_writes_whole_content_is_sent_as_a_file(tmp_path):
    """The other half. A Write's `content` really is the whole translation unit, so
    the whole-file rules are sound for it — sending `fragment` for everything would
    make the flag decorative."""
    r = _run(tmp_path, {
        "hook_event_name": "PreToolUse", "tool_name": "Write",
        "prompt_id": _PROMPT_ID,
        "tool_input": {"file_path": "/p/newfile.c", "content": "int g(void){return 1;}"},
    }, stub=_OK)
    assert opt(r.scans[0], "--content-kind") == "file"


def test_the_scan_path_is_joined_so_a_leading_dash_is_not_an_option(tmp_path):
    """`--path <value>` lets a value beginning with `-` become the next option;
    argparse then answers "expected one argument" and exits 2. `post-edit-hook.sh`
    already uses the joined form."""
    r = _run(tmp_path, _edit("/p/-weird.c"), stub=_OK)
    assert "--path" not in r.scans[0], r.scans[0]
    assert opt(r.scans[0], "--path") == "/p/-weird.c"


def test_an_older_cli_still_gets_the_pre_scan(tmp_path):
    """`--content-kind` postdates the pin, and the pinned CLI is the one production
    runs. An unconditional flag would take the pre-scan away from every install
    today — so on a usage error the hook asks again without it."""
    r = _run(tmp_path, _edit("/p/blink.c"), stub=_NO_NEW_FLAGS)
    assert len(r.scans) == 2, f"scans={r.scans!r}"
    assert opt(r.scans[1], "--content-kind") is None
    assert opt(r.scans[1], "--path") == "/p/blink.c"


def test_the_pre_scan_report_is_surfaced(tmp_path):
    """What the scoping is FOR. The report is piped straight into the model's
    context, so it is the text a scope note has to land in."""
    stub = ("echo '{\"ok\":true,\"data\":{\"report\":"
            "\"[loci · pre-scan] f: call graph clean in the edited text\"}}'")
    r = _run(tmp_path, _edit("/p/blink.c"), stub=stub)
    assert "in the edited text" in r.out, r.out


def test_the_project_root_is_sent_in_the_joined_form(tmp_path):
    """Asserted on the raw argv, because `opt()` deliberately reads both spellings —
    so every other assertion about `--project-root` passes under either, and
    switching to the separate form survived a mutation campaign.

    The joined form is what keeps a value beginning with `-` from being read as the
    next option; argparse then answers "expected one argument" and exits 2, which
    this hook cannot tell from "this CLI does not know the flag" — so it would drop
    `--turn` and lose the turn's baseline over a path."""
    r = _run(tmp_path, _edit("/p/blink.c", cwd=r"C:\proj"), stub=_OK)
    call = r.snapshots[0]
    assert "--project-root" not in call, call
    assert r"--project-root=C:\proj" in call, call


def test_the_turn_and_target_are_sent_in_the_joined_form(tmp_path):
    """Same property, same reason, for the two flags whose values this hook does not
    control at all: `prompt_id` is undocumented, and the target comes from another
    repo's detector."""
    proj = tmp_path / "proj"
    proj.mkdir()
    _context(tmp_path, proj, "armv6-m")
    r = _run(tmp_path, _edit("/p/blink.c", cwd=str(proj)), stub=_OK)
    call = r.snapshots[0]
    assert "--turn" not in call and "--loci-target" not in call, call
    assert f"--turn={_PROMPT_ID}" in call and "--loci-target=armv6-m" in call, call


def test_the_context_file_is_the_only_one_opened(tmp_path):
    """The lookup names ONE file. It used to pass every `project-context-*.json` to a
    single `jq` and loop over the results, which was wrong twice: the `IFS=$(printf
    '\t')` prefix on the `read` re-forked a command substitution per iteration (6.4 s
    at 500 files, against this hook's 8 s budget), and the glob hit the Windows 32 KB
    argv limit at about 200 files and silently returned nothing. Both scaled with a
    directory that only grows.

    Pinned by cost rather than by inspection: 400 decoy contexts must not slow the
    hook down, and one of them holding a different target must not be read."""
    proj = tmp_path / "proj"
    proj.mkdir()
    _context(tmp_path, proj, "armv6-m")
    state = tmp_path / ".loci" / "state"
    for i in range(400):
        (state / f"project-context-{i:012x}.json").write_text(
            json.dumps({"project_root": f"/nowhere/{i}", "loci_target": "tc399"}),
            encoding="utf-8")

    import time
    start = time.monotonic()
    r = _run(tmp_path, _edit("/p/blink.c", cwd=str(proj)), stub=_OK)
    elapsed = time.monotonic() - start
    assert opt(r.snapshots[0], "--loci-target") == "armv6-m", r.snapshots[0]
    # Generous by design — this is a cliff detector, not a benchmark. The scanning
    # version took seconds here and returned nothing at all past ~200 files.
    assert elapsed < 8, f"the hook took {elapsed:.1f}s with 400 context files"
