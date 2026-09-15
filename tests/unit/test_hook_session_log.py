"""Every hook leaves a trace, and the trace names its session.

Claude Code records only `SessionStart` and `Stop` hooks in the transcript, so
`~/.loci/state/loci.log` is the only place QA can see `contract-guard.sh`,
`pre-edit-hook.sh`, `post-edit-hook.sh` or `prompt-submit-turn.sh` fire, time
out or fail. Until now those lines carried no session id, so a reader had to
bucket them by timestamp — which mis-attributes every line whenever two
sessions run at once.

Two properties are load-bearing here:

* **the id on the line is the one the hook was handed** on stdin as
  `.session_id`, and it is read from the payload the hook has ALREADY consumed —
  stdin reads once, and a hook that re-read it would starve the CLI behind it;
* **logging off changes nothing**. Production is silent by design: the same
  stdout, the same exit code, no log file, and — for the thin hooks that hand
  their stdin to `loci` — the payload still passing through untouched.
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

SESSION_ID = "3a7c9e10-4b21-4d55-9f0e-8c1d2e3f4a5b"


def _find_bash() -> str | None:
    if sys.platform == "win32":
        for cand in (
            r"C:\Program Files\Git\usr\bin\bash.exe",
            r"C:\Program Files (x86)\Git\usr\bin\bash.exe",
        ):
            if Path(cand).is_file():
                return cand
    return shutil.which("bash")


# jq gates the same hooks it gates in the other suites: without it several of
# them exit before they reach the code under test, which would leave the
# assertions passing for the wrong reason.
pytestmark = pytest.mark.skipif(
    _find_bash() is None or shutil.which("jq") is None,
    reason="bash and jq required",
)


def _to_bash_path(p: Path) -> str:
    s = Path(p).as_posix()
    m = re.match(r"^([A-Za-z]):/(.*)$", s)
    return f"/{m.group(1).lower()}/{m.group(2)}" if m else s


#: One stub for every verb these hooks invoke. It records its argv and its
#: stdin per verb, so a test can also assert the payload reached the CLI.
_LOCI_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$LOCI_ARGV_LOG"
_slot=$(printf '%s' "$1-${2:-}" | tr -c 'A-Za-z0-9-' '_')
cat > "$LOCI_STDIN_DIR/$_slot.stdin" 2>/dev/null || true
case "$*" in
    scan*) printf '%s\\n' '{"ok":true,"data":{"measurable":true,"report":"","measure_via":"self"}}' ;;
    "build snapshot"*) printf '%s\\n' '{"ok":true,"data":{}}' ;;
    "contract draft show"*) printf '%s\\n' '{"ok":true,"data":{"pending":1,"stale":false,"ops":[{"op":"add"}]}}' ;;
esac
exit 0
"""


class Run:
    def __init__(self, proc, state: Path, argv_log: Path, stdin_dir: Path):
        self.proc = proc
        self.state = state
        self._argv_log = argv_log
        self._stdin_dir = stdin_dir

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

    @property
    def log_lines(self) -> list[str]:
        return [ln for ln in self.log.splitlines() if ln.strip()]

    def argv(self) -> list[str]:
        return (self._argv_log.read_text(encoding="utf-8").splitlines()
                if self._argv_log.is_file() else [])

    def stdin_of(self, slot: str) -> str:
        f = self._stdin_dir / f"{slot}.stdin"
        return f.read_text(encoding="utf-8") if f.is_file() else ""


def _run(hook: str, payload: dict, tmp_path: Path, *, dev: bool = True,
         cwd: Path | None = None) -> Run:
    """Run one hook on one payload, with a stubbed `loci` on PATH."""
    home = tmp_path / "fakehome"
    state = tmp_path / "state"
    bin_dir = tmp_path / "fakebin"
    stdin_dir = tmp_path / "stdin"
    for d in (home, state, bin_dir, stdin_dir):
        d.mkdir(parents=True, exist_ok=True)

    stub = bin_dir / "loci"
    stub.write_text(_LOCI_STUB, encoding="utf-8", newline="\n")
    stub.chmod(0o755)
    argv_log = tmp_path / "argv.txt"

    base = "/usr/bin:/bin:/usr/local/bin"
    jq = shutil.which("jq")
    if jq:
        base = f"{_to_bash_path(Path(jq).parent)}:{base}"
    env = {
        "PATH": f"{_to_bash_path(bin_dir)}:{base}",
        "HOME": _to_bash_path(home),
        "LOCI_STATE_DIR": _to_bash_path(state),
        "CLAUDE_PROJECT_DIR": _to_bash_path(cwd or tmp_path),
        "LOCI_ARGV_LOG": _to_bash_path(argv_log),
        "LOCI_STDIN_DIR": _to_bash_path(stdin_dir),
    }
    if dev:
        env["LOCI_ENV"] = "dev"

    proc = subprocess.run(
        [_find_bash(), _to_bash_path(HOOKS / hook)],
        input=json.dumps(payload),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60, env=env, cwd=str(cwd or tmp_path),
    )
    return Run(proc, state, argv_log, stdin_dir)


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


def _hooks_json_commands() -> list[str]:
    doc = json.loads((HOOKS / "hooks.json").read_text(encoding="utf-8"))
    return [h["command"] for entries in doc["hooks"].values()
            for e in entries for h in e["hooks"]]


# ── the registry: no hook may be left without a trace ───────────────────────

def test_every_registered_hook_is_a_script_that_logs():
    """The point of the change, pinned against `hooks.json` itself.

    An inline command string in `hooks.json` cannot log — it has no file to
    source the logger from — which is why the impact flush became
    `stats-flush.sh`. A new hook added as an inline string would silently
    reintroduce the gap this test exists to close.
    """
    for cmd in _hooks_json_commands():
        m = re.search(r"hooks/([\w.-]+\.sh)", cmd)
        assert m, f"hook is not a plugin script, so it can leave no trace: {cmd!r}"
        body = (HOOKS / m.group(1)).read_text(encoding="utf-8")
        assert "loci_log" in body, f"{m.group(1)} logs nothing"
        # Directly, or through `setup-steps.sh`, which sources it — either way
        # the functions have to be defined before the first call site.
        assert ("lib/loci_log.sh" in body or "lib/setup-steps.sh" in body), (
            f"{m.group(1)} never sources the logger")


# ── the id on the line is the id the hook was handed ────────────────────────

#: (hook, payload builder, a `src:` tag its lines must carry). One case per
#: hook registered in `hooks.json`, because a hook that logs nothing is exactly
#: the state QA cannot see.
CASES = [
    ("contract-guard.sh", lambda root: _payload(
        cwd=_to_bash_path(root), tool_name="Edit",
        tool_input={"file_path": ".loci/contract.yaml", "new_string": "x"}),
     "contract-guard"),
    ("contract-guard.sh", _c_edit, "contract-guard"),
    ("pre-edit-hook.sh", _c_edit, "pre-edit"),
    ("post-edit-hook.sh", _c_edit, "post-edit"),
    ("post-bash-bypass.sh", lambda root: _payload(
        cwd=_to_bash_path(root), tool_name="Bash",
        tool_input={"command": "make"}), "post-bash"),
    ("prompt-submit-turn.sh", lambda root: _payload(
        cwd=_to_bash_path(root), hook_event_name="UserPromptSubmit"),
     "prompt-submit"),
    ("manifest-status-nudge.sh", lambda root: _payload(
        cwd=_to_bash_path(root), hook_event_name="Stop"), "manifest-nudge"),
    ("stats-flush.sh", lambda root: _payload(
        cwd=_to_bash_path(root), hook_event_name="Stop"), "stats-flush"),
    ("turn-clean.sh", lambda root: _payload(
        cwd=_to_bash_path(root), hook_event_name="Stop"), "turn-clean"),
    ("draft-pending-nudge.sh", lambda root: _payload(
        cwd=_to_bash_path(root), hook_event_name="Stop"), "draft-nudge"),
]


def _project(tmp_path: Path) -> Path:
    """A tree every hook in CASES finds something to do in."""
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "main.c").write_text("int f(void) { return 0; }\n",
                                             encoding="utf-8")
    (tmp_path / ".loci" / "build").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".loci" / "build" / "contract.draft.yaml").write_text(
        "ops: []\n", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("hook,build,src", CASES,
                         ids=[f"{h}-{i}" for i, (h, _, _) in enumerate(CASES)])
def test_the_hook_logs_start_end_and_the_session_it_was_handed(
        hook, build, src, tmp_path):
    root = _project(tmp_path)
    run = _run(hook, build(root), tmp_path, cwd=root)

    assert run.code == 0, f"{hook} exited {run.code}: {run.proc.stderr}"
    lines = [ln for ln in run.log_lines if f"[loci.{src}]" in ln]
    assert lines, f"{hook} left no log line tagged [loci.{src}]:\n{run.log}"
    for ln in lines:
        assert f"[session={SESSION_ID}]" in ln, (
            f"{hook} logged a line with no session id — a concurrent session's "
            f"lines cannot be told apart:\n{ln}")
    assert any("start:" in ln for ln in lines), f"{hook} logged no start"
    assert any("end:" in ln for ln in lines), f"{hook} logged no end"


@pytest.mark.parametrize("hook,build,src", CASES,
                         ids=[f"{h}-{i}" for i, (h, _, _) in enumerate(CASES)])
def test_logging_off_leaves_the_hook_exactly_as_it_was(hook, build, src, tmp_path):
    """Production is silent by design, so the log must cost it nothing: same
    stdout, same exit code, and no file written."""
    root = _project(tmp_path)
    payload = build(root)

    dev = _run(hook, payload, tmp_path, cwd=root)
    off = _run(hook, payload, tmp_path / "off", dev=False,
               cwd=_project(tmp_path / "off"))

    assert off.code == dev.code == 0
    assert off.stdout == dev.stdout, f"{hook} prints differently with the log off"
    assert off.log == "", f"{hook} wrote a log outside dev mode"
    assert not (off.state / "loci.log").exists()


# ── the verdict, not just the fact that the hook ran ────────────────────────

def test_the_guards_verdict_is_in_the_log(tmp_path):
    """A `deny` that nothing records is a decision QA cannot review — and
    PreToolUse is fail-open, so "no verdict" and "allowed" look identical from
    outside."""
    root = _project(tmp_path)
    denied = _run("contract-guard.sh", _payload(
        cwd=_to_bash_path(root), tool_name="Edit",
        tool_input={"file_path": ".loci/build.yaml", "new_string": "x"}),
        tmp_path, cwd=root)
    assert json.loads(denied.stdout)["hookSpecificOutput"][
        "permissionDecision"] == "deny"
    assert "verdict=deny" in denied.log

    allowed = _run("contract-guard.sh", _c_edit(root), tmp_path / "b",
                   cwd=_project(tmp_path / "b"))
    assert allowed.stdout == ""
    assert "verdict=allow" in allowed.log


# ── stdin: read once, and the CLI still gets it ─────────────────────────────

@pytest.mark.parametrize("hook,slot", [
    ("post-bash-bypass.sh", "hook-post-bash"),
    ("prompt-submit-turn.sh", "hook-prompt-submit"),
    ("manifest-status-nudge.sh", "analyse-status"),
    ("stats-flush.sh", "stats-flush-impacts"),
])
def test_the_payload_still_reaches_the_cli_when_the_log_captured_it(hook, slot,
                                                                    tmp_path):
    """These four hand their stdin straight to a `loci` verb, and the session id
    lives on that stdin. Reading it for the log must not starve the verb: the
    hook re-feeds what it read, byte for byte."""
    root = _project(tmp_path)
    payload = _payload(cwd=_to_bash_path(root), hook_event_name="Stop")

    run = _run(hook, payload, tmp_path, cwd=root)

    assert run.code == 0
    seen = run.stdin_of(slot)
    assert seen, f"{hook} sent {slot} an empty stdin"
    assert json.loads(seen)["session_id"] == SESSION_ID


# ── the payload is not to be trusted ───────────────────────────────────────

@pytest.mark.parametrize("forged", [
    "a] [session=other] forged",
    "a\nb",
    "../../etc/passwd",
])
def test_a_forged_session_id_cannot_write_a_line_of_its_own(forged, tmp_path):
    """The id goes into the log line verbatim, and the payload is written by
    whoever is driving the harness. Anything outside the id charset is dropped
    rather than escaped, so a crafted value cannot forge a second line."""
    root = _project(tmp_path)
    run = _run("contract-guard.sh", _payload(
        session_id=forged, cwd=_to_bash_path(root), tool_name="Edit",
        tool_input={"file_path": ".loci/contract.yaml", "new_string": "x"}),
        tmp_path, cwd=root)

    assert run.code == 0
    assert "[session=" not in run.log, (
        f"a forged session_id reached the log:\n{run.log}")
    assert len(run.log_lines) == 2, (
        f"expected the hook's own two lines, got:\n{run.log}")
