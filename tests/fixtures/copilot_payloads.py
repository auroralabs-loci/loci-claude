"""The hook payloads GitHub Copilot CLI sends, and the two hosts as one fixture (AAD-7790).

Every hook test in this suite was written against what Claude Code sends:
`prompt_id` on every payload of a turn, `tool_input.file_path` / `old_string` /
`new_string` / `content`, a `tool_response`, `CLAUDE_PLUGIN_ROOT` and
`CLAUDE_PROJECT_DIR`, and the hook started in the project. Copilot (Epic
AAD-7779) runs the same Claude-format hooks and sends none of that the same way;
the first five Stories of the Epic taught the hooks its spelling, each behind
the `COPILOT_CLI` gate, and nothing in the suite would notice a sixth change
undoing one of them. So:

* **The payloads below are the ones observed** — COPILOT-PROBE-EVIDENCE.md §5 on
  the Epic, Copilot CLI 1.0.89–1.0.91 on Windows, 2026-09-30 — one per event and
  tool, with the long strings the evidence elided filled in and the probe's
  project re-rooted by `at()`. Their shape is the contract; do not tidy it.
* **`Host` is the pair of hosts as a test sees them.** `respell()` turns a
  Claude-shaped payload into the one Copilot would have sent for the same
  action, `env()` is what the host exports, `cwd()` is where it starts a hook
  (Copilot: the PLUGIN root, not the project), `context()` reads the reply where
  that host reads it, and `turn()` is the id a hook presents for a turn when the
  host never sent one.
* **The `host` fixture (tests/conftest.py) is parametrised over both.** A module
  that adds `pytest.mark.usefixtures("host")` to its `pytestmark` runs every
  test twice; a helper that calls `current()` gets the active host without a
  signature change, and `Host("claude")` when no fixture is active — so a
  helper imported by a module that did not opt in behaves as it always did.

What the Claude-shaped half must stay: byte for byte what it was. `respell()`
returns a NEW document and `Host("claude").respell(doc)` IS `doc`, so a test's
Claude run is the run it had before this module existed.

Outside this module's scope, deliberately: path SPELLING (Copilot on Windows
sends `C:\\…` backslash paths; the tests that care pass `native=True` and are not
re-spelled here), the subagent fields (`agent_id`: Copilot never sends it, and
the adapter supplies it — the subagent shapes sit below, outside `PAYLOADS`,
AAD-7788) and the Stop `transcript_path` (bug-report's). A test about those is
a test about one host.
"""

from __future__ import annotations

import copy
import hashlib
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent

HOSTS = ("claude", "copilot")

#: What the probe saw Copilot put on every payload (§5 "Common").
SESSION = "6d3a0c1e-7790-4c0d-9b4e-0c0b1107c0de"
TIMESTAMP = "2026-09-30T19:00:00.000Z"
COPILOT_VERSION = "1.0.91"

#: The shape of an id `lib/loci_host.sh` mints (`cp-<epoch>-<hex>`).
TURN_ID = re.compile(r"^cp-[0-9]+-[0-9a-f]+$")
#: The epoch `turn()` stamps into a seeded id: now, taken once, so that the
#: SessionStart sweep (which retires an id older than a week) keeps it.
_EPOCH = int(time.time())

# ── the payloads, verbatim (COPILOT-PROBE-EVIDENCE.md §5) ───────────────────
#
# `{project}` is the probe project's directory and `{file}` the file the probe
# wrote and edited (`a.txt` in it); `at()` fills them in. Everything else is as
# the log had it, including the texts Copilot puts in `text_result_for_llm`.

_COMMON = {"session_id": SESSION, "timestamp": TIMESTAMP, "cwd": "{project}"}

SESSION_START = {"hook_event_name": "SessionStart", **_COMMON,
                 "source": "new", "initial_prompt": "Say hello"}

SESSION_START_RESUME = {"hook_event_name": "SessionStart", **_COMMON,
                        "source": "resume"}

USER_PROMPT_SUBMIT = {"hook_event_name": "UserPromptSubmit", **_COMMON,
                      "prompt": "Say hello"}

PRE_TOOL_USE_WRITE = {"hook_event_name": "PreToolUse", **_COMMON,
                      "tool_name": "Write",
                      "tool_input": {"path": "{file}", "file_text": "hello"}}

PRE_TOOL_USE_EDIT = {"hook_event_name": "PreToolUse", **_COMMON,
                     "tool_name": "Edit",
                     "tool_input": {"path": "{file}", "old_str": "hello",
                                    "new_str": "hello world"}}

PRE_TOOL_USE_BASH = {"hook_event_name": "PreToolUse", **_COMMON,
                     "tool_name": "Bash",
                     "tool_input": {"command": "echo probe-done",
                                    "description": "Echo probe-done"}}

POST_TOOL_USE_WRITE = {"hook_event_name": "PostToolUse", **_COMMON,
                       "tool_name": "Write",
                       "tool_input": {"path": "{file}", "file_text": "hello"},
                       "tool_result": {"result_type": "success",
                                       "text_result_for_llm":
                                           "Created file {file} with 5 characters"}}

POST_TOOL_USE_EDIT = {"hook_event_name": "PostToolUse", **_COMMON,
                      "tool_name": "Edit",
                      "tool_input": {"path": "{file}", "old_str": "hello",
                                     "new_str": "hello world"},
                      "tool_result": {"result_type": "success",
                                      "text_result_for_llm":
                                          "File {file} updated with changes."}}

POST_TOOL_USE_BASH = {"hook_event_name": "PostToolUse", **_COMMON,
                      "tool_name": "Bash",
                      "tool_input": {"command": "echo probe-done",
                                     "description": "Echo probe-done"},
                      "tool_result": {"result_type": "success",
                                      "text_result_for_llm":
                                          "probe-done\n<shellId: 0 completed with exit code 0>"}}

STOP = {"hook_event_name": "Stop", **_COMMON,
        "transcript_path": "{home}{sep}.copilot{sep}session-state{sep}" + SESSION
                           + "{sep}events.jsonl",
        "stop_reason": "end_turn", "stop_hook_active": False}

SESSION_END = {"hook_event_name": "SessionEnd", **_COMMON, "reason": "complete"}

#: Every observed payload, by the name a test id can carry.
PAYLOADS = {
    "SessionStart": SESSION_START,
    "SessionStart-resume": SESSION_START_RESUME,
    "UserPromptSubmit": USER_PROMPT_SUBMIT,
    "PreToolUse-Write": PRE_TOOL_USE_WRITE,
    "PreToolUse-Edit": PRE_TOOL_USE_EDIT,
    "PreToolUse-Bash": PRE_TOOL_USE_BASH,
    "PostToolUse-Write": POST_TOOL_USE_WRITE,
    "PostToolUse-Edit": POST_TOOL_USE_EDIT,
    "PostToolUse-Bash": POST_TOOL_USE_BASH,
    "Stop": STOP,
    "SessionEnd": SESSION_END,
}

# ── a subagent's session (AAD-7788; probed 2026-10-01 on 1.0.91) ───────────
#
# Not in `PAYLOADS`: one host's shapes, with no Claude counterpart in the field
# lint's table. A Copilot subagent (the `task` tool) is a session of its own —
# its payloads are the ordinary shapes above under ITS `session_id`, starting
# with a `UserPromptSubmit` of its own (the task prompt) and ending in a `Stop`,
# and none carries `agent_id`. `SubagentStart` fires in the PARENT session,
# camelCase and with no event name and no child id; `SubagentStop`, snake_case,
# is the one place the child's id appears (`agent_id`).
CHILD_SESSION = "bf5dab17-8d4b-4d27-8e56-39ee48fac916"

SUBAGENT_START = {"sessionId": SESSION, "timestamp": 1790868527440, "cwd": "{project}",
                  "transcriptPath": "{home}{sep}.copilot{sep}session-state{sep}" + SESSION
                                    + "{sep}events.jsonl",
                  "agentName": "general-purpose"}

#: A subagent's `Edit` as Copilot's default subagent model (gpt-5.4) sends it
#: (probe run 3, 2026-10-01): `tool_input` is ONE STRING in the apply_patch
#: format, not an object — no `path`, no `old_str`/`new_str` to respell. The
#: file header may be absolute (observed) or relative to `cwd` (also observed).
#: `{file}` is filled by `at()`; `_CHILD_COMMON` is the child session's.
_CHILD_COMMON = {"session_id": CHILD_SESSION, "timestamp": TIMESTAMP, "cwd": "{project}"}
_PATCH = ("*** Begin Patch\n*** Update File: {file}\n@@\n+// probe\n"
          " int add(int a, int b) { return a + b; }\n int main(void) { return add(1, 2); }\n"
          "*** End Patch\n")

PRE_TOOL_USE_EDIT_PATCH = {"hook_event_name": "PreToolUse", **_CHILD_COMMON,
                           "tool_name": "Edit", "tool_input": _PATCH}

POST_TOOL_USE_EDIT_PATCH = {"hook_event_name": "PostToolUse", **_CHILD_COMMON,
                            "tool_name": "Edit", "tool_input": _PATCH,
                            "tool_result": {"result_type": "success",
                                            "text_result_for_llm": "Modified 1 file(s): {file}"}}

SUBAGENT_STOP = {"hook_event_name": "SubagentStop", **_COMMON,
                 "transcript_path": "{home}{sep}.copilot{sep}session-state{sep}" + SESSION
                                    + "{sep}events.jsonl",
                 "agent_id": CHILD_SESSION, "agent_type": "general-purpose",
                 "agent_name": "general-purpose",
                 "last_assistant_message": "Done: `// probe` is the first line of main.c.",
                 "stop_reason": "end_turn"}

#: The environment the probe found in a hook (§4), less the paths `env()` fills.
COPILOT_ENV_FIXED = {
    "COPILOT_CLI": "1",
    "COPILOT_CLI_BINARY_VERSION": COPILOT_VERSION,
}


def at(payload: dict, project: str, *, file: str | None = None,
       home: str | None = None) -> dict:
    """`payload` with the probe's project re-rooted at `project` (a string in
    the spelling the test wants: the probe's was `C:\\…\\probe-proj`), its file
    at `file` (default `<project>/a.txt`, joined with the project's own
    separator) and the Stop transcript under `home` (default: a home in the
    project's spelling)."""
    sep = "\\" if "\\" in project or re.match(r"^[A-Za-z]:", project) else "/"
    f = file if file is not None else project.rstrip("/\\") + sep + "a.txt"
    if home is None:
        home = "C:\\Users\\User" if sep == "\\" else "/home/user"

    def fill(v):
        if isinstance(v, str):
            return (v.replace("{project}", project).replace("{file}", f)
                    .replace("{home}", home).replace("{sep}", sep))
        if isinstance(v, dict):
            return {k: fill(x) for k, x in v.items()}
        return v

    return fill(copy.deepcopy(payload))


# ── the two hosts ───────────────────────────────────────────────────────────

_RESPELL_EDIT = {"file_path": "path", "old_string": "old_str", "new_string": "new_str"}
_RESPELL_WRITE = {"file_path": "path", "content": "file_text"}


@dataclass
class Host:
    """One of the two hosts, as a test runs a hook under it."""

    name: str
    #: (session_id, turn id) for every `prompt_id` `respell()` took off a
    #: payload: what `seed()` writes so the hook resolves the same turn.
    records: list[tuple[str, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        assert self.name in HOSTS, self.name

    @property
    def copilot(self) -> bool:
        return self.name == "copilot"

    # -- the payload ---------------------------------------------------------

    def respell(self, doc: dict, *, event: str | None = None) -> dict:
        """The payload Copilot would have sent for the action `doc` (a
        Claude-shaped payload) describes; `doc` itself under Claude Code.

        Copilot's differences, each from the evidence table in §5: no
        `prompt_id` (the hook resolves the turn from the session's record —
        `seed()`), `session_id` and `timestamp` on every payload, an Edit's or a
        Write's `tool_input` in its names, and the outcome as `tool_result`
        rather than `tool_response`. Copilot also names the event on every
        payload where some Claude-shaped test payloads leave it off (the hook
        under test never read it): `event` is the name to supply then, since the
        adapter's turn step keys on it — `UserPromptSubmit` mints, `SessionStart`
        sweeps, anything else resolves. A field this module does not own is left
        as it came (see the module doc)."""
        if not self.copilot:
            return doc
        out = copy.deepcopy(doc)
        if event is not None:
            out.setdefault("hook_event_name", event)
        session = out.setdefault("session_id", SESSION)
        out.setdefault("timestamp", TIMESTAMP)
        if "prompt_id" in out:
            pid = out.pop("prompt_id")
            if isinstance(pid, str) and pid:
                self.records.append((session, self.turn(pid)))
        tool = out.get("tool_name")
        ti = out.get("tool_input")
        if isinstance(ti, dict) and tool in ("Edit", "Write"):
            names = _RESPELL_EDIT if tool == "Edit" else _RESPELL_WRITE
            out["tool_input"] = {names.get(k, k): v for k, v in ti.items()}
        if "tool_response" in out:
            out["tool_result"] = _result_of(out.pop("tool_response"), tool, out)
        if "hook_event_name" not in out and tool is not None:
            out["hook_event_name"] = "PostToolUse" if "tool_result" in out else "PreToolUse"
        return out

    def turn(self, prompt_id: str) -> str:
        """The turn id a hook presents under this host for a turn Claude Code
        would have called `prompt_id`: the same string under Claude Code; under
        Copilot an id in the minted shape, derived from it so that two payloads
        of one turn get one id."""
        if not self.copilot:
            return prompt_id
        return f"cp-{_EPOCH}-" + hashlib.sha1(prompt_id.encode("utf-8")).hexdigest()[:12]

    def is_turn(self, value, prompt_id: str) -> bool:
        """Is `value` the id of the turn `prompt_id` names, as a `UserPromptSubmit`
        hook presents it: exactly `prompt_id` under Claude Code; under Copilot
        an id in the minted shape that is NOT the seeded one — every prompt is a
        new turn, so the hook mints there and never reads the record. Every
        later hook of the turn presents the seeded id itself: `turn()`."""
        if not self.copilot:
            return value == prompt_id
        return (isinstance(value, str) and bool(TURN_ID.match(value))
                and value != self.turn(prompt_id))

    def seed(self, state_dir: Path) -> None:
        """Write the `turn-<session_id>` record for every turn `respell()` took
        a `prompt_id` off, into the state directory the hook will read
        (`LOCI_STATE_DIR`, else `$HOME/.loci/state`). Nothing under Claude Code;
        nothing when no payload carried one."""
        if not self.copilot or not self.records:
            return
        state_dir.mkdir(parents=True, exist_ok=True)
        for session, turn in self.records:
            (state_dir / f"turn-{_file_sid(session)}").write_text(
                turn + "\n", encoding="utf-8", newline="\n")
        # Written is written: a second `respell()` + `seed()` in the same test
        # seeds only what it took off its own payload.
        self.records.clear()

    def carried(self, state_dir: Path, tag: str, session: str = SESSION) -> str | None:
        """The message a Stop hook recorded for the session's next prompt under
        Copilot — `turn-<session_id>.nudge-<tag>`, first line an age stamp
        (AAD-7783, `loci_host_carry_add`) — or None when it recorded nothing.
        Always None under Claude Code, which records nothing."""
        rec = state_dir / f"turn-{_file_sid(session)}.nudge-{tag}"
        if not rec.is_file():
            return None
        return rec.read_text(encoding="utf-8").partition("\n")[2].rstrip("\n")

    # -- the process ---------------------------------------------------------

    def env(self, *, project: str | None = None,
            plugin_root: str | None = None) -> dict:
        """What this host exports to a hook beyond what the test sets itself:
        nothing under Claude Code (the tests already set `CLAUDE_PROJECT_DIR`);
        under Copilot the gate, its version and both spellings of the plugin
        root and the project (§4). `plugin_root` and `project` are strings in
        whatever spelling the test runs with."""
        if not self.copilot:
            return {}
        env = dict(COPILOT_ENV_FIXED)
        root = plugin_root if plugin_root is not None else str(PLUGIN_ROOT)
        env["CLAUDE_PLUGIN_ROOT"] = root
        env["COPILOT_PLUGIN_ROOT"] = root
        if project is not None:
            env["CLAUDE_PROJECT_DIR"] = project
            env["COPILOT_PROJECT_DIR"] = project
        return env

    def cwd(self, before: Path | str | None, *,
            plugin_root: Path | str | None = None) -> str | None:
        """Where the host starts a hook. `before` is what the test passed as
        `cwd` before it knew two hosts — a directory, or None for "inherit the
        test process's" — and under Claude Code that is returned unchanged, so
        the Claude run starts where it always started (a test that inherited
        pytest's cwd, the plugin root, keeps catching a hook that falls back to
        `$PWD` for the project). Under Copilot: the PLUGIN root (§4: `pwd`
        inside the hook was the plugin dir), or `plugin_root` when the hook
        under test lives in a copy."""
        if not self.copilot:
            return None if before is None else str(before)
        return str(plugin_root if plugin_root is not None else PLUGIN_ROOT)

    # -- the reply -----------------------------------------------------------

    def context(self, reply: dict | None) -> str:
        """The context the host reads out of a hook's reply: the nested
        `hookSpecificOutput.additionalContext` under Claude Code; under Copilot
        the TOP-LEVEL `additionalContext` (§6 — the nested one is dropped),
        which the writers put beside the nested one with the same text
        (AAD-7783). A reply that reached the harness from a stubbed verb may
        carry only the nested field; then that is what is returned, since the
        envelope is the CLI's and not the hook's to add."""
        if not reply:
            return ""
        nested = (reply.get("hookSpecificOutput") or {}).get("additionalContext", "")
        if not self.copilot:
            assert "additionalContext" not in reply, (
                "a Claude Code reply carries no top-level additionalContext — the "
                f"Copilot envelope leaked past its gate: {reply!r}")
            return nested
        assert "additionalContext" in reply, (
            "under Copilot the context must ride at the top level too (AAD-7783); "
            f"Copilot drops the nested one: {reply!r}")
        top = reply["additionalContext"]
        if nested:
            assert top == nested, (
                "under Copilot the top-level additionalContext must be the nested "
                f"text: top={top!r} nested={nested!r}")
        return top

    def user_message_keys(self, event: str = "UserPromptSubmit") -> set:
        """The keys of a reply that carries only a user-facing message: just
        `systemMessage` under Claude Code; under Copilot, which shows
        `systemMessage` to nobody, the same text also rides as the top-level
        `additionalContext` on every event but `Stop` (AAD-7783)."""
        if not self.copilot or event == "Stop":
            return {"systemMessage"}
        return {"systemMessage", "additionalContext"}

    def decision(self, reply: dict | None) -> str | None:
        """A PreToolUse decision, which both hosts read NESTED (§6): the one
        reply shape Copilot honours from the hook as it stands."""
        if not reply:
            return None
        return (reply.get("hookSpecificOutput") or {}).get("permissionDecision")

    def __repr__(self) -> str:  # the id in a failure message
        return f"Host({self.name})"


def _file_sid(session: str) -> str:
    """A session id as `_loci_host_file` spells it into a file name: every
    character outside `[A-Za-z0-9._-]` becomes `_`."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", session)


def _result_of(response, tool: str | None, doc: dict) -> dict:
    """Copilot's `tool_result` for a Claude `tool_response` (§5): a failed
    tool — the `*Failure` event, or an `error` in the response — is
    `result_type: failure` with the error as its text; otherwise `success` with
    the sentence Copilot writes for that tool."""
    failed = str(doc.get("hook_event_name", "")).endswith("Failure")
    error = response.get("error") if isinstance(response, dict) else None
    if failed or error:
        return {"result_type": "failure",
                "text_result_for_llm": str(error or "The tool failed.")}
    ti = doc.get("tool_input") or {}
    path = ti.get("path") or ti.get("file_path") or ""
    if tool == "Write":
        n = len(str(ti.get("file_text", ti.get("content", ""))))
        text = f"Created file {path} with {n} characters"
    elif tool == "Edit":
        text = f"File {path} updated with changes."
    elif tool == "Bash":
        out = response.get("stdout", "") if isinstance(response, dict) else ""
        text = f"{out}\n<shellId: 0 completed with exit code 0>" if out else \
            "<shellId: 0 completed with exit code 0>"
    else:
        text = str(response)
    return {"result_type": "success", "text_result_for_llm": text}


# ── the active host ─────────────────────────────────────────────────────────
#
# Set by the `host` fixture for the duration of a test. A helper that is
# shared by many tests reads it here rather than taking a parameter, so that
# a module opts in with one `usefixtures` mark and no test's signature moves.

_CLAUDE = Host("claude")
_active: list[Host] = []


def activate(host: Host) -> None:
    _active.append(host)


def deactivate(host: Host) -> None:
    assert _active and _active[-1] is host, "host fixtures must nest"
    _active.pop()


def current() -> Host:
    """The host the running test is parametrised over; Claude Code when the
    test did not ask for one. A `Host("claude")` outside a fixture carries no
    records, so `seed()` on it is a no-op."""
    return _active[-1] if _active else _CLAUDE
