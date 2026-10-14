"""The hook payload in Copilot's spelling (`lib/loci_host.sh`, AAD-7782).

GitHub Copilot CLI runs the plugin's Claude-format hooks and keeps the Claude
TOOL names (`Edit`, `Write`, `Bash`), but puts its own argument names inside
`tool_input` — `path`, `file_text`, `old_str`, `new_str` — and reports the
outcome as `tool_result: {result_type, text_result_for_llm}` where Claude Code
sends `tool_response: {structuredPatch, error}`. Every shell reader of the edited
path spells the Claude name, so under Copilot the recipe was never guarded, no
pre-edit baseline was frozen and no post-edit reminder was made. It also runs
plugin hooks with the PLUGIN root as the working directory.

The host adapter respells the four `tool_input` keys in the bounded prefix the
hooks read, before any hook reads it and before the payload is piped to a
`loci hook …` verb; the CLI reads both spellings of the content-sized fields
and of the outcome object itself. What is load-bearing, and pinned here:

* **The Claude Code path is untouched.** Without `COPILOT_CLI` nothing is
  read or changed; with it, a Claude key already present wins for that field,
  so no payload ever carries both spellings of one argument.
* **Only the prefix is rewritten, and only for an `Edit` or a `Write`.** A
  200 KB `file_text` passes through byte for byte; a `Read`'s `path` is left
  alone, so a read of the recipe can never become a denied write of it.
* **Every hook accepts both shapes of every event** (criterion 2), and
* **no hook depends on its working directory** (criterion 3): each is run from
  an unrelated directory with the project named only by the payload's `cwd`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

import pytest

from .test_contract_guard import _legacy_bash
from .test_host_turn_id import (
    HOOKS,
    PLUGIN_ROOT,
    _RS,
    _adapt,
    _find_bash,
    _payload,
    _record,
    _run_hook,
    _to_bash_path,
)

pytestmark = pytest.mark.skipif(_find_bash() is None, reason="bash required")

GUARD_SRC = (HOOKS / "contract-guard.sh").read_text(encoding="utf-8")


def _reason(name: str) -> str:
    """The first sentence of a deny reason, as the guard spells it."""
    m = re.search(rf'^{name}="([^"]+?)\.', GUARD_SRC, re.M)
    assert m, name
    return m.group(1)


# ── the two spellings ────────────────────────────────────────────────────────

def _copilot_edit(path: str, *, event: str = "PreToolUse", old: str = "1",
                  new: str = "2", **extra) -> dict:
    doc = _payload(event, tool_name="Edit",
                   tool_input={"path": path, "old_str": old, "new_str": new}, **extra)
    if event.startswith("Post"):
        doc["tool_result"] = {"result_type": "success",
                              "text_result_for_llm": f"File {path} updated with changes."}
    return doc


def _copilot_write(path: str, *, event: str = "PreToolUse",
                   text: str = "int f(void) { return 1; }\n", **extra) -> dict:
    doc = _payload(event, tool_name="Write",
                   tool_input={"path": path, "file_text": text}, **extra)
    if event.startswith("Post"):
        doc["tool_result"] = {"result_type": "success",
                              "text_result_for_llm": f"Created file {path}"}
    return doc


def _claude_edit(path: str, *, event: str = "PreToolUse", old: str = "1",
                 new: str = "2", **extra) -> dict:
    doc = _payload(event, tool_name="Edit",
                   tool_input={"file_path": path, "old_string": old, "new_string": new},
                   **extra)
    if event.startswith("Post"):
        doc["tool_response"] = {"filePath": path, "structuredPatch": [
            {"lines": [f"-{old}", f"+{new}"]}]}
    return doc


# ── the adapter: what is renamed, and what is not ────────────────────────────

@pytest.mark.parametrize("event", ["PreToolUse", "PostToolUse", "PostToolUseFailure"])
def test_an_edits_four_keys_are_respelled_and_nothing_else_moves(tmp_path, event):
    doc = _copilot_edit("C:\\proj\\main.c", event=event)
    r = _adapt(tmp_path, doc)
    assert r.changed
    got = r.doc
    assert got["tool_input"] == {"file_path": "C:\\proj\\main.c",
                                 "old_string": "1", "new_string": "2"}
    for key in doc:
        if key != "tool_input":
            assert got[key] == doc[key], key
    assert "prompt_id" not in got                 # no prompt on record: none minted


def test_a_write_is_respelled_to_file_path_and_content(tmp_path):
    r = _adapt(tmp_path, _copilot_write("C:\\proj\\new.c", event="PostToolUse"))
    assert r.changed
    assert r.doc["tool_input"] == {"file_path": "C:\\proj\\new.c",
                                   "content": "int f(void) { return 1; }\n"}
    assert r.doc["tool_result"]["result_type"] == "success"


def test_the_renamed_text_is_the_original_with_four_keys_longer(tmp_path):
    """A rewrite, not a re-serialisation: the bytes either side of each key are
    the host's own, escapes included."""
    doc = _copilot_edit("C:\\proj\\a b\\main.c", old="x\"\\y", new="\u00e9\n")
    r = _adapt(tmp_path, doc)
    raw = json.dumps(doc)
    expected = (raw.replace('"path"', '"file_path"', 1)
                   .replace('"old_str"', '"old_string"', 1)
                   .replace('"new_str"', '"new_string"', 1))
    assert r.raw == expected


def test_outside_copilot_nothing_is_touched(tmp_path):
    """One environment-variable test, returning before either global is set."""
    doc = _copilot_edit("C:\\proj\\main.c")
    r = _adapt(tmp_path, doc, copilot=False)
    assert not r.changed and r.raw == "" and r.prompt_id == ""


@pytest.mark.parametrize("event", ["PreToolUse", "PostToolUse"])
def test_a_claude_payload_under_copilot_is_left_exactly_as_it_came(tmp_path, event):
    """`COPILOT_CLI` with Claude's own names: every field is already the one
    the hooks read, so no key is renamed and the text is byte-identical."""
    doc = _claude_edit("C:\\proj\\main.c", event=event)
    r = _adapt(tmp_path, doc)
    assert not r.changed and r.raw == json.dumps(doc)


def test_the_claude_name_wins_per_field_so_no_field_is_ever_spelled_twice(tmp_path):
    doc = _payload("PreToolUse", tool_name="Edit", tool_input={
        "file_path": "C:\\claude.c", "path": "C:\\copilot.c",
        "old_str": "a", "new_string": "b", "new_str": "c"})
    r = _adapt(tmp_path, doc)
    assert r.changed
    assert r.doc["tool_input"] == {
        "file_path": "C:\\claude.c", "path": "C:\\copilot.c",   # untouched
        "old_string": "a",                                       # renamed
        "new_string": "b", "new_str": "c"}                       # untouched
    assert r.raw.count('"file_path"') == 1 and r.raw.count('"new_string"') == 1


@pytest.mark.parametrize("tool,tool_input", [
    ("Read", {"path": "C:\\proj\\.loci\\build.yaml"}),
    ("Glob", {"pattern": "*.c", "path": "C:\\proj"}),
    ("Grep", {"pattern": "path", "path": "C:\\proj"}),
    ("Bash", {"command": "cat path", "description": "path"}),
    ("", {"path": "C:\\proj\\x.c"}),
])
def test_only_an_edit_or_a_write_is_respelled(tmp_path, tool, tool_input):
    """Copilot's `view`, `glob` and `grep` carry a `path` too. A `Read` of the
    recipe turned into a `file_path` of it would be a DENIED read."""
    doc = _payload("PreToolUse", tool_name=tool, tool_input=tool_input)
    r = _adapt(tmp_path, doc)
    assert not r.changed and r.raw == json.dumps(doc)


def test_a_path_inside_a_string_value_is_not_the_key(tmp_path):
    """Inside a JSON string every quote is escaped, so `\\"path\\"` in the
    content cannot match the key's `"path"` — the key renamed is the real one."""
    doc = _copilot_write("C:\\proj\\a.c", text='const char *s = "\\"path\\": 1";')
    r = _adapt(tmp_path, doc)
    assert r.doc["tool_input"]["file_path"] == "C:\\proj\\a.c"
    assert r.doc["tool_input"]["content"] == 'const char *s = "\\"path\\": 1";'


def test_a_word_that_is_a_value_before_the_key_is_stepped_over(tmp_path):
    """`"path"` as a VALUE — a comma follows it, no `{`/`,` precedes it — is
    not the key; the key after it is the one renamed."""
    doc = _payload("PreToolUse", tool_name="Write",
                   tool_input={"kind": "path", "path": "/a.c", "file_text": "x"})
    r = _adapt(tmp_path, doc)
    assert r.changed
    assert r.doc["tool_input"] == {"kind": "path", "file_path": "/a.c", "content": "x"}


@pytest.mark.parametrize("cwd", ['C:\\x"path', 'C:\\x\\"path'])
def test_an_occurrence_an_escape_precedes_is_not_the_key(tmp_path, cwd):
    """`\\"path"` at the end of a string value spells the five characters of
    the key; what precedes it is a backslash, not `{` or `,`, so it is stepped
    over and the real key is renamed."""
    doc = _payload("PreToolUse", cwd=cwd, tool_name="Edit",
                   tool_input={"path": "/a.c", "old_str": "1", "new_str": "2"})
    r = _adapt(tmp_path, doc)
    assert r.changed
    assert r.doc["cwd"] == cwd
    assert r.doc["tool_input"] == {"file_path": "/a.c", "old_string": "1", "new_string": "2"}


def test_a_key_that_ends_in_the_word_is_not_the_key(tmp_path):
    doc = {"hook_event_name": "PreToolUse", "session_id": "s", 'x"path': 1,
           "tool_name": "Edit", "tool_input": {"path": "/a.c", "new_str": "2"}}
    r = _adapt(tmp_path, doc)
    assert r.changed
    assert r.doc['x"path'] == 1
    assert r.doc["tool_input"] == {"file_path": "/a.c", "new_string": "2"}


def test_a_cut_prefix_with_no_tool_name_is_taken_for_an_edit(tmp_path):
    """Only an Edit or a Write carries content enough to push `tool_name` out
    of the prefix; a host that serialised it after a 20 KB `tool_input` would
    otherwise get a recipe write the guard never sees."""
    doc = {"hook_event_name": "PreToolUse", "session_id": "s",
           "tool_input": {"path": "C:\\p\\.loci\\build.yaml", "file_text": "x" * 20_000},
           "tool_name": "Write"}
    r = _adapt(tmp_path, doc)
    assert r.changed
    assert r.doc["tool_input"]["file_path"] == "C:\\p\\.loci\\build.yaml"


def test_a_read_with_a_small_payload_is_never_taken_for_an_edit(tmp_path):
    doc = {"hook_event_name": "PreToolUse", "session_id": "s",
           "tool_input": {"path": "C:\\p\\.loci\\build.yaml"}, "tool_name": "Read"}
    r = _adapt(tmp_path, doc)
    assert not r.changed and r.raw == json.dumps(doc)


def test_an_escaped_tool_name_key_takes_the_slow_read_and_still_gates(tmp_path):
    """Found by the library's escaped-name pass, the reader's slice is of a
    parked copy; the adapter must not read it fast."""
    raw = ('{"hook_event_name":"PreToolUse","session_id":"s","cwd":"C:\\\\p",'
           '"\\u0074ool_name":"Edit","tool_input":{"path":"C:\\\\p\\\\a.c","new_str":"2"}}')
    r = _adapt(tmp_path, raw)
    assert r.changed
    assert r.doc["tool_input"] == {"file_path": "C:\\p\\a.c", "new_string": "2"}


def test_the_fields_mode_renames_without_touching_the_turn(tmp_path):
    """The contract guard reads no turn id: `fields` skips the state-directory
    probe, the file read and the injection, and only respells."""
    state = tmp_path / "state"
    turn = _record(tmp_path)
    doc = _copilot_edit("C:\\proj\\main.c")
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    payload_file = tmp_path / "p.json"
    payload_file.write_text(json.dumps(doc), encoding="utf-8", newline="\n")
    script = (f". '{_to_bash_path(PLUGIN_ROOT / 'lib' / 'loci_host.sh')}'\n"
              f"p=$(cat '{_to_bash_path(payload_file)}')\n"
              'loci_host_adapt "$p" fields; rc=$?\n'
              "printf '%s\\036%s\\036%s' \"$rc\" \"$LOCI_HOST_PAYLOAD\" \"$LOCI_HOST_PROMPT_ID\"\n")
    env = {"PATH": "/usr/bin:/bin", "HOME": _to_bash_path(home), "COPILOT_CLI": "1",
           "LOCI_STATE_DIR": _to_bash_path(state)}
    proc = subprocess.run([_find_bash(), "-c", script], capture_output=True, text=True,
                          timeout=60, env=env)
    rc, raw, pid = proc.stdout.split(_RS)
    assert rc == "0" and pid == ""
    got = json.loads(raw)
    assert "prompt_id" not in got and got["tool_input"]["file_path"] == "C:\\proj\\main.c"
    assert turn  # recorded, and deliberately not read


def test_a_pretty_printed_payload_is_read_the_same(tmp_path):
    doc = _copilot_edit("C:\\proj\\main.c")
    r = _adapt(tmp_path, json.dumps(doc, indent=2))
    assert r.changed
    assert r.doc["tool_input"] == {"file_path": "C:\\proj\\main.c",
                                   "old_string": "1", "new_string": "2"}


def test_a_whole_file_passes_through_byte_for_byte_and_fast(tmp_path):
    """The rename touches the bounded prefix; a 200 KB `file_text` behind it
    is reattached unread. The bound is what keeps a hook on a 5 s budget."""
    text = ("int f(void) { return 1; }\n" * 8000)
    doc = _copilot_write("C:\\proj\\big.c", event="PostToolUse", text=text)
    raw = json.dumps(doc)
    assert len(raw) > 200_000
    t0 = time.monotonic()
    r = _adapt(tmp_path, raw)
    elapsed = time.monotonic() - t0
    assert r.changed
    head = raw.replace('"path"', '"file_path"', 1).replace('"file_text"', '"content"', 1)
    assert r.raw == head
    assert r.doc["tool_input"]["content"] == text
    assert elapsed < 10.0, elapsed       # a bash spawn and one pass; 4.9 s was the quadratic bill


def test_a_key_past_the_prefix_keeps_its_spelling_and_the_payload_stays_whole(tmp_path):
    """A `new_str` behind a 20 KB `old_str` sits past the prefix: it is not
    renamed (the CLI reads both spellings) and nothing around it is damaged."""
    old = "x" * 20_000
    doc = _copilot_edit("C:\\proj\\main.c", event="PostToolUse", old=old, new="y")
    r = _adapt(tmp_path, doc)
    assert r.changed
    got = r.doc
    assert got["tool_input"]["file_path"] == "C:\\proj\\main.c"
    assert got["tool_input"]["old_string"] == old
    assert got["tool_input"]["new_str"] == "y" and "new_string" not in got["tool_input"]
    assert got["tool_result"] == doc["tool_result"]


def test_the_turn_id_and_the_field_names_land_in_one_pass(tmp_path):
    state = tmp_path / "state"
    turn = _record(tmp_path)
    r = _adapt(tmp_path, _copilot_edit("C:\\proj\\main.c"), state_dir=state)
    assert r.changed and r.prompt_id == turn
    assert r.doc["prompt_id"] == turn
    assert r.raw.startswith('{"prompt_id":"' + turn + '",')
    assert r.doc["tool_input"]["file_path"] == "C:\\proj\\main.c"


def test_a_hosts_own_prompt_id_is_kept_while_the_fields_are_still_respelled(tmp_path):
    doc = _copilot_edit("C:\\proj\\main.c", prompt_id="host-7")
    r = _adapt(tmp_path, doc)
    assert r.changed
    assert r.doc["prompt_id"] == "host-7" and r.prompt_id == "host-7"
    assert r.raw.count('"prompt_id"') == 1
    assert r.doc["tool_input"]["file_path"] == "C:\\proj\\main.c"


# ── through the hooks: both shapes, every event (criterion 2) ────────────────

_SCAN_OK = ("echo '{\"ok\":true,\"data\":{\"report\":\"\",\"applied\":true,"
            "\"measurable\":true,\"governed\":true,\"artifact_only\":false}}'")


@pytest.mark.parametrize("shape", ["copilot", "claude"])
def test_pre_edit_freezes_the_baseline_of_an_edit_in_either_spelling(tmp_path, shape):
    root = tmp_path / "proj"
    root.mkdir()
    src = root / "main.c"
    src.write_text("int f(void) { return 1; }\n", encoding="utf-8")
    doc = (_copilot_edit if shape == "copilot" else _claude_edit)(str(src), cwd=str(root))
    r = _run_hook(tmp_path, "pre-edit-hook.sh", doc, body=_SCAN_OK, project=root)
    snap = r.call("build", "snapshot")
    assert snap is not None, r.calls
    assert snap[snap.index("--source") + 1] == str(src)
    scan = r.stdin_of("hook", "edit-scan")
    assert scan is not None
    assert scan["tool_input"]["file_path"] == str(src)
    assert scan["tool_input"]["new_string"] == "2"
    assert "path" not in scan["tool_input"] and "new_str" not in scan["tool_input"]


def test_pre_edit_snapshots_the_file_the_verb_resolved(tmp_path):
    """AAD-7810: `build snapshot` resolves `--source` from the hook's own
    directory — under Copilot the plugin root — so a relative `path` froze
    nothing. The verb joins it to the payload's `cwd`; the snapshot names that."""
    root = tmp_path / "proj"
    root.mkdir()
    src = root / "main.c"
    src.write_text("int f(void) { return 1; }\n", encoding="utf-8")
    body = ("echo '{\"ok\":true,\"data\":{\"report\":\"\",\"applied\":true,"
            f"\"file_path\":{json.dumps(str(src))},"
            "\"measurable\":true,\"governed\":true,\"artifact_only\":false}}'")
    r = _run_hook(tmp_path, "pre-edit-hook.sh", _copilot_edit("main.c", cwd=str(root)),
                  body=body, project=root)
    snap = r.call("build", "snapshot")
    assert snap is not None, r.calls
    assert snap[snap.index("--source") + 1] == str(src)


@pytest.mark.parametrize("shape", ["copilot", "claude"])
def test_pre_edit_of_a_new_file_in_either_spelling_hands_the_verb_the_content(tmp_path, shape):
    root = tmp_path / "proj"
    root.mkdir()
    src = root / "new.c"
    if shape == "copilot":
        doc = _copilot_write(str(src), cwd=str(root))
    else:
        doc = _payload("PreToolUse", cwd=str(root), tool_name="Write",
                       tool_input={"file_path": str(src),
                                   "content": "int f(void) { return 1; }\n"})
    r = _run_hook(tmp_path, "pre-edit-hook.sh", doc, body=_SCAN_OK, project=root)
    assert r.call("build", "snapshot") is not None, r.calls
    scan = r.stdin_of("hook", "edit-scan")
    assert scan["tool_input"] == {"file_path": str(src),
                                  "content": "int f(void) { return 1; }\n"}


@pytest.mark.parametrize("shape", ["copilot", "claude"])
def test_post_edit_reminds_after_an_edit_in_either_spelling(tmp_path, shape):
    turn = _record(tmp_path)
    root = tmp_path / "proj"
    (root / ".loci").mkdir(parents=True)
    (root / ".loci" / "build.yaml").write_text("version: 1\n", encoding="utf-8")
    src = root / "main.c"
    src.write_text("int f(void) { return 2; }\n", encoding="utf-8")
    doc = (_copilot_edit if shape == "copilot" else _claude_edit)(
        str(src), event="PostToolUse", cwd=str(root))
    r = _run_hook(tmp_path, "post-edit-hook.sh", doc, body=_SCAN_OK, project=root)
    out = json.loads(r.out.strip())
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "main.c was modified. You MUST invoke the loci:loci-post-edit skill" in ctx, ctx
    if shape == "copilot":
        assert f"Pass turn id {turn}" in ctx
    scan = r.stdin_of("hook", "edit-scan")
    assert scan["tool_input"]["file_path"] == str(src)
    assert scan["tool_input"]["old_string"] == "1"
    if shape == "copilot":
        # The outcome object follows the content and is the CLI's to read.
        assert scan["tool_result"]["result_type"] == "success"
        assert "tool_response" not in scan


def test_post_edit_stays_silent_on_a_copilot_edit_the_cli_calls_unapplied(tmp_path):
    root = tmp_path / "proj"
    (root / ".loci").mkdir(parents=True)
    (root / ".loci" / "build.yaml").write_text("version: 1\n", encoding="utf-8")
    src = root / "main.c"
    src.write_text("int f(void) { return 2; }\n", encoding="utf-8")
    doc = _copilot_edit(str(src), event="PostToolUse", cwd=str(root))
    doc["tool_result"] = {"result_type": "failure", "text_result_for_llm": "no such file"}
    body = ("echo '{\"ok\":true,\"data\":{\"applied\":false,\"measurable\":true,"
            "\"governed\":true}}'")
    r = _run_hook(tmp_path, "post-edit-hook.sh", doc, body=body, project=root)
    assert r.out.strip() == "", r.out


@pytest.mark.parametrize("hook", ["pre-edit-hook.sh", "post-edit-hook.sh"])
def test_a_copilot_edit_of_an_unmeasurable_file_never_reaches_the_cli(tmp_path, hook):
    """The extension gate reads the respelled path: a `.md` edit costs no spawn."""
    root = tmp_path / "proj"
    root.mkdir()
    event = "PreToolUse" if hook.startswith("pre") else "PostToolUse"
    doc = _copilot_edit(str(root / "README.md"), event=event, cwd=str(root))
    r = _run_hook(tmp_path, hook, doc, body=_SCAN_OK, project=root)
    assert r.calls == [] and r.out.strip() == ""


# ── a patch for an input (AAD-7788) ─────────────────────────────────────────
#
# Copilot's default subagent model (gpt-5.4 on 1.0.91, probed 2026-10-01)
# sends an `Edit` whose `tool_input` is ONE STRING in the apply_patch format,
# so there is no key to respell. The adapter injects the first file the patch
# names as a top-level `file_path` at the front — where every shell reader of
# the path looks — joined to the payload's `cwd` when relative; the CLI reads
# the same header and the hunks out of the whole payload.

from tests.fixtures.copilot_payloads import (  # noqa: E402
    CHILD_SESSION, POST_TOOL_USE_EDIT_PATCH, PRE_TOOL_USE_EDIT_PATCH, at as _at)


def _patch_edit(path: str, *, event: str = "PreToolUse", cwd: str = "C:\\proj",
                header: str = "Update") -> dict:
    doc = _at(PRE_TOOL_USE_EDIT_PATCH if event == "PreToolUse" else POST_TOOL_USE_EDIT_PATCH,
              cwd, file=path)
    doc["tool_input"] = doc["tool_input"].replace("Update File", f"{header} File")
    return doc


def test_a_patch_edit_gets_the_file_it_names_as_a_top_level_file_path(tmp_path):
    """The observed payload, verbatim shape: an absolute Windows path in the
    header, injected at the front and read back by the library as the path."""
    a = _adapt(tmp_path, _patch_edit("C:\\proj\\main.c"))
    assert a.changed
    assert a.doc["file_path"] == "C:\\proj\\main.c"
    assert a.raw.startswith('{"file_path":"C:\\\\proj\\\\main.c",'), a.raw[:80]
    assert isinstance(a.doc["tool_input"], str), "the patch itself is left as it came"


@pytest.mark.parametrize("cwd,rel,joined", [
    ("C:\\proj", "main.c", "C:\\proj\\main.c"),
    ("C:\\proj\\", "main.c", "C:\\proj\\main.c"),
    ("/home/u/proj", "src/new.c", "/home/u/proj/src/new.c"),
    ("/home/u/proj/", "new.c", "/home/u/proj/new.c"),
])
def test_a_relative_patch_path_is_joined_to_the_cwd_with_its_own_separator(tmp_path, cwd, rel, joined):
    a = _adapt(tmp_path, _patch_edit(rel, cwd=cwd))
    assert a.changed and a.doc["file_path"] == joined


@pytest.mark.parametrize("header", ["Update", "Add", "Delete"])
def test_every_header_kind_names_the_file(tmp_path, header):
    a = _adapt(tmp_path, _patch_edit("/p/a.c", header=header))
    assert a.doc["file_path"] == "/p/a.c"


def test_the_first_file_of_a_patch_decides(tmp_path):
    doc = _patch_edit("C:\\proj\\first.c")
    doc["tool_input"] = doc["tool_input"].replace(
        "*** End Patch\n", "*** Update File: C:\\proj\\second.c\n@@\n+int s;\n*** End Patch\n")
    a = _adapt(tmp_path, doc)
    assert a.doc["file_path"] == "C:\\proj\\first.c"


def test_a_delete_first_patch_names_the_file_it_edits(tmp_path):
    """A deleted file has nothing to measure: the first Update or Add decides,
    and a Delete only when it is all the patch does (review round 2)."""
    doc = _patch_edit("C:\\proj\\gone.c", header="Delete")
    doc["tool_input"] = doc["tool_input"].replace(
        "*** End Patch\n", "*** Update File: C:\\proj\\kept.c\n@@\n+int k;\n*** End Patch\n")
    a = _adapt(tmp_path, doc)
    assert a.doc["file_path"] == "C:\\proj\\kept.c"
    only = _patch_edit("C:\\proj\\gone.c", header="Delete")
    assert _adapt(tmp_path, only).doc["file_path"] == "C:\\proj\\gone.c"


def test_a_move_to_line_leaves_the_header_as_the_file(tmp_path):
    doc = _patch_edit("C:\\proj\\old.c")
    doc["tool_input"] = doc["tool_input"].replace("@@\n", "*** Move to: C:\\proj\\new.c\n@@\n", 1)
    assert _adapt(tmp_path, doc).doc["file_path"] == "C:\\proj\\old.c"


def test_a_hunk_line_quoting_a_header_is_not_a_header(tmp_path):
    """A header is read at a line start; `+*** Add File:` is a line the patch
    ADDS to the file, as the CLI's line-anchored regex reads it."""
    doc = _patch_edit("C:\\proj\\real.c")
    doc["tool_input"] = doc["tool_input"].replace(
        "*** Begin Patch\n", "*** Begin Patch\n*** Update File: C:\\proj\\outer.c\n@@\n+*** Add File: evil.c\n", 1)
    assert _adapt(tmp_path, doc).doc["file_path"] == "C:\\proj\\outer.c"


def test_trailing_spaces_in_a_header_are_not_part_of_the_path(tmp_path):
    doc = _patch_edit("C:\\proj\\a.c")
    doc["tool_input"] = doc["tool_input"].replace("a.c\n", "a.c   \n", 1)
    assert _adapt(tmp_path, doc).doc["file_path"] == "C:\\proj\\a.c"


@pytest.mark.parametrize("path", ['C:\\proj\\we"ird.c', "\\\\server\\share\\a.c", "C:\\proj\\\u00e9.c"])
def test_an_escaped_quote_a_unc_path_and_a_non_ascii_name_survive_the_walk(tmp_path, path):
    a = _adapt(tmp_path, _patch_edit(path))
    assert a.changed and a.doc["file_path"] == path, a.raw[:120]


def test_a_header_the_head_cut_short_injects_no_partial_path(tmp_path):
    """A `tool_input` pushed past the 16 KB head by a large field before it:
    the walker meets the end of the head before the header's line end, and a
    partial path is not the path (review round 2). The hooks' TRUNCATED gate
    then hands the whole payload to the CLI, which reads the header itself."""
    doc = _patch_edit("C:\\proj\\verylongname.c")
    doc = {"hook_event_name": doc["hook_event_name"], "session_id": doc["session_id"],
           "pad": "x" * (16384 - 60), **{k: v for k, v in doc.items()
                                        if k not in ("hook_event_name", "session_id")}}
    a = _adapt(tmp_path, doc)
    assert "file_path" not in a.doc


def test_every_file_of_a_patch_is_listed_for_the_guard(tmp_path):
    after = ('printf "\\036%s" "$LOCI_HOST_PATCH_FILES"')
    doc = _patch_edit("C:\\proj\\first.c")
    doc["tool_input"] = doc["tool_input"].replace(
        "*** End Patch\n",
        "*** Add File: second.c\n@@\n+int s;\n*** Delete File: C:\\proj\\third.c\n*** End Patch\n")
    a = _adapt(tmp_path, doc, after=after)
    assert a.extra == ["C:\\\\proj\\\\first.c\nC:\\\\proj\\\\second.c\nC:\\\\proj\\\\third.c"], a.extra


def test_an_escaped_backslash_before_an_n_is_not_the_end_of_the_header(tmp_path):
    r"""`C:\new\a.c` is `C:\\new\\a.c` in the JSON text, which holds the two
    characters `\n` inside `\\n`; the header ends at the escape PAIR `\n`."""
    a = _adapt(tmp_path, _patch_edit("C:\\new\\a.c"))
    assert a.doc["file_path"] == "C:\\new\\a.c"


def test_a_crlf_patch_header_loses_its_return(tmp_path):
    doc = _patch_edit("C:\\proj\\main.c")
    doc["tool_input"] = doc["tool_input"].replace("\n", "\r\n")
    a = _adapt(tmp_path, doc)
    assert a.doc["file_path"] == "C:\\proj\\main.c"


def test_a_patch_with_no_file_header_injects_nothing(tmp_path):
    doc = _patch_edit("x")
    doc["tool_input"] = "*** Begin Patch\n@@\n+x\n*** End Patch\n"
    a = _adapt(tmp_path, doc)
    assert "file_path" not in a.doc


def test_a_string_input_that_is_not_a_patch_injects_nothing(tmp_path):
    doc = _patch_edit("x")
    doc["tool_input"] = "*** Update File: C:\\proj\\main.c\n"
    a = _adapt(tmp_path, doc)
    assert "file_path" not in a.doc


def test_a_patch_on_a_tool_that_is_not_an_edit_is_left_alone(tmp_path):
    doc = _patch_edit("C:\\proj\\main.c")
    doc["tool_name"] = "Read"
    a = _adapt(tmp_path, doc)
    assert "file_path" not in a.doc


def test_a_file_path_the_host_sent_wins_over_the_patch(tmp_path):
    doc = _patch_edit("C:\\proj\\main.c")
    doc["tool_input"] = {"file_path": "C:\\proj\\host.c", "patch": doc["tool_input"]}
    a = _adapt(tmp_path, doc)
    assert a.raw.count('"file_path"') == 1


def test_outside_copilot_a_patch_edit_is_not_read(tmp_path):
    a = _adapt(tmp_path, _patch_edit("C:\\proj\\main.c"), copilot=False)
    assert not a.changed and a.raw == ""


def test_pre_edit_freezes_the_baseline_of_a_patch_edit(tmp_path):
    """Criterion 1 for the observed subagent shape: the snapshot names the
    patched file, and the verb gets the whole patch plus the injected path."""
    turn = _record(tmp_path, CHILD_SESSION)
    root = tmp_path / "proj"
    root.mkdir()
    src = root / "main.c"
    src.write_text("int f(void) { return 1; }\n", encoding="utf-8")
    doc = _patch_edit("main.c", cwd=str(root))
    r = _run_hook(tmp_path, "pre-edit-hook.sh", doc, body=_SCAN_OK, project=root)
    snap = r.call("build", "snapshot")
    assert snap is not None, r.calls
    # Joined with the cwd's own separator: the payload spells the project as
    # the host does, and the file lands beside it in that spelling.
    assert snap[snap.index("--source") + 1] == str(src)
    assert f"--turn={turn}" in snap
    scan = r.stdin_of("hook", "edit-scan")
    assert scan["file_path"] == str(src)
    assert scan["tool_input"].startswith("*** Begin Patch")


def test_post_edit_reminds_after_a_patch_edit(tmp_path):
    turn = _record(tmp_path, CHILD_SESSION)
    root = tmp_path / "proj"
    (root / ".loci").mkdir(parents=True)
    (root / ".loci" / "build.yaml").write_text("version: 1\n", encoding="utf-8")
    src = root / "main.c"
    src.write_text("// probe\nint f(void) { return 1; }\n", encoding="utf-8")
    doc = _patch_edit(str(src), event="PostToolUse", cwd=str(root))
    r = _run_hook(tmp_path, "post-edit-hook.sh", doc, body=_SCAN_OK, project=root)
    ctx = json.loads(r.out.strip())["hookSpecificOutput"]["additionalContext"]
    assert "main.c was modified. You MUST invoke the loci:loci-post-edit skill" in ctx, ctx
    assert f"Pass turn id {turn}" in ctx
    scan = r.stdin_of("hook", "edit-scan")
    assert scan["file_path"] == str(src)
    assert scan["tool_result"]["result_type"] == "success"


@pytest.mark.parametrize("hook", ["pre-edit-hook.sh", "post-edit-hook.sh"])
def test_a_patch_edit_of_an_unmeasurable_file_never_reaches_the_cli(tmp_path, hook):
    root = tmp_path / "proj"
    root.mkdir()
    event = "PreToolUse" if hook.startswith("pre") else "PostToolUse"
    doc = _patch_edit(str(root / "README.md"), event=event, cwd=str(root))
    r = _run_hook(tmp_path, hook, doc, body=_SCAN_OK, project=root)
    assert r.calls == [] and r.out.strip() == ""


# -- the contract guard --------------------------------------------------------

def _guard(tmp_path: Path, doc: dict | str, *, copilot: bool = True, cwd: Path | None = None,
           project_env: Path | None = None, extra_env: dict | None = None) -> dict | None:
    """Run the guard on one payload (a str is sent as written — the only way to
    send a name twice); its decision, or None when allowed."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": _to_bash_path(home),
           "LOCI_STATE_DIR": _to_bash_path(tmp_path / "state"), **(extra_env or {})}
    if project_env is not None:
        env["CLAUDE_PROJECT_DIR"] = _to_bash_path(project_env)
    if copilot:
        env["COPILOT_CLI"] = "1"
    run_in = cwd if cwd is not None else tmp_path
    run_in.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run([_find_bash(), _to_bash_path(HOOKS / "contract-guard.sh")],
                          input=doc if isinstance(doc, str) else json.dumps(doc),
                          capture_output=True, text=True,
                          encoding="utf-8", timeout=60, env=env, cwd=str(run_in))
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == "", proc.stderr
    out = proc.stdout.strip()
    return json.loads(out)["hookSpecificOutput"] if out else None


def _guarded_project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / ".loci" / "build").mkdir(parents=True)
    for rel in (".loci/contract.yaml", ".loci/build.yaml", ".loci/build/flags.json"):
        (root / rel).write_text("x\n", encoding="utf-8")
    (root / "main.c").write_text("int f(void) { return 1; }\n", encoding="utf-8")
    return root


@pytest.mark.parametrize("rel,reason", [
    (".loci/build.yaml", "REASON_RECIPE"),
    (".loci/contract.yaml", "REASON_FILE"),
    (".loci/build/flags.json", "REASON_FLAGS"),
])
@pytest.mark.parametrize("make", [_copilot_edit, _copilot_write])
def test_the_guard_denies_a_copilot_write_to_a_guarded_file(tmp_path, rel, reason, make):
    """Criterion 1: `Denied by preToolUse hook: <REASON_RECIPE text>`."""
    root = _guarded_project(tmp_path)
    doc = make(str(root / rel).replace("/", "\\"), cwd=str(root))
    decision = _guard(tmp_path, doc, project_env=root)
    assert decision is not None and decision["permissionDecision"] == "deny", decision
    assert decision["permissionDecisionReason"].startswith(_reason(reason)), decision


# AAD-7810: relative spellings past the suffix arms. Copilot runs the hook from
# the PLUGIN root, so each was walked from there and allowed at `81bbdc3`.
_RELATIVE_GUARDED = [
    (".loci/./build.yaml", "REASON_RECIPE"),
    (".loci//build.yaml", "REASON_RECIPE"),
    (".loci/x/../build.yaml", "REASON_RECIPE"),
    ("src/../.loci/./build.yaml", "REASON_RECIPE"),
    (".loci\\.\\build.yaml", "REASON_RECIPE"),
    (".loci/./contract.yaml", "REASON_FILE"),
    (".loci/build/./flags.json", "REASON_FLAGS"),
]


@pytest.mark.parametrize("rel,reason", _RELATIVE_GUARDED)
@pytest.mark.parametrize("make", [_copilot_edit, _copilot_write])
@pytest.mark.parametrize("with_env", [True, False], ids=["project-env", "no-env"])
def test_a_relative_copilot_path_is_anchored_at_the_payload_cwd(
        tmp_path, rel, reason, make, with_env):
    root = _guarded_project(tmp_path)
    doc = make(rel, cwd=str(root))
    decision = _guard(tmp_path, doc, cwd=PLUGIN_ROOT,
                      project_env=root if with_env else None)
    assert decision is not None and decision["permissionDecision"] == "deny", decision
    assert decision["permissionDecisionReason"].startswith(_reason(reason)), decision


@pytest.mark.parametrize("rel,reason", _RELATIVE_GUARDED)
def test_a_relative_copilot_path_creating_a_guarded_file_is_decided_by_the_walk(
        tmp_path, rel, reason):
    """With the guarded files present `-ef` answers first; absent — the Write
    that creates one — only the walk can, and it must walk from `cwd`."""
    root = tmp_path / "proj"
    (root / ".loci" / "build").mkdir(parents=True)
    decision = _guard(tmp_path, _copilot_write(rel, cwd=str(root)), cwd=PLUGIN_ROOT)
    assert decision is not None and decision["permissionDecision"] == "deny", decision
    assert decision["permissionDecisionReason"].startswith(_reason(reason)), decision


_WINDOWS_ONLY = pytest.mark.skipif(os.name != "nt", reason="Win32 path spellings")


def _drive_relative(root: Path, rel: str) -> str:
    return f"{str(root)[:2]}{rel}"                        # C:.loci/./build.yaml


def _root_relative(root: Path, rel: str) -> str:
    return f"{str(root)[2:]}\\{rel}"                      # \Users\…\proj\.loci\…


@_WINDOWS_ONLY
@pytest.mark.parametrize("spell", [_drive_relative, _root_relative])
@pytest.mark.parametrize("rel,reason", [
    (".loci\\build.yaml", "REASON_RECIPE"),
    (".loci\\.\\build.yaml", "REASON_RECIPE"),
    (".loci/./contract.yaml", "REASON_FILE"),
])
@pytest.mark.parametrize("copilot", [True, False], ids=["copilot", "claude"])
def test_a_drive_or_root_relative_path_is_anchored_at_the_cwds_drive(
        tmp_path, spell, rel, reason, copilot):
    """AAD-7810 review: `C:.loci\\build.yaml` and `\\Users\\…\\.loci\\.\\build.yaml`
    open the project's recipe through Win32 and were ALLOWED on `main` under
    both hosts — the drive-relative one in its plain spelling too."""
    root = tmp_path / "proj"
    (root / ".loci" / "build").mkdir(parents=True)
    path = spell(root, rel)
    if copilot:
        doc = _copilot_write(path, cwd=str(root))
        decision = _guard(tmp_path, doc, cwd=PLUGIN_ROOT)
    else:
        doc = _payload("PreToolUse", cwd=str(root), tool_name="Write",
                       tool_input={"file_path": path, "content": "x"})
        decision = _guard(tmp_path, doc, copilot=False, cwd=root, project_env=root)
    assert decision is not None and decision["permissionDecision"] == "deny", decision
    assert decision["permissionDecisionReason"].startswith(_reason(reason)), decision


@_WINDOWS_ONLY
@pytest.mark.parametrize("spell", [_drive_relative, _root_relative])
def test_a_drive_or_root_relative_ordinary_file_is_still_allowed(tmp_path, spell):
    root = _guarded_project(tmp_path)
    doc = _copilot_write(spell(root, "main.c"), cwd=str(root))
    assert _guard(tmp_path, doc, cwd=PLUGIN_ROOT) is None


@pytest.mark.parametrize("rel", [".loci/./build.yaml", "../.loci/./build.yaml"])
def test_claude_code_denies_on_either_anchor_when_cwd_is_not_the_hooks(tmp_path, rel):
    """Under Claude Code the hook runs in the session's directory, so `$PWD` is
    kept as a reading beside the payload's `cwd`: `main` denied the first by
    `$PWD`, the branch's `cwd` reading denies the second, and neither may be
    lost. `cwd` = `<proj>/src`, the hook in `<proj>`, `CLAUDE_PROJECT_DIR` set
    as Claude Code sets it."""
    root = tmp_path / "proj"
    (root / ".loci" / "build").mkdir(parents=True)
    (root / "src").mkdir()
    doc = _payload("PreToolUse", cwd=str(root / "src"), tool_name="Write",
                   tool_input={"file_path": rel, "content": "x"})
    decision = _guard(tmp_path, doc, copilot=False, cwd=root, project_env=root)
    assert decision is not None and decision["permissionDecision"] == "deny", decision
    assert decision["permissionDecisionReason"].startswith(_reason("REASON_RECIPE")), decision


@pytest.mark.parametrize("rel,reason", _RELATIVE_GUARDED)
def test_the_same_spellings_still_deny_under_claude_code(tmp_path, rel, reason):
    """The control: Claude Code runs the hook in the project."""
    root = _guarded_project(tmp_path)
    doc = _payload("PreToolUse", cwd=str(root), tool_name="Write",
                   tool_input={"file_path": rel, "content": "x"})
    decision = _guard(tmp_path, doc, copilot=False, cwd=root, project_env=root)
    assert decision is not None and decision["permissionDecision"] == "deny", decision
    assert decision["permissionDecisionReason"].startswith(_reason(reason)), decision


@pytest.mark.parametrize("rel", ["main.c", "src/new.c", ".loci/notes.txt"])
def test_an_ordinary_relative_copilot_path_is_still_allowed(tmp_path, rel):
    root = _guarded_project(tmp_path)
    assert _guard(tmp_path, _copilot_write(rel, cwd=str(root)), cwd=PLUGIN_ROOT) is None


def _pair(root: Path, *inputs: tuple[str, str]) -> str:
    """A Copilot Write whose `tool_input` carries these (key, path) pairs in
    this order — as raw JSON, so a key may repeat."""
    keys = ",".join(f"{json.dumps(k)}:{json.dumps(v)}" for k, v in inputs)
    return ('{"hook_event_name":"PreToolUse","session_id":"s1","timestamp":1,'
            f'"cwd":{json.dumps(str(root))},"tool_name":"Write",'
            f'"tool_input":{{{keys},"file_text":"x"}}}}')


@pytest.mark.parametrize("inputs", [
    (("path", ".loci/build.yaml"), ("file_path", "main.c")),
    (("path", ".loci/./build.yaml"), ("file_path", "main.c")),
    (("file_path", "main.c"), ("path", ".loci/./build.yaml")),
    (("path", "main.c"), ("path", ".loci/./contract.yaml")),
    (("path", ".loci/build/./flags.json"), ("path", "main.c")),
    (("path", "main.c"), ("file_path", "main.c")),
    (("path", "main.c"), ("path", "other.c")),
], ids=["recipe-then-file_path", "dotted-recipe-then-file_path",
        "file_path-then-recipe", "two-paths-contract-second",
        "two-paths-flags-first", "harmless-pair", "harmless-two-paths"])
def test_a_copilot_tool_input_naming_its_file_twice_is_refused(tmp_path, inputs):
    """The guard reads `file_path`, Copilot writes `path`: a payload carrying
    both — or two `path` keys, the first of which the adapter renames — is a
    question with two answers. `recipe-then-file_path` was allowed at the
    prefilter at `81bbdc3`, plain spelling and all."""
    root = _guarded_project(tmp_path)
    decision = _guard(tmp_path, _pair(root, *inputs), cwd=PLUGIN_ROOT)
    assert decision is not None and decision["permissionDecision"] == "deny", decision
    assert decision["permissionDecisionReason"].startswith(_reason("REASON_PATH_PAIR")), decision


def test_a_claude_tool_input_with_a_path_key_is_read_as_before(tmp_path):
    """The pair screen is Copilot's: Claude Code's payload is untouched by it."""
    root = _guarded_project(tmp_path)
    doc = _payload("PreToolUse", cwd=str(root), tool_name="Write",
                   tool_input={"file_path": str(root / "main.c"), "path": ".loci/build.yaml",
                               "content": "x"})
    assert _guard(tmp_path, doc, copilot=False, cwd=root, project_env=root) is None


def _patch_text(*files: tuple[str, str]) -> str:
    """An apply_patch with one hunk per (kind, path)."""
    body = "*** Begin Patch\n"
    for kind, path in files:
        body += f"*** {kind} File: {path}\n"
        if kind != "Delete":
            body += "@@\n+// x\n"
    return body + "*** End Patch\n"


@pytest.mark.parametrize("rel,reason", [
    (".loci/build.yaml", "REASON_RECIPE"),
    (".loci/contract.yaml", "REASON_FILE"),
    (".loci/build/flags.json", "REASON_FLAGS"),
])
@pytest.mark.parametrize("kind,position", [
    ("Update", 0), ("Update", 1), ("Add", 2), ("Delete", 2)])
def test_the_guard_denies_a_patch_that_names_a_guarded_file_anywhere(
        tmp_path, rel, reason, kind, position):
    """Review round 2's Medium: an apply_patch batches files, so the guard
    decides EVERY header — the guarded file as the first, the second or the
    third, by any kind, with the others ordinary sources."""
    root = _guarded_project(tmp_path)
    guarded = str(root / rel).replace("/", "\\")
    files = [("Update", str(root / "main.c")), ("Update", "other.c"), ("Add", "new.c")]
    files[position] = (kind, guarded)
    doc = _payload("PreToolUse", cwd=str(root), tool_name="Edit",
                   tool_input=_patch_text(*files))
    decision = _guard(tmp_path, doc, project_env=root)
    assert decision is not None and decision["permissionDecision"] == "deny", decision
    assert decision["permissionDecisionReason"].startswith(_reason(reason)), decision


def test_the_guard_denies_a_patch_with_more_headers_than_it_lists(tmp_path):
    """Review round 3: the 64-header bound was silent — the guarded file as
    the 65th header was allowed. A patch the adapter could not read to its end
    is refused as a whole."""
    root = _guarded_project(tmp_path)
    files = [("Update", f"src/f{i}.c") for i in range(64)]
    files.append(("Update", str(root / ".loci" / "build.yaml").replace("/", "\\")))
    doc = _payload("PreToolUse", cwd=str(root), tool_name="Edit",
                   tool_input=_patch_text(*files))
    decision = _guard(tmp_path, doc, project_env=root)
    assert decision is not None and decision["permissionDecision"] == "deny", decision
    assert decision["permissionDecisionReason"].startswith(_reason("REASON_PATCH_UNREAD")), decision


def test_a_guarded_header_behind_a_long_hunk_is_still_decided(tmp_path):
    """Review round 3: the guard's adapter call read the library's 16 KB head,
    not the guard's 64 KB cap, so a header behind a 20 KB hunk was unread and
    the write allowed. Inside the cap it is listed and denied for what it is;
    past the cap the patch is refused as unread.

    Under bash 3 with no newer bash (stock macOS) the cap is 4 KB
    (lib/bash-compat.sh, AAD-7771), so the pair of sizes sits either side of
    THAT: a 26 KB hunk is past the cap there and the header behind it is
    refused unread — the documented over-deny, not this test's subject
    (AAD-7806)."""
    root = _guarded_project(tmp_path)
    guarded = str(root / ".loci" / "contract.yaml").replace("/", "\\")
    lines = 60 if _legacy_bash() else 700                        # ~2 KB / ~26 KB
    hunk = "".join(f"+// line {i} of a long generated file\n" for i in range(lines))
    doc = _payload("PreToolUse", cwd=str(root), tool_name="Edit",
                   tool_input=f"*** Begin Patch\n*** Update File: {root}\\main.c\n@@\n{hunk}"
                              f"*** Update File: {guarded}\n@@\n+x\n*** End Patch\n")
    decision = _guard(tmp_path, doc, project_env=root)
    assert decision is not None and decision["permissionDecision"] == "deny", decision
    assert decision["permissionDecisionReason"].startswith(_reason("REASON_FILE")), decision
    hunk = hunk * 3                                                       # ~7 KB / ~78 KB
    doc["tool_input"] = (f"*** Begin Patch\n*** Update File: {root}\\main.c\n@@\n{hunk}"
                         f"*** Update File: {guarded}\n@@\n+x\n*** End Patch\n")
    decision = _guard(tmp_path, doc, project_env=root)
    assert decision is not None and decision["permissionDecision"] == "deny", decision
    assert decision["permissionDecisionReason"].startswith(_reason("REASON_PATCH_UNREAD")), decision


@pytest.mark.parametrize("rel,reason", [
    (".loci/contract.yaml", "REASON_FILE"), (".loci/build.yaml", "REASON_RECIPE"),
    (".loci/build/flags.json", "REASON_FLAGS")])
@pytest.mark.parametrize("alone", [True, False])
def test_a_header_the_walker_cannot_read_is_an_unread_file(tmp_path, rel, reason, alone):
    r"""Review round 4: a `.\`-padded path has one escape pair per step, and
    past the walker's 256-pair bound the header was skipped in silence — the
    object shape denied it, the patch shape allowed it. It is unread now, so
    the patch is refused — alone (nothing listed, no `file_path` injected: the
    prefilter's patch arms carry it) and behind an ordinary file."""
    root = _guarded_project(tmp_path)
    padded = ".\\" * 300 + rel.replace("/", "\\")
    files = [("Update", padded)] if alone else [("Update", str(root / "main.c")), ("Update", padded)]
    doc = _payload("PreToolUse", cwd=str(root), tool_name="Edit", tool_input=_patch_text(*files))
    decision = _guard(tmp_path, doc, project_env=root)
    assert decision is not None and decision["permissionDecision"] == "deny", decision
    assert decision["permissionDecisionReason"].startswith(_reason("REASON_PATCH_UNREAD")), decision


def test_a_complete_patch_beside_a_long_field_is_not_unread(tmp_path):
    """The flag is about the PATCH: `*** End Patch` inside the head means every
    file is listed, whatever field follows (review round 4)."""
    root = _guarded_project(tmp_path)
    doc = _payload("PostToolUse", cwd=str(root), tool_name="Edit",
                   tool_input=_patch_text(("Update", str(root / "main.c"))),
                   tool_result={"result_type": "success",
                                "text_result_for_llm": "the contract of this API " * 4000})
    assert _guard(tmp_path, doc, project_env=root) is None


def test_a_stray_end_patch_line_inside_a_hunk_is_not_the_end(tmp_path):
    """Review round 5: `*** End Patch` counts as the patch's end only with
    the string's closing quote right after it; a line that merely looks like
    the end, followed by a guarded header past the head, leaves the patch
    unread — and refused."""
    root = _guarded_project(tmp_path)
    guarded = str(root / ".loci" / "contract.yaml").replace("/", "\\")
    hunk = "".join(f"+// line {i} of a long generated file\n" for i in range(2100))  # ~78 KB
    doc = _payload("PreToolUse", cwd=str(root), tool_name="Edit",
                   tool_input=f"*** Begin Patch\n*** Update File: {root}\\main.c\n@@\n+x\n"
                              f"*** End Patch\n{hunk}*** Update File: {guarded}\n@@\n+y\n*** End Patch\n")
    decision = _guard(tmp_path, doc, project_env=root)
    assert decision is not None and decision["permissionDecision"] == "deny", decision
    assert decision["permissionDecisionReason"].startswith(_reason("REASON_PATCH_UNREAD")), decision


def test_a_guarded_header_inside_the_head_of_an_over_cap_patch_is_denied_for_what_it_is(tmp_path):
    root = _guarded_project(tmp_path)
    guarded = str(root / ".loci" / "build.yaml").replace("/", "\\")
    hunk = "".join(f"+// line {i} of a long generated file\n" for i in range(2100))  # ~78 KB
    doc = _payload("PreToolUse", cwd=str(root), tool_name="Edit",
                   tool_input=f"*** Begin Patch\n*** Update File: {guarded}\n@@\n+x\n"
                              f"*** Update File: {root}\\main.c\n@@\n{hunk}*** End Patch\n")
    decision = _guard(tmp_path, doc, project_env=root)
    assert decision is not None and decision["permissionDecision"] == "deny", decision
    assert decision["permissionDecisionReason"].startswith(_reason("REASON_RECIPE")), decision


def test_a_long_ordinary_patch_with_no_guarded_token_is_allowed(tmp_path):
    """The unread refusal comes after the prefilter: a long patch naming no
    guarded file anywhere is an ordinary edit."""
    root = _guarded_project(tmp_path)
    hunk = "".join(f"+// line {i} of a long generated file\n" for i in range(2100))  # ~78 KB
    doc = _payload("PreToolUse", cwd=str(root), tool_name="Edit",
                   tool_input=f"*** Begin Patch\n*** Update File: {root}\\main.c\n@@\n{hunk}*** End Patch\n")
    assert _guard(tmp_path, doc, project_env=root) is None


def test_the_adapter_flags_a_patch_it_could_not_read_to_its_end(tmp_path):
    after = 'printf "\\036%s\\036%s" "$LOCI_HOST_PATCH_TRUNCATED" "$(printf %s "$LOCI_HOST_PATCH_FILES" | wc -l)"'
    doc = _patch_edit("C:\\proj\\a.c")
    doc["tool_input"] = _patch_text(*[("Update", f"f{i}.c") for i in range(65)])
    a = _adapt(tmp_path, doc, after=after)
    assert a.extra[0] == "1" and a.extra[1].strip() == "63", a.extra       # 64 listed = 63 newlines
    doc["tool_input"] = _patch_text(*[("Update", f"f{i}.c") for i in range(64)])
    a = _adapt(tmp_path, doc, after=after)
    assert a.extra[0] == "" and a.extra[1].strip() == "63", a.extra
    long = _patch_edit("C:\\proj\\a.c")
    long["tool_input"] = long["tool_input"].replace("@@\n", "@@\n" + "+// x\n" * 4000, 1)   # > 16 KB head
    a = _adapt(tmp_path, long, after=after)
    assert a.extra[0] == "1", a.extra
    assert a.doc["file_path"] == "C:\\proj\\a.c", "the first file is still the measured one"


def test_the_guard_allows_a_patch_of_ordinary_sources(tmp_path):
    root = _guarded_project(tmp_path)
    doc = _payload("PreToolUse", cwd=str(root), tool_name="Edit",
                   tool_input=_patch_text(("Update", str(root / "main.c")),
                                          ("Add", "src/new.c"), ("Delete", "old.c")))
    assert _guard(tmp_path, doc, project_env=root) is None


# AAD-7811: the file a `*** Move to:` line creates was in no list — the adapter
# read three header kinds and left Move to the CLI, which skips it as a rename —
# so a rename onto the recipe was decided by its SOURCE file and allowed.

def _move_patch(src: str, dst: str) -> str:
    return f"*** Begin Patch\n*** Update File: {src}\n*** Move to: {dst}\n@@\n+target: x\n*** End Patch\n"


@pytest.mark.parametrize("rel,reason", [
    (".loci/build.yaml", "REASON_RECIPE"),
    (".loci/contract.yaml", "REASON_FILE"),
    (".loci/build/flags.json", "REASON_FLAGS"),
    (".loci/./build.yaml", "REASON_RECIPE"),
])
@pytest.mark.parametrize("spell", ["absolute", "relative", "backslash"])
@pytest.mark.parametrize("run_in", ["plugin", "project"])
def test_a_rename_onto_a_guarded_file_is_denied(tmp_path, rel, reason, spell, run_in):
    root = _guarded_project(tmp_path)
    dst = {"absolute": str(root / rel).replace("/", "\\"), "relative": rel,
           "backslash": rel.replace("/", "\\")}[spell]
    doc = _payload("PreToolUse", cwd=str(root), tool_name="Edit",
                   tool_input=_move_patch(str(root / "scratch.yaml"), dst))
    decision = _guard(tmp_path, doc, cwd=PLUGIN_ROOT if run_in == "plugin" else root)
    assert decision is not None and decision["permissionDecision"] == "deny", decision
    assert decision["permissionDecisionReason"].startswith(_reason(reason)), decision


def test_a_rename_between_two_sources_is_allowed(tmp_path):
    root = _guarded_project(tmp_path)
    doc = _payload("PreToolUse", cwd=str(root), tool_name="Edit",
                   tool_input=_move_patch(str(root / "main.c"), "src/renamed.c"))
    assert _guard(tmp_path, doc, cwd=PLUGIN_ROOT, project_env=root) is None


def test_the_move_target_is_listed_and_the_header_stays_the_measured_file(tmp_path):
    after = 'printf "\\036%s" "$LOCI_HOST_PATCH_FILES"'
    doc = _patch_edit("x")
    doc["tool_input"] = _move_patch("C:\\proj\\old.c", "new.c")
    a = _adapt(tmp_path, doc, after=after)
    assert a.doc["file_path"] == "C:\\proj\\old.c"
    assert a.extra == ["C:\\\\proj\\\\old.c\nC:\\\\proj\\\\new.c"], a.extra


def test_a_move_line_past_the_header_bound_flags_the_patch(tmp_path):
    after = 'printf "\\036%s" "$LOCI_HOST_PATCH_TRUNCATED"'
    doc = _patch_edit("x")
    doc["tool_input"] = _patch_text(*[("Update", f"f{i}.c") for i in range(64)]).replace(
        "*** End Patch\n", "*** Move to: .loci/build.yaml\n*** End Patch\n")
    assert _adapt(tmp_path, doc, after=after).extra == ["1"]


# A string `tool_input` the adapter cannot read as a patch named no file, and
# the guard decided none: the prefilter's patch arm saw the recipe, route 1 had
# no path. Copilot's patch opens with exactly `*** Begin Patch`, so the refusal
# is of a shape the host does not send — whatever it names.

@pytest.mark.parametrize("text", [
    " *** Begin Patch\n*** Update File: {g}\n@@\n+x\n*** End Patch\n",
    "\n*** Begin Patch\n*** Update File: {g}\n@@\n+x\n*** End Patch\n",
    "*** Begin Patch\n *** Update File: {g}\n@@\n+x\n*** End Patch\n",
    "*** Begin Patch\n*** Move to: {g}\n@@\n+x\n*** End Patch\n",
    "*** Begin Patch\n@@\n+x\n*** End Patch\n",
], ids=["space-first", "newline-first", "indented-header", "move-only", "no-header"])
def test_a_string_input_the_adapter_cannot_read_is_refused(tmp_path, text):
    root = _guarded_project(tmp_path)
    guarded = str(root / ".loci" / "build.yaml").replace("/", "\\")
    doc = _payload("PreToolUse", cwd=str(root), tool_name="Edit",
                   tool_input=text.format(g=guarded))
    decision = _guard(tmp_path, doc, cwd=PLUGIN_ROOT)
    assert decision is not None and decision["permissionDecision"] == "deny", decision
    assert decision["permissionDecisionReason"].startswith(_reason("REASON_PATCH_UNREAD")), decision


def test_the_adapter_flags_an_edit_it_could_name_no_file_of(tmp_path):
    after = 'printf "\\036%s" "$LOCI_HOST_EDIT_UNREAD"'
    doc = _patch_edit("x")
    doc["tool_input"] = " *** Begin Patch\n*** Update File: a.c\n*** End Patch\n"
    assert _adapt(tmp_path, doc, after=after).extra == ["patch"]
    assert _adapt(tmp_path, _patch_edit("C:\\proj\\a.c"), after=after).extra == [""]
    late = _payload("PreToolUse", tool_name="Write",
                    tool_input={"file_text": "x" * 20000, "path": "C:\\proj\\a.c"})
    assert _adapt(tmp_path, late, after=after).extra == ["path"]
    first = _copilot_write("C:\\proj\\a.c", text="x" * 20000)
    assert _adapt(tmp_path, first, after=after).extra == [""]
    # An uncut object naming no file is not flagged: Copilot's apply_patch
    # takes only a string, so such an Edit writes nothing (review round 2).
    wrapped = _payload("PreToolUse", tool_name="Edit", tool_input={"input": "*** Begin Patch\n"})
    assert _adapt(tmp_path, wrapped, after=after).extra == [""]
    read = _payload("PreToolUse", tool_name="Read", tool_input={"input": "x"})
    assert _adapt(tmp_path, read, after=after).extra == [""]


# Copilot's key order inside `tool_input` is the model's. A `path` behind more
# content than the guard's head was never renamed, the prefilter saw no
# `file_path`, and the write was allowed on bash 4+ — the unread-path rule was
# gated on bash 3.

def _late_path(root: Path, path: str, size: int = 70000) -> dict:
    return _payload("PreToolUse", cwd=str(root), tool_name="Write",
                    tool_input={"file_text": "x" * size, "path": path})


@pytest.mark.parametrize("rel", [".loci/build.yaml", ".loci/contract.yaml",
                                 ".loci/build/flags.json", "main.c"])
def test_a_path_past_the_head_is_refused_as_unread(tmp_path, rel):
    root = _guarded_project(tmp_path)
    decision = _guard(tmp_path, _late_path(root, str(root / rel).replace("/", "\\")),
                      cwd=PLUGIN_ROOT)
    assert decision is not None and decision["permissionDecision"] == "deny", decision
    assert decision["permissionDecisionReason"].startswith(_reason("REASON_PATH_UNREAD")), decision


def test_a_path_whose_value_the_head_cut_is_refused_as_unread(tmp_path):
    """The key is inside the head and renamed; its value runs past it."""
    root = _guarded_project(tmp_path)
    padded = str(root) + "\\" + ".\\" * 40000 + ".loci\\build.yaml"
    doc = _copilot_write(padded, cwd=str(root))
    decision = _guard(tmp_path, doc, cwd=PLUGIN_ROOT)
    assert decision is not None and decision["permissionDecision"] == "deny", decision
    want = "REASON_UNREAD" if _legacy_bash() else "REASON_PATH_UNREAD"
    assert decision["permissionDecisionReason"].startswith(_reason(want)), decision


def test_a_long_write_with_its_path_first_is_decided_as_before(tmp_path):
    root = _guarded_project(tmp_path)
    ok = _copilot_write(str(root / "main.c"), text="int x;\n" * 12000, cwd=str(root))
    assert _guard(tmp_path, ok, cwd=PLUGIN_ROOT, project_env=root) is None
    bad = _copilot_write(str(root / ".loci" / "build.yaml"), text="x: 1\n" * 16000, cwd=str(root))
    decision = _guard(tmp_path, bad, cwd=PLUGIN_ROOT)
    assert decision is not None
    assert decision["permissionDecisionReason"].startswith(_reason("REASON_RECIPE")), decision


def test_claude_codes_long_payload_is_read_as_before(tmp_path):
    """No `COPILOT_CLI`: neither refusal runs — a Claude Code payload carries
    `file_path` first, and one that does not is decided by what it names."""
    root = _guarded_project(tmp_path)
    doc = _payload("PreToolUse", cwd=str(root), tool_name="Write",
                   tool_input={"content": "x" * 70000, "path": str(root / "main.c")})
    assert _guard(tmp_path, doc, copilot=False, cwd=root, project_env=root) is None
    doc["tool_input"] = " *** Begin Patch\n*** Update File: main.c\n*** End Patch\n"
    assert _guard(tmp_path, doc, copilot=False, cwd=root, project_env=root) is None


def test_an_inherited_unread_flag_never_reaches_claude_code(tmp_path):
    """The guard reads `LOCI_HOST_EDIT_UNREAD` only under Copilot: exported
    into a Claude Code session, it must not refuse an ordinary write that the
    prefilter admits (its content names the contract)."""
    root = _guarded_project(tmp_path)
    doc = _payload("PreToolUse", cwd=str(root), tool_name="Write",
                   tool_input={"file_path": str(root / "main.c"),
                               "content": "// see the contract\n"})
    for flag in ("patch", "path"):
        assert _guard(tmp_path, doc, copilot=False, cwd=root, project_env=root,
                      extra_env={"LOCI_HOST_EDIT_UNREAD": flag}) is None, flag


@pytest.mark.parametrize("pad", ["/" * 70000, ".\\" * 40000], ids=["separators", "dot-pads"])
def test_claude_codes_cut_file_path_value_is_refused_as_unread(tmp_path, pad):
    """AAD-7813: this was allowed under Claude Code on bash 4+, on the grounds
    that no host opens a path longer than the 64 KB head. False on Windows:
    Node resolves the path — separators collapsed — before the OS sees its
    length, and writes `<root>` + `/`x70000 + `.loci/build.yaml` to the recipe
    (measured). bash 3 refuses it with its own reason, as it always did."""
    root = _guarded_project(tmp_path)
    padded = str(root) + "\\" + pad + ".loci\\build.yaml"
    doc = _payload("PreToolUse", cwd=str(root), tool_name="Write",
                   tool_input={"file_path": padded, "content": "x"})
    decision = _guard(tmp_path, doc, copilot=False, cwd=root, project_env=root)
    want = "REASON_UNREAD" if _legacy_bash() else "REASON_PATH_UNREAD"
    assert decision is not None and decision["permissionDecisionReason"].startswith(
        _reason(want)), decision


# AAD-7811 review, H1: a decoy file key in front of a late `path`. The decoy
# names the file the head reads; the recipe sits behind 64 KB of content.

@pytest.mark.parametrize("decoy", [
    '"file_path":"{main}",',
    '"opts":{{"path":"main.c"}},',
    '"opts":{{"file_path":"main.c"}},',
    '"opts":[{{"path":"main.c"}}],',
], ids=["file_path", "nested-path", "nested-file_path", "array-path"])
@pytest.mark.parametrize("tool,body", [("Write", "file_text"), ("Edit", "old_str")])
def test_a_decoy_key_in_front_of_a_late_path_is_refused(tmp_path, decoy, tool, body):
    root = _guarded_project(tmp_path)
    main = json.dumps(str(root / "main.c"))[1:-1]
    recipe = json.dumps(str(root / ".loci" / "build.yaml"))
    raw = ('{"hook_event_name":"PreToolUse","session_id":"s1","timestamp":1,'
           f'"cwd":{json.dumps(str(root))},"tool_name":"{tool}",'
           f'"tool_input":{{{decoy.format(main=main)}"{body}":"{"x" * 70000}","path":{recipe}}}}}')
    decision = _guard(tmp_path, raw, cwd=PLUGIN_ROOT)
    assert decision is not None and decision["permissionDecision"] == "deny", decision
    assert decision["permissionDecisionReason"].startswith(_reason("REASON_PATH_PAIR")), decision


@pytest.mark.parametrize("indent", ["  ", "\t", " "])
@pytest.mark.parametrize("before", ["*** Add File: new.c\n+int a;\n", "*** Delete File: old.c\n"])
def test_an_indented_header_after_a_read_one_is_unread(tmp_path, indent, before):
    """Review M1: a parser that trims the line reads it as a header."""
    root = _guarded_project(tmp_path)
    text = f"*** Begin Patch\n{before}{indent}*** Add File: .loci/build.yaml\n+x\n*** End Patch\n"
    doc = _payload("PreToolUse", cwd=str(root), tool_name="Edit", tool_input=text)
    decision = _guard(tmp_path, doc, cwd=PLUGIN_ROOT)
    assert decision is not None and decision["permissionDecision"] == "deny", decision
    assert decision["permissionDecisionReason"].startswith(_reason("REASON_PATCH_UNREAD")), decision


_TRIMMED = ["\f", "\r", "\v", "\u00a0", "\u2028", "\u3000", "\u0085"]


@pytest.mark.parametrize("ascii_json", [True, False], ids=["escaped", "raw"])
@pytest.mark.parametrize("ws", _TRIMMED, ids=[f"U+{ord(c):04X}" for c in _TRIMMED])
def test_a_header_indented_by_any_whitespace_is_unread(tmp_path, ws, ascii_json):
    """Review round 2: Copilot's parser (codex apply-patch) trims a line with
    Rust's `trim`, Unicode whitespace included, so after an Add hunk each of
    these indents a real header."""
    root = _guarded_project(tmp_path)
    text = (f"*** Begin Patch\n*** Add File: {root}\\a.c\n+x\n{ws}*** Update File: "
            f"{root}\\.loci\\build.yaml\n@@\n-a\n+b\n*** End Patch\n")
    doc = json.dumps(_payload("PreToolUse", cwd=str(root), tool_name="Edit", tool_input=text),
                     ensure_ascii=ascii_json)
    decision = _guard(tmp_path, doc, cwd=PLUGIN_ROOT)
    assert decision is not None and decision["permissionDecision"] == "deny", decision
    assert decision["permissionDecisionReason"].startswith(_reason("REASON_PATCH_UNREAD")), decision


@pytest.mark.parametrize("ascii_json", [True, False], ids=["escaped", "raw"])
@pytest.mark.parametrize("ws", ["\t", "\f", "\v", "\u00a0", "\u3000", "\u0085"],
                         ids=["tab", "ff", "vt", "nbsp", "ideographic", "nel"])
@pytest.mark.parametrize("kind", ["Update", "Delete", "Move"])
def test_a_header_path_ending_in_whitespace_a_trim_takes_is_unread(tmp_path, ws, ascii_json, kind):
    """Review round 2: the walker strips trailing spaces only, the parser's
    trim takes every kind — `build.yaml<TAB>` WAS the recipe, allowed."""
    root = _guarded_project(tmp_path)
    guarded = f"{root}\\.loci\\build.yaml{ws}"
    text = (_move_patch(str(root / "main.c"), guarded) if kind == "Move"
            else _patch_text((kind, guarded)))
    doc = json.dumps(_payload("PreToolUse", cwd=str(root), tool_name="Edit", tool_input=text),
                     ensure_ascii=ascii_json)
    decision = _guard(tmp_path, doc, cwd=PLUGIN_ROOT)
    assert decision is not None and decision["permissionDecision"] == "deny", decision
    assert decision["permissionDecisionReason"].startswith(_reason("REASON_PATCH_UNREAD")), decision


@pytest.mark.parametrize("path,listed", [
    ("C:\\proj\\t", True),            # `\\t`: a backslash and a `t`, not a tab
    ("C:\\proj\\a\\", True),          # ends in an escaped backslash
    ("C:\\proj\\caf\u00e9", True),     # a name, not padding (round 3)
    ("C:\\proj\\a.c\t", False),
    ("C:\\proj\\\\\ta.c\t", False),    # `\\\t`: a backslash, then a tab
])
def test_the_end_of_a_header_path_is_read_by_escape_parity(tmp_path, path, listed):
    after = 'printf "\\036%s\\036%s" "$LOCI_HOST_PATCH_TRUNCATED" "$LOCI_HOST_PATCH_FILES"'
    doc = _patch_edit("x")
    doc["tool_input"] = _patch_text(("Update", path))
    a = _adapt(tmp_path, doc, after=after)
    if listed:
        assert a.extra[0] == "" and a.extra[1] == json.dumps(path)[1:-1], a.extra
    else:
        assert a.extra[0] == "1" and a.extra[1] == "", a.extra


def test_many_deep_header_paths_are_decided_inside_the_hook_budget(tmp_path):
    """Review round 2: the header walker was handed the whole rest of the
    head and copied it once per escape — 62 paths of 200 backslashes in front
    of a 20 KB hunk took 10.5 s on Git Bash, past the 5 s kill."""
    root = _guarded_project(tmp_path)
    deep = "\\".join(["a"] * 200)
    for count, tail in ((30, 30000), (62, 20000)):
        hdrs = "".join(f"*** Add File: C:\\{deep}\\f{i}.c\n+x\n" for i in range(count))
        text = (f"*** Begin Patch\n{hdrs}*** Update File: {root}\\.loci\\build.yaml\n@@\n-a\n+b\n"
                f"*** Add File: z.c\n+{'y' * tail}\n*** End Patch\n")
        doc = _payload("PreToolUse", cwd=str(root), tool_name="Edit", tool_input=text)
        start = time.monotonic()
        decision = _guard(tmp_path, doc, cwd=PLUGIN_ROOT)
        elapsed = time.monotonic() - start
        assert decision is not None and decision["permissionDecision"] == "deny", (count, decision)
        assert elapsed < 5.0, f"{count} headers: {elapsed:.1f}s against the 5 s in hooks.json"


def test_a_long_edit_whose_old_text_is_the_word_path_is_allowed(tmp_path):
    """Review round 2: the cut-payload pair screen matched the VALUE `"path"`
    too; a key carries its colon."""
    root = _guarded_project(tmp_path)
    for old in ("path", "file_path"):
        doc = _payload("PreToolUse", cwd=str(root), tool_name="Edit",
                       tool_input={"path": str(root / "main.c"), "old_str": old,
                                   "new_str": "x" * 70000})
        assert _guard(tmp_path, doc, cwd=PLUGIN_ROOT, project_env=root) is None, old


def test_many_paths_naming_a_guarded_file_are_bounded_before_they_fork(tmp_path):
    """Review rounds 3-4: each path carrying a guarded basename forks route 1's
    walk (~85 ms on Git Bash, more for a long path), so 63 `.loci/build.yaml.<n>`
    headers and then the recipe took 5.5 s — past the 5 s kill. The forks are
    budgeted where they happen, weighted by length, so neither a fold the
    locale does (Git Bash folds U+0130 to `i`) nor a deep path escapes it."""
    root = _guarded_project(tmp_path)
    deep = "/" + "x./" * 430
    shapes = {
        "decoys": "".join(f"*** Add File: .loci/build.yaml.{i}\n+x\n" for i in range(63)),
        "folded": "".join(f"*** Add File: /x./.loci/bu{chr(0x130)}ld.yaml.{i}\n+x\n" for i in range(63)),
        "deep": "".join(f"*** Add File: {deep}.loci/build.yaml.{i}\n+x\n" for i in range(8)),
    }
    for label, hdrs in shapes.items():
        text = f"*** Begin Patch\n{hdrs}*** Update File: .loci/build.yaml\n@@\n-x\n+y\n*** End Patch\n"
        doc = json.dumps(_payload("PreToolUse", cwd=str(root), tool_name="Edit", tool_input=text),
                         ensure_ascii=False)
        start = time.monotonic()
        decision = _guard(tmp_path, doc, cwd=PLUGIN_ROOT, extra_env={"LANG": "C.UTF-8"})
        elapsed = time.monotonic() - start
        assert decision is not None and decision["permissionDecision"] == "deny", (label, decision)
        assert elapsed < 2.5, f"{label}: {elapsed:.1f}s, and the bound is for the 5 s kill"
    # Ordinary names that hold a guarded one are not the bound's business: a
    # walk costs a unit per 256 bytes of path and root, and pytest's temp root
    # is long, so five files here are about ten of a short project's.
    for name in ("cloudbuild.yaml", "featureFlags.json"):
        files = "".join(f"*** Update File: svc{i}/{name}\n@@\n-a\n+b\n" for i in range(5))
        doc = _payload("PreToolUse", cwd=str(root), tool_name="Edit",
                       tool_input=f"*** Begin Patch\n{files}*** End Patch\n")
        assert _guard(tmp_path, doc, cwd=PLUGIN_ROOT, project_env=root) is None, name


def test_a_long_root_is_charged_to_every_walk(tmp_path):
    """Review round 5: with no git checkout and no `CLAUDE_PROJECT_DIR` the
    root is the payload's `cwd`, and every walk descends it — padded with
    `x\\..` to 2.6 KB, 24 short headers took 19 s, past the 5 s kill."""
    root = _guarded_project(tmp_path)
    (root / "x").mkdir()
    cwd = str(root) + "\\x\\.." * 600
    hdrs = "".join(f"*** Add File: {root}\\b{i}.build.yaml\n+x\n" for i in range(24))
    text = f"*** Begin Patch\n{hdrs}*** Update File: .loci/build.yaml\n@@\n-x\n+y\n*** End Patch\n"
    doc = _payload("PreToolUse", cwd=cwd, tool_name="Edit", tool_input=text)
    start = time.monotonic()
    decision = _guard(tmp_path, doc, cwd=PLUGIN_ROOT)
    elapsed = time.monotonic() - start
    assert decision is not None and decision["permissionDecision"] == "deny", decision
    # Under bash 3 the 4 KB head ends inside this `cwd`, so the payload names
    # no path the guard read and is refused for that, before any walk.
    want = "REASON_PATH_UNREAD" if _legacy_bash() else "REASON_PATCH_UNREAD"
    assert decision["permissionDecisionReason"].startswith(_reason(want)), decision
    assert elapsed < 2.5, f"{elapsed:.1f}s, and the bound is for the 5 s kill"


@pytest.mark.parametrize("line", [
    " A rename uses `*** Move to: .loci/build.yaml` here",
    '     payload = "*** Update File: .loci/build.yaml" + rest',
    "@@ def f(): return '*** Add File: .loci/contract.yaml'",
], ids=["prose", "string", "chunk-header"])
def test_a_header_mentioned_inside_a_line_is_content(tmp_path, line):
    """Review round 3: only WHITESPACE before `*** ` makes a line a trimmed
    header; text before it is a line the patch edits (this repo's own tests,
    edited under Copilot)."""
    root = _guarded_project(tmp_path)
    text = f"*** Begin Patch\n*** Update File: {root}\\tests\\a.py\n@@\n{line}\n-x\n+y\n*** End Patch\n"
    doc = _payload("PreToolUse", cwd=str(root), tool_name="Edit", tool_input=text)
    assert _guard(tmp_path, doc, cwd=PLUGIN_ROOT, project_env=root) is None


@pytest.mark.parametrize("ascii_json", [True, False], ids=["escaped", "raw"])
def test_a_path_ending_in_a_non_ascii_letter_is_listed(tmp_path, ascii_json):
    """Review round 3: `src/contract/f` + u-umlaut is a name, not padding."""
    root = _guarded_project(tmp_path)
    text = _patch_text(("Update", "src/contract/f" + chr(0xFC)))
    doc = json.dumps(_payload("PreToolUse", cwd=str(root), tool_name="Edit", tool_input=text),
                     ensure_ascii=ascii_json)
    assert _guard(tmp_path, doc, cwd=PLUGIN_ROOT, project_env=root) is None


@_WINDOWS_ONLY
@pytest.mark.parametrize("text", [
    "*** Begin Patch\n*** Update File: {main}\n*** Move to: C:.loci/build.yaml\n@@\n+x\n*** End Patch\n",
    "*** Begin Patch\n*** Update File: C:.loci/build.yaml\n@@\n+x\n*** End Patch\n",
    "*** Begin Patch\n*** Add File: {main}\n+x\n*** Add File: C:.loci\\build.yaml\n+x\n*** End Patch\n",
], ids=["move", "update", "second-add"])
def test_a_drive_relative_patch_header_is_decided_at_the_cwds_drive(tmp_path, text):
    """Review H3: joined to the cwd, `C:.loci/build.yaml` named no file."""
    root = tmp_path / "proj"
    (root / ".loci" / "build").mkdir(parents=True)
    (root / "main.c").write_text("int f;\n", encoding="utf-8")
    text = text.replace("C:", str(root)[:2]).format(main=str(root / "main.c"))
    doc = _payload("PreToolUse", cwd=str(root), tool_name="Edit", tool_input=text)
    decision = _guard(tmp_path, doc, cwd=PLUGIN_ROOT)
    assert decision is not None and decision["permissionDecision"] == "deny", decision
    assert decision["permissionDecisionReason"].startswith(_reason("REASON_RECIPE")), decision


def test_a_header_spelled_with_a_leading_space_names_the_trimmed_path(tmp_path):
    root = _guarded_project(tmp_path)
    doc = _payload("PreToolUse", cwd=str(root), tool_name="Edit",
                   tool_input=_move_patch(str(root / "main.c"), " .loci/build.yaml"))
    decision = _guard(tmp_path, doc, cwd=PLUGIN_ROOT)
    assert decision is not None and decision["permissionDecisionReason"].startswith(
        _reason("REASON_RECIPE")), decision


@pytest.mark.skipif(_legacy_bash(), reason="bash 3: 1024 steps of `*** ` need more than its 4 KB head")
def test_the_walk_is_bounded_in_steps_and_the_bound_denies(tmp_path):
    """Every `*** ` costs the walker a step; past the bound the patch is
    unread, so a header hidden behind a thousand of them is never allowed."""
    after = 'printf "\\036%s\\036%s" "$LOCI_HOST_PATCH_TRUNCATED" "$LOCI_HOST_PATCH_FILES"'
    doc = _patch_edit("x")
    doc["tool_input"] = ("*** Begin Patch\n*** Update File: a.c\n@@\n" + "+*** x\n" * 1100
                         + "*** Update File: .loci/build.yaml\n@@\n+y\n*** End Patch\n")
    a = _adapt(tmp_path, doc, after=after)
    assert a.extra[0] == "1" and ".loci" not in a.extra[1], a.extra
    doc["tool_input"] = doc["tool_input"].replace("+*** x\n" * 1100, "+*** x\n" * 900)
    a = _adapt(tmp_path, doc, after=after)
    assert a.extra[0] == "" and a.extra[1].endswith(".loci/build.yaml"), a.extra


@pytest.mark.skipif(_legacy_bash(), reason="bash 3: the 4 KB head cuts this shape short")
def test_a_wide_patch_around_a_long_hunk_is_decided_inside_the_hook_budget(tmp_path):
    """Review H2: one scan per header KIND per header made this guarded write
    8.3 s on Git Bash at `78765c0` (0.3 s now) — past the 5 s kill, which
    fails open."""
    root = _guarded_project(tmp_path)
    guarded = str(root / ".loci" / "build.yaml").replace("/", "\\")
    small = "".join(f"*** Update File: src/f{i}.c\n@@\n+x\n" for i in range(58))
    hunk = "".join(f"+// line {i} of a long generated file\n" for i in range(1500))   # ~57 KB
    # Every kind still present past the hunk: each was scanned for, to there,
    # at every header before it.
    text = (f"*** Begin Patch\n*** Update File: {guarded}\n@@\n+x\n{small}"
            f"*** Update File: src/big.c\n@@\n{hunk}*** Add File: src/n.c\n+x\n"
            f"*** Delete File: src/d.c\n*** Update File: src/u.c\n*** Move to: src/m.c\n@@\n+x\n"
            f"*** End Patch\n")
    # And the walker's own worst: the step bound reached with a long tail
    # still to copy at every step (0.8 s here when measured).
    steps = (f"*** Begin Patch\n*** Update File: {root}\\main.c\n@@\n" + "+*** x\n" * 1020
             + "+" + "y" * 56000 + f"\n*** Update File: {guarded}\n*** End Patch\n")
    for label, body in (("wide", text), ("steps", steps)):
        doc = _payload("PreToolUse", cwd=str(root), tool_name="Edit", tool_input=body)
        start = time.monotonic()
        decision = _guard(tmp_path, doc, cwd=PLUGIN_ROOT)
        elapsed = time.monotonic() - start
        assert decision is not None and decision["permissionDecision"] == "deny", (label, decision)
        assert elapsed < 5.0, f"{label}: {elapsed:.1f}s against the 5 s in hooks.json"


def test_the_guard_outside_copilot_reads_a_patch_as_no_path(tmp_path):
    """Claude Code never sends the shape; without the gate nothing is read."""
    root = _guarded_project(tmp_path)
    doc = _payload("PreToolUse", cwd=str(root), tool_name="Edit",
                   tool_input=_patch_text(("Update", str(root / ".loci" / "build.yaml"))))
    assert _guard(tmp_path, doc, project_env=root, copilot=False) is None


@pytest.mark.parametrize("make", [_copilot_edit, _copilot_write, _claude_edit])
def test_the_guard_allows_an_ordinary_source_in_either_spelling(tmp_path, make):
    root = _guarded_project(tmp_path)
    assert _guard(tmp_path, make(str(root / "main.c"), cwd=str(root)), project_env=root) is None


def test_the_guard_never_denies_a_copilot_read_of_the_recipe(tmp_path):
    root = _guarded_project(tmp_path)
    doc = _payload("PreToolUse", cwd=str(root), tool_name="Read",
                   tool_input={"path": str(root / ".loci" / "build.yaml")})
    assert _guard(tmp_path, doc, project_env=root) is None


def test_the_guards_route_two_still_denies_the_verb_under_copilot(tmp_path):
    root = _guarded_project(tmp_path)
    doc = _payload("PreToolUse", cwd=str(root), tool_name="Bash",
                   tool_input={"command": "loci contract accept",
                               "description": "Accept the draft"})
    decision = _guard(tmp_path, doc, project_env=root)
    assert decision is not None and decision["permissionDecision"] == "deny", decision


def test_the_guard_does_not_fork_under_copilot(tmp_path):
    """The adapter runs above the guard's prefilter on every tool call under
    Copilot, so its cost IS the behaviour: on a payload nothing can deny, the
    one process is the payload read (`cat`) — no `mkdir`, no `git`, no
    `realpath`. Counted with shims ahead of the real binaries."""
    import shutil
    bindir = tmp_path / "countbin"
    bindir.mkdir()
    counter = tmp_path / "forks.txt"
    for name in ("mkdir", "realpath", "git", "cat", "date"):
        real = shutil.which(name)
        if real is None:
            pytest.skip(f"{name} not available")
        shim = bindir / name
        shim.write_text("#!/bin/sh\n"
                        f'printf "%s\\n" {name} >> "{_to_bash_path(counter)}"\n'
                        f'exec "{_to_bash_path(Path(real))}" "$@"\n',
                        encoding="utf-8", newline="\n")
        shim.chmod(0o755)
    root = _guarded_project(tmp_path)
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    env = {"PATH": f"{_to_bash_path(bindir)}:/usr/bin:/bin", "COPILOT_CLI": "1",
           "HOME": _to_bash_path(tmp_path / "home"), "LOCI_STATE_DIR": _to_bash_path(state),
           "CLAUDE_PROJECT_DIR": _to_bash_path(root)}

    def forks(doc) -> list[str]:
        counter.write_text("", encoding="utf-8")
        proc = subprocess.run([_find_bash(), _to_bash_path(HOOKS / "contract-guard.sh")],
                              input=json.dumps(doc), capture_output=True, text=True,
                              timeout=60, env=env, cwd=str(tmp_path))
        assert proc.returncode == 0 and proc.stdout.strip() == "", proc.stdout
        return counter.read_text(encoding="utf-8").split()

    bash = _payload("PreToolUse", cwd=str(root), tool_name="Bash",
                    tool_input={"command": "npm test -- --watch=false", "description": "t"})
    assert forks(bash) in ([], ["cat"]), forks(bash)
    edit = _copilot_edit(str(root / "web" / "index.ts"), cwd=str(root))
    assert forks(edit) in ([], ["cat"]), forks(edit)


def test_the_guard_outside_copilot_reads_a_copilot_path_as_no_path(tmp_path):
    """The Claude Code path is untouched: without `COPILOT_CLI` the adapter does
    not run, `path` is not a field the guard reads, and the write is allowed —
    exactly as on `main` before this change."""
    root = _guarded_project(tmp_path)
    doc = _copilot_edit(str(root / ".loci" / "build.yaml"), cwd=str(root))
    assert _guard(tmp_path, doc, copilot=False, project_env=root) is None


# -- every hook, both shapes: exit 0, one JSON object or nothing, silent -------

def _every_event(root: Path) -> list[tuple[str, str, dict]]:
    src = str(root / "main.c")
    out: list[tuple[str, str, dict]] = []
    for shape in ("copilot", "claude"):
        edit = _copilot_edit if shape == "copilot" else _claude_edit
        base = {"cwd": str(root)}
        if shape == "claude":
            base["prompt_id"] = "t-claude"
        bash_pre = _payload("PreToolUse", tool_name="Bash",
                            tool_input={"command": "echo x >> main.c"}, **base)
        bash_post = _payload("PostToolUse", tool_name="Bash",
                             tool_input={"command": "echo x >> main.c"}, **base)
        if shape == "copilot":
            bash_pre["tool_input"]["description"] = "Append"
            bash_post["tool_result"] = {"result_type": "success",
                                        "text_result_for_llm": "<shellId: 0 completed>"}
        else:
            bash_post["tool_response"] = {"stdout": "", "stderr": "", "interrupted": False}
        out += [
            (shape, "session-init.sh", _payload("SessionStart", source="new", **base)),
            (shape, "turn-clean.sh", _payload("SessionStart", source="new", **base)),
            (shape, "prompt-submit-turn.sh", _payload("UserPromptSubmit", prompt="hi", **base)),
            (shape, "contract-guard.sh", edit(src, **base)),
            (shape, "contract-guard.sh", bash_pre),
            (shape, "pre-edit-hook.sh", edit(src, **base)),
            (shape, "post-edit-hook.sh", edit(src, event="PostToolUse", **base)),
            (shape, "post-edit-hook.sh", edit(src, event="PostToolUseFailure", **base)),
            (shape, "post-bash-bypass.sh", bash_post),
            (shape, "stats-flush.sh", _payload("Stop", stop_hook_active=False, **base)),
            (shape, "draft-pending-nudge.sh", _payload("Stop", stop_hook_active=False, **base)),
            (shape, "manifest-status-nudge.sh", _payload("Stop", stop_hook_active=False, **base)),
            (shape, "turn-clean.sh", _payload("Stop", stop_hook_active=False, **base)),
        ]
    return out


_EVENTS = _every_event(Path("/proj"))


@pytest.mark.parametrize("shape,hook,doc", _EVENTS,
                         ids=[f"{s}-{h}-{d['hook_event_name']}" for s, h, d in _EVENTS])
def test_every_hook_accepts_both_shapes_of_its_event(tmp_path, shape, hook, doc):
    """Criterion 2, as a gate on the whole bus: exit 0, nothing or one JSON
    object on stdout, nothing on stderr, from a stubbed `loci` that answers
    every verb with an empty envelope."""
    root = tmp_path / "proj"
    (root / ".loci" / "build").mkdir(parents=True)
    (root / ".loci" / "build.yaml").write_text("version: 1\n", encoding="utf-8")
    (root / "main.c").write_text("int f(void) { return 1; }\n", encoding="utf-8")
    doc = json.loads(json.dumps(doc).replace("/proj", str(root).replace("\\", "\\\\")))
    # No install knob here: nothing read `LOCI_CLI_INSTALL`, and the install
    # it meant to skip is skipped by `_run_hook`'s `_LOCI_BOOTSTRAP` (AAD-7806).
    _run_hook(tmp_path, hook, doc, copilot=(shape == "copilot"),
              body="echo '{\"ok\":true,\"data\":{}}'", project=root)


# ── no hook depends on its working directory (criterion 3) ──────────────────

class _Run:
    def __init__(self, proc, log: Path):
        self.out = proc.stdout
        self.calls: list[dict] = []
        if log.is_file():
            for chunk in log.read_bytes().split(b"\x1d"):
                if chunk:
                    args, stdin, pwd = chunk.split(b"\x1f")
                    self.calls.append({
                        "args": [a.decode("utf-8") for a in args.split(b"\x1e")[:-1]],
                        "stdin": stdin.decode("utf-8"),
                        "pwd": pwd.decode("utf-8").rstrip("\n"),
                    })

    def call(self, *verb: str) -> dict | None:
        return next((c for c in self.calls if tuple(c["args"][:len(verb)]) == verb), None)


def _run_from_elsewhere(tmp_path: Path, hook: str, doc: dict, *, body: str = "exit 0",
                        copilot: bool = True) -> _Run:
    """Run the hook from a directory that is NOT the project — the plugin root,
    as Copilot does — with no `CLAUDE_PROJECT_DIR`: the payload's `cwd` is the
    only thing naming the project. The stub records argv, stdin and `$PWD`."""
    home = tmp_path / "home"
    elsewhere = tmp_path / "plugin-root"
    elsewhere.mkdir(parents=True, exist_ok=True)
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    log = home / "calls.log"
    (bin_dir / "loci").write_text(
        "#!/usr/bin/env bash\n"
        f'{{ for a in "$@"; do printf "%s\\036" "$a"; done; printf "\\037"; cat; '
        f'printf "\\037"; pwd -W 2>/dev/null || pwd; printf "\\035"; }} '
        f'>> "{_to_bash_path(log)}"\n'
        f"{body}\n", encoding="utf-8")
    (bin_dir / "loci").chmod(0o755)
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    env = {"PATH": f"{_to_bash_path(bin_dir)}:/usr/bin:/bin:/usr/local/bin",
           "HOME": _to_bash_path(home), "LOCI_STATE_DIR": _to_bash_path(state)}
    if copilot:
        env["COPILOT_CLI"] = "1"
    proc = subprocess.run([_find_bash(), _to_bash_path(HOOKS / hook)],
                          input=json.dumps(doc), capture_output=True, text=True,
                          timeout=60, env=env, cwd=str(elsewhere))
    assert proc.returncode == 0, f"{hook}: {proc.returncode} {proc.stderr!r}"
    assert proc.stderr == "", proc.stderr
    # In every spelling a hook could print it: native, forward-slash, MSYS.
    for spelling in (str(elsewhere), elsewhere.as_posix(), _to_bash_path(elsewhere)):
        assert spelling not in proc.stdout, proc.stdout
    return _Run(proc, log)


def _same_dir(a: str, b: Path) -> bool:
    return Path(a).resolve() == b.resolve()


def _project(tmp_path: Path, *, recipe: bool = True) -> Path:
    root = tmp_path / "proj"
    (root / ".loci" / "build" / "turns").mkdir(parents=True)
    if recipe:
        (root / ".loci" / "build.yaml").write_text("version: 1\n", encoding="utf-8")
    (root / "main.c").write_text("int f(void) { return 1; }\n", encoding="utf-8")
    return root


def test_pre_edit_names_the_project_from_the_payload_not_its_cwd(tmp_path):
    root = _project(tmp_path)
    doc = _copilot_edit(str(root / "main.c"), cwd=str(root))
    body = ("echo '{\"ok\":true,\"data\":{\"report\":\"\",\"measurable\":true,"
            "\"governed\":true,\"project_root\":\"\"}}'")
    r = _run_from_elsewhere(tmp_path, "pre-edit-hook.sh", doc, body=body)
    snap = r.call("build", "snapshot")
    assert snap is not None, r.calls
    assert f"--project-root={root}" in snap["args"], snap["args"]


def test_post_edit_nudges_about_the_project_not_its_cwd(tmp_path):
    root = _project(tmp_path, recipe=False)
    doc = _copilot_edit(str(root / "main.c"), event="PostToolUse", cwd=str(root))
    body = ("echo '{\"ok\":true,\"data\":{\"applied\":true,\"measurable\":true,"
            "\"governed\":false,\"checkout_root\":\"\"}}'")
    r = _run_from_elsewhere(tmp_path, "post-edit-hook.sh", doc, body=body)
    out = json.loads(r.out.strip())
    msg = out["hookSpecificOutput"]["additionalContext"]
    assert "LOCI is not initialized for" in msg, msg
    named = re.search(r"not initialized for (.+?); run", msg).group(1)
    assert _same_dir(named, root), named


def test_turn_clean_cleans_the_project_from_the_payloads_cwd(tmp_path):
    turn = _record(tmp_path)
    root = _project(tmp_path)
    doc = _payload("Stop", cwd=str(root), stop_hook_active=False)
    r = _run_from_elsewhere(tmp_path, "turn-clean.sh", doc)
    clean = r.call("build", "clean")
    assert clean is not None, r.calls
    assert f"--turn={turn}" in clean["args"]
    assert _same_dir(clean["pwd"], root), clean["pwd"]


def test_the_draft_nudge_asks_from_the_project_not_its_cwd(tmp_path):
    root = _project(tmp_path)
    (root / ".loci" / "build" / "contract.draft.yaml").write_text("ops: []\n", encoding="utf-8")
    doc = _payload("Stop", cwd=str(root), stop_hook_active=False)
    body = "echo '{\"ok\":true,\"data\":{\"pending\":1,\"stale\":false,\"ops\":[{\"op\":\"add\"}]}}'"
    r = _run_from_elsewhere(tmp_path, "draft-pending-nudge.sh", doc, body=body)
    show = r.call("contract", "draft", "show")
    assert show is not None, r.calls
    assert _same_dir(show["pwd"], root), show["pwd"]
    assert "contract draft not applied" in r.out


@pytest.mark.parametrize("hook,verb,doc", [
    ("prompt-submit-turn.sh", ("hook", "prompt-submit"),
     _payload("UserPromptSubmit", cwd="/proj", prompt="hi")),
    ("post-bash-bypass.sh", ("hook", "post-bash"),
     _payload("PostToolUse", cwd="/proj", tool_name="Bash",
              tool_input={"command": "echo x >> main.c", "description": "Append"},
              tool_result={"result_type": "success", "text_result_for_llm": "ok"})),
    ("manifest-status-nudge.sh", ("analyse", "status"),
     _payload("Stop", cwd="/proj", stop_hook_active=False)),
    ("stats-flush.sh", ("stats", "flush-impacts"),
     _payload("Stop", cwd="/proj", stop_hook_active=False)),
])
def test_the_thin_hooks_hand_the_verb_the_payload_that_names_the_project(tmp_path, hook,
                                                                          verb, doc):
    """These hooks resolve nothing themselves: the verb reads `cwd` off the
    payload. So the payload must reach it whole, from any working directory."""
    _record(tmp_path)
    root = _project(tmp_path)
    doc = json.loads(json.dumps(doc).replace("/proj", str(root).replace("\\", "\\\\")))
    r = _run_from_elsewhere(tmp_path, hook, doc)
    call = r.call(*verb)
    assert call is not None, r.calls
    assert json.loads(call["stdin"])["cwd"] == str(root)


def test_the_guard_denies_from_an_unrelated_cwd_on_the_payloads_cwd_alone(tmp_path):
    root = _guarded_project(tmp_path)
    doc = _copilot_edit(str(root / ".loci" / "build.yaml"), cwd=str(root))
    decision = _guard(tmp_path, doc, cwd=tmp_path / "plugin-root-dir")
    assert decision is not None and decision["permissionDecision"] == "deny", decision


def test_the_guard_denies_from_an_unrelated_cwd_on_the_env_alone(tmp_path):
    root = _guarded_project(tmp_path)
    doc = _copilot_edit(str(root / ".loci" / "build.yaml"))
    del doc["cwd"]
    decision = _guard(tmp_path, doc, cwd=tmp_path / "plugin-root-dir", project_env=root)
    assert decision is not None and decision["permissionDecision"] == "deny", decision


# ── AAD-7813: three older doors ─────────────────────────────────────────────
#
# Found by the AAD-7810 review, on `main` and under both hosts: an NTFS 8.3
# short name stopped at the prefilter, a `file_path` past the 64 KB head under
# Claude Code, and a root that is not the project the write lands in.

_NTFS = pytest.mark.skipif(os.name != "nt", reason="NTFS 8.3 short names")


def _short_project(tmp_path: Path) -> tuple[Path, dict]:
    """The guarded project and the 8.3 names Windows gave it: keyed by the long
    name, `.loci` included. Skips where the volume makes no short names."""
    root = _guarded_project(tmp_path)
    loci = root / ".loci"
    paths = [loci / "build.yaml", loci / "contract.yaml", loci / "build" / "flags.json", loci]
    out = subprocess.run("cmd /c for %I in (" + " ".join(f'"{p}"' for p in paths)
                         + ") do @echo %~snxI", capture_output=True, text=True, timeout=60)
    names = out.stdout.split()
    if len(names) != len(paths) or any(n.lower() == p.name.lower() for n, p in zip(names, paths)):
        pytest.skip("8.3 short names are off on this volume")
    return root, dict(zip(("build.yaml", "contract.yaml", "flags.json", ".loci"), names))


_SHORT_SPELLINGS = [
    ("{.loci}\\{build.yaml}", "REASON_RECIPE"),
    (".loci\\{build.yaml}", "REASON_RECIPE"),
    (".loci\\{contract.yaml}", "REASON_FILE"),
    (".loci\\build\\{flags.json}", "REASON_FLAGS"),
    ("{.loci}\\build\\{flags.json}", "REASON_FLAGS"),
    (".loci\\.\\{build.yaml}", "REASON_RECIPE"),
]


def _short(template: str, names: dict) -> str:
    for long, short in names.items():
        template = template.replace("{" + long + "}", short)
    return template


@_NTFS
@pytest.mark.parametrize("template,reason", _SHORT_SPELLINGS)
@pytest.mark.parametrize("absolute", [False, True], ids=["relative", "absolute"])
@pytest.mark.parametrize("copilot", [True, False], ids=["copilot", "claude"])
@pytest.mark.parametrize("env", ["project", "elsewhere"])
def test_an_8_3_short_name_is_denied(tmp_path, template, reason, absolute, copilot, env):
    """Item 1: `.loci\\BUILD~1.YAM` opens the recipe and carries no guarded
    name, so it exited ALLOW at the prefilter. The content says nothing a
    prefilter arm reads, so the new arm is what admits it; with the root
    `elsewhere`, the walk's `-ef` against the sibling is what decides it."""
    root, names = _short_project(tmp_path)
    path = _short(template, names)
    if absolute:
        path = f"{root}\\{path}"
    project = root if env == "project" else tmp_path / "elsewhere"
    project.mkdir(exist_ok=True)
    if copilot:
        doc = _copilot_write(path, cwd=str(root), text="x")
        decision = _guard(tmp_path, doc, cwd=PLUGIN_ROOT, project_env=project)
    else:
        doc = _payload("PreToolUse", cwd=str(root), tool_name="Write",
                       tool_input={"file_path": path, "content": "x"})
        decision = _guard(tmp_path, doc, copilot=False, cwd=root, project_env=project)
    assert decision is not None and decision["permissionDecision"] == "deny", (path, decision)
    assert decision["permissionDecisionReason"].startswith(_reason(reason)), decision


@_NTFS
@pytest.mark.parametrize("relative", [False, True], ids=["absolute", "relative"])
def test_an_8_3_short_name_in_a_patch_header_is_denied(tmp_path, relative):
    root, names = _short_project(tmp_path)
    path = f".loci\\{names['build.yaml']}"
    if not relative:
        path = f"{root}\\{path}"
    decision = _guard(tmp_path, _patch_edit(path, cwd=str(root)), cwd=PLUGIN_ROOT)
    assert decision is not None and decision["permissionDecision"] == "deny", decision
    assert decision["permissionDecisionReason"].startswith(_reason("REASON_RECIPE")), decision


@pytest.mark.parametrize("path", [
    ".loci\\NOTES~1.TXT",
    "src\\MAIN~1.C",
    "C:\\Users\\RUNNER~1\\AppData\\Local\\Temp\\a.c",
    ".loci\\build\\CACHE~1",
])
@pytest.mark.parametrize("copilot", [True, False], ids=["copilot", "claude"])
def test_a_tilde_name_that_is_no_guarded_file_is_still_allowed(tmp_path, path, copilot):
    root = _guarded_project(tmp_path)
    if copilot:
        decision = _guard(tmp_path, _copilot_write(path, cwd=str(root)), cwd=PLUGIN_ROOT,
                          project_env=root)
    else:
        doc = _payload("PreToolUse", cwd=str(root), tool_name="Write",
                       tool_input={"file_path": path, "content": "x"})
        decision = _guard(tmp_path, doc, copilot=False, cwd=root, project_env=root)
    assert decision is None, (path, decision)


def _verdict(tmp_path: Path) -> str:
    log = (tmp_path / "state" / "loci.log").read_text(encoding="utf-8", errors="replace")
    ends = [ln for ln in log.splitlines() if "contract-guard" in ln and "end: verdict=" in ln]
    assert ends, log
    return ends[-1]


@pytest.mark.parametrize("content", [
    "x = ~0; y = ~1u; z &= ~1; w = ~0x10;\n" * 2000,
    "int a = ~1 ;\n",
], ids=["64KB-of-C", "one-line"])
def test_a_tilde_in_code_stays_at_the_prefilter(tmp_path, content):
    """The arm reads the SHAPE of an 8.3 tail — `~`, a digit 1-9, then a digit,
    a dot, a separator or the value's end — so C's `~0`, `~1u` and `~1;` pay
    nothing."""
    root = _guarded_project(tmp_path)
    doc = _payload("PreToolUse", cwd=str(root), tool_name="Write",
                   tool_input={"file_path": "src/main.c", "content": content})
    assert _guard(tmp_path, doc, copilot=False, cwd=root, project_env=root,
                  extra_env={"LOCI_HOOK_LOG": "1"}) is None
    assert "prefilter: no guarded token" in _verdict(tmp_path)


@pytest.mark.parametrize("rel,reason", [
    (".loci/build.yaml", "REASON_RECIPE"),
    (".loci/contract.yaml", "REASON_FILE"),
    (".loci/build/flags.json", "REASON_FLAGS"),
])
def test_claude_codes_late_file_path_is_refused_as_unread(tmp_path, rel, reason):
    """Item 2, the Claude Code shape: `content` first, then `file_path`. The
    head held no `file_path`, route 1 never ran, and the write was allowed —
    the refusal was Copilot's alone."""
    root = _guarded_project(tmp_path)
    doc = _payload("PreToolUse", cwd=str(root), tool_name="Write",
                   tool_input={"content": "x" * 70000, "file_path": str(root / rel)})
    decision = _guard(tmp_path, doc, copilot=False, cwd=root, project_env=root)
    want = "REASON_UNREAD" if _legacy_bash() else "REASON_PATH_UNREAD"
    assert decision is not None and decision["permissionDecisionReason"].startswith(
        _reason(want)), decision


def test_a_content_first_write_is_refused_only_when_admitted_and_that_is_deliberate(tmp_path):
    """The price of the rule above: a content-first write longer than the head
    is refused when the prefilter admits it (here, the content says
    `contract`), whatever file it names. The models send the path first; the
    reason asks for it. One that names no guarded token stays at the
    prefilter, as before."""
    root = _guarded_project(tmp_path)
    for text, denied in (("x" * 70000, False), ("the contract says " * 4000, True)):
        doc = _payload("PreToolUse", cwd=str(root), tool_name="Write",
                       tool_input={"content": text, "file_path": str(root / "src" / "a.c")})
        decision = _guard(tmp_path, doc, copilot=False, cwd=root, project_env=root)
        assert (decision is not None) is denied, decision


@pytest.mark.parametrize("copilot", [True, False], ids=["copilot", "claude"])
def test_a_second_file_path_past_the_head_is_a_duplicate(tmp_path, copilot):
    """The field read takes the FIRST `file_path` and the tool the last, so a
    harmless one in front and the recipe behind 70 KB of content read as the
    harmless one. The duplicate screen reads the whole text of a cut payload."""
    root = _guarded_project(tmp_path)
    body = ('"file_path":%s,"content":"%s","file_path":%s' % (
        json.dumps(str(root / "main.c")), "the contract " * 6000,
        json.dumps(str(root / ".loci" / "build.yaml"))))
    doc = ('{"hook_event_name":"PreToolUse","session_id":"s1","cwd":%s,'
           '"tool_name":"Write","tool_input":{%s}}' % (json.dumps(str(root)), body))
    decision = _guard(tmp_path, doc, copilot=copilot, cwd=PLUGIN_ROOT if copilot else root,
                      project_env=root)
    assert decision is not None and decision["permissionDecision"] == "deny", decision
    assert "'file_path' twice" in decision["permissionDecisionReason"], decision


_SUBDIR_SPELLINGS = [
    ("../.loci/./build.yaml", "REASON_RECIPE"),
    ("../.loci/./contract.yaml", "REASON_FILE"),
    ("../.loci/build/./flags.json", "REASON_FLAGS"),
    ("..\\.loci\\.\\build.yaml", "REASON_RECIPE"),
    ("../src/../.loci//build.yaml", "REASON_RECIPE"),
]


@pytest.mark.parametrize("rel,reason", _SUBDIR_SPELLINGS)
@pytest.mark.parametrize("copilot", [True, False], ids=["copilot", "claude"])
@pytest.mark.parametrize("present", [True, False], ids=["present", "absent"])
def test_a_non_git_session_in_a_subdirectory_still_denies(tmp_path, rel, reason, copilot, present):
    """Item 3: no `CLAUDE_PROJECT_DIR` and no git, so the root falls back to
    the session's `cwd` — here `<proj>/src`. Walked against that root, these
    named no guarded file and were allowed. Absent = the Write that creates
    one, which only the walk's text can decide."""
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    if present:
        root = _guarded_project(tmp_path)
        (root / "src").mkdir(exist_ok=True)
    else:
        (root / ".loci" / "build").mkdir(parents=True)
    cwd = root / "src"
    if copilot:
        decision = _guard(tmp_path, _copilot_write(rel, cwd=str(cwd)), cwd=PLUGIN_ROOT)
    else:
        doc = _payload("PreToolUse", cwd=str(cwd), tool_name="Write",
                       tool_input={"file_path": rel, "content": "x"})
        decision = _guard(tmp_path, doc, copilot=False, cwd=cwd)
    assert decision is not None and decision["permissionDecision"] == "deny", decision
    assert decision["permissionDecisionReason"].startswith(_reason(reason)), decision


@pytest.mark.parametrize("rel,reason", [
    ("fw/.loci/./build.yaml", "REASON_RECIPE"),
    ("fw/src/../.loci/./contract.yaml", "REASON_FILE"),
    ("fw/.loci/build/./flags.json", "REASON_FLAGS"),
    # A subproject that does not exist yet: the whole of it is tail text.
    ("fw2/.loci/./build.yaml", "REASON_RECIPE"),
])
@pytest.mark.parametrize("copilot", [True, False], ids=["copilot", "claude"])
def test_a_subprojects_guarded_file_is_denied_when_the_env_names_the_repository(
        tmp_path, rel, reason, copilot):
    """Found while reproducing item 3: the same door WITH `CLAUDE_PROJECT_DIR`
    set, when it names a repository whose `.loci/` lives in a subproject —
    the root is `mono`, the recipe is `mono/fw`'s, and the walk compared
    against the root alone. Allowed on `main` under both hosts."""
    mono = tmp_path / "mono"
    (mono / "fw" / ".loci" / "build").mkdir(parents=True)
    (mono / "fw" / "src").mkdir()
    (mono / "fw" / ".loci" / "build.yaml").write_text("x\n", encoding="utf-8")
    if copilot:
        decision = _guard(tmp_path, _copilot_write(rel, cwd=str(mono)), cwd=PLUGIN_ROOT,
                          project_env=mono)
    else:
        doc = _payload("PreToolUse", cwd=str(mono), tool_name="Write",
                       tool_input={"file_path": rel, "content": "x"})
        decision = _guard(tmp_path, doc, copilot=False, cwd=mono, project_env=mono)
    assert decision is not None and decision["permissionDecision"] == "deny", decision
    assert decision["permissionDecisionReason"].startswith(_reason(reason)), decision


@pytest.mark.parametrize("name", [
    "a" * 60000 + "~1",
    ".loci/" + "a" * 60000 + "~1.yam",
    "/".join(["b" * 59] * 1000) + "/x~1",
    # Admitted by the BASENAME gate, so it reaches `_gm_names` with a 60 KB
    # tail: the shape the 9.5 s draft paid for.
    "a" * 60000 + "build.yaml",
    ".loci/" + "a" * 60000 + "contract.yaml",
], ids=["one-60KB-name", "60KB-name-in-loci", "1000-names", "60KB-basename", "60KB-basename-in-loci"])
def test_a_long_last_name_is_decided_inside_the_hook_budget(tmp_path, name):
    """The 8.3 gate and `_gm_names` read a LAST component, and the first draft
    did it with `${t##*/}` — 0.77 s per call on a 60 KB name, seven calls on
    one path: 9.5 s, past the 5 s kill, which fails open. Measured 0.16-0.37 s
    with the linear reads; 2.5 s is half the budget. The `basename` rows are
    the ones that reach `_gm_names` (the 8.3 gate refuses a name over 12
    characters), and `${t##*/}` put back there takes them to 5 s."""
    root = _guarded_project(tmp_path)
    doc = _payload("PreToolUse", cwd=str(root), tool_name="Write",
                   tool_input={"file_path": f"{root}/{name}", "content": "x"})
    start = time.monotonic()
    decision = _guard(tmp_path, doc, copilot=False, cwd=root, project_env=root)
    elapsed = time.monotonic() - start
    if not _legacy_bash():
        assert decision is None, decision
    assert elapsed < 2.5, f"{elapsed:.2f} s for a long name ending in an 8.3 tail"


@pytest.mark.parametrize("rel", [
    "fw/.loci/./notes.txt",
    "fw/.loci2/./build.yaml",
    "fw/src/./build.yaml",
    "fw/.loci/build/./flags.json.bak",
    "fw/.loci/x/../contract.yml",
])
def test_a_neighbour_of_a_subprojects_guarded_file_is_still_allowed(tmp_path, rel):
    mono = tmp_path / "mono"
    (mono / "fw" / ".loci" / "build").mkdir(parents=True)
    (mono / "fw" / ".loci2").mkdir()
    (mono / "fw" / "src").mkdir()
    doc = _payload("PreToolUse", cwd=str(mono), tool_name="Write",
                   tool_input={"file_path": rel, "content": "the contract"})
    assert _guard(tmp_path, doc, copilot=False, cwd=mono, project_env=mono) is None, rel


# The AAD-7813 review's findings: a namespace prefix in front of the path, a
# `.loci` that is itself a link, and whitespace before a late key's colon.

_BS = chr(92)


def _prefixed(root: Path, prefix: str, rel: str) -> str:
    drive, rest = str(root)[0], str(root)[3:]
    if prefix == "volume":
        # `\\?\Volume{GUID}\` — the volume the project is on, by its GUID.
        vol = subprocess.run(["mountvol", f"{drive}:{_BS}", "/L"], capture_output=True,
                             text=True, timeout=30).stdout.strip().rstrip(_BS)
        if not vol.startswith(f"{_BS * 2}?{_BS}Volume"):
            pytest.skip("mountvol named no volume for this drive")
        return f"{vol}{_BS}{rest}{_BS}{rel}"
    if prefix == "nt-device":
        # `\\.\GLOBALROOT\Device\HarddiskVolumeN\` — the drive's NT device.
        import ctypes
        buf = ctypes.create_unicode_buffer(1024)
        if not ctypes.windll.kernel32.QueryDosDeviceW(f"{drive}:", buf, 1024):
            pytest.skip("QueryDosDevice named no device for this drive")
        return f"{_BS * 2}.{_BS}GLOBALROOT{buf.value}{_BS}{rest}{_BS}{rel}"
    head = {
        "long": f"{_BS * 2}?{_BS}{root}",
        "device": f"{_BS * 2}.{_BS}{root}",
        "nt": f"{_BS}??{_BS}{root}",
        "unc-long": f"{_BS * 2}?{_BS}UNC{_BS}localhost{_BS}{drive}${_BS}{rest}",
        "globalroot": f"{_BS * 2}?{_BS}GLOBALROOT{_BS}??{_BS}{root}",
        "admin-share": f"{_BS * 2}127.0.0.1{_BS}{drive.lower()}${_BS}{rest}",
    }[prefix]
    return head + _BS + rel


_PREFIXES = ["long", "device", "nt", "unc-long", "globalroot", "admin-share", "volume", "nt-device"]


@_WINDOWS_ONLY
@pytest.mark.parametrize("prefix", _PREFIXES)
@pytest.mark.parametrize("rel,reason", [
    (".loci\\.\\build.yaml", "REASON_RECIPE"),
    (".loci\\build\\.\\flags.json", "REASON_FLAGS"),
    ("src\\..\\.loci\\.\\contract.yaml", "REASON_FILE"),
    # A link `J` to `.loci`: the only rows here no TEXT reading can decide, so
    # the ones that fail without the prefix fix (the walk has to stand in J).
    ("J\\.\\build.yaml", "REASON_RECIPE"),
    ("J\\build\\.\\flags.json", "REASON_FLAGS"),
])
@pytest.mark.parametrize("copilot", [True, False], ids=["copilot", "claude"])
def test_a_namespace_prefix_names_the_file_behind_it(tmp_path, prefix, rel, reason, copilot):
    """Review finding 1 (on `main` too): `\\\\?\\C:\\…`, `\\\\.\\`, `\\??\\`,
    `\\\\?\\UNC\\localhost\\C$\\`, `\\\\?\\GLOBALROOT\\??\\`, an admin share,
    and (round 2) `\\\\?\\Volume{GUID}\\` and `\\\\.\\GLOBALROOT\\Device\\
    HarddiskVolumeN\\` each open the file behind them — Node writes through all
    of them — and the `//` collapse turned every one into text the walk could
    not stand in. The root is elsewhere, so only the walk's own reading can
    decide."""
    root = _guarded_project(tmp_path)
    (root / "src").mkdir(exist_ok=True)
    link = root / "J"
    if rel.startswith("J"):
        _link_dir(link, root / ".loci")
    path = _prefixed(root, prefix, rel)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    try:
        if copilot:
            decision = _guard(tmp_path, _copilot_write(path, cwd=str(root)), cwd=PLUGIN_ROOT,
                              project_env=elsewhere)
        else:
            doc = _payload("PreToolUse", cwd=str(root), tool_name="Write",
                           tool_input={"file_path": path, "content": "x"})
            decision = _guard(tmp_path, doc, copilot=False, cwd=root, project_env=elsewhere)
    finally:
        if link.is_dir():
            subprocess.run(["cmd", "/c", "rmdir", str(link)], capture_output=True, timeout=30)
    assert decision is not None and decision["permissionDecision"] == "deny", (path, decision)
    assert decision["permissionDecisionReason"].startswith(_reason(reason)), decision


@_NTFS
@pytest.mark.parametrize("prefix", ["long", "unc-long", "admin-share", "volume", "nt-device"])
def test_a_namespace_prefix_does_not_hide_an_8_3_name(tmp_path, prefix):
    root, names = _short_project(tmp_path)
    path = _prefixed(root, prefix, f"{names['.loci']}\\{names['build.yaml']}")
    doc = _payload("PreToolUse", cwd=str(root), tool_name="Write",
                   tool_input={"file_path": path, "content": "x"})
    decision = _guard(tmp_path, doc, copilot=False, cwd=root, project_env=root)
    assert decision is not None and decision["permissionDecisionReason"].startswith(
        _reason("REASON_RECIPE")), (path, decision)


@_WINDOWS_ONLY
@pytest.mark.parametrize("path", [
    "{long}\\src\\main.c",
    "\\\\server\\share\\.loci\\notes.txt",
    "\\\\server\\share\\proj\\.loci\\.\\build.yml",
])
def test_a_prefixed_ordinary_file_is_still_allowed(tmp_path, path):
    root = _guarded_project(tmp_path)
    path = path.replace("{long}", f"{_BS * 2}?{_BS}{root}")
    doc = _payload("PreToolUse", cwd=str(root), tool_name="Write",
                   tool_input={"file_path": path, "content": "the contract"})
    assert _guard(tmp_path, doc, copilot=False, cwd=root, project_env=root) is None, path


def _link_dir(link: Path, target: Path) -> None:
    """A directory link: a junction on Windows (no privilege needed), else a
    symlink. Skips where the host can make neither."""
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                       capture_output=True, text=True, timeout=30)
    else:
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            pass
    if not link.is_dir():
        pytest.skip("this host can make no directory link")


@pytest.fixture
def linked_loci(tmp_path):
    """`proj2/.loci` is a link to `store/p2data`, a directory of another name,
    holding the three guarded files. Unlinked on the way out, so the tmp tree
    never carries a link for a later `rmtree` to follow."""
    proj = tmp_path / "proj2"
    (proj / "src").mkdir(parents=True)
    store = tmp_path / "store" / "p2data"
    (store / "build").mkdir(parents=True)
    for rel in ("contract.yaml", "build.yaml", "build/flags.json"):
        (store / rel).write_text("x\n", encoding="utf-8")
    link = proj / ".loci"
    _link_dir(link, store)
    yield proj
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "rmdir", str(link)], capture_output=True, timeout=30)
    else:
        link.unlink()


@pytest.mark.parametrize("rel,reason", [
    ("../.loci/./build.yaml", "REASON_RECIPE"),
    ("../.loci/./contract.yaml", "REASON_FILE"),
    ("../.loci/build/./flags.json", "REASON_FLAGS"),
])
@pytest.mark.parametrize("copilot", [True, False], ids=["copilot", "claude"])
@pytest.mark.parametrize("env", ["none", "elsewhere"])
def test_a_loci_that_is_a_link_keeps_its_name(tmp_path, linked_loci, rel, reason, copilot, env):
    """Review finding 2: the walk's directory comes from `cd -P`, so a `.loci`
    linked to `store/p2data` was `store/p2data`, whose parent has no `.loci`
    to be identical with — every one of these was ALLOWED. `_gm_names` is
    asked of the spelled directory too, which keeps the name."""
    cwd = linked_loci / "src"
    project = None
    if env == "elsewhere":
        project = tmp_path / "elsewhere"
        project.mkdir()
    if copilot:
        decision = _guard(tmp_path, _copilot_write(rel, cwd=str(cwd)), cwd=PLUGIN_ROOT,
                          project_env=project)
    else:
        doc = _payload("PreToolUse", cwd=str(cwd), tool_name="Write",
                       tool_input={"file_path": rel, "content": "x"})
        decision = _guard(tmp_path, doc, copilot=False, cwd=cwd, project_env=project)
    assert decision is not None and decision["permissionDecision"] == "deny", decision
    assert decision["permissionDecisionReason"].startswith(_reason(reason)), decision


def test_a_neighbour_inside_a_linked_loci_is_still_allowed(tmp_path, linked_loci):
    doc = _payload("PreToolUse", cwd=str(linked_loci), tool_name="Write",
                   tool_input={"file_path": ".loci/./notes.txt", "content": "the contract"})
    assert _guard(tmp_path, doc, copilot=False, cwd=linked_loci, project_env=linked_loci) is None


@pytest.mark.parametrize("ws", ["\t", "\n", "  ", " "], ids=["tab", "newline", "two-spaces", "space"])
@pytest.mark.parametrize("shape", ["late", "duplicate", "copilot-pair"])
def test_any_json_whitespace_before_the_colon_is_still_a_key(tmp_path, ws, shape):
    """Review finding 3: the whole-text key globs read a colon or ONE space
    after the name. JSON allows any whitespace there and the field reader
    accepts it; no host serialiser writes it, but the globs now read it too."""
    root = _guarded_project(tmp_path)
    recipe = json.dumps(str(root / ".loci" / "build.yaml"))
    big = "the contract " * 6000
    key = "path" if shape == "copilot-pair" else "file_path"
    text = "file_text" if shape == "copilot-pair" else "content"
    head = "" if shape == "late" else f'"{key}":"src/a.c",'
    doc = ('{"hook_event_name":"PreToolUse","session_id":"s1","cwd":%s,"tool_name":"Write",'
           '"tool_input":{%s"%s":"%s","%s"%s:%s}}'
           % (json.dumps(str(root)), head, text, big, key, ws, recipe))
    copilot = shape == "copilot-pair"
    decision = _guard(tmp_path, doc, copilot=copilot, cwd=PLUGIN_ROOT if copilot else root,
                      project_env=root)
    assert decision is not None and decision["permissionDecision"] == "deny", decision


# ── the wiring ───────────────────────────────────────────────────────────────

def test_the_guard_adapts_above_its_prefilter():
    """The prefilter's arms name `file_path`; the respelling has to come first,
    and the guard's rule that nothing forks above the prefilter still holds
    (the adapter's reads are parameter expansions)."""
    body = GUARD_SRC
    adapt = body.index("loci_host_adapt \"$payload\"")
    prefilter = body.index("case \"$payload\" in")
    assert adapt < prefilter
    assert body.index("payload=$(cat)") < adapt
