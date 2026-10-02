"""`/loci:bug-report`'s host-records recipe (AAD-7789), run as the file ships it.

Under GitHub Copilot CLI an agent session's process log is empty (1.0.91 leaves
`--log-dir` empty and writes 0-byte `process-*.log` files), so the one record of
what the hooks did is the session transcript its Stop hook names (`transcriptPath`
in the recorded hook input):
`~/.copilot/session-state/<session>/events.jsonl`. The recipe reads four facts
out of it — per-event hook runs and failures, the failure messages, the tool
calls a hook denied, the model the session last switched to — and this file runs
the FENCE against a transcript shaped like the Epic's run A (every hook failing
`Hook command failed … No such file or directory`, every tool call denied),
because a retyped copy proves nothing about what ships (see
`test_bug_report_turn_recipe.py` for why).

The fixture carries one `skill.invoked` event whose `content` is the fence
itself, JSON-escaped, the way Copilot records a skill's prose into the same
file. The recipe must not count its own text.
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
SKILL = PLUGIN_ROOT / "skills" / "bug-report" / "SKILL.md"


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


def _text() -> str:
    return SKILL.read_text(encoding="utf-8").replace("\r\n", "\n")


def _fence() -> str:
    """The one bash fence that reads the Copilot transcript.

    Selected on `session-state`, the thing it is FOR; the count is asserted so a
    selector that matches nothing cannot pass vacuously."""
    blocks = re.findall(r"```bash\n(.*?)```", _text(), re.S)
    hits = [b for b in blocks if "session-state" in b]
    assert len(hits) == 1, f"expected one host-records fence, found {len(hits)}"
    program = hits[0]
    # The fence depends on nothing the skill does not say where to get: the
    # session id is the one Copilot exports to the model's shell (probed
    # 2026-10-02, 1.0.91: `COPILOT_AGENT_SESSION_ID`), and `$HOME` is the shell's.
    for name in re.findall(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)", program):
        assert name in {"HOME", "COPILOT_AGENT_SESSION_ID"} or f"{name}=" in program, (
            f"the fence reads ${name}, which nothing in it or in the skill sets")
    return program


SID = "7ea0cd28-02db-43c5-99b5-638ee9d91ebb"


def _ev(type_: str, data: dict) -> str:
    # Compact separators: Copilot writes the file that way, and the recipe's
    # patterns are written against those bytes.
    return json.dumps({"type": type_, "data": data, "id": "x", "timestamp": "t",
                       "parentId": None}, separators=(",", ":"))


def _hook(hook_type: str, ok: bool, script: str | None = None) -> list[str]:
    hid = f"h-{hook_type}-{script}"
    end: dict = {"hookInvocationId": hid, "hookType": hook_type, "success": ok}
    if not ok and hook_type == "preToolUse":
        # The real shape (run A, 2026-10-02): a failed preToolUse carries the
        # denial it caused, keyed by tool call id, BETWEEN `success` and `error`.
        # The first version of the fence matched `"success":false,"error"` and so
        # never listed the one hook the run-A symptom is about.
        end["output"] = {"toolu_x": 'Denied by preToolUse hook from "loci" (hook errored)'}
    if not ok:
        end["error"] = {
            "message": ("Error: Hook command failed with code 1\nStderr: /usr/bin/bash: "
                        f"/hooks/{script}: No such file or directory\n"),
            "source": "loci"}
    return [_ev("hook.start", {"hookInvocationId": hid, "hookType": hook_type,
                               "input": {"sessionId": SID}}),
            _ev("hook.end", end)]


def _denied() -> str:
    return _ev("tool.execution_complete", {
        "toolCallId": "toolu_x", "model": "claude-sonnet-5", "success": False,
        "error": {"message": 'Denied by preToolUse hook from "loci" (hook errored)',
                  "code": "denied"}})


def _run_a_transcript() -> str:
    """Shaped like the Epic's run A as Copilot 1.0.91 recorded it on 2026-10-02
    (session 7ea0cd28…), one denial longer: five hook.end events fail with the
    launcher's `No such file` (userPromptSubmitted, sessionStart, preToolUse
    twice, agentStop — four scripts), two tool calls are denied, one later hook
    pair succeeds, and the model is Sonnet 5 at startup, then a subagent's."""
    lines = [
        _ev("session.start", {"sessionId": SID, "copilotVersion": "1.0.91"}),
        _ev("session.model_change", {"source": "startup", "newModel": "claude-sonnet-5"}),
        *_hook("userPromptSubmitted", False, "prompt-submit-turn.sh"),
        *_hook("sessionStart", False, "turn-clean.sh"),
        # the skill's own prose lands in the transcript, JSON-escaped
        _ev("skill.invoked", {"name": "bug-report", "path": str(SKILL),
                              "content": _fence()}),
        *_hook("preToolUse", False, "contract-guard.sh"),
        _denied(),
        *_hook("preToolUse", False, "contract-guard.sh"),
        _denied(),
        *_hook("preToolUse", True),
        *_hook("postToolUse", True),
        _ev("session.model_change", {"source": "agent", "newModel": "gpt-5.6-luna"}),
        *_hook("agentStop", False, "stats-flush.sh"),
    ]
    return "\n".join(lines) + "\n"


def _run(home: Path, sid: str = SID) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_find_bash(), "-c", _fence()],
        capture_output=True, text=True, timeout=60,
        env={"PATH": "/usr/bin:/bin", "HOME": _to_bash_path(home),
             "COPILOT_AGENT_SESSION_ID": sid},
    )


def _write(home: Path, transcript: str, sid: str = SID) -> None:
    d = home / ".copilot" / "session-state" / sid
    d.mkdir(parents=True)
    (d / "events.jsonl").write_text(transcript, encoding="utf-8", newline="\n")


def test_the_recipe_counts_runs_and_failures_per_hook_event(tmp_path):
    _write(tmp_path, _run_a_transcript())
    proc = _run(tmp_path)
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout.replace("\r\n", "\n")
    # Line 1 of the recipe: `uniq -c` over hookType+success.
    for count, line in (
        (1, '"hookType":"userPromptSubmitted","success":false'),
        (1, '"hookType":"sessionStart","success":false'),
        (2, '"hookType":"preToolUse","success":false'),
        (1, '"hookType":"preToolUse","success":true'),
        (1, '"hookType":"postToolUse","success":true'),
        (1, '"hookType":"agentStop","success":false'),
    ):
        assert re.search(rf"^\s*{count} {re.escape(line)}$", out, re.M), (line, out)


def test_the_recipe_quotes_the_failure_messages_once_each(tmp_path):
    _write(tmp_path, _run_a_transcript())
    out = _run(tmp_path).stdout.replace("\r\n", "\n")
    msgs = [ln for ln in out.splitlines()
            if ln.startswith('"hookType":"') and '"success":false,' in ln]
    # Five failing hook.end events, four distinct lines: `sort -u` folds the two
    # identical contract-guard failures, which is what keeps a long session's
    # repeats to one line each. The preToolUse line is the one with `output`.
    assert len(msgs) == 4, msgs
    assert any('"hookType":"preToolUse"' in m and '"output":{' in m for m in msgs), msgs
    scripts = {re.search(r"/hooks/([a-z-]+\.sh)", m).group(1) for m in msgs}
    assert scripts == {"prompt-submit-turn.sh", "turn-clean.sh",
                       "contract-guard.sh", "stats-flush.sh"}, scripts
    # The message is the host's own words — QA recognises the run-A symptom by them.
    assert all("Hook command failed with code 1" in m for m in msgs), msgs


def test_the_recipe_counts_the_denials_and_names_the_last_model(tmp_path):
    _write(tmp_path, _run_a_transcript())
    out = _run(tmp_path).stdout.replace("\r\n", "\n").splitlines()
    # Line 3 (`grep -c`) is a bare count printed right before line 4's rows; line 4
    # is every model the session used with a count each, so a subagent's model
    # shows beside the session's rather than replacing it.
    models = [ln for ln in out if '"newModel"' in ln]
    assert out[out.index(models[0]) - 1] == "2", out
    assert [m.split()[-1] for m in models] == ['"newModel":"claude-sonnet-5"',
                                             '"newModel":"gpt-5.6-luna"'], out


def test_the_recipe_does_not_count_its_own_prose(tmp_path):
    """Copilot records a skill's prose into the same transcript (`skill.invoked`),
    JSON-escaped. The fixture carries the fence itself that way; if the recipe's
    patterns matched the escaped text, every count here would be one too many."""
    bare = [ln for ln in _run_a_transcript().splitlines() if '"skill.invoked"' not in ln]
    _write(tmp_path, "\n".join(bare) + "\n")
    without = _run(tmp_path).stdout.replace("\r\n", "\n")
    shutil.rmtree(tmp_path / ".copilot")
    _write(tmp_path, _run_a_transcript())
    with_prose = _run(tmp_path).stdout.replace("\r\n", "\n")
    assert without == with_prose


def test_a_session_with_no_transcript_fails_loudly(tmp_path):
    """A wrong or absent session id must not read as "no hooks ran, nothing was
    denied": every grep names the missing path on stderr, which the model sees
    beside the empty counts. (The fence's exit status is `tail`'s, so it is not
    the signal; the four messages are.)"""
    proc = _run(tmp_path, sid="not-a-session")
    assert proc.stdout.strip() in ("", "0"), proc.stdout
    assert proc.stderr.count("not-a-session/events.jsonl") == 4, proc.stderr


def test_the_skill_gates_the_host_records_on_copilot_and_keeps_claude_unchanged():
    text = " ".join(_text().split())   # the prose wraps; the sentences do not
    assert "17. **Host records** (only when item 1 named GitHub Copilot CLI" in text
    assert "n/a — Claude Code" in text
    # Claude Code's own probe stays what it was before the Story.
    assert 'otherwise Claude Code, `claude --version 2>/dev/null || echo "unknown"`' in text
    # The id the fence reads is the one Copilot exports to the model's shell, and
    # the skill says where an earlier session's id comes from.
    assert "`$COPILOT_AGENT_SESSION_ID` is this session's id" in text
    assert "`Resume` line Copilot printed when it ended, else the newest other directory" in text


def test_the_skill_says_where_the_records_go_and_what_run_a_looks_like():
    text = " ".join(_text().split())
    # Into the report: check 9's detail and a Raw Data block, so the report's
    # shape is the one triage already reads.
    assert "under Copilot item 17's per-event counts go in the detail" in text
    # PowerShell is Copilot's shell tool on Windows and the fence's quotes do not
    # survive `bash -c '…'` (a live run printed a mangled pattern and no counts).
    assert "`mkdir -p .loci/build`, save a fence to a file there and run `bash <file>`" in text
    assert "<summary>host records (item 17)</summary>" in text
    # The Epic's run-A symptom, in the host's words, under the root-cause chain.
    assert 'Denied by preToolUse hook from "loci" (hook errored)' in text
    assert "Hook command failed … /hooks/<name>.sh: No such file or directory" in text
    assert "a plugin before v0.2.69" in text
    # The registry row is the installed copy, not necessarily the running one.
    assert "`copilot plugin list --json`" in text
    assert "the context's `plugin dir:` is the copy running" in text
    assert "item 17's registry row says which versions are installed" in text


def test_the_checklist_arithmetic_agrees_with_the_root_cause_step():
    """`If all 11 checks pass` outlived the checklist's shrink to ten."""
    text = _text()
    claimed = int(re.search(r"Run (\d+)-point diagnostics checklist", text).group(1))
    assert f"If all {claimed} checks pass" in text
