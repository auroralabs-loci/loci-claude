"""`tests/acceptance/copilot_smoke.sh` judges a Copilot session from its logs (AAD-7793).

The smoke runs a real `copilot -p` session, which needs a signed-in Copilot and
Claude Sonnet on the other end — nothing a unit suite can have. What a unit
suite CAN hold to account is the judging: the twelve checks the script applies
to Copilot's process log, the session JSONL, the plugin's log and the project's
turn tree. `--recheck <workdir>` runs exactly those checks on a kept run, so
these tests build a work dir shaped like the real one (the shapes below are
copied from the Windows run of 2026-10-02, Copilot 1.0.91) and prove that the
green run passes, and that each way a Copilot release could break the plugin
turns the right check red and names it. The negative control's staging
(`--stage-control`) is covered the same way: the copy must carry the pre-Epic
launcher and nothing else may change.

What is NOT tested here: that Copilot still writes these shapes. That is the
acceptance run itself, on three hosts, before a release (RELEASING.md).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = PLUGIN_ROOT / "tests" / "acceptance" / "copilot_smoke.sh"

TURN = "cp-1790978611-07911b3b2962136f1fa87d"
SID = "59c6075f-3e27-4f05-b4e6-707214f97b73"
REASON = ("The build recipe (.loci/build.yaml) is written by 'loci init', never edited. "
          "It records the target ISA, compiler and flags every LOCI measurement is made with, "
          "so an edit here changes what your own numbers mean")
VERSION = json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
REMINDER = ("[loci] main.c was modified. You MUST invoke the loci:loci-post-edit skill NOW — "
            "do not proceed to the next edit or respond to the user first. "
            f"Pass turn id {TURN} to the skill as its --turn value. ")
HOOKS = ("session-init", "prompt-submit", "contract-guard", "pre-edit", "post-edit",
         "post-bash", "stats-flush", "draft-nudge", "manifest-nudge", "turn-clean")


def _find_bash() -> str | None:
    if sys.platform == "win32":
        for cand in (r"C:\Program Files\Git\usr\bin\bash.exe",
                     r"C:\Program Files (x86)\Git\usr\bin\bash.exe"):
            if Path(cand).is_file():
                return cand
    return shutil.which("bash")


def _to_bash_path(p: Path) -> str:
    s = Path(p).as_posix()
    if sys.platform == "win32" and len(s) > 1 and s[1] == ":":
        s = "/" + s[0].lower() + s[2:]
    return s


def _context(version: str = VERSION, plugin_dir: str | None = None,
             host: str = "GitHub Copilot CLI 1.0.91") -> str:
    """The SessionStart context as it sits, JSON-escaped, in the process log.
    Check 3 holds its VALUES to the checkout the smoke runs from, so the fixture
    carries this checkout's version and directory, in the hook's bash spelling
    (the Windows runs of 2026-10-03 recorded `/c/Projects/loci-claude-dev`)."""
    return (f"loci version: {version} — LOCI's only user-facing version.\\n"
            f"plugin dir: {plugin_dir or _to_bash_path(PLUGIN_ROOT)}\\n"
            f"host: {host}. Tool names in LOCI's skills read as yours.\\n"
            "Available: /loci:help, /loci:init")


CONTEXT = _context()
BASH = _find_bash()
pytestmark = [pytest.mark.unit, pytest.mark.skipif(BASH is None, reason="bash required")]


def _run(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("COPILOT_CLI", None)
    return subprocess.run([BASH, _to_bash_path(SCRIPT), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", env=env, timeout=60)


# ── a work dir shaped like a real kept run ──────────────────────────────────
def _process_log(turn: str = TURN, *, reminder_turn: str | None = None, deny: int = 1,
                 injected: bool = True, extra: str = "", context: str = CONTEXT) -> str:
    reminder = REMINDER.replace(TURN, reminder_turn or turn)
    stamp = f"[loci] turn={turn}"
    lines = [
        '2026-10-02T22:03:28.000Z [DEBUG] [rust:hooks] [hook stdout] {"hookSpecificOutput":'
        f'{{"hookEventName":"SessionStart","additionalContext":"{context}"}},'
        f'"additionalContext":"{context}"}}',
        # The UserPromptSubmit reply is pretty-printed by `loci hook prompt-submit`.
        "2026-10-02T22:03:31.000Z [DEBUG] [rust:hooks] [hook stdout] {",
        '  "hookSpecificOutput": {',
        '    "hookEventName": "UserPromptSubmit",',
        f'    "additionalContext": "{stamp}"',
        "  },",
        f'  "additionalContext": "{stamp}"',
        "}",
        '2026-10-02T22:03:49.000Z [DEBUG] [rust:hooks] [hook stdout] {"hookSpecificOutput":'
        f'{{"hookEventName":"PostToolUse","additionalContext":"{reminder}"}},'
        f'"additionalContext":"{reminder}"}}',
    ]
    if injected:
        lines.append(f'          "text": "{context}",')
        lines.append(f'          "text": "<system_reminder>\\n{stamp}\\n</system_reminder>",')
    for _ in range(deny):
        lines.append('2026-10-02T22:03:58.000Z [DEBUG] [rust:hooks] [hook stdout] {"hookSpecificOutput":'
                     f'{{"hookEventName":"PreToolUse","permissionDecision":"deny",'
                     f'"permissionDecisionReason":"{REASON}"}}}}')
        lines.append(f'          "content": "Denied by preToolUse hook: {REASON}"')
    lines.append(extra)
    return "\n".join(lines) + "\n"


def _jsonl(turn: str = TURN, *, quoted: bool = True, shell: bool = True, sid: str = SID) -> str:
    answer = f"LOCI LINES BEFORE\\n[loci] turn={turn}\\nLOCI LINES AFTER\\n[loci] turn={turn}\\ndone"
    if not quoted:
        answer = "LOCI LINES BEFORE: none\\ndone"
    lines = [
        '{"type":"tool.execution_start","data":{"toolName":"edit","arguments":{"path":"main.c"}}}',
        '{"type":"tool.execution_complete","data":{"success":true,"result":{"content":"File main.c updated"}}}',
        '{"type":"tool.execution_complete","data":{"success":false,"error":{"message":"Denied by preToolUse hook: '
        + REASON + '"}}}',
    ]
    if shell:
        lines.append('{"type":"tool.execution_complete","data":{"success":true,"result":{"content":"copilot-smoke-done\\n"}}}')
    lines.append(f'{{"type":"assistant.message","data":{{"model":"claude-sonnet-5","content":"{answer}"}}}}')
    lines.append(f'{{"type":"result","data":{{"sessionId":"{sid}","exitCode":0}}}}')
    return "\n".join(lines) + "\n"


def _hook_log(*, hooks=HOOKS, stop_clean: bool = True, stop_rc: int = 0, sid: str = SID) -> str:
    t = "2026-10-02T22:03:31.687Z"
    lines = []
    for h in hooks:
        if h == "session-init":
            lines.append(f"{t} [INFO] [loci.session-init] [session={sid}] start: SessionStart hook "
                         "(cwd=/c/Projects/loci-claude-dev state_dir=/tmp/x/state)")
        elif h == "turn-clean":
            # SessionStart's turn-clean writes the same `end:` line the Stop one does.
            lines.append(f"{t} [INFO] [loci.turn-clean] [session={sid}] start: SessionStart")
            lines.append(f"{t} [INFO] [loci.turn-clean] [session={sid}] end: build clean rc=0 (hook rc=0)")
        else:
            lines.append(f"{t} [INFO] [loci.{h}] [session={sid}] start: something")
    if stop_clean:
        lines.append(f"{t} [INFO] [loci.turn-clean] [session={sid}] start: Stop")
        lines.append(f"{t} [INFO] [loci.turn-clean] [session={sid}] end: build clean rc={stop_rc} (hook rc=0)")
    return "\n".join(lines) + "\n"


def _put(path: Path, text: str) -> None:
    """LF on every host: the real files are LF, and Windows must test the same bytes."""
    path.write_text(text, encoding="utf-8", newline="\n")


def _work(tmp_path: Path) -> Path:
    w = tmp_path / "work"
    (w / "logs").mkdir(parents=True)
    (w / "state").mkdir()
    proj = w / "proj"
    (proj / ".loci" / "build" / "turn").mkdir(parents=True)
    (proj / ".loci" / "build" / "turns" / "88e91d3baaadefef" / "linked").mkdir(parents=True)
    _put(w / "logs" / "process-1790978607140-24424.log", _process_log())
    _put(w / "session.jsonl", _jsonl())
    _put(w / "state" / "loci.log", _hook_log())
    _put(w / "state" / f"turn-{SID}", TURN + "\n")
    _put(proj / "main.c", "int add(int a, int b) { return a - b; }\n")
    _put(proj / ".loci" / "build.yaml", "target: cortex-m4\n")
    _put(w / "build.yaml.before", "target: cortex-m4\n")
    _put(proj / ".loci" / "build" / "turn" / "current", TURN + "\n")
    tdir = proj / ".loci" / "build" / "turns" / "88e91d3baaadefef"
    _put(tdir / "turn.json", json.dumps({"turn": TURN, "created": "2026-10-02T22:03:50Z"}))
    (tdir / "linked" / "main.elf.prev").write_bytes(b"\x7fELF")
    return w


def _recheck(w: Path) -> subprocess.CompletedProcess:
    return _run("--recheck", _to_bash_path(w))


def _fails(out: str) -> list[int]:
    return sorted(int(line.split()[1]) for line in out.splitlines() if line.startswith("FAIL  "))


# ── the script itself ───────────────────────────────────────────────────────
def test_the_script_parses():
    r = subprocess.run([BASH, "-n", _to_bash_path(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_help_names_every_check_and_both_modes():
    r = _run("--help")
    assert r.returncode == 0
    for n in range(1, 13):
        assert f"\n#  {n:2d}. " in r.stdout or f"\n#   {n}. " in r.stdout, f"check {n} missing from --help"
    assert "--negative-control" in r.stdout
    assert "--recheck" in r.stdout
    assert "LOCI_STATE_DIR" in r.stdout  # the isolation is documented


def test_an_unknown_argument_is_refused():
    r = _run("--bogus")
    assert r.returncode == 2
    assert "unknown argument" in r.stderr


# ── the green run ───────────────────────────────────────────────────────────
def test_a_recorded_green_run_passes_all_twelve_checks(tmp_path):
    r = _recheck(_work(tmp_path))
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count("PASS  ") == 12
    assert "Copilot smoke: all 12 checks passed" in r.stdout
    assert f"turn={TURN}" in r.stdout            # check 10 names the id it found
    assert "linked/main.elf.prev" in r.stdout   # check 6 names the capture it found
    assert "hook cwd: /c/Projects/loci-claude-dev" in r.stdout  # the informational line


# ── each way a Copilot release could break the plugin turns one check red ──
PLOG = Path("logs") / "process-1790978607140-24424.log"


def _append(w: Path, rel: Path, text: str) -> None:
    _put(w / rel, (w / rel).read_text(encoding="utf-8") + text)


def _mut_hook_failed(w: Path):
    _append(w, PLOG, '2026-10-02T22:06:43.345Z [ERROR] [rust:hooks] Hook from "loci" execution failed: '
                     'Error: Hook command failed with code 1\n')


def _mut_hook_errored_denial(w: Path):
    _append(w, PLOG, '          "content": "Denied by preToolUse hook from \\"loci\\" (hook errored)"\n')


def _mut_context_not_injected(w: Path):
    _put(w / PLOG, _process_log(injected=False))


def _mut_context_of_another_version(w: Path):
    """A context built by another plugin — an installed one loading beside the
    checkout — still says `loci version:`, `plugin dir:` and `host:`."""
    _put(w / PLOG, _process_log(context=_context(version="0.0.1")))


def _mut_context_of_another_plugin_dir(w: Path):
    _put(w / PLOG, _process_log(context=_context(plugin_dir="/c/Users/x/.copilot/installed-plugins/loci")))


def _mut_context_of_another_host(w: Path):
    """The host line a host detection that missed Copilot would leave."""
    _put(w / PLOG, _process_log(context=_context(host="Claude Code")))


def _mut_two_turn_ids(w: Path):
    _append(w, PLOG, '  "additionalContext": "[loci] turn=cp-1790978700-0000000000000000000000"\n')


def _mut_reminder_names_another_turn(w: Path):
    _put(w / PLOG, _process_log(reminder_turn="cp-1790978700-0000000000000000000000"))


def _mut_turn_current_differs(w: Path):
    _put(w / "proj" / ".loci" / "build" / "turn" / "current", "cp-1-other\n")


def _mut_no_capture(w: Path):
    (w / "proj" / ".loci" / "build" / "turns" / "88e91d3baaadefef" / "linked" / "main.elf.prev").unlink()


def _mut_adapter_record_differs(w: Path):
    _put(w / "state" / f"turn-{SID}", "cp-1-other\n")


def _mut_no_deny(w: Path):
    _put(w / PLOG, _process_log(deny=0))


def _mut_two_denies(w: Path):
    _put(w / PLOG, _process_log(deny=2))


def _mut_edit_did_not_land(w: Path):
    _put(w / "proj" / "main.c", "int add(int a, int b) { return a + b; }\n")


def _mut_shell_only_in_the_prompt(w: Path):
    _put(w / "session.jsonl", _jsonl(shell=False))


def _mut_model_never_quoted_the_stamp(w: Path):
    _put(w / "session.jsonl", _jsonl(quoted=False))


def _mut_no_stop_clean(w: Path):
    _put(w / "state" / "loci.log", _hook_log(stop_clean=False))


def _mut_stop_clean_failed(w: Path):
    """The Stop clean exited 1; SessionStart's earlier `rc=0` line must not stand in for it."""
    _put(w / "state" / "loci.log", _hook_log(stop_rc=1))


def _mut_a_hook_never_ran(w: Path):
    _put(w / "state" / "loci.log", _hook_log(hooks=tuple(h for h in HOOKS if h != "post-bash")))


def _mut_recipe_changed(w: Path):
    _put(w / "proj" / ".loci" / "build.yaml", "target: cortex-m4\n# note\n")


@pytest.mark.parametrize("mutate, red, named", [
    (_mut_a_hook_never_ran, {1}, "post-bash"),
    (_mut_hook_failed, {2}, "Hook command failed"),
    (_mut_hook_errored_denial, {2}, "hook errored"),
    (_mut_context_not_injected, {3}, '"text": ?"loci version: '),
    (_mut_context_of_another_version, {3}, "missing: \\[hook stdout\\].*loci version: "),
    (_mut_context_of_another_plugin_dir, {3}, "missing: \\[hook stdout\\].*loci version: "),
    (_mut_context_of_another_host, {3}, "host: GitHub Copilot CLI"),
    # Two ids: check 4 names the count, and every check keyed on the id says `__none__`
    # rather than pass on a guess (the test below pins that no id is invented).
    (_mut_two_turn_ids, {4, 5, 6, 7, 10, 11}, "found 2"),
    (_mut_reminder_names_another_turn, {5}, f"Pass turn id {TURN}"),
    (_mut_turn_current_differs, {6, 11}, "cp-1-other"),
    (_mut_no_capture, {6}, "no turn tree with a capture"),
    (_mut_adapter_record_differs, {7}, "cp-1-other"),
    (_mut_no_deny, {8}, "denies seen: 0"),
    (_mut_two_denies, {8}, "2 denies"),
    (_mut_edit_did_not_land, {9}, "main.c edited: NO"),
    (_mut_shell_only_in_the_prompt, {9}, "shell output in a tool result: NO"),
    (_mut_model_never_quoted_the_stamp, {10}, "never quotes"),
    (_mut_no_stop_clean, {11}, "turn-clean at Stop"),
    (_mut_stop_clean_failed, {11}, "rc=1"),
    (_mut_recipe_changed, {12}, "the recipe changed"),
], ids=lambda x: getattr(x, "__name__", None) or str(x))
def test_each_breakage_turns_the_right_check_red(tmp_path, mutate, red, named):
    w = _work(tmp_path)
    mutate(w)
    r = _recheck(w)
    assert r.returncode == 1, r.stdout + r.stderr
    assert set(_fails(r.stdout)) == red, r.stdout
    assert named in r.stdout, f"the failure should name {named!r}:\n{r.stdout}"
    assert "Copilot smoke: all 12 checks passed" not in r.stdout
    assert f"Copilot smoke: {len(red)} check(s) FAILED" in r.stdout


def test_the_context_may_spell_the_plugin_dir_the_hosts_way(tmp_path):
    """Check 3 takes the directory in the hook's bash spelling (`/c/…`, which
    the Windows runs recorded) or the host's own (`C:/…` under Git Bash)."""
    host_spelling = subprocess.run(
        [BASH, "-c", f'command -v cygpath >/dev/null && cygpath -m "{_to_bash_path(PLUGIN_ROOT)}" '
                     f'|| printf %s "{_to_bash_path(PLUGIN_ROOT)}"'],
        capture_output=True, text=True, encoding="utf-8").stdout.strip()
    w = _work(tmp_path)
    _put(w / PLOG, _process_log(context=_context(plugin_dir=host_spelling)))
    r = _recheck(w)
    assert r.returncode == 0, r.stdout


def test_prose_that_mentions_a_hook_error_is_not_one(tmp_path):
    """Check 2 matches Copilot's `(hook errored)` marker, not the words: at
    --log-level all a request replays the model's context and its answers, and
    "no hook errored" there is prose (AAD-7835)."""
    w = _work(tmp_path)
    _append(w, PLOG, '          "text": "Checked: no hook errored and none was denied.",\n')
    r = _recheck(w)
    assert r.returncode == 0, r.stdout
    assert "PASS  2 " in r.stdout


def test_two_turn_ids_make_the_id_checks_say_so_rather_than_guess(tmp_path):
    w = _work(tmp_path)
    _mut_two_turn_ids(w)
    r = _recheck(w)
    for n in (5, 6, 7, 10):
        line = next(ln for ln in r.stdout.splitlines() if ln.startswith(f"FAIL  {n} "))
        assert "__none__" in line, line


def test_a_misquote_replayed_in_the_requests_is_not_a_second_turn_id(tmp_path):
    """At --log-level all Copilot replays every request's messages, the model's own
    `LOCI LINES` quote among them; a mangled character there is the model's, not the
    hook's, and must not fail check 4 (check 10 judges the quote)."""
    w = _work(tmp_path)
    _append(w, PLOG, f'          "text": "LOCI LINES BEFORE\\n[loci] turn={TURN[:-2]}xx\\n",\n')
    r = _recheck(w)
    assert r.returncode == 0, r.stdout
    assert f"4 one turn id minted and stamped: {TURN}" in r.stdout


def test_an_empty_process_log_fails_rather_than_passing_the_forbid(tmp_path):
    w = _work(tmp_path)
    _put(w / PLOG, "")
    r = _recheck(w)
    assert r.returncode == 1
    fails = _fails(r.stdout)
    assert 2 in fails and 3 in fails, r.stdout
    assert "no such file or empty: process.log" in r.stdout


def test_a_missing_session_jsonl_is_a_named_failure_not_a_crash(tmp_path):
    w = _work(tmp_path)
    (w / "session.jsonl").unlink()
    r = _recheck(w)
    assert r.returncode == 1
    assert {7, 9, 10} <= set(_fails(r.stdout)), r.stdout
    assert "no sessionId in session.jsonl" in r.stdout


def test_a_recheck_without_the_recipe_snapshot_says_so(tmp_path):
    w = _work(tmp_path)
    (w / "build.yaml.before").unlink()
    r = _recheck(w)
    assert 12 in _fails(r.stdout)
    assert "build.yaml.before is missing" in r.stdout


def test_a_missing_hook_log_names_every_hook(tmp_path):
    w = _work(tmp_path)
    (w / "state" / "loci.log").unlink()
    r = _recheck(w)
    assert 1 in _fails(r.stdout)
    for h in HOOKS:
        assert h in r.stdout


# ── the negative control's staging ──────────────────────────────────────────
def test_stage_control_rewrites_only_the_launcher(tmp_path):
    out = tmp_path / "broken"
    r = _run("--stage-control", _to_bash_path(out))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "12 hook commands rewritten" in r.stdout
    for d in (".claude-plugin", "hooks", "lib", "skills", "agents.txt"):
        assert (out / d).exists(), d
    original = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    staged = json.loads((out / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    # One broken launcher on every host: a variable no shell sets. The literal
    # pre-Epic line (`$CLAUDE_PLUGIN_ROOT`) is broken only while Copilot hands the
    # command to PowerShell, which a Copilot release could stop doing (AAD-7835).
    var = "$CLAUDE_PLUGIN_ROOT_UNSET"
    n = 0
    for event, entries in original["hooks"].items():
        for i, entry in enumerate(entries):
            for j, hook in enumerate(entry["hooks"]):
                got = staged["hooks"][event][i]["hooks"][j]
                script = hook["command"].split("/hooks/")[1].split('"')[0]
                assert got["command"] == f'bash "{var}/hooks/{script}"', got["command"]
                for k, v in hook.items():
                    if k != "command":
                        assert got[k] == v, (event, script, k)
                n += 1
    assert n == 12
    assert "variable empty" in r.stdout


def test_stage_control_refuses_a_plugin_whose_launcher_it_does_not_know(tmp_path):
    other = tmp_path / "other"
    for d in (".claude-plugin", "hooks", "lib", "skills"):
        shutil.copytree(PLUGIN_ROOT / d, other / d)
    shutil.copy(PLUGIN_ROOT / "agents.txt", other / "agents.txt")
    hooks = other / "hooks" / "hooks.json"
    _put(hooks, hooks.read_text(encoding="utf-8").replace("exec bash", "exec sh"))
    r = _run("--plugin-dir", _to_bash_path(other), "--stage-control", _to_bash_path(tmp_path / "out"))
    assert r.returncode == 1, r.stdout
    assert "could not rewrite hooks.json (0 of 12" in r.stdout


def test_stage_control_refuses_a_partial_rewrite(tmp_path):
    """One hook already on another launcher: the control must not call 11 of 12 a control."""
    other = tmp_path / "other"
    for d in (".claude-plugin", "hooks", "lib", "skills"):
        shutil.copytree(PLUGIN_ROOT / d, other / d)
    shutil.copy(PLUGIN_ROOT / "agents.txt", other / "agents.txt")
    hooks = other / "hooks" / "hooks.json"
    text = hooks.read_text(encoding="utf-8")
    _put(hooks, text.replace("exec bash \\\"$CLAUDE_PLUGIN_ROOT/hooks/stats-flush.sh",
                             "exec sh \\\"$CLAUDE_PLUGIN_ROOT/hooks/stats-flush.sh"))
    r = _run("--plugin-dir", _to_bash_path(other), "--stage-control", _to_bash_path(tmp_path / "out"))
    assert r.returncode == 1, r.stdout
    assert "11 of 12" in r.stdout


# ── the release page and the suite header point at it ──────────────────────
def test_releasing_and_run_tests_name_the_gates():
    releasing = (PLUGIN_ROOT / "RELEASING.md").read_text(encoding="utf-8")
    header = (PLUGIN_ROOT / "run_tests.sh").read_text(encoding="utf-8")
    for text in (releasing, header):
        assert "tests/acceptance/copilot_smoke.sh" in text
        assert "--negative-control" in text
        assert "tools/claude-parity.sh" in text
    assert "Windows" in releasing and "macOS" in releasing and "Linux" in releasing
    assert "all 12 checks passed" in releasing and "all 10 checks passed" in releasing
