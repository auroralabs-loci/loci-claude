"""`LOCI_FAIL_FAST=1`: a failing `loci` call halts instead of being papered over.

The recoveries this mode switches off are what hide LOCI's own defects — a turn
whose pre-scan, snapshot, turn stamp or status check failed reads afterwards as a
turn that worked. QA tests production artifacts, so the switch is read
independently of `LOCI_ENV` and works in a build where that says nothing.

Two properties are load-bearing, and the second matters most:

* **on**, a non-zero `loci` exit reaches the model on the channel the hook owns,
  carrying the command, the exit code and the output verbatim — and no recovery,
  fallback or repair runs behind it. A hook still exits 0: a non-zero exit from
  an edge hook reads to the model as a tool failure, so "halt" is an instruction,
  never a status.
* **off**, nothing moves. Same stdout, same exit code, same recovery, same log.
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
HOOKS = PLUGIN_ROOT / "hooks"

SESSION_ID = "9d1f4c22-7a03-4e18-b6c5-0e2a91d374bf"


def _find_bash() -> str | None:
    if sys.platform == "win32":
        for cand in (
            r"C:\Program Files\Git\usr\bin\bash.exe",
            r"C:\Program Files (x86)\Git\usr\bin\bash.exe",
        ):
            if Path(cand).is_file():
                return cand
    return shutil.which("bash")


from tests.fixtures.copilot_payloads import current as _host

pytestmark = pytest.mark.skipif(_find_bash() is None, reason="bash required")

#: The tests that run under BOTH hosts (AAD-7790). Not the module: the on/off
#: byte comparison and the session-init block cost a third of this file and
#: say nothing a host changes, so only the tests where the notice, the halt or
#: the attribution reaches a host are doubled, as `[claude]` / `[copilot]`.
#: Under Copilot the payload is respelled (no `prompt_id` — the turn is the
#: seeded `turn-<session_id>` record — and an Edit's keys in Copilot's names),
#: the hook starts in the plugin root, and the notice rides where Copilot reads
#: it: the top-level `additionalContext` beside the nested one, and for a Stop
#: hook a record for the next prompt instead of a message nobody sees.
both_hosts = pytest.mark.usefixtures("host")


def _to_bash_path(p: Path) -> str:
    s = Path(p).as_posix()
    m = re.match(r"^([A-Za-z]):/(.*)$", s)
    return f"/{m.group(1).lower()}/{m.group(2)}" if m else s


#: A `loci` that fails the way the CLI fails: an error envelope on stdout, the
#: reason on stderr, a non-zero code. Both streams are asserted on, because a
#: traceback goes to stderr and that is the failure QA is looking for.
_FAILING_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$LOCI_ARGV_LOG"
cat >/dev/null 2>&1
printf 'Traceback: boom in %s\\n' "$1" >&2
printf '%s\\n' '{"ok":false,"error":{"message":"stub refused"}}'
exit 7
"""

#: The same verbs, answering. Kept in step with `test_hook_session_log.py`'s
#: stub: a hook that exits before its `loci` call proves nothing here.
_WORKING_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$LOCI_ARGV_LOG"
cat >/dev/null 2>&1
case "$*" in
    "hook edit-scan"*) printf '%s\\n' '{"ok":true,"data":{"applied":true,"measurable":true,"report":"","measure_via":"self","turn":"t-1"}}' ;;
    "build snapshot"*) printf '%s\\n' '{"ok":true,"data":{}}' ;;
    "contract draft show"*) printf '%s\\n' '{"ok":true,"data":{"pending":1,"stale":false,"ops":[{"op":"add"}]}}' ;;
    "build clean"*) ;;
    *) printf '%s\\n' '{"ok":true,"data":{}}' ;;
esac
exit 0
"""


class Run:
    def __init__(self, proc, state: Path, argv_log: Path):
        self.proc = proc
        self.state = state
        self._argv_log = argv_log

    @property
    def code(self) -> int:
        return self.proc.returncode

    @property
    def stdout(self) -> str:
        return self.proc.stdout

    @property
    def log(self) -> str:
        f = self.state / "loci.log"
        return f.read_text(encoding="utf-8", errors="replace") if f.is_file() else ""

    def argv(self) -> list[str]:
        return (self._argv_log.read_text(encoding="utf-8").splitlines()
                if self._argv_log.is_file() else [])

    def channel(self, name: str) -> str:
        """`additionalContext` or `systemMessage` out of the hook's one JSON doc."""
        doc = json.loads(self.stdout)
        if name == "systemMessage":
            return doc.get("systemMessage", "")
        return doc.get("hookSpecificOutput", {}).get("additionalContext", "")


def _run(hook: str, payload: dict, tmp_path: Path, *, fail_fast: bool,
         stub: str = _FAILING_STUB, dev: bool = False,
         cwd: Path | None = None) -> Run:
    home = tmp_path / "fakehome"
    state = tmp_path / "state"
    bin_dir = tmp_path / "fakebin"
    for d in (home, state, bin_dir):
        d.mkdir(parents=True, exist_ok=True)
    loci = bin_dir / "loci"
    loci.write_text(stub, encoding="utf-8", newline="\n")
    loci.chmod(0o755)
    argv_log = tmp_path / "argv.txt"

    env = {
        "PATH": f"{_to_bash_path(bin_dir)}:/usr/bin:/bin:/usr/local/bin",
        "HOME": _to_bash_path(home),
        "LOCI_STATE_DIR": _to_bash_path(state),
        "CLAUDE_PROJECT_DIR": _to_bash_path(cwd or tmp_path),
        "LOCI_ARGV_LOG": _to_bash_path(argv_log),
    }
    if fail_fast:
        env["LOCI_FAIL_FAST"] = "1"
    if dev:
        env["LOCI_ENV"] = "dev"

    host = _host()
    payload = host.respell(payload)
    env.update(host.env(project=_to_bash_path(cwd or tmp_path)))
    host.seed(state)
    proc = subprocess.run(
        [_find_bash(), _to_bash_path(HOOKS / hook)],
        input=json.dumps(payload),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60, env=env, cwd=host.cwd(cwd or tmp_path),
    )
    return Run(proc, state, argv_log)


def _stop_records(state: Path) -> list[Path]:
    """What a Stop hook left for the session's next prompt under Copilot
    (`lib/loci_host.sh`, `loci_host_carry_add`): one file per writer."""
    return sorted(state.glob(f"turn-{SESSION_ID}.nudge-*"))


def _project(tmp_path: Path) -> Path:
    """A tree every hook under test finds something to do in."""
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "main.c").write_text("int f(void) { return 0; }\n",
                                             encoding="utf-8")
    (tmp_path / ".loci" / "build").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".loci" / "build" / "contract.draft.yaml").write_text(
        "ops: []\n", encoding="utf-8")
    return tmp_path


def _payload(**fields) -> dict:
    base = {"session_id": SESSION_ID, "prompt_id": "t-1"}
    base.update(fields)
    return base


def _c_edit(root: Path, **fields) -> dict:
    return _payload(
        cwd=_to_bash_path(root), tool_name="Edit",
        tool_input={"file_path": _to_bash_path(root / "src" / "main.c"),
                    "new_string": "int f(void) { return 1; }"},
        **fields)


#: (hook, payload builder, the channel the hook owns, the command it reports).
#: One case per hook that invokes a `loci` verb — a hook left out of this table
#: is a hook whose failure QA still cannot see.
CASES = [
    ("pre-edit-hook.sh", _c_edit, "additionalContext", "loci hook edit-scan"),
    ("post-edit-hook.sh", _c_edit, "additionalContext", "loci hook edit-scan"),
    ("post-bash-bypass.sh", lambda root: _payload(
        cwd=_to_bash_path(root), tool_name="Bash",
        tool_input={"command": "make"}), "additionalContext",
     "loci hook post-bash"),
    ("prompt-submit-turn.sh", lambda root: _payload(
        cwd=_to_bash_path(root), hook_event_name="UserPromptSubmit"),
     "additionalContext", "loci hook prompt-submit"),
    ("manifest-status-nudge.sh", lambda root: _payload(
        cwd=_to_bash_path(root), hook_event_name="Stop"), "systemMessage",
     "loci analyse status --turn --hook-json"),
    ("stats-flush.sh", lambda root: _payload(
        cwd=_to_bash_path(root), hook_event_name="Stop"), "systemMessage",
     "loci stats flush-impacts"),
    ("turn-clean.sh", lambda root: _payload(
        cwd=_to_bash_path(root), hook_event_name="Stop"), "systemMessage",
     "loci build clean"),
    ("draft-pending-nudge.sh", lambda root: _payload(
        cwd=_to_bash_path(root), hook_event_name="Stop"), "systemMessage",
     "loci contract draft show"),
]

_IDS = [c[0].removesuffix(".sh") for c in CASES]


# ── on: the failure is loud, and nothing routes around it ───────────────────

@both_hosts
@pytest.mark.parametrize("hook,build,channel,command", CASES, ids=_IDS)
def test_a_failing_loci_call_is_surfaced_verbatim(hook, build, channel, command,
                                                  tmp_path):
    root = _project(tmp_path)
    run = _run(hook, build(root), tmp_path, fail_fast=True, cwd=root)

    assert run.code == 0, (
        f"{hook} exited {run.code} — a non-zero exit from a hook reads to the "
        f"model as a tool failure, so fast-fail must never use one:\n{run.proc.stderr}")
    text = run.channel(channel)
    assert "LOCI fast-fail is on" in text, (
        f"{hook} said nothing about the failure on its {channel}:\n{run.stdout}")
    assert command in text, f"{hook} did not name the command that failed:\n{text}"
    assert "exit 7" in text, f"{hook} did not report the exit code:\n{text}"
    assert "Traceback: boom" in text, (
        f"{hook} dropped the failing command's stderr, which is the part that "
        f"says what broke:\n{text}")
    assert "Stop this turn now" in text, (
        f"{hook} reported the failure without instructing a halt — a hook "
        f"cannot halt anything by its exit code:\n{text}")

    # Where the host reads it (AAD-7783): Copilot drops the nested object and
    # injects only a top-level `additionalContext`, so the notice goes through
    # the shared writers and rides there too; its Stop reply reaches nobody,
    # so a Stop notice is recorded for the session's next prompt instead.
    host = _host()
    doc = json.loads(run.stdout)
    if channel == "additionalContext":
        assert host.context(doc) == text, (
            f"{hook}'s notice is not where {host} reads it:\n{run.stdout}")
        if host.copilot:
            assert doc.get("additionalContext") == text
    elif host.copilot:
        assert "additionalContext" not in doc, (
            f"a Stop reply has no context field under Copilot:\n{run.stdout}")
        records = _stop_records(run.state)
        assert records, f"{hook} recorded no Stop notice for the next prompt"
        assert any("LOCI fast-fail is on" in r.read_text(encoding="utf-8")
                   for r in records), [r.name for r in records]


@both_hosts
@pytest.mark.parametrize("hook,build,channel,command", CASES, ids=_IDS)
def test_the_failure_is_attributable_to_a_session_without_dev_mode(
        hook, build, channel, command, tmp_path):
    """Fast-fail pairs with the session log, and QA runs production builds: the
    ERROR line has to land with `LOCI_ENV` unset."""
    root = _project(tmp_path)
    run = _run(hook, build(root), tmp_path, fail_fast=True, cwd=root)

    lines = [ln for ln in run.log.splitlines() if "fail-fast:" in ln]
    assert lines, f"{hook} logged no fail-fast line:\n{run.log}"
    for ln in lines:
        assert "[ERROR]" in ln, f"a fail-fast line is not an error:\n{ln}"
        assert f"[session={SESSION_ID}]" in ln, (
            f"the fail-fast line cannot be attributed to a session:\n{ln}")


@both_hosts
def test_the_pre_edit_snapshot_is_not_attempted_after_a_failed_scan(tmp_path):
    """The halt is a halt: the second `loci` call of the pair never runs."""
    root = _project(tmp_path)
    run = _run("pre-edit-hook.sh", _c_edit(root), tmp_path, fail_fast=True, cwd=root)

    assert run.argv() == ["hook edit-scan"], (
        f"a recovery path ran behind the halt: {run.argv()}")
    assert "No static pre-scan" not in run.stdout, (
        "the advisory that turns the failure into a degraded-but-fine notice is "
        "still being printed")


@both_hosts
def test_the_post_edit_reminder_is_not_emitted_behind_a_halt(tmp_path):
    """Off, exit 2 reports the edit as measurable and reminds anyway. On, a
    reminder over a broken CLI is the papering-over itself."""
    root = _project(tmp_path)
    run = _run("post-edit-hook.sh", _c_edit(root), tmp_path, fail_fast=True, cwd=root)

    text = run.channel("additionalContext")
    assert "MUST invoke" not in text, f"the reminder survived the halt:\n{text}"
    assert "out of step" not in text, f"the exit-2 fallback survived:\n{text}"


@both_hosts
def test_the_edge_hooks_start_no_install_when_loci_is_absent(tmp_path):
    """`loci` missing is answered with a background install — a repair, which
    the mode forbids. It is reported instead."""
    root = _project(tmp_path)
    host = _host()
    for hook, event in (("pre-edit-hook.sh", "PreToolUse"),
                        ("post-edit-hook.sh", "PostToolUse")):
        home = tmp_path / hook / "fakehome"
        state = tmp_path / hook / "state"
        for d in (home, state):
            d.mkdir(parents=True, exist_ok=True)
        payload = host.respell(_c_edit(root))
        host.seed(state)
        proc = subprocess.run(
            [_find_bash(), _to_bash_path(HOOKS / hook)],
            input=json.dumps(payload),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60, cwd=host.cwd(root),
            env={"PATH": "/usr/bin:/bin", "HOME": _to_bash_path(home),
                 "LOCI_STATE_DIR": _to_bash_path(state),
                 "CLAUDE_PROJECT_DIR": _to_bash_path(root),
                 "LOCI_FAIL_FAST": "1",
                 **host.env(project=_to_bash_path(root))},
        )
        assert proc.returncode == 0, proc.stderr
        doc = json.loads(proc.stdout)
        text = doc["hookSpecificOutput"]["additionalContext"]
        assert host.context(doc) == text, doc
        assert "loci is not on PATH" in text, text
        assert "No background install was started" in text, text
        assert not (state / "loci-cli-status.json").exists(), (
            f"{hook} started an install while fast-fail was on")


# ── off: the switch is invisible ────────────────────────────────────────────

@pytest.mark.parametrize("hook,build,channel,command", CASES, ids=_IDS)
def test_a_successful_call_prints_the_same_bytes_either_way(
        hook, build, channel, command, tmp_path):
    """The half that matters: on a session where nothing fails, the mode changes
    no byte of what the model and the user are handed."""
    root = _project(tmp_path / "a")
    payload = build(root)
    on = _run(hook, payload, tmp_path / "a", fail_fast=True,
              stub=_WORKING_STUB, cwd=root)
    off_root = _project(tmp_path / "b")
    off = _run(hook, build(off_root), tmp_path / "b", fail_fast=False,
               stub=_WORKING_STUB, cwd=off_root)

    assert on.code == off.code == 0
    assert on.stdout.replace(_to_bash_path(root), "") == \
        off.stdout.replace(_to_bash_path(off_root), ""), (
        f"{hook} prints differently with fast-fail on")
    assert "fast-fail" not in on.stdout


@both_hosts
@pytest.mark.parametrize("hook,build,channel,command", CASES, ids=_IDS)
def test_a_failing_call_keeps_todays_recovery_when_the_switch_is_off(
        hook, build, channel, command, tmp_path):
    """Same failing CLI, switch off: no notice, no halt instruction, and the
    hook still exits 0 the way it always did."""
    root = _project(tmp_path)
    run = _run(hook, build(root), tmp_path, fail_fast=False, cwd=root)

    assert run.code == 0, run.proc.stderr
    assert "fast-fail" not in run.stdout, (
        f"{hook} emitted a fast-fail notice with the switch off:\n{run.stdout}")
    assert "Stop this turn now" not in run.stdout
    assert run.log == "", (
        f"{hook} wrote a log outside dev mode with the switch off:\n{run.log}")


@both_hosts
def test_the_pre_edit_recovery_still_runs_with_the_switch_off(tmp_path):
    """The recovery the halt replaces, pinned: the scan fails, the snapshot is
    still attempted, and the notice still says the pre-scan was lost."""
    root = _project(tmp_path)
    run = _run("pre-edit-hook.sh", _c_edit(root), tmp_path, fail_fast=False, cwd=root)

    assert "hook edit-scan" in run.argv()
    assert any(a.startswith("build snapshot") for a in run.argv()), (
        f"the snapshot no longer runs after a failed scan: {run.argv()}")
    assert "No static pre-scan for this edit" in run.stdout


@both_hosts
def test_the_thin_hooks_still_pass_the_cli_output_through_with_the_switch_off(
        tmp_path):
    """These four print what the verb prints. Off, a failing verb's own envelope
    is what reaches the harness — unwrapped, exactly as before."""
    root = _project(tmp_path)
    for hook in ("post-bash-bypass.sh", "prompt-submit-turn.sh",
                 "manifest-status-nudge.sh", "stats-flush.sh"):
        run = _run(hook, _payload(cwd=_to_bash_path(root),
                                  hook_event_name="Stop"),
                   tmp_path / hook, fail_fast=False, cwd=root)
        assert '"ok":false' in run.stdout, (
            f"{hook} no longer passes the verb's output through:\n{run.stdout}")


# ── the injected session line: nothing when off ─────────────────────────────

def _session_init(tmp_path: Path, *, fail_fast: bool,
                  stub: str | None = None,
                  payload: dict | None = None) -> tuple[str, str]:
    home = tmp_path / "home"
    state = home / ".loci" / "state"
    proj = tmp_path / "proj"
    bin_dir = tmp_path / "bin"
    for d in (home, state, proj, bin_dir):
        d.mkdir(parents=True, exist_ok=True)
    (proj / "Makefile").write_text("all:\n\t@true\n", encoding="utf-8")
    path = "/usr/bin:/bin:/usr/local/bin"
    if stub is not None:
        loci = bin_dir / "loci"
        loci.write_text(stub, encoding="utf-8", newline="\n")
        loci.chmod(0o755)
        path = f"{_to_bash_path(bin_dir)}:{path}"
    env = {
        "PATH": path,
        "HOME": _to_bash_path(home),
        "LOCI_STATE_DIR": _to_bash_path(state),
    }
    if fail_fast:
        env["LOCI_FAIL_FAST"] = "1"
    proc = subprocess.run(
        [_find_bash(), _to_bash_path(HOOKS / "session-init.sh")],
        env=env, cwd=str(proj), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60,
        input=json.dumps(payload) if payload is not None else "",
    )
    assert proc.returncode == 0, proc.stderr
    log = state / "loci.log"
    return (json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"],
            log.read_text(encoding="utf-8", errors="replace") if log.is_file() else "")


#: What the rule is allowed to cost the injected block, and it is charged only to
#: a session that sets the variable. The steady-state block
#: (`test_project_detection_gate.py`) pays nothing, which is why the line is
#: conditional rather than trimmed to fit a budget it would have to share.
_FAIL_FAST_LINE_ALLOWANCE = 700


def test_the_session_block_carries_the_rule_only_while_the_switch_is_on(tmp_path):
    off, off_log = _session_init(tmp_path / "off", fail_fast=False)
    on, _ = _session_init(tmp_path / "on", fail_fast=True)

    assert "LOCI fast-fail" not in off, (
        "an ordinary session pays for a mode it does not run:\n" + off)
    assert "LOCI fail" not in off
    assert off_log == "", "an ordinary session started writing a log"
    assert "LOCI fast-fail: LOCI_FAIL_FAST is set" in on, (
        "the one rule that covers every skill never reaches the model:\n" + on)
    for phrase in ("stop there", "verbatim", "OVERRIDES"):
        assert phrase in on, f"the rule no longer says {phrase!r}"

    cost = len(on.encode()) - len(off.encode())
    assert 0 < cost <= _FAIL_FAST_LINE_ALLOWANCE, (
        f"the fast-fail line costs {cost} B of injected context, over its "
        f"{_FAIL_FAST_LINE_ALLOWANCE} B allowance")


def test_session_start_asks_the_cli_nothing_about_a_project(tmp_path):
    """AAD-7531 AC 4: a directory declaring a build used to be armed and written."""
    calls = tmp_path / "calls.log"
    stub = ("#!/usr/bin/env bash\n"
            f'printf "%s\\n" "$*" >> "{_to_bash_path(calls)}"\n'
            "cat >/dev/null 2>&1\nexit 1\n")
    _session_init(tmp_path, fail_fast=True, stub=stub)

    made = calls.read_text(encoding="utf-8").splitlines() if calls.is_file() else []
    assert not [c for c in made if c.startswith("hook project-context")], made
    state = tmp_path / "home" / ".loci" / "state"
    assert not list(state.glob("project-context-*.json"))
