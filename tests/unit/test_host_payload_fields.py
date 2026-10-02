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
import re
import subprocess
import time
from pathlib import Path

import pytest

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

def _guard(tmp_path: Path, doc: dict, *, copilot: bool = True, cwd: Path | None = None,
           project_env: Path | None = None) -> dict | None:
    """Run the guard on one payload; its decision, or None when allowed."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": _to_bash_path(home),
           "LOCI_STATE_DIR": _to_bash_path(tmp_path / "state")}
    if project_env is not None:
        env["CLAUDE_PROJECT_DIR"] = _to_bash_path(project_env)
    if copilot:
        env["COPILOT_CLI"] = "1"
    run_in = cwd if cwd is not None else tmp_path
    run_in.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run([_find_bash(), _to_bash_path(HOOKS / "contract-guard.sh")],
                          input=json.dumps(doc), capture_output=True, text=True,
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
    past the cap the patch is refused as unread."""
    root = _guarded_project(tmp_path)
    guarded = str(root / ".loci" / "contract.yaml").replace("/", "\\")
    hunk = "".join(f"+// line {i} of a long generated file\n" for i in range(700))   # ~26 KB
    doc = _payload("PreToolUse", cwd=str(root), tool_name="Edit",
                   tool_input=f"*** Begin Patch\n*** Update File: {root}\\main.c\n@@\n{hunk}"
                              f"*** Update File: {guarded}\n@@\n+x\n*** End Patch\n")
    decision = _guard(tmp_path, doc, project_env=root)
    assert decision is not None and decision["permissionDecision"] == "deny", decision
    assert decision["permissionDecisionReason"].startswith(_reason("REASON_FILE")), decision
    hunk = hunk * 3                                                                   # ~78 KB
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
    _run_hook(tmp_path, hook, doc, copilot=(shape == "copilot"),
              body="echo '{\"ok\":true,\"data\":{}}'", project=root,
              env_extra={"LOCI_CLI_INSTALL": "skip"})


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
