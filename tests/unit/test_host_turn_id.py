"""A turn id when the host sends no `prompt_id` (`lib/loci_host.sh`, AAD-7781).

Every turn-scoped thing LOCI does keys on the `prompt_id` Claude Code puts on
every hook payload of a user turn: the stamp in `.loci/build/turn/current`, the
`[loci] turn=` context line, the first-write-wins pre-edit snapshot, the
post-edit reminder's `--turn`, the Stop-time manifest check and the turn-tree
clean. GitHub Copilot CLI (Epic AAD-7779) sends `session_id` and a `timestamp`
and never `prompt_id`, so under it all of that fell through in silence.

The host adapter mints an id at `UserPromptSubmit`, records it per session in
the state directory (`turn-<session_id>`), and every later hook of the session
injects it as `prompt_id` before its own reads and before the payload reaches a
`loci hook …` verb. What is load-bearing, and pinned here:

* **The Claude Code path is untouched.** Without `COPILOT_CLI` the adapter is
  one environment-variable test; with it, a payload that already carries
  `prompt_id` wins. The thin hooks that pass stdin through unread keep doing so.
* **One id per prompt, the same id for every hook of that prompt**, across
  events, and a different one for the next prompt and for another session.
* **The file is the session's only and cannot name anything else**: the host's
  `session_id` is sanitised into the file name, and a value in the file that
  this library did not mint is not used.
* **The injection lands inside the bounded prefix** `loci_json_load` parses,
  whatever the payload's size.
* **Bash 3.2 runs it** (stock macOS, AAD-7771): the file uses none of the
  constructs bash 4 added.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
LIB = PLUGIN_ROOT / "lib" / "loci_host.sh"
HOOKS = PLUGIN_ROOT / "hooks"

_RS = "\x1e"
_ID_RE = re.compile(r"^cp-[0-9]+-[0-9a-f]+$")


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


def _payload(event: str, session: str = "sess-A", **extra) -> dict:
    """A Copilot-shaped payload: `session_id`, `timestamp`, `cwd`, no `prompt_id`."""
    doc = {"hook_event_name": event, "session_id": session,
           "timestamp": "2026-09-30T19:00:00.000Z", "cwd": "C:\\proj"}
    doc.update(extra)
    return doc


class Adapted:
    def __init__(self, raw: str):
        parts = raw.split(_RS)
        self.rc = int(parts[0])
        self.raw = parts[1]
        self.prompt_id = parts[2]
        # What `after` printed, one field per RS it wrote.
        self.extra = parts[3:]

    @property
    def changed(self) -> bool:
        return self.rc == 0

    @property
    def doc(self) -> dict:
        return json.loads(self.raw)


def _adapt(tmp_path: Path, payload: dict | str, *, copilot: bool = True,
           state_dir: Path | None = None, home: Path | None = None,
           after: str = "") -> Adapted:
    """Source the library and call `loci_host_adapt` on the payload.

    `after` is extra bash run once the adapter has answered, with
    `LOCI_HOST_PAYLOAD` and `LOCI_HOST_PROMPT_ID` set; whatever it prints is
    appended after the third field."""
    home = home if home is not None else tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": _to_bash_path(home)}
    if state_dir is not None:
        env["LOCI_STATE_DIR"] = _to_bash_path(state_dir)
    if copilot:
        env["COPILOT_CLI"] = "1"
    # Through a file, not the environment: a 200 KB payload is over Linux's
    # per-string environment limit (E2BIG, "Argument list too long").
    payload_file = tmp_path / "payload.json"
    payload_file.write_text(payload if isinstance(payload, str) else json.dumps(payload),
                            encoding="utf-8", newline="\n")
    script = (
        f". '{_to_bash_path(LIB)}'\n"
        f"LOCI_TEST_PAYLOAD=$(cat '{_to_bash_path(payload_file)}')\n"
        'loci_host_adapt "$LOCI_TEST_PAYLOAD"; rc=$?\n'
        "printf '%s\\036%s\\036%s' \"$rc\" \"$LOCI_HOST_PAYLOAD\" \"$LOCI_HOST_PROMPT_ID\"\n"
        f"{after}\n"
    )
    proc = subprocess.run([_find_bash(), "-c", script], capture_output=True,
                          text=True, timeout=60, env=env)
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == "", f"the adapter must be silent on stderr: {proc.stderr!r}"
    return Adapted(proc.stdout)


def _turn_file(state_dir: Path, session: str = "sess-A") -> Path:
    return state_dir / f"turn-{session}"


# ── the Claude Code path ─────────────────────────────────────────────────────

def test_outside_copilot_nothing_is_read_written_or_changed(tmp_path):
    """The gate is `COPILOT_CLI`, which Claude Code does not export. A payload
    with no `prompt_id` — a Claude host that dropped it, a test fixture — is left
    exactly as it came, and the state directory is not even created."""
    state = tmp_path / "state"
    doc = _payload("UserPromptSubmit")
    a = _adapt(tmp_path, doc, copilot=False, state_dir=state)
    assert not a.changed
    # Not even the output globals are touched: the hook keeps its own copy.
    assert a.raw == "" and a.prompt_id == ""
    assert not state.exists(), "the Claude Code path must not touch the state directory"


def test_the_hosts_prompt_id_wins_even_under_copilot(tmp_path):
    """The one field the whole Epic keys on. A host that sends `prompt_id` — Claude
    Code, or a Copilot that learns to — is never second-guessed: no mint, no file,
    no rewrite, and the id it sent is the one reported."""
    state = tmp_path / "state"
    doc = _payload("UserPromptSubmit", prompt_id="5e1b8673-df09-42d3-a338-c13726ff8d32")
    a = _adapt(tmp_path, doc, state_dir=state)
    assert not a.changed
    assert a.raw == json.dumps(doc)
    assert a.prompt_id == "5e1b8673-df09-42d3-a338-c13726ff8d32"
    assert not _turn_file(state).exists(), "a host id must not be recorded as a minted one"


def test_an_empty_prompt_id_from_the_host_still_wins(tmp_path):
    """The rule is on the KEY. Minting beside an empty `prompt_id` would put two
    keys in one object, which the shell readers (first wins) and the Python
    verbs (last wins) would answer differently."""
    state = tmp_path / "state"
    doc = _payload("UserPromptSubmit", prompt_id="")
    a = _adapt(tmp_path, doc, state_dir=state)
    assert not a.changed
    assert a.raw == json.dumps(doc)
    assert a.prompt_id == ""
    assert not _turn_file(state).exists()


# ── one id per prompt, shared by every hook of the prompt ───────────────────

def test_a_prompt_mints_records_and_injects_one_id(tmp_path):
    state = tmp_path / "state"
    doc = _payload("UserPromptSubmit", prompt="edit main.c")
    a = _adapt(tmp_path, doc, state_dir=state)
    assert a.changed
    assert _ID_RE.match(a.prompt_id), a.prompt_id
    out = a.doc
    assert out["prompt_id"] == a.prompt_id
    assert {k: v for k, v in out.items() if k != "prompt_id"} == doc, \
        "every other field must ride through untouched"
    assert list(out)[0] == "prompt_id", \
        "the id is injected at the FRONT, inside the prefix loci_json_load parses"
    assert _turn_file(state).read_text(encoding="utf-8").strip() == a.prompt_id


@pytest.mark.parametrize("event,extra", [
    ("PreToolUse", {"tool_name": "Edit",
                    "tool_input": {"path": "C:\\proj\\main.c", "old_str": "a", "new_str": "b"}}),
    ("PostToolUse", {"tool_name": "Write", "tool_input": {"path": "C:\\proj\\a.c"},
                     "tool_result": {"result_type": "success"}}),
    ("Stop", {"stop_reason": "end_turn", "stop_hook_active": False}),
])
def test_every_later_hook_of_the_session_resolves_the_prompts_id(tmp_path, event, extra):
    state = tmp_path / "state"
    minted = _adapt(tmp_path, _payload("UserPromptSubmit"), state_dir=state).prompt_id
    a = _adapt(tmp_path, _payload(event, **extra), state_dir=state)
    assert a.changed
    assert a.prompt_id == minted
    assert a.doc["prompt_id"] == minted
    assert list(a.doc)[0] == "prompt_id"


def test_stop_ends_the_turn_but_leaves_the_file(tmp_path):
    """The Stop hooks run in parallel and every one of them reads the id; and a
    `--resume`d session gets its fresh id from the next UserPromptSubmit, which
    overwrites the file. So nothing deletes it at Stop."""
    state = tmp_path / "state"
    minted = _adapt(tmp_path, _payload("UserPromptSubmit"), state_dir=state).prompt_id
    _adapt(tmp_path, _payload("Stop"), state_dir=state)
    assert _turn_file(state).read_text(encoding="utf-8").strip() == minted
    again = _adapt(tmp_path, _payload("Stop"), state_dir=state)
    assert again.prompt_id == minted, "a second Stop hook must see the same id"


def test_two_prompts_get_two_ids_and_the_later_hooks_follow_the_newest(tmp_path):
    state = tmp_path / "state"
    first = _adapt(tmp_path, _payload("UserPromptSubmit"), state_dir=state).prompt_id
    second = _adapt(tmp_path, _payload("UserPromptSubmit"), state_dir=state).prompt_id
    assert first != second
    assert _turn_file(state).read_text(encoding="utf-8").strip() == second
    assert _adapt(tmp_path, _payload("PreToolUse"), state_dir=state).prompt_id == second


def test_sessions_do_not_share_an_id(tmp_path):
    state = tmp_path / "state"
    a_id = _adapt(tmp_path, _payload("UserPromptSubmit", "sess-A"), state_dir=state).prompt_id
    # Session B has not prompted yet: its hooks get what the host sent, nothing.
    b_hook = _adapt(tmp_path, _payload("PreToolUse", "sess-B"), state_dir=state)
    assert not b_hook.changed and b_hook.prompt_id == ""
    b_id = _adapt(tmp_path, _payload("UserPromptSubmit", "sess-B"), state_dir=state).prompt_id
    assert b_id != a_id
    assert _adapt(tmp_path, _payload("PostToolUse", "sess-A"), state_dir=state).prompt_id == a_id
    assert _adapt(tmp_path, _payload("PostToolUse", "sess-B"), state_dir=state).prompt_id == b_id


def test_a_hook_before_the_first_prompt_gets_nothing(tmp_path):
    """SessionStart and any hook of a session with no prompt on record. The
    payload goes on as the host sent it — the same degraded state as today, and
    never an id that belongs to nobody."""
    state = tmp_path / "state"
    for event in ("SessionStart", "PreToolUse", "Stop"):
        a = _adapt(tmp_path, _payload(event, "fresh"), state_dir=state)
        assert not a.changed and a.prompt_id == "", event
        assert "prompt_id" not in a.doc


# ── the file is the session's and nothing else's ────────────────────────────

def test_no_session_id_means_no_id(tmp_path):
    state = tmp_path / "state"
    doc = {"hook_event_name": "UserPromptSubmit", "prompt": "hi"}
    a = _adapt(tmp_path, doc, state_dir=state)
    assert not a.changed and a.prompt_id == ""
    assert a.raw == json.dumps(doc)
    if state.exists():
        assert not list(state.glob("turn-*"))


@pytest.mark.parametrize("session", ["../../escape", "a/b", "..", "with space", "x\ty"])
def test_a_session_id_cannot_name_a_file_outside_the_state_directory(tmp_path, session):
    state = tmp_path / "state"
    a = _adapt(tmp_path, _payload("UserPromptSubmit", session), state_dir=state)
    if session == "..":
        assert not a.changed, "a session id that is only dots names no file"
        return
    assert a.changed
    files = list(state.glob("turn-*"))
    assert len(files) == 1, files
    assert re.fullmatch(r"turn-[A-Za-z0-9._-]+", files[0].name), files[0].name
    assert files[0].read_text(encoding="utf-8").strip() == a.prompt_id
    # Nothing landed beside or above the state directory.
    assert not (tmp_path / "escape").exists()
    assert not (state / "a").exists()


@pytest.mark.parametrize("content", [
    "../../x", "5e1b8673-df09-42d3-a338-c13726ff8d32", "cp-1/evil", "cp-1-ABC", "", "cp-",
])
def test_a_value_this_library_did_not_mint_is_not_used(tmp_path, content):
    """The file sits under the user's home. A value that is not one of ours would
    otherwise reach `--turn=` and the turn tree's name."""
    state = tmp_path / "state"
    state.mkdir()
    _turn_file(state).write_text(content + "\n", encoding="utf-8", newline="\n")
    a = _adapt(tmp_path, _payload("PreToolUse"), state_dir=state)
    assert not a.changed and a.prompt_id == "", content


def test_a_file_with_windows_line_ends_still_resolves(tmp_path):
    """The adapter writes LF; a hand-edited file on Windows may carry CRLF, and a
    CR is not part of the id (it would fail the alphabet and drop the turn)."""
    state = tmp_path / "state"
    state.mkdir()
    _turn_file(state).write_bytes(b"cp-1700000000-0badf00d\r\n")
    a = _adapt(tmp_path, _payload("PreToolUse"), state_dir=state)
    assert a.changed and a.prompt_id == "cp-1700000000-0badf00d"


def test_the_minted_alphabet_is_path_and_flag_safe(tmp_path):
    """`turn-clean.sh` refuses an id with a slash and passes `--turn=<id>` joined
    because a leading `-` would be read as an option; the CLI hashes the id into a
    directory name. Lower-case hex, digits and dashes, starting with `cp-`."""
    state = tmp_path / "state"
    ids = {_adapt(tmp_path, _payload("UserPromptSubmit"), state_dir=state).prompt_id
           for _ in range(5)}
    assert len(ids) == 5, "five prompts in a row must mint five ids"
    for turn in ids:
        assert _ID_RE.match(turn), turn
        assert turn == turn.lower()


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0,
                    reason="a read-only directory is a POSIX permission; root ignores it")
def test_a_mint_that_cannot_be_recorded_injects_nothing_and_ends_the_stale_record(tmp_path):
    """The id would reach `loci hook prompt-submit` (stamp, context line) while
    every later hook of the turn read the PREVIOUS prompt's id from the untouched
    file — a first-write-wins baseline from the wrong turn. So: no injection, and
    the old record is emptied (the directory may forbid unlinking it) so the
    later hooks get nothing rather than the wrong id."""
    state = tmp_path / "state"
    old = _adapt(tmp_path, _payload("UserPromptSubmit"), state_dir=state).prompt_id
    state.chmod(0o555)
    try:
        doc = _payload("UserPromptSubmit", prompt="second")
        a = _adapt(tmp_path, doc, state_dir=state)
        assert not a.changed
        assert a.raw == json.dumps(doc)
        assert a.prompt_id == ""
        later = _adapt(tmp_path, _payload("PreToolUse"), state_dir=state)
        assert not later.changed and later.prompt_id == "", \
            f"a later hook must not resolve the previous prompt's id {old}"
        assert not list(state.glob("turn-*.tmp-*")), "no temp file is left behind"
    finally:
        state.chmod(0o755)


# ── the injection sits where the hooks read ─────────────────────────────────

def test_the_id_is_readable_from_the_bounded_prefix_of_a_large_payload(tmp_path):
    """The edit hooks read `prompt_id` with `loci_json_get`, which parses only
    the first `LOCI_JSON_MAX` bytes. A Write payload carries a whole file behind
    `session_id`, so the id must be injected ahead of everything."""
    state = tmp_path / "state"
    minted = _adapt(tmp_path, _payload("UserPromptSubmit"), state_dir=state).prompt_id
    big = _payload("PreToolUse", tool_name="Write",
                   tool_input={"path": "C:\\proj\\big.c", "file_text": "x" * 200_000})
    a = _adapt(tmp_path, big, state_dir=state, after=(
        f". '{_to_bash_path(PLUGIN_ROOT / 'lib' / 'loci_json.sh')}'\n"
        'loci_json_load "$LOCI_HOST_PAYLOAD"\n'
        "printf '\\036%s' \"$(loci_json_get prompt_id)\"\n"))
    assert a.rc == 0 and a.prompt_id == minted
    # The fourth field is what the hooks' own reader answers.
    assert a.extra == [minted]


@pytest.mark.parametrize("raw", ["[1,2]", "  {\"hook_event_name\":\"Stop\",\"session_id\":\"s\"}", "", "null"])
def test_a_payload_that_is_not_an_object_at_its_first_byte_is_left_alone(tmp_path, raw):
    """Every host payload is `JSON.stringify` output and starts with `{`. Anything
    else is not rewritten — a rewrite that guessed wrong would corrupt the JSON
    the verbs parse, which is worse than the missing id."""
    state = tmp_path / "state"
    state.mkdir()
    _turn_file(state, "s").write_text("cp-1700000000-abc\n", encoding="utf-8", newline="\n")
    a = _adapt(tmp_path, raw, state_dir=state)
    assert not a.changed
    assert a.raw == raw


# ── the state directory ladder, and the sweep ───────────────────────────────

def test_the_file_lives_under_home_when_no_state_dir_is_pinned(tmp_path):
    home = tmp_path / "home"
    a = _adapt(tmp_path, _payload("UserPromptSubmit"), home=home)
    assert a.changed
    assert (home / ".loci" / "state" / "turn-sess-A").read_text(
        encoding="utf-8").strip() == a.prompt_id


def test_session_start_sweeps_only_this_librarys_stale_files(tmp_path):
    """One file per session accumulates otherwise. The age is read out of the id
    itself (no `find`, whose name resolves to System32's FIND.EXE on some Windows
    PATHs; no `stat`), so only a file holding a `cp-<epoch>-…` id is ever aged,
    and the CLI's own `turn-intent-*` notes are stepped over by name."""
    state = tmp_path / "state"
    state.mkdir()
    now = int(time.time())
    old, fresh = now - 9 * 86400, now - 3600
    (state / "turn-old").write_text(f"cp-{old}-abc\n", encoding="utf-8", newline="\n")
    (state / "turn-fresh").write_text(f"cp-{fresh}-abc\n", encoding="utf-8", newline="\n")
    (state / "turn-intent-cp-1-x.txt").write_text(f"cp-{old}-abc\n", encoding="utf-8", newline="\n")
    (state / "turn-foreign").write_text("not-ours\n", encoding="utf-8", newline="\n")
    (state / "turn-dir").mkdir()
    a = _adapt(tmp_path, _payload("SessionStart", "new-session", source="new"), state_dir=state)
    assert not a.changed
    assert not (state / "turn-old").exists(), "a file older than the retention is swept"
    assert (state / "turn-fresh").exists()
    assert (state / "turn-intent-cp-1-x.txt").exists(), "the CLI's notes are the CLI's"
    assert (state / "turn-foreign").exists(), "a value we did not mint has no age"
    assert (state / "turn-dir").is_dir()


def test_a_temp_file_a_killed_mint_left_is_swept_by_the_same_age(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    now = int(time.time())
    (state / "turn-dead.tmp-4242").write_text(f"cp-{now - 9 * 86400}-abc\n",
                                             encoding="utf-8", newline="\n")
    (state / "turn-live.tmp-4243").write_text(f"cp-{now - 60}-abc\n",
                                             encoding="utf-8", newline="\n")
    _adapt(tmp_path, _payload("SessionStart", "new-session", source="new"), state_dir=state)
    assert not (state / "turn-dead.tmp-4242").exists()
    assert (state / "turn-live.tmp-4243").exists()


def test_a_prompt_does_not_sweep(tmp_path):
    """The sweep is once per session, on SessionStart, never on the prompt path."""
    state = tmp_path / "state"
    state.mkdir()
    (state / "turn-old").write_text(f"cp-{int(time.time()) - 30 * 86400}-abc\n", encoding="utf-8", newline="\n")
    _adapt(tmp_path, _payload("UserPromptSubmit"), state_dir=state)
    assert (state / "turn-old").exists()


# ── bash 3.2 ────────────────────────────────────────────────────────────────

def test_the_library_uses_no_bash_4_construct():
    src = LIB.read_text(encoding="utf-8")
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    for needle, why in [
        ("declare -A", "associative arrays are bash 4"),
        ("declare -n", "namerefs are bash 4.3"),
        ("local -n", "namerefs are bash 4.3"),
        ("mapfile", "bash 4"),
        ("readarray", "bash 4"),
        (",,}", "case modification is bash 4"),
        ("^^}", "case modification is bash 4"),
        ("&>", "bash 4 redirection"),
        ("|&", "bash 4 pipe"),
        ("printf -v", "not in 3.2 with the %(…)T format the log uses"),
    ]:
        assert needle not in code, f"{needle}: {why}"
    assert "${EPOCHSECONDS:-}" in src and "date +%s" in src, \
        "EPOCHSECONDS (bash 5) needs its `date` fallback"


# ── the hooks, end to end ───────────────────────────────────────────────────

def _stub_loci(home: Path, body: str = "exit 0") -> tuple[Path, Path]:
    """A `loci` that records argv (RS-separated, GS after each call) and stdin
    (GS after each call), then runs `body`."""
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    args_log, stdin_log = home / "args.log", home / "stdin.log"
    (bin_dir / "loci").write_text(
        "#!/usr/bin/env bash\n"
        f'{{ for a in "$@"; do printf "%s\\036" "$a"; done; printf "\\035"; }} '
        f'>> "{_to_bash_path(args_log)}"\n'
        f'{{ cat; printf "\\035"; }} >> "{_to_bash_path(stdin_log)}"\n'
        f"{body}\n", encoding="utf-8")
    (bin_dir / "loci").chmod(0o755)
    return args_log, stdin_log


class HookRun:
    def __init__(self, proc, args_log: Path, stdin_log: Path):
        self.proc = proc
        self.out = proc.stdout
        self.calls: list[list[str]] = []
        if args_log.is_file():
            for chunk in args_log.read_bytes().split(b"\x1d"):
                if chunk:
                    self.calls.append([a.decode("utf-8") for a in chunk.split(b"\x1e")[:-1]])
        self.stdins: list[str] = []
        if stdin_log.is_file():
            self.stdins = [c.decode("utf-8")
                           for c in stdin_log.read_bytes().split(b"\x1d")[:-1]]

    def call(self, *verb: str) -> list[str] | None:
        return next((c for c in self.calls if tuple(c[:len(verb)]) == verb), None)

    def stdin_of(self, *verb: str) -> dict | None:
        for c, s in zip(self.calls, self.stdins):
            if tuple(c[:len(verb)]) == verb:
                return json.loads(s)
        return None


def _run_hook(tmp_path: Path, hook: str, payload: dict, *, copilot: bool = True,
              body: str = "exit 0", env_extra: dict | None = None,
              project: Path | None = None) -> HookRun:
    home = tmp_path / "home"
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    args_log, stdin_log = _stub_loci(home, body)
    env = {
        "PATH": f"{_to_bash_path(home / '.local' / 'bin')}:/usr/bin:/bin:/usr/local/bin",
        "HOME": _to_bash_path(home),
        "LOCI_STATE_DIR": _to_bash_path(state),
    }
    if project is not None:
        env["CLAUDE_PROJECT_DIR"] = _to_bash_path(project)
    if copilot:
        env["COPILOT_CLI"] = "1"
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run([_find_bash(), _to_bash_path(HOOKS / hook)],
                          input=json.dumps(payload), capture_output=True, text=True,
                          timeout=60, env=env, cwd=str(tmp_path))
    assert proc.returncode == 0, f"{hook} must always exit 0; got {proc.returncode}: {proc.stderr!r}"
    assert proc.stderr == "", f"{hook} must be silent on stderr: {proc.stderr!r}"
    # stdout is what the host parses: nothing, or exactly one JSON object.
    out = proc.stdout.strip()
    if out:
        assert isinstance(json.loads(out), dict), f"{hook} stdout is not one JSON object: {out!r}"
    return HookRun(proc, args_log, stdin_log)


def _record(tmp_path: Path, session: str = "sess-A", turn: str = "cp-1700000000-0badf00d") -> str:
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    _turn_file(state, session).write_text(turn + "\n", encoding="utf-8", newline="\n")
    return turn


def test_prompt_submit_hands_the_verb_a_payload_carrying_the_minted_id(tmp_path):
    """`loci hook prompt-submit` stamps `.loci/build/turn/current` and echoes
    `[loci] turn=<id>` from the `prompt_id` it reads; under Copilot that field is
    the one this hook minted, and the file holds the same value."""
    r = _run_hook(tmp_path, "prompt-submit-turn.sh", _payload("UserPromptSubmit", prompt="hi"))
    seen = r.stdin_of("hook", "prompt-submit")
    assert seen is not None, r.calls
    assert _ID_RE.match(seen["prompt_id"]), seen
    assert _turn_file(tmp_path / "state").read_text(encoding="utf-8").strip() == seen["prompt_id"]
    assert seen["prompt"] == "hi" and seen["session_id"] == "sess-A"


def test_prompt_submit_outside_copilot_passes_stdin_through_untouched(tmp_path):
    """The Claude Code contract of `test_prompt_submit_turn.py`, restated against a
    payload with NO `prompt_id`: nothing is minted, nothing is injected."""
    doc = _payload("UserPromptSubmit", prompt="hi")
    r = _run_hook(tmp_path, "prompt-submit-turn.sh", doc, copilot=False)
    assert r.stdin_of("hook", "prompt-submit") == doc
    assert not list((tmp_path / "state").glob("turn-*"))


def test_prompt_submit_adapts_the_payload_the_logger_already_captured(tmp_path):
    """In dev mode the logger reads stdin first (`loci_hook_payload_read`) and the
    hook re-feeds `LOCI_HOOK_PAYLOAD`; the adapter must then work on THAT copy,
    since stdin is gone."""
    r = _run_hook(tmp_path, "prompt-submit-turn.sh", _payload("UserPromptSubmit"),
                  env_extra={"LOCI_ENV": "dev"})
    seen = r.stdin_of("hook", "prompt-submit")
    assert seen is not None and _ID_RE.match(seen["prompt_id"]), seen


@pytest.mark.parametrize("hook,verb", [
    ("post-bash-bypass.sh", ("hook", "post-bash")),
    ("manifest-status-nudge.sh", ("analyse", "status")),
])
def test_the_thin_hooks_hand_the_verb_the_sessions_id(tmp_path, hook, verb):
    turn = _record(tmp_path)
    event = "PostToolUse" if hook.startswith("post-bash") else "Stop"
    doc = _payload(event, tool_name="Bash", tool_input={"command": "make"})
    r = _run_hook(tmp_path, hook, doc)
    seen = r.stdin_of(*verb)
    assert seen is not None, r.calls
    assert seen["prompt_id"] == turn
    assert {k: v for k, v in seen.items() if k != "prompt_id"} == doc


def test_fail_fast_hands_the_verb_the_captured_and_adapted_payload(tmp_path):
    """`loci_fail_fast_run` re-feeds `LOCI_HOOK_PAYLOAD` when it is set. Under
    Copilot that is the adapter's copy — stdin is already consumed — so the
    verb must still see the id, not an empty stdin."""
    turn = _record(tmp_path)
    doc = _payload("PostToolUse", tool_name="Bash", tool_input={"command": "make"})
    r = _run_hook(tmp_path, "post-bash-bypass.sh", doc, env_extra={"LOCI_FAIL_FAST": "1"})
    seen = r.stdin_of("hook", "post-bash")
    assert seen is not None, r.calls
    assert seen["prompt_id"] == turn


@pytest.mark.parametrize("hook,verb", [
    ("post-bash-bypass.sh", ("hook", "post-bash")),
    ("manifest-status-nudge.sh", ("analyse", "status")),
])
def test_the_thin_hooks_outside_copilot_pass_stdin_through(tmp_path, hook, verb):
    _record(tmp_path)
    doc = _payload("PostToolUse", tool_name="Bash", tool_input={"command": "make"})
    r = _run_hook(tmp_path, hook, doc, copilot=False)
    assert r.stdin_of(*verb) == doc


def test_turn_clean_retires_the_sessions_turn_tree_at_stop(tmp_path):
    turn = _record(tmp_path)
    root = tmp_path / "proj"
    (root / ".loci" / "build" / "turns").mkdir(parents=True)
    doc = _payload("Stop", cwd=str(root), stop_hook_active=False)
    r = _run_hook(tmp_path, "turn-clean.sh", doc)
    clean = r.call("build", "clean")
    assert clean is not None, r.calls
    assert f"--turn={turn}" in clean, clean


def test_turn_clean_outside_copilot_passes_no_turn_it_was_not_given(tmp_path):
    _record(tmp_path)
    root = tmp_path / "proj"
    (root / ".loci" / "build" / "turns").mkdir(parents=True)
    doc = _payload("Stop", cwd=str(root), stop_hook_active=False)
    r = _run_hook(tmp_path, "turn-clean.sh", doc, copilot=False)
    clean = r.call("build", "clean")
    assert clean is not None, r.calls
    assert not any(t.startswith("--turn=") for t in clean), clean


_SCAN_OK = "echo '{\"ok\":true,\"data\":{\"report\":\"\",\"snapshotted\":true,\"measurable\":true}}'"


def test_pre_edit_snapshots_under_the_sessions_turn(tmp_path):
    """The first-write-wins baseline: `loci build snapshot --turn=<id>`, and the
    payload `loci hook edit-scan` reads carries the same id."""
    turn = _record(tmp_path)
    root = tmp_path / "proj"
    root.mkdir()
    src = root / "main.c"
    src.write_text("int f(void) { return 1; }\n", encoding="utf-8")
    doc = _payload("PreToolUse", cwd=str(root), tool_name="Edit",
                   tool_input={"file_path": str(src), "old_string": "1", "new_string": "2"})
    r = _run_hook(tmp_path, "pre-edit-hook.sh", doc, body=_SCAN_OK, project=root)
    snap = r.call("build", "snapshot")
    assert snap is not None, r.calls
    assert f"--turn={turn}" in snap, snap
    scan = r.stdin_of("hook", "edit-scan")
    assert scan is not None and scan["prompt_id"] == turn


def test_post_edit_reminder_names_the_sessions_turn(tmp_path):
    """The reminder is the only channel to the skill: "Pass turn id <id> … --turn"."""
    turn = _record(tmp_path)
    root = tmp_path / "proj"
    (root / ".loci").mkdir(parents=True)
    (root / ".loci" / "build.yaml").write_text("version: 1\n", encoding="utf-8")
    src = root / "main.c"
    src.write_text("int f(void) { return 2; }\n", encoding="utf-8")
    doc = _payload("PostToolUse", cwd=str(root), tool_name="Edit",
                   tool_input={"file_path": str(src), "old_string": "1", "new_string": "2"},
                   tool_response={"filePath": str(src), "structuredPatch": []})
    r = _run_hook(tmp_path, "post-edit-hook.sh", doc, body=_SCAN_OK, project=root)
    out = json.loads(r.out.strip())
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "You MUST invoke the loci:loci-post-edit skill" in ctx, ctx
    assert f"Pass turn id {turn} to the skill as its --turn value" in ctx, ctx
    scan = r.stdin_of("hook", "edit-scan")
    assert scan is not None and scan["prompt_id"] == turn


def test_every_hook_that_reads_the_turn_sources_the_adapter():
    """The six readers the ticket names, plus the contract guard — which reads
    no turn but reads `file_path` (AAD-7782) — plus the one Stop WRITER that
    reads neither and names the adapter for its carry (AAD-7783), and no other
    hook names it. What is pinned is who CALLS the adapter: since AAD-7783
    `lib/loci_failfast.sh` sources `lib/loci_host.sh` for its Stop notice, so
    every hook loads the file (a parse measured within noise); a hook that
    neither reads a field nor carries a message still has no business calling
    it, and this is the list of the ones that may. Since AAD-7784 the session
    hook is the one HOST reader: it reads no turn and carries nothing, but asks
    the adapter whether this SessionStart is a resumed session's, what the host
    is called and how a user-facing message travels there. Since AAD-7788 the
    SubagentStart hook is the one LINKER: it reads no turn and carries nothing,
    but records the start for the child's first prompt to claim."""
    readers = {"prompt-submit-turn.sh", "pre-edit-hook.sh", "post-edit-hook.sh",
               "post-bash-bypass.sh", "manifest-status-nudge.sh", "turn-clean.sh",
               "contract-guard.sh"}
    writers = {"draft-pending-nudge.sh"}
    hosts = {"session-init.sh"}
    linkers = {"subagent-start.sh"}
    sourcing = {h.name for h in HOOKS.glob("*.sh")
                if "lib/loci_host.sh" in h.read_text(encoding="utf-8")}
    allowed = readers | writers | hosts | linkers
    assert sourcing == allowed, sourcing ^ allowed
    for name in readers:
        body = (HOOKS / name).read_text(encoding="utf-8")
        assert "loci_host_adapt" in body or "loci_host_payload_adapt" in body, name
    for name in writers:
        body = (HOOKS / name).read_text(encoding="utf-8")
        assert "loci_host_carry_add" in body, name
    for name in hosts:
        body = (HOOKS / name).read_text(encoding="utf-8")
        for fn in ("loci_host_reads_payload", "loci_host_session_resumed",
                   "loci_host_name", "loci_host_notice"):
            assert fn in body, f"{name} no longer asks {fn}"
        assert "loci_host_adapt" not in body and "loci_host_carry_add" not in body, (
            f"{name} reads no turn and carries nothing")
    for name in linkers:
        body = (HOOKS / name).read_text(encoding="utf-8")
        assert "loci_host_subagent_start" in body, name
        assert "loci_host_adapt" not in body and "loci_host_carry_add" not in body, (
            f"{name} reads no turn and carries nothing")


# ── subagents (AAD-7788) ────────────────────────────────────────────────────
#
# A Copilot subagent (the `task` tool) is a SESSION OF ITS OWN: a fresh
# `session_id`, a `UserPromptSubmit` of its own (the task prompt), a `Stop`, and
# no `agent_id` on any payload — probed 2026-10-01 on 1.0.91 (the stage is
# C:\Playground\copilot-7788). Under the rule above its first prompt MINTED: a
# wrong id, not a missing one, with the subagent's edits filed under a turn
# nobody ends and the parent turn's baseline never shared. `SubagentStart` fires
# in the PARENT's session right before the child's prompt — `sessionId`,
# camelCase, no event name, no child id — and `hooks/subagent-start.sh` records
# it as a marker the child's first prompt claims: the child's record then holds
# the parent's turn id and names the parent, every later hook of the child
# resolves the parent's turn, and the child's payloads get `agent_id` (its
# session id), the field Claude Code puts on a subagent's payloads.

from tests.fixtures.copilot_payloads import (  # noqa: E402
    CHILD_SESSION as CHILD, SESSION as PARENT, SUBAGENT_START, at as _at)

#: `_payload`'s cwd, which the claim matches the marker's cwd against.
_CWD = "C:\\proj"


def _sub_start(session: str = PARENT, cwd: str = _CWD) -> dict:
    """The `SubagentStart` payload as Copilot 1.0.91 sends it, for `session`."""
    doc = _at(SUBAGENT_START, cwd)
    doc["sessionId"] = session
    return doc


def _start(tmp_path: Path, payload: dict, *, state_dir: Path, copilot: bool = True) -> bool:
    """Source the library and call `loci_host_subagent_start` on the payload:
    True when it recorded a marker."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": _to_bash_path(home),
           "LOCI_STATE_DIR": _to_bash_path(state_dir)}
    if copilot:
        env["COPILOT_CLI"] = "1"
    f = tmp_path / "start.json"
    f.write_text(json.dumps(payload), encoding="utf-8", newline="\n")
    script = (f". '{_to_bash_path(LIB)}'\n"
              f"loci_host_subagent_start \"$(cat '{_to_bash_path(f)}')\"\n")
    proc = subprocess.run([_find_bash(), "-c", script], capture_output=True,
                          text=True, timeout=60, env=env)
    assert proc.stdout == "" and proc.stderr == "", (proc.stdout, proc.stderr)
    return proc.returncode == 0


def _markers(state: Path, session: str = PARENT) -> list[Path]:
    return sorted(p for p in state.glob(f"turn-{session}.child-*")
                  if ".tmp-" not in p.name and ".claim-" not in p.name)


def _child_files(state: Path) -> list[str]:
    """Every file a start or a claim could leave: markers, half-written
    markers and claims alike — what a tidy claim leaves none of."""
    return sorted(p.name for p in state.glob("turn-*.child-*"))


def _state(tmp_path: Path) -> Path:
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    return state


def _edit(session: str, event: str = "PreToolUse", **extra) -> dict:
    doc = _payload(event, session, tool_name="Edit",
                   tool_input={"path": "C:\\proj\\a.c", "old_str": "1", "new_str": "2"})
    if event == "PostToolUse":
        doc["tool_result"] = {"result_type": "success", "text_result_for_llm": "ok"}
    doc.update(extra)
    return doc


def test_a_subagent_start_is_recorded_as_a_marker_for_the_parent_session(tmp_path):
    """Stamp (an id of ours — its age), the parent's session, the cwd."""
    state = _state(tmp_path)
    assert _start(tmp_path, _sub_start(), state_dir=state)
    (marker,) = _markers(state)
    stamp, parent, cwd = marker.read_text(encoding="utf-8").splitlines()
    assert _ID_RE.match(stamp), stamp
    assert parent == PARENT
    assert cwd == _CWD


def test_the_childs_first_prompt_inherits_the_parents_turn_and_an_agent_id(tmp_path):
    """The defect, fixed: the child's prompt mints nothing. It claims the marker,
    records the parent's id with a line naming the parent, and injects both
    `prompt_id` and `agent_id` at the front of the payload."""
    state = _state(tmp_path)
    parent = _adapt(tmp_path, _payload("UserPromptSubmit", PARENT), state_dir=state)
    assert _start(tmp_path, _sub_start(), state_dir=state)
    child = _adapt(tmp_path, _payload("UserPromptSubmit", CHILD, prompt="task"), state_dir=state)
    assert child.changed
    assert child.prompt_id == parent.prompt_id
    assert child.doc["prompt_id"] == parent.prompt_id
    assert child.doc["agent_id"] == CHILD
    assert child.raw.startswith(f'{{"prompt_id":"{parent.prompt_id}","agent_id":"{CHILD}",')
    assert _turn_file(state, CHILD).read_text(encoding="utf-8").splitlines() == [
        parent.prompt_id, f"parent {PARENT}"]
    assert _child_files(state) == [], "a claimed marker is gone, its claim too"


@pytest.mark.parametrize("event,extra", [
    ("PreToolUse", {}),
    ("PostToolUse", {}),
    ("Stop", {"stop_hook_active": False}),
])
def test_every_later_hook_of_the_child_resolves_the_parents_turn(tmp_path, event, extra):
    state = _state(tmp_path)
    parent = _adapt(tmp_path, _payload("UserPromptSubmit", PARENT), state_dir=state)
    assert _start(tmp_path, _sub_start(), state_dir=state)
    _adapt(tmp_path, _payload("UserPromptSubmit", CHILD), state_dir=state)
    doc = _edit(CHILD, event, **extra) if event != "Stop" else _payload(event, CHILD, **extra)
    a = _adapt(tmp_path, doc, state_dir=state)
    assert a.changed
    assert a.prompt_id == parent.prompt_id
    assert a.doc["agent_id"] == CHILD
    assert a.doc["session_id"] == CHILD, "the host's own fields are left as they came"


def test_the_parents_own_hooks_get_no_agent_id(tmp_path):
    """The sentence the reminder adds for a subagent must not reach the main
    agent (test_post_edit_hook pins the sentence; this pins the field)."""
    state = _state(tmp_path)
    parent = _adapt(tmp_path, _payload("UserPromptSubmit", PARENT), state_dir=state)
    assert _start(tmp_path, _sub_start(), state_dir=state)
    _adapt(tmp_path, _payload("UserPromptSubmit", CHILD), state_dir=state)
    a = _adapt(tmp_path, _edit(PARENT), state_dir=state)
    assert a.changed and a.prompt_id == parent.prompt_id
    assert "agent_id" not in a.doc


def test_a_session_that_already_prompted_is_not_taken_for_the_child(tmp_path):
    """Only a session with no record may claim: the parent's own next prompt,
    between the start and the child's prompt, mints as every prompt does and
    leaves the marker for the child — which then inherits the NEWEST turn."""
    state = _state(tmp_path)
    first = _adapt(tmp_path, _payload("UserPromptSubmit", PARENT), state_dir=state)
    assert _start(tmp_path, _sub_start(), state_dir=state)
    second = _adapt(tmp_path, _payload("UserPromptSubmit", PARENT), state_dir=state)
    assert second.prompt_id != first.prompt_id
    assert "agent_id" not in second.doc
    assert len(_markers(state)) == 1, "the parent's prompt did not claim"
    child = _adapt(tmp_path, _payload("UserPromptSubmit", CHILD), state_dir=state)
    assert child.prompt_id == second.prompt_id
    assert _markers(state) == []


def test_a_marker_past_the_wait_is_dropped_and_the_prompt_mints(tmp_path):
    """A child that never came (a spawn the host refused) must not hand its
    turn to the next session that prompts in the same cwd."""
    state = _state(tmp_path)
    parent = _adapt(tmp_path, _payload("UserPromptSubmit", PARENT), state_dir=state)
    (state / f"turn-{PARENT}.child-old").write_text(
        f"cp-{int(time.time()) - 120}-abc\n{PARENT}\n{_CWD}\n", encoding="utf-8", newline="\n")
    child = _adapt(tmp_path, _payload("UserPromptSubmit", CHILD), state_dir=state)
    assert child.changed
    assert child.prompt_id != parent.prompt_id, "a fresh turn, not the parent's"
    assert "agent_id" not in child.doc
    assert _markers(state) == [], "the stale marker is dropped where it is met"


def test_a_marker_from_another_cwd_is_not_claimed(tmp_path):
    """A subagent works where its parent works; a session in another project
    is another session's, and the marker waits."""
    state = _state(tmp_path)
    parent = _adapt(tmp_path, _payload("UserPromptSubmit", PARENT), state_dir=state)
    assert _start(tmp_path, _sub_start(cwd="D:\\other"), state_dir=state)
    other = _adapt(tmp_path, _payload("UserPromptSubmit", CHILD), state_dir=state)
    assert other.changed and other.prompt_id != parent.prompt_id
    assert "agent_id" not in other.doc
    assert len(_markers(state)) == 1


def test_two_subagents_starting_together_claim_one_marker_each(tmp_path):
    """Parallel spawns (`/fleet`): two markers, two prompts, both inherit."""
    state = _state(tmp_path)
    parent = _adapt(tmp_path, _payload("UserPromptSubmit", PARENT), state_dir=state)
    assert _start(tmp_path, _sub_start(), state_dir=state)
    assert _start(tmp_path, _sub_start(), state_dir=state)
    assert len(_markers(state)) == 2, "one marker per start, named apart"
    a = _adapt(tmp_path, _payload("UserPromptSubmit", CHILD), state_dir=state)
    b = _adapt(tmp_path, _payload("UserPromptSubmit", "child-b"), state_dir=state)
    assert a.prompt_id == parent.prompt_id and b.prompt_id == parent.prompt_id
    assert a.doc["agent_id"] == CHILD and b.doc["agent_id"] == "child-b"
    assert _markers(state) == []


def test_a_nested_subagent_inherits_the_turn_by_value(tmp_path):
    """The child's record holds the parent's id, so a start recorded under the
    child hands its own child that same id; its record names its parent."""
    state = _state(tmp_path)
    parent = _adapt(tmp_path, _payload("UserPromptSubmit", PARENT), state_dir=state)
    assert _start(tmp_path, _sub_start(), state_dir=state)
    _adapt(tmp_path, _payload("UserPromptSubmit", CHILD), state_dir=state)
    assert _start(tmp_path, _sub_start(session=CHILD), state_dir=state)
    grand = _adapt(tmp_path, _payload("UserPromptSubmit", "grandchild"), state_dir=state)
    assert grand.prompt_id == parent.prompt_id
    assert grand.doc["agent_id"] == "grandchild"
    assert _turn_file(state, "grandchild").read_text(encoding="utf-8").splitlines() == [
        parent.prompt_id, f"parent {CHILD}"]


def test_a_marker_whose_parent_has_no_turn_on_record_is_dropped(tmp_path):
    """No parent turn to inherit (its prompt was never adapted): the child's
    prompt gets a turn of its own, as any first prompt does, and the marker
    does not wait for anyone."""
    state = _state(tmp_path)
    assert _start(tmp_path, _sub_start(), state_dir=state)
    child = _adapt(tmp_path, _payload("UserPromptSubmit", CHILD), state_dir=state)
    assert child.changed and _ID_RE.match(child.prompt_id)
    assert "agent_id" not in child.doc
    assert _child_files(state) == []
    assert _turn_file(state, CHILD).read_text(encoding="utf-8").splitlines() == [child.prompt_id]


def test_an_orphan_marker_met_first_does_not_end_the_walk(tmp_path):
    """Two parents spawned in one cwd; the first marker in name order belongs
    to a session with no turn on record. It is dropped and the walk goes on
    to the marker that can be inherited (review finding 1)."""
    state = _state(tmp_path)
    parent = _adapt(tmp_path, _payload("UserPromptSubmit", PARENT), state_dir=state)
    assert _start(tmp_path, _sub_start(session="0000-orphan"), state_dir=state)
    assert _start(tmp_path, _sub_start(), state_dir=state)
    assert _child_files(state)[0].startswith("turn-0000-orphan"), _child_files(state)
    child = _adapt(tmp_path, _payload("UserPromptSubmit", CHILD), state_dir=state)
    assert child.prompt_id == parent.prompt_id
    assert child.doc["agent_id"] == CHILD
    assert _child_files(state) == [], "the orphan dropped, the live one claimed"


def test_a_session_whose_record_was_emptied_does_not_claim(tmp_path):
    """"No record" means no FILE: a record emptied by a failed write (or
    swept to nothing) is a session that was here, and its next prompt mints
    rather than take another session's marker (review finding 3)."""
    state = _state(tmp_path)
    parent = _adapt(tmp_path, _payload("UserPromptSubmit", PARENT), state_dir=state)
    _turn_file(state, "old-session").write_text("", encoding="utf-8")
    assert _start(tmp_path, _sub_start(), state_dir=state)
    old = _adapt(tmp_path, _payload("UserPromptSubmit", "old-session"), state_dir=state)
    assert old.changed and old.prompt_id != parent.prompt_id
    assert "agent_id" not in old.doc
    assert len(_markers(state)) == 1, "the marker still waits for the child"


def test_a_marker_stamped_in_the_future_is_dropped(tmp_path):
    """A clock stepped back between the two hooks, or no clock at all (epoch
    0 stamps): the wait is two-sided, so such a marker never outlives it
    (review finding 4)."""
    state = _state(tmp_path)
    parent = _adapt(tmp_path, _payload("UserPromptSubmit", PARENT), state_dir=state)
    (state / f"turn-{PARENT}.child-future").write_text(
        f"cp-{int(time.time()) + 100000}-abc\n{PARENT}\n{_CWD}\n", encoding="utf-8", newline="\n")
    child = _adapt(tmp_path, _payload("UserPromptSubmit", CHILD), state_dir=state)
    assert child.changed and child.prompt_id != parent.prompt_id
    assert "agent_id" not in child.doc
    assert _child_files(state) == []


def test_the_hosts_agent_id_wins(tmp_path):
    """As `prompt_id` does: a payload that carries the key keeps the host's
    value, and never ends up with the field twice."""
    state = _state(tmp_path)
    _adapt(tmp_path, _payload("UserPromptSubmit", PARENT), state_dir=state)
    assert _start(tmp_path, _sub_start(), state_dir=state)
    _adapt(tmp_path, _payload("UserPromptSubmit", CHILD), state_dir=state)
    a = _adapt(tmp_path, _edit(CHILD, agent_id="host-said"), state_dir=state)
    assert a.changed
    assert a.doc["agent_id"] == "host-said"
    assert a.raw.count('"agent_id"') == 1


def test_the_hosts_agent_id_wins_on_the_claiming_prompt_too(tmp_path):
    state = _state(tmp_path)
    parent = _adapt(tmp_path, _payload("UserPromptSubmit", PARENT), state_dir=state)
    assert _start(tmp_path, _sub_start(), state_dir=state)
    child = _adapt(tmp_path, _payload("UserPromptSubmit", CHILD, agent_id="host-said"),
                   state_dir=state)
    assert child.prompt_id == parent.prompt_id
    assert child.doc["agent_id"] == "host-said"
    assert child.raw.count('"agent_id"') == 1


def test_a_childs_stop_records_no_carry_and_the_parents_does(tmp_path):
    """A subagent's session prompts once: nobody would take the record. The
    parent's Stop reports the same turn to the one who does prompt."""
    state = _state(tmp_path)
    _adapt(tmp_path, _payload("UserPromptSubmit", PARENT), state_dir=state)
    assert _start(tmp_path, _sub_start(), state_dir=state)
    _adapt(tmp_path, _payload("UserPromptSubmit", CHILD), state_dir=state)
    after = ('loci_host_carry_add manifest "unmeasured" "$LOCI_TEST_PAYLOAD"; '
             'printf "\\036%s" "$?"')
    child = _adapt(tmp_path, _payload("Stop", CHILD, stop_hook_active=False),
                   state_dir=state, after=after)
    assert child.extra == ["1"], child.extra
    assert not (state / f"turn-{CHILD}.nudge-manifest").exists()
    parent = _adapt(tmp_path, _payload("Stop", PARENT, stop_hook_active=False),
                    state_dir=state, after=after)
    assert parent.extra == ["0"], parent.extra
    assert (state / f"turn-{PARENT}.nudge-manifest").is_file()


def test_the_snake_case_session_id_is_read_before_the_camel_case_one(tmp_path):
    """Copilot 1.0.91 spells this payload `sessionId`; a later version that
    spells it as every other payload does is read the ordinary way first."""
    state = _state(tmp_path)
    doc = {"session_id": "snake", **_sub_start()}
    assert _start(tmp_path, doc, state_dir=state)
    assert _markers(state, "snake"), sorted(p.name for p in state.iterdir())
    assert not _markers(state, PARENT)


@pytest.mark.parametrize("session", ["", "../x", "a/b", "sp ace", 'q"uote'])
def test_a_session_id_outside_the_file_alphabet_records_no_marker(tmp_path, session):
    """The id goes into a file name and a record line: nothing outside
    `[A-Za-z0-9._-]` is written anywhere."""
    state = _state(tmp_path)
    assert not _start(tmp_path, _sub_start(session=session), state_dir=state)
    assert list(state.iterdir()) == []


def test_a_stale_marker_is_swept_at_session_start(tmp_path):
    """Its first line is a stamp of ours, so the sweep ages it like a record."""
    state = _state(tmp_path)
    now = int(time.time())
    (state / f"turn-{PARENT}.child-old").write_text(
        f"cp-{now - 9 * 86400}-abc\n{PARENT}\n{_CWD}\n", encoding="utf-8", newline="\n")
    (state / f"turn-{PARENT}.child-new").write_text(
        f"cp-{now - 10}-abc\n{PARENT}\n{_CWD}\n", encoding="utf-8", newline="\n")
    _adapt(tmp_path, _payload("SessionStart", "new-session", source="new"), state_dir=state)
    assert not (state / f"turn-{PARENT}.child-old").exists()
    assert (state / f"turn-{PARENT}.child-new").exists()


def test_outside_copilot_a_subagent_start_records_nothing(tmp_path):
    """Claude Code puts the parent's `prompt_id` and an `agent_id` on a
    subagent's payloads itself; the library has nothing to add and reads nothing."""
    state = _state(tmp_path)
    assert not _start(tmp_path, _sub_start(), state_dir=state, copilot=False)
    assert list(state.iterdir()) == []
    doc = _edit(PARENT, prompt_id="claude-turn", agent_id="a19d292387c73a8c2")
    a = _adapt(tmp_path, doc, state_dir=state, copilot=False)
    assert not a.changed and a.raw == "" and a.prompt_id == "", "one environment test, nothing touched"
    assert list(state.iterdir()) == []


# the hook, and the edit hooks end to end

def test_the_subagent_start_hook_records_the_marker_and_prints_nothing(tmp_path):
    r = _run_hook(tmp_path, "subagent-start.sh", _sub_start())
    assert r.out == "" and r.calls == []
    assert len(_markers(tmp_path / "state")) == 1


def test_the_subagent_start_hook_outside_copilot_leaves_no_trace(tmp_path):
    r = _run_hook(tmp_path, "subagent-start.sh", _sub_start(), copilot=False)
    assert r.out == "" and r.calls == []
    assert list((tmp_path / "state").iterdir()) == []


def test_a_subagents_edit_is_captured_and_reminded_under_the_parent_turn(tmp_path):
    """Criteria 1 and 2 of AAD-7788, through the hooks: the child's prompt hands
    `loci hook prompt-submit` the parent's id (so the child reads `[loci]
    turn=<parent>`), its pre-edit snapshot is `--turn=<parent>` (the shared
    first-write-wins baseline), and its reminder names the parent's turn and
    tells it, as a subagent, to run the skill here and carry the verdict into
    its report — the sentence Claude Code's subagents get."""
    root = tmp_path / "proj"
    (root / ".loci").mkdir(parents=True)
    (root / ".loci" / "build.yaml").write_text("version: 1\n", encoding="utf-8")
    src = root / "main.c"
    src.write_text("int f(void) { return 1; }\n", encoding="utf-8")
    turn = _record(tmp_path, PARENT)
    r = _run_hook(tmp_path, "subagent-start.sh", _sub_start(cwd=str(root)))
    assert r.out == ""
    ups = _run_hook(tmp_path, "prompt-submit-turn.sh",
                    _payload("UserPromptSubmit", CHILD, cwd=str(root), prompt="edit main.c"))
    assert ups.stdin_of("hook", "prompt-submit")["prompt_id"] == turn
    edit = {"file_path": str(src), "old_string": "1", "new_string": "2"}
    pre = _run_hook(tmp_path, "pre-edit-hook.sh",
                    _payload("PreToolUse", CHILD, cwd=str(root), tool_name="Edit",
                             tool_input=edit),
                    body=_SCAN_OK, project=root)
    snap = pre.call("build", "snapshot")
    assert snap is not None and f"--turn={turn}" in snap, pre.calls
    assert pre.stdin_of("hook", "edit-scan")["agent_id"] == CHILD
    post = _run_hook(tmp_path, "post-edit-hook.sh",
                     _payload("PostToolUse", CHILD, cwd=str(root), tool_name="Edit",
                              tool_input=edit,
                              tool_response={"filePath": str(src), "structuredPatch": []}),
                     body=_SCAN_OK, project=root)
    reply = json.loads(post.out.strip())
    ctx = reply["hookSpecificOutput"]["additionalContext"]
    assert "You MUST invoke the loci:loci-post-edit skill" in ctx, ctx
    assert f"Pass turn id {turn} to the skill as its --turn value" in ctx, ctx
    assert "You are running as a subagent" in ctx and "final report" in ctx, ctx
    # The channel a Copilot subagent reads: the top-level mirror (AAD-7783).
    assert reply["additionalContext"] == ctx
    scan = post.stdin_of("hook", "edit-scan")
    assert scan["prompt_id"] == turn and scan["agent_id"] == CHILD
