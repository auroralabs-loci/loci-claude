"""Every hook's reply, end to end, in the envelope each host reads (AAD-7790).

`test_host_reply_envelope.py` pins the two WRITERS in `lib/loci_json.sh`; this
module drives the HOOKS that call them — one run per hook that writes a reply
of its own (not one it relays from a `loci` verb), under both hosts — and
checks the document that reaches the harness:

* **One JSON object on stdout**, parseable, and nothing else (Claude Code and
  Copilot both parse stdout whole; a second line is a parse error that costs
  the reply).
* **Claude Code's shape is untouched**: the nested `hookSpecificOutput` with the
  event's own name, a `systemMessage` where there is one, and NO top-level
  `additionalContext` — the field is the Copilot envelope and a Claude Code
  reply that grew it would be a Copilot branch that leaked past its gate.
* **Copilot's reply carries the same text where Copilot reads it**: a top-level
  `additionalContext` equal to the nested text, or to the `systemMessage` when
  the message is all there is — except on `Stop`, which has no context channel
  (COPILOT-PROBE-EVIDENCE.md §6) and is carried to the next prompt instead.
* **A deny is nested under both** — the one hook reply Copilot honoured before
  the Epic, kept exactly so.
* **A PostToolUse reply stays under 10 KB.** It is injected into the model's
  turn after every edit; the Story's bound.

The fixture module (`tests/fixtures/copilot_payloads.py`) supplies the hosts;
every Claude-shaped payload here is built as the older tests build theirs and
re-spelled by the host, so the Claude run is the run those tests make.
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

from tests.fixtures.copilot_payloads import Host

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
HOOKS = PLUGIN_ROOT / "hooks"

POST_TOOL_USE_BOUND = 10 * 1024


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


_SCAN_OK = ("echo '{\"ok\":true,\"data\":{\"report\":\"[loci · pre-scan] f: call graph clean\","
            "\"applied\":true,\"measurable\":true,\"governed\":true,\"artifact_only\":false}}'")

PROMPT_ID = "7790c0de-0000-4000-8000-000000000001"


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / ".loci" / "build" / "turns").mkdir(parents=True)
    (root / ".loci" / "build.yaml").write_text("version: 1\n", encoding="utf-8")
    (root / "main.c").write_text("int f(void) { return 1; }\n", encoding="utf-8")
    return root


def _run(tmp_path: Path, host: Host, hook: str, payload: dict, *, stub: str | None,
         env_extra: dict | None = None, timeout: int = 60) -> dict | None:
    """Run `hook` on the Claude-shaped `payload` as `host` would: re-spelled,
    with the host's environment, from the host's working directory, with a
    `loci` stub (or none) on PATH. Returns the one JSON object on stdout."""
    root = tmp_path / "proj"
    home = tmp_path / "home"
    state = tmp_path / "state"
    bin_dir = home / ".local" / "bin"
    for d in (home, state, bin_dir):
        d.mkdir(parents=True, exist_ok=True)
    if stub is not None:
        (bin_dir / "loci").write_text(f"#!/usr/bin/env bash\n{stub}\n", encoding="utf-8",
                                      newline="\n")
        (bin_dir / "loci").chmod(0o755)
    env = {
        "PATH": f"{_to_bash_path(bin_dir)}:/usr/bin:/bin:/usr/local/bin",
        "HOME": _to_bash_path(home),
        "LOCI_STATE_DIR": _to_bash_path(state),
        "CLAUDE_PROJECT_DIR": _to_bash_path(root),
        "_LOCI_BOOTSTRAP": "1",
    }
    env.update(host.env(project=_to_bash_path(root)))
    if env_extra:
        env.update(env_extra)
    doc = host.respell(payload)
    host.seed(state)
    proc = subprocess.run([_find_bash(), _to_bash_path(HOOKS / hook)],
                          input=json.dumps(doc), capture_output=True, text=True,
                          encoding="utf-8", timeout=timeout, env=env,
                          cwd=host.cwd(root))
    assert proc.returncode == 0, f"{hook} must exit 0; got {proc.returncode}: {proc.stderr!r}"
    assert proc.stderr == "", f"{hook} must be silent on stderr: {proc.stderr!r}"
    out = proc.stdout
    if not out.strip():
        return None
    assert out.count("\n") <= 1 and out.endswith("\n"), (
        f"{hook}: stdout must be one JSON line, got {out!r}")
    reply = json.loads(out)
    assert isinstance(reply, dict), f"{hook}: stdout is not one JSON object: {out!r}"
    return reply


# ── the cases: every hook that writes a reply of its own ────────────────────

def _edit(root: Path, event: str = "PreToolUse") -> dict:
    doc = {"hook_event_name": event, "prompt_id": PROMPT_ID, "cwd": _to_bash_path(root),
           "tool_name": "Edit",
           "tool_input": {"file_path": _to_bash_path(root / "main.c"),
                          "old_string": "1", "new_string": "2"}}
    if event.startswith("Post"):
        doc["tool_response"] = {"structuredPatch": []}
    return doc


def _write(root: Path, rel: str) -> dict:
    return {"hook_event_name": "PreToolUse", "prompt_id": PROMPT_ID, "cwd": _to_bash_path(root),
            "tool_name": "Write",
            "tool_input": {"file_path": _to_bash_path(root / rel), "content": "x"}}


def _stop(root: Path) -> dict:
    return {"hook_event_name": "Stop", "prompt_id": PROMPT_ID, "cwd": _to_bash_path(root),
            "stop_hook_active": False}


#: (hook, event, payload builder, loci stub or None, extra env, what the reply
#: must carry). `None` for the stub is "no `loci` on PATH".
CASES = [
    ("session-init.sh", "SessionStart",
     lambda root: {"hook_event_name": "SessionStart", "cwd": _to_bash_path(root),
                   "source": "new"},
     "case \"$1\" in --version) echo 'loci 0.2.64';; *) echo '{\"ok\":true,\"data\":{}}';; esac",
     {}, "context"),
    ("prompt-submit-turn.sh", "UserPromptSubmit",
     lambda root: {"hook_event_name": "UserPromptSubmit", "prompt_id": PROMPT_ID,
                   "cwd": _to_bash_path(root), "prompt": "hi"},
     None, {}, "message"),
    ("pre-edit-hook.sh", "PreToolUse", _edit, _SCAN_OK, {}, "context"),
    ("post-edit-hook.sh", "PostToolUse", lambda root: _edit(root, "PostToolUse"),
     _SCAN_OK, {}, "context"),
    ("contract-guard.sh", "PreToolUse", lambda root: _write(root, ".loci/build.yaml"),
     None, {}, "deny"),
    ("manifest-status-nudge.sh", "Stop", _stop, None, {}, "message"),
    ("manifest-status-nudge.sh", "Stop", _stop, "exit 1", {"LOCI_FAIL_FAST": "1"}, "message"),
    ("pre-edit-hook.sh", "PreToolUse", _edit, "exit 1", {"LOCI_FAIL_FAST": "1"}, "context"),
    ("post-edit-hook.sh", "PostToolUse", lambda root: _edit(root, "PostToolUse"),
     "exit 1", {"LOCI_FAIL_FAST": "1"}, "context"),
]
_IDS = [f"{h}-{e}{'-failfast' if x else ''}" for h, e, _, _, x, _ in CASES]


def _check(host: Host, hook: str, event: str, reply: dict, kind: str) -> None:
    nested = reply.get("hookSpecificOutput")
    top = reply.get("additionalContext")
    msg = reply.get("systemMessage")

    if kind == "deny":
        assert nested and nested.get("permissionDecision") == "deny", reply
        assert nested.get("hookEventName") == "PreToolUse", reply
        assert "permissionDecision" not in reply, "a decision is nested under both hosts"
        assert top is None and msg is None, reply
        return

    if kind == "context":
        assert nested and nested.get("additionalContext"), f"{hook}: no context: {reply}"
        assert nested.get("hookEventName") == event, reply
    else:
        assert msg, f"{hook}: no message: {reply}"

    if not host.copilot:
        assert top is None, (
            f"{hook}: a Claude Code reply carries no top-level additionalContext — the "
            f"Copilot envelope leaked past its COPILOT_CLI gate: {reply}")
        assert set(reply) <= {"hookSpecificOutput", "systemMessage"}, reply
        return

    if event == "Stop":
        assert top is None, f"{hook}: Stop has no context channel under Copilot: {reply}"
        return
    if nested and nested.get("additionalContext"):
        assert top == nested["additionalContext"], (
            f"{hook}: the top-level text must be the nested text: {reply}")
    else:
        assert top == msg, (
            f"{hook}: a message outside Stop also rides as top-level context: {reply}")


@pytest.mark.parametrize("hook,event,build,stub,extra,kind", CASES, ids=_IDS)
def test_every_hook_that_writes_answers_in_the_envelope_its_host_reads(
        tmp_path, host, hook, event, build, stub, extra, kind):
    root = _project(tmp_path)
    reply = _run(tmp_path, host, hook, build(root), stub=stub, env_extra=extra,
                 timeout=120 if hook == "session-init.sh" else 60)
    assert reply is not None, f"{hook} wrote nothing under {host}"
    _check(host, hook, event, reply, kind)


@pytest.mark.parametrize("hook,event,build,stub,extra,kind",
                         [c for c in CASES if c[1] == "PostToolUse"],
                         ids=[i for i, c in zip(_IDS, CASES) if c[1] == "PostToolUse"])
def test_a_post_tool_use_reply_stays_under_the_bound(tmp_path, host, hook, event, build,
                                                     stub, extra, kind):
    root = _project(tmp_path)
    reply = _run(tmp_path, host, hook, build(root), stub=stub, env_extra=extra)
    assert reply is not None
    size = len(json.dumps(reply, ensure_ascii=False).encode("utf-8"))
    assert size < POST_TOOL_USE_BOUND, f"{hook}: {size} bytes injected after every edit"


# ── the relays: a verb's document goes out as it came ───────────────────────

@pytest.mark.parametrize("hook,event", [
    ("prompt-submit-turn.sh", "UserPromptSubmit"),
    ("post-bash-bypass.sh", "PostToolUse"),
])
def test_a_relayed_verb_document_is_not_rewritten(tmp_path, host, hook, event):
    """The thin hooks hand the verb's reply through unchanged; the envelope is
    the CLI's to write there (loci-tools 0.2.63 adds the top-level field under
    COPILOT_CLI itself). So a verb document in Claude's shape reaches the
    harness in Claude's shape under both hosts, and one in both shapes in both:
    the hook neither adds nor strips the Copilot field."""
    root = _project(tmp_path)
    verb = {"hookSpecificOutput": {"hookEventName": event, "additionalContext": "[loci] x"}}
    if host.copilot:
        verb["additionalContext"] = "[loci] x"
    stub = f"cat >/dev/null; printf '%s\\n' '{json.dumps(verb)}'"
    if hook.startswith("prompt"):
        doc = {"hook_event_name": event, "prompt_id": PROMPT_ID, "cwd": _to_bash_path(root),
               "prompt": "hi"}
    else:
        doc = {"hook_event_name": event, "prompt_id": PROMPT_ID, "cwd": _to_bash_path(root),
               "tool_name": "Bash", "tool_input": {"command": "make"},
               "tool_response": {"stdout": "", "stderr": "", "interrupted": False}}
    reply = _run(tmp_path, host, hook, doc, stub=stub)
    assert reply == verb, reply


# ── the gate, read off the sources ──────────────────────────────────────────

def test_no_hook_writes_the_copilot_field_itself():
    """The top-level `additionalContext` is written in exactly one place per
    channel — the two writers in `lib/loci_json.sh` — and nowhere in a hook. A
    hook that spelled it would be a second gate to keep right."""
    for f in sorted(HOOKS.glob("*.sh")):
        src = f.read_text(encoding="utf-8")
        code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
        assert ',"additionalContext"' not in code and '{"additionalContext"' not in code, (
            f"{f.name} writes the top-level additionalContext itself; route it through "
            f"loci_json_hook_output / loci_json_system_message")
