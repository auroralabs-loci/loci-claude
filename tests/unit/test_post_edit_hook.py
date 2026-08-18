"""The post-edit hook must ask for the analysis on a real code change.

This hook is the only harness-enforced reminder that `loci-post-edit` should run;
the skill's frontmatter says MANDATORY, but prose in a description can be forgotten
and a `PostToolUse` hook cannot. Until now it had **no tests at all**, and that is
how the following shipped:

    the Edit tool passes `tool_input.new_string`, a FRAGMENT of the file, and the
    classifier judged it by the whole-file rule "no `{` means no function body".
    A one-line change inside an existing function carries no brace, so the
    commonest edit an agent makes was classified unmeasurable and the hook exited
    silently. No reminder, no analysis, no report.

The fix classifies the APPLIED edit's diff — `tool_response.structuredPatch` —
with the `-`/`+` markers KEPT, so the classifier can compare the two sides. These
tests assert what the hook *sends* (which bytes, under which `--content-kind`)
rather than trusting a stub's answer, because the send is the hook's whole job. The
classification rules are pinned CLI-side in `loci-cli`'s `test_scan_snapshot.py`.

Two invariants carry most of the weight:

* **Always exit 0.** A non-zero exit from a `PostToolUse` hook surfaces to the
  model as a tool failure.
* **Fail open when the CLI cannot answer.** A `loci` predating `--content-kind`
  exits 2 from argparse. Re-asking it without the flag would re-apply the
  whole-file brace rule to a brace-less diff, answer "no", and silently re-run the
  exact bug this fixes — so an unparseable flag must produce the reminder, not a
  second question. Under-triggering loses a measurement silently; over-triggering
  costs one wasted analysis.
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
HOOK = PLUGIN_ROOT / "hooks" / "post-edit-hook.sh"

# ASCII record separator — delimits one captured stdin from the next.
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
    """A PATH the hook can actually work on.

    The hook's first act is `command -v jq || exit 0`, so a PATH without jq makes
    every one of these tests pass vacuously — the hook exits before doing anything
    and an assertion about silence is met for the wrong reason. jq is not in
    /usr/bin on a Windows checkout (chocolatey, scoop and winget all put it
    elsewhere), so resolve its real directory rather than assuming one."""
    base = "/usr/bin:/bin:/usr/local/bin"
    jq = shutil.which("jq")
    if jq:
        base = f"{_to_bash_path(Path(jq).parent)}:{base}"
    return base


class Result:
    def __init__(self, proc, calls: list[str], stdin_bytes: list[bytes]):
        self.code = proc.returncode
        self.out = proc.stdout
        self.stderr = proc.stderr
        self.calls = calls              # argv of each `loci` invocation, joined
        self.stdin_bytes = stdin_bytes  # raw bytes piped to each invocation

    @property
    def context(self) -> str | None:
        """The additionalContext the hook emitted, or None if it stayed silent."""
        if not self.out.strip():
            return None
        return json.loads(self.out)["hookSpecificOutput"]["additionalContext"]

    def stdin(self, i: int = 0) -> str:
        """Invocation `i`'s stdin, CR-normalised for convenience. Use
        `stdin_bytes` when the exact bytes matter."""
        return self.stdin_bytes[i].decode("utf-8").replace("\r\n", "\n")

    def flag(self, i: int = 0) -> str | None:
        m = re.search(r"--content-kind (\S+)", self.calls[i])
        return m.group(1) if m else None


def _run(home: Path, payload: dict, *, stub: str,
         drop_home: bool = False, expect_zero: bool = True) -> Result:
    """Run the hook against a stubbed `loci`.

    The hook prepends `$HOME/.local/bin` to PATH (where `uv tool install` puts the
    real CLI), so the stub goes there — pointing HOME at a tmp dir also keeps the
    developer's own installed `loci` from shadowing it and making the test
    silently exercise a different binary."""
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    args_log = home / "args.log"
    stdin_log = home / "stdin.log"

    (bin_dir / "loci").write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> "{_to_bash_path(args_log)}"\n'
        f'{{ cat; printf "{_RS}"; }} >> "{_to_bash_path(stdin_log)}"\n'
        f"{stub}\n",
        encoding="utf-8",
    )
    (bin_dir / "loci").chmod(0o755)

    env = {
        "PATH": f"{_to_bash_path(bin_dir)}:{_base_path()}",
        "CLAUDE_PROJECT_DIR": _to_bash_path(home),
    }
    if not drop_home:
        env["HOME"] = _to_bash_path(home)

    proc = subprocess.run(
        [_find_bash(), _to_bash_path(HOOK)],
        input=json.dumps(payload), capture_output=True, text=True, timeout=30,
        env=env,
    )
    if expect_zero:
        assert proc.returncode == 0, (
            "the post-edit hook is advisory and must always exit 0; "
            f"got {proc.returncode}. stderr={proc.stderr!r}"
        )
    calls = args_log.read_text(encoding="utf-8").splitlines() if args_log.is_file() else []
    raw = stdin_log.read_bytes() if stdin_log.is_file() else b""
    stdins = raw.split(_RS.encode())[:-1] if raw else []
    return Result(proc, calls, stdins)


# Stub behaviours.
_MEASURABLE = "echo '{\"ok\":true,\"data\":{\"measurable\":true}}'"
_NOT_MEASURABLE = "echo '{\"ok\":true,\"data\":{\"measurable\":false}}'"
# A `loci` predating --content-kind: argparse writes usage to stderr and exits 2.
_OLD_CLI = (
    'if [[ "$*" == *--content-kind* ]]; then\n'
    '  echo "loci: error: unrecognized arguments: --content-kind" >&2; exit 2\n'
    "fi\n" + _MEASURABLE
)

# The patch a real session produced for a one-line change inside a function.
# Context lines are space-prefixed; only the -/+ pair is the change.
_ONE_LINE_PATCH = [
    " uint32_t compute(uint32_t n) {",
    "     uint32_t acc = 0;",
    "     for (uint32_t i = 0; i < n; i++) {",
    "-        acc += i * 3;",
    "+        acc += i * 7;",
    "     }",
    "     return acc;",
    " }",
]


def _edit(path: str, *, patch: list[str] | None = None, new_string: str = "x",
          response: dict | None = None) -> dict:
    payload: dict = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Edit",
        "tool_input": {"file_path": path, "old_string": "y", "new_string": new_string},
    }
    if response is not None:
        payload["tool_response"] = response
    elif patch is not None:
        payload["tool_response"] = {
            "filePath": path,
            "structuredPatch": [{"oldStart": 3, "newStart": 3, "lines": patch}],
        }
    return payload


def _write(path: str, content: str = "int g(void){ return 1; }",
           patch: list[str] | None = None) -> dict:
    return {
        "hook_event_name": "PostToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": path, "content": content},
        "tool_response": {
            "type": "create" if patch is None else "update",
            "filePath": path,
            "content": content,
            # A Write to a NEW file really does report an empty patch.
            "structuredPatch": [] if patch is None
                               else [{"oldStart": 1, "newStart": 1, "lines": patch}],
        },
    }


# ── the regression ───────────────────────────────────────────────────────────

def test_a_one_line_body_edit_sends_the_marked_changed_lines(tmp_path):
    """THE regression. Only the -/+ lines, markers KEPT, under `--content-kind
    diff`."""
    r = _run(tmp_path, _edit("/p/blink.c", patch=_ONE_LINE_PATCH), stub=_MEASURABLE)

    assert r.flag(0) == "diff", f"expected --content-kind diff, got {r.calls[0]!r}"
    assert r.stdin().splitlines() == [
        "-        acc += i * 3;",
        "+        acc += i * 7;",
    ], f"got {r.stdin()!r}"
    # Context lines carry the braces. Letting them through would make the old
    # whole-file brace rule accidentally pass and hide the real bug again.
    assert "{" not in r.stdin()
    assert "uint32_t compute" not in r.stdin()
    assert r.context is not None and "loci-post-edit" in r.context


def test_the_markers_survive_as_raw_bytes(tmp_path):
    """Asserted on bytes, not text. `read_text`, `.splitlines()` and `.rstrip()`
    each normalise line endings away, so a text-only assertion is blind to payload
    corruption — and a native-Windows jq really does write CRLF here. The markers
    and the line content must arrive intact and unmerged; the line terminator is a
    platform wart, so it is normalised rather than asserted."""
    r = _run(tmp_path, _edit("/p/blink.c", patch=_ONE_LINE_PATCH), stub=_MEASURABLE)
    raw = r.stdin_bytes[0]
    assert b"-        acc += i * 3;" in raw
    assert b"+        acc += i * 7;" in raw
    # Not merged into one line, whatever the terminator is.
    assert re.search(rb"\* 3;\r?\n\+", raw), f"lines merged or reordered: {raw!r}"


def test_the_reminder_names_the_file_and_forbids_deferring(tmp_path):
    r = _run(tmp_path, _edit("/p/blink.c", patch=_ONE_LINE_PATCH), stub=_MEASURABLE)
    ctx = r.context
    assert "blink.c" in ctx
    assert "MUST invoke" in ctx
    # The preflight carve-out must survive: a preflight-driven edit reports itself.
    assert "loci-preflight" in ctx


def test_no_reminder_when_the_classifier_says_unmeasurable(tmp_path):
    """The gate must still gate — the half that must not regress while fixing the
    false negative."""
    r = _run(tmp_path, _edit("/p/blink.c", patch=_ONE_LINE_PATCH), stub=_NOT_MEASURABLE)
    assert r.context is None
    assert r.out.strip() == ""
    assert len(r.calls) == 1, "an unmeasurable verdict must not be re-asked"


# ── fail open, and only one question ────────────────────────────────────────

def test_an_older_cli_that_rejects_the_flag_makes_the_hook_fail_open(tmp_path):
    """A flagless retry is NOT a fallback: the CLI would default to whole-file
    mode, re-apply the brace rule to a brace-less diff, answer "no", and the
    reminder would vanish — the pre-fix bug, restored. Verified against the real
    0.1.102: the retry returns `measurable:false, reason:"no function body"`. So
    an unsupported flag must remind, and must not ask again."""
    r = _run(tmp_path, _edit("/p/blink.c", patch=_ONE_LINE_PATCH), stub=_OLD_CLI)

    assert r.context is not None, "an old CLI must not cost us the reminder"
    assert len(r.calls) == 1, (
        f"exit 2 means the question cannot be answered, not that it should be "
        f"re-asked; calls={r.calls!r}"
    )


@pytest.mark.parametrize("stub,label", [
    ("exit 1", "CLI failed"),
    ("exit 127", "CLI not runnable"),
    ("echo 'not json at all'", "unparseable stdout"),
    ("echo '{\"ok\":false}'", "error envelope, no data"),
    ("echo ''", "silence"),
])
def test_a_broken_cli_is_silent_and_never_fails_the_tool_call(tmp_path, stub, label):
    """Distinct from the unsupported-flag case: if `loci` is broken the SKILL could
    not run either, so a reminder would only produce a failed invocation. Exit 2
    means "I cannot parse your question"; anything else means "I am broken"."""
    r = _run(tmp_path, _edit("/p/blink.c", patch=_ONE_LINE_PATCH), stub=stub)
    assert r.code == 0
    assert r.context is None, label


# ── the fallbacks, both load-bearing ────────────────────────────────────────

def test_a_write_that_creates_a_file_is_classified_as_a_whole_file(tmp_path):
    """A create reports `structuredPatch: []`, so the patch path yields nothing and
    the hook falls back to the full content under `file` — where the brace rule is
    correct and still wanted."""
    r = _run(tmp_path, _write("/p/newfile.c"), stub=_MEASURABLE)
    assert r.flag(0) == "file"
    assert r.stdin().rstrip("\n") == "int g(void){ return 1; }"


def test_a_write_that_overwrites_an_existing_file_takes_the_diff_path(tmp_path):
    """Verified against a live session: a Write over an existing file reports
    `type: "update"` with a NON-empty patch. So `file` mode is reachable only for a
    create, and almost all traffic is diffs — which is why leaving whole-file rules
    applied to a diff mattered."""
    r = _run(tmp_path, _write("/p/over.c", patch=[
        "-int f(int x){ return x + 1; }",
        "+int f(int x){ return x + 5; }",
        r"\ No newline at end of file",
    ]), stub=_MEASURABLE)
    assert r.flag(0) == "diff"
    assert r.stdin().splitlines() == [
        "-int f(int x){ return x + 1; }",
        "+int f(int x){ return x + 5; }",
    ], "the \\ No newline note is not a change and must be filtered"


def test_an_edit_with_no_patch_at_all_falls_back_to_the_fragment(tmp_path):
    """`tool_response` is undocumented, so the hook must not depend on it. With no
    patch, `new_string` is a fragment and must be labelled one — never `file`,
    which would reinstate the bug."""
    r = _run(tmp_path, _edit("/p/blink.c", patch=None, new_string="  acc += 1;"),
             stub=_MEASURABLE)
    assert r.flag(0) == "fragment"
    assert r.stdin().rstrip("\n") == "  acc += 1;"
    assert r.context is not None


def test_a_blank_line_added_is_a_change_not_an_absent_patch(tmp_path):
    """A bare `+` is an added blank line. Keeping the marker makes it a non-empty
    line, so it stays on the diff path instead of being mistaken for "no patch"
    and silently falling back to the whole of new_string."""
    r = _run(tmp_path, _edit("/p/blink.c", patch=[" f(){", "+", " }"]),
             stub=_NOT_MEASURABLE)
    assert r.flag(0) == "diff"
    assert r.stdin().rstrip("\n") == "+"


# ── malformed and hostile payloads ──────────────────────────────────────────

@pytest.mark.parametrize("response", [
    "Applied edit to /p/blink.c",              # a string
    [{"type": "text", "text": "done"}],        # an array
    None,                                      # JSON null
    {"structuredPatch": None},                 # null patch
    {"structuredPatch": [{"oldStart": 1}]},    # a hunk with no lines
])
def test_a_non_object_tool_response_is_handled_without_leaking_jq_errors(tmp_path, response):
    """`.structuredPatch[]?` guards only the `[]` suffix — the field access on a
    non-object dies first, and a bare `jq: error` in the transcript on every edit is
    exactly the noise an advisory hook must not make."""
    payload = _edit("/p/blink.c", response=response) if response is not None \
        else _edit("/p/blink.c", patch=None)
    if response is None:
        payload["tool_response"] = None
    r = _run(tmp_path, payload, stub=_MEASURABLE)
    assert r.code == 0
    assert "jq: error" not in r.stderr, f"stderr leaked: {r.stderr!r}"
    assert r.flag(0) == "fragment", "no usable patch must mean fragment, never file"


def test_a_failed_edit_is_not_announced_as_a_modification(tmp_path):
    """PostToolUse is not supposed to fire on failure, but the payload shape is
    undocumented. A response carrying an error applied nothing, so claiming the file
    "was modified" would be false."""
    r = _run(tmp_path, _edit("/p/blink.c",
                             response={"error": "String to replace not found in file"}),
             stub=_MEASURABLE)
    assert r.calls == [], "nothing was applied; there is nothing to classify"
    assert r.context is None


def test_a_relative_file_path_starting_with_a_dash_is_not_parsed_as_an_option(tmp_path):
    """The path must be BARE, not `/p/-weird.c`: after `basename` an absolute path is
    already `-weird.c`, but `basename` itself received `/p/-weird.c`, which is not
    option-shaped — so an absolute fixture exercises neither `basename --` nor
    `--path=`. Verified with a bare name that both hardenings are load-bearing:
    `basename "-weird.c"` fails with `unknown option -- w`."""
    r = _run(tmp_path, _edit("-weird.c", patch=_ONE_LINE_PATCH), stub=_MEASURABLE)
    assert r.context is not None, "the reminder must survive a dash-leading path"
    assert "-weird.c" in r.context, f"filename lost from the reminder: {r.context!r}"
    assert "unknown option" not in r.stderr
    # And the path reached the CLI as a value, not as another option.
    assert "--path=-weird.c" in r.calls[0], f"calls={r.calls!r}"


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="MSYS2 repopulates HOME regardless of the passed env, so the hook can "
           "never see it unset here — the assertion would pass vacuously",
)
def test_the_hook_exits_zero_even_with_no_HOME(tmp_path):
    """`export PATH="$HOME/..."` under `set -u` exits 1 where HOME is unset —
    Windows sets USERPROFILE, and hooks run non-interactive so no profile is
    sourced. Exit 1 from PostToolUse reads to the model as a tool failure.

    Skipped rather than silently vacuous on win32: with only PATH in the env,
    `bash -c 'echo ${HOME-UNSET}'` still printed a real home, so this asserts
    nothing there. It is a genuine guard on POSIX CI."""
    r = _run(tmp_path, _edit("/p/blink.c", patch=_ONE_LINE_PATCH),
             stub=_MEASURABLE, drop_home=True, expect_zero=False)
    assert r.code == 0, f"stderr={r.stderr!r}"
    assert "unbound variable" not in r.stderr


def test_a_multi_hunk_patch_contributes_every_hunk(tmp_path):
    """Every other fixture here has ONE hunk, so the hook's concatenation across
    `.structuredPatch[]` was never exercised end to end — and cross-hunk behaviour is
    the whole reason the classifier strips comments per line rather than across the
    blob."""
    r = _run(tmp_path, _edit("/p/blink.c", response={
        "filePath": "/p/blink.c",
        "structuredPatch": [
            {"oldStart": 3, "newStart": 3, "lines": [" a() {", "-    x = 1;", "+    x = 2;", " }"]},
            {"oldStart": 40, "newStart": 40, "lines": [" b() {", "-    y = 3;", "+    y = 4;", " }"]},
        ],
    }), stub=_MEASURABLE)
    assert r.flag(0) == "diff"
    assert r.stdin().splitlines() == [
        "-    x = 1;", "+    x = 2;",
        "-    y = 3;", "+    y = 4;",
    ], f"got {r.stdin()!r}"
    assert r.context is not None


# ── what must never reach the CLI at all ────────────────────────────────────

@pytest.mark.parametrize("path", ["/p/notes.md", "/p/build.py", "/p/Makefile", "/p/x"])
def test_a_non_source_path_never_invokes_loci(tmp_path, path):
    r = _run(tmp_path, _edit(path, patch=_ONE_LINE_PATCH), stub=_MEASURABLE)
    assert r.calls == [], f"{path} must be filtered in the hook; got {r.calls!r}"
    assert r.context is None


@pytest.mark.parametrize("path", [
    "/p/.claude/plans/plan.c",
    "/p/.claude/settings.c",
])
def test_plan_and_settings_files_are_skipped(tmp_path, path):
    """Both paths carry a SOURCE extension deliberately. `.claude/settings.json`
    would be rejected by the extension filter several lines earlier, so it would
    assert silence produced by a different branch than the one named here."""
    r = _run(tmp_path, _edit(path, patch=_ONE_LINE_PATCH), stub=_MEASURABLE)
    assert r.calls == []
    assert r.context is None


@pytest.mark.parametrize("ext", [".c", ".cc", ".cpp", ".cxx", ".c++", ".rs",
                                 ".S", ".s"])
def test_every_compilable_source_extension_is_handled(tmp_path, ext):
    """Extensions that emit an object of their own. `.S`/`.s` are here because they
    do too, and a header edit reaches an `.S` through its `#include` just as it
    reaches a `.c`."""
    r = _run(tmp_path, _edit(f"/p/mod{ext}", patch=_ONE_LINE_PATCH), stub=_MEASURABLE)
    assert r.calls, f"{ext} must reach the classifier"
    assert r.context is not None


@pytest.mark.parametrize("ext", [".h", ".hpp", ".hxx", ".h++", ".hh",
                                 ".inc", ".ipp", ".tcc"])
def test_headers_reach_the_cli_which_is_what_decides_them(tmp_path, ext):
    """Pins where the header decision is made. The hook does NOT filter headers, so
    the routing decision is one rule in one place — the CLI's.

    The list matches the CLI's `_SNAPSHOT_SOURCE_EXTS`, and being narrower than it is
    a correctness bug rather than a scoping choice: a header this hook drops is one
    the pre-edit hook also drops (same list), so it is never captured, and a
    reconstruction then reads it at its edited content and publishes a hybrid as a
    clean Before."""
    r = _run(tmp_path, _edit(f"/p/api{ext}", patch=_ONE_LINE_PATCH),
             stub=_NOT_MEASURABLE)
    assert r.calls, f"{ext} must be the CLI's decision, not the hook's"
    assert r.context is None


@pytest.mark.parametrize("ext", [".h", ".hpp", ".inc", ".tcc"])
def test_a_header_the_cli_calls_measurable_gets_the_reminder(tmp_path, ext):
    """The other half of the rule above, and the one that makes a header edit
    reachable at all: when the CLI answers `measurable: true` for a header — because
    it can now route the edit to the translation units that include it — the hook
    must remind exactly as it does for a `.c`. Without this the routing exists and
    nothing ever invokes it."""
    r = _run(tmp_path, _edit(f"/p/api{ext}", patch=_ONE_LINE_PATCH), stub=_MEASURABLE)
    assert r.calls, f"{ext} must reach the classifier"
    assert r.context is not None, (
        f"a measurable {ext} produced no reminder — the header route is unreachable")


# ---------------------------------------------------------------------------
# The turn id the reminder carries (phase 02b)
#
# The pre-edit hook has always stamped the baseline with `prompt_id`, but nothing
# ever CHECKED it: the compile that reads the baseline back was never told which turn
# it wanted, so a capture left by a PREVIOUS turn was served as this edit's Before and
# the delta silently spanned two turns — measured at +77.8% ROM reported for an edit
# whose true effect was 0. Reachable whenever the pre-edit hook did not capture for
# this turn: killed on its 8 s budget, `loci` briefly absent, or the file changed
# outside Claude Code.
#
# The reminder text is the only channel — a PostToolUse hook cannot call the skill it
# asks for — so what it says is load-bearing, and it had no tests at all.
# ---------------------------------------------------------------------------

_TURN_ID = "b52ae369-e1ba-4823-9c6e-3d51b9e0166e"


def _edit_with_turn(path: str, *, prompt_id: str | None = _TURN_ID,
                    patch: list[str] | None = None) -> dict:
    payload = _edit(path, patch=patch or ["-  acc += 1;", "+  acc += 2;"])
    if prompt_id is not None:
        payload["prompt_id"] = prompt_id
    return payload


def test_the_reminder_carries_the_turn_id(tmp_path):
    r = _run(tmp_path, _edit_with_turn("app.c"), stub=_MEASURABLE)
    ctx = r.context
    assert ctx is not None, "the reminder was not emitted at all"
    assert _TURN_ID in ctx, f"the turn id is not in the reminder: {ctx!r}"
    assert "--turn" in ctx, (
        "the reminder names the id but not what to do with it; the skill has to be "
        f"told it is the --turn value: {ctx!r}"
    )


def test_the_reminder_still_works_when_the_payload_has_no_prompt_id(tmp_path):
    """Degrading is the whole design: no id means the skill omits `--turn` and the
    compile simply does not verify the turn — the behaviour before this channel
    existed. What must NOT happen is the reminder being lost, or an empty id being
    passed on for the skill to send as a literal."""
    r = _run(tmp_path, _edit_with_turn("app.c", prompt_id=None), stub=_MEASURABLE)
    ctx = r.context
    assert ctx is not None, "the reminder was dropped when there was no turn id"
    assert "MUST invoke" in ctx, ctx
    assert "--turn" not in ctx, (
        f"offered a --turn instruction with no id to put in it: {ctx!r}"
    )
    assert "turn id" not in ctx, ctx


def test_an_empty_prompt_id_is_treated_as_absent(tmp_path):
    """`.prompt_id // ""` yields the empty string for an explicit `null` too, and an
    empty id must take the same path as a missing one rather than producing
    `Pass turn id  to the skill`."""
    payload = _edit_with_turn("app.c", prompt_id=None)
    payload["prompt_id"] = ""
    r = _run(tmp_path, payload, stub=_MEASURABLE)
    assert r.context is not None
    assert "--turn" not in r.context, r.context


def test_the_turn_id_the_reminder_carries_is_the_one_the_pre_edit_hook_stamps(tmp_path):
    """Both hooks read `.prompt_id` from their own payload. If they ever diverge, the
    compile's turn check rejects every baseline and the Before column disappears
    silently — a lost measurement is invisible, which is why this is pinned rather
    than left to inspection."""
    pre_hook = PLUGIN_ROOT / "hooks" / "pre-edit-hook.sh"
    post_hook = PLUGIN_ROOT / "hooks" / "post-edit-hook.sh"
    for hook in (pre_hook, post_hook):
        text = hook.read_text(encoding="utf-8")
        assert re.search(r"^[^#\n]*\.prompt_id", text, re.M), (
            f"{hook.name} no longer reads .prompt_id outside a comment"
        )
    # And the pre-edit side must still be the one that STAMPS it — asserted by
    # RUNNING it, not by grepping it.
    #
    # This was a text lint: "some non-comment line mentions both `build snapshot`
    # and `--turn`". It caught the mutation it was written for, then phase 10 broke
    # it by assembling the argv in an array — a refactor that changes no behaviour
    # at all. A lint whose selector is a spelling fails on the spelling; the
    # property here is about the ARGV, so read the argv. (That both hooks discuss
    # `--turn` at length in comments, forcing the lint to exclude them, was the tell
    # that it was matching prose in the first place.)
    from tests.unit.test_pre_edit_hook import _run as _run_pre
    from tests.unit.test_pre_edit_hook import opt as _opt
    pre = _run_pre(tmp_path / "pre-home", {
        "hook_event_name": "PreToolUse", "tool_name": "Edit",
        "prompt_id": _TURN_ID,
        "tool_input": {"file_path": "app.c", "old_string": "y", "new_string": "  x();"},
    }, stub="echo '{\"ok\":true,\"data\":{\"report\":\"\"}}'")
    stamped = [_opt(c, "--turn") for c in pre.snapshots]
    assert _TURN_ID in stamped, (
        "the pre-edit hook no longer stamps a turn id on `build snapshot`, so "
        f"nothing writes the marker the compile is asked to check; calls={pre.calls!r}"
    )
    # Both sides, one token, from one live pair of runs — not from two reads of the
    # same constant.
    post = _run(tmp_path, _edit_with_turn("app.c"), stub=_MEASURABLE)
    assert _TURN_ID in (post.context or ""), post.context


def test_no_reminder_means_no_turn_id_leaked(tmp_path):
    """An unmeasurable edit emits nothing, so there is no half-message carrying a turn
    id with no instruction attached to it."""
    r = _run(tmp_path, _edit_with_turn("app.c"), stub=_NOT_MEASURABLE)
    assert r.out.strip() == "", r.out


# ---------------------------------------------------------------------------
# The route the reminder carries (phase 06d)
#
# `measurable` says the edit can change compiled code. `measure_via` says whether
# THIS file can be compiled at all — a header cannot, and is measured through the
# units that #include it. The hook carries the second through because the skill's
# alternative is re-deriving header-ness from its own list of suffixes, which would
# be the fourth copy of that set and the only one no test can compare with the CLI's.
# ---------------------------------------------------------------------------

_MEASURABLE_HEADER = (
    "echo '{\"ok\":true,\"data\":{\"measurable\":true,\"measure_via\":\"dependents\"}}'")
_MEASURABLE_SELF = (
    "echo '{\"ok\":true,\"data\":{\"measurable\":true,\"measure_via\":\"self\"}}'")


def test_a_header_reminder_says_to_measure_through_its_dependents(tmp_path):
    r = _run(tmp_path, _edit("/p/api.h", patch=_ONE_LINE_PATCH),
             stub=_MEASURABLE_HEADER)
    assert r.context is not None
    assert "emits no object of its own" in r.context, r.context
    assert "Step 0b" in r.context, r.context


def test_an_ordinary_source_reminder_does_not(tmp_path):
    """The control. A route hint on every edit is one every reader learns to skip,
    and it would send a `.c` down a path that cannot apply to it."""
    r = _run(tmp_path, _edit("/p/mod.c", patch=_ONE_LINE_PATCH),
             stub=_MEASURABLE_SELF)
    assert r.context is not None
    assert "emits no object of its own" not in r.context, r.context


def test_a_cli_that_does_not_report_a_route_still_reminds(tmp_path):
    """`measure_via` is new, and the pin is an exact `==`, so a CLI without it is a
    normal state. The reminder must degrade to its previous text rather than
    embedding an empty hint or dropping out."""
    r = _run(tmp_path, _edit("/p/mod.c", patch=_ONE_LINE_PATCH), stub=_MEASURABLE)
    assert r.context is not None
    assert "You MUST invoke" in r.context
    assert "emits no object of its own" not in r.context, r.context


# ---------------------------------------------------------------------------
# Subagents, and the failure event (phase 10)
#
# Both edit hooks fire inside a subagent. Probing a live session settled two facts
# the design needed and neither of which is documented:
#
#   * a subagent's tool payloads carry `agent_id`/`agent_type`; the main agent's do
#     not. Everything else is identical — same `session_id`, same
#     `transcript_path`, and the same `prompt_id` (the PARENT turn's).
#   * `PostToolUse` does not fire for a failed tool call. An Edit that fails
#     VALIDATION fires nothing at all, not even PreToolUse; one that fails while
#     WRITING fires PreToolUse and then `PostToolUseFailure`, whose payload carries
#     a top-level `error` and no `tool_response`.

_AGENT = {"agent_id": "a19d292387c73a8c2", "agent_type": "general-purpose"}


def _subagent_edit(path: str = "app.c") -> dict:
    payload = _edit_with_turn(path)
    payload.update(_AGENT)
    return payload


def test_a_subagent_is_told_to_relay_the_verdict(tmp_path):
    """The reminder reaches the agent that made the edit, and a subagent's
    transcript is not shown to the user. Measured and then discarded is the same
    outcome as not measured, from where the user sits."""
    r = _run(tmp_path, _subagent_edit(), stub=_MEASURABLE)
    ctx = r.context
    assert ctx is not None
    assert "subagent" in ctx, ctx
    assert "final report" in ctx, ctx


def test_the_main_agent_gets_no_such_sentence(tmp_path):
    """The other half. Without it, a change that appended the sentence
    unconditionally passes the test above while telling the main agent it is a
    subagent — and the reminder is the one piece of text this hook exists to
    produce."""
    r = _run(tmp_path, _edit_with_turn("app.c"), stub=_MEASURABLE)
    assert "subagent" not in (r.context or ""), r.context


def test_a_subagents_edit_is_still_measured(tmp_path):
    """The decision, stated as a test. Suppressing the reminder inside subagents
    would make a subagent's edits the one kind that is never measured — and bulk
    edits are exactly what gets delegated. The relay sentence is an ADDITION to the
    reminder, never a replacement for it."""
    r = _run(tmp_path, _subagent_edit(), stub=_MEASURABLE)
    ctx = r.context
    assert "MUST invoke" in ctx, ctx
    assert _TURN_ID in ctx, ctx


def test_a_subagent_carries_the_parent_turn_id(tmp_path):
    """`prompt_id` inside a subagent is the parent turn's — verified against a live
    session. That is what a per-turn baseline wants: a subagent fan-out must share
    the turn's Before rather than fragmenting it into one baseline per agent."""
    r = _run(tmp_path, _subagent_edit(), stub=_MEASURABLE)
    assert _TURN_ID in r.context


def test_a_post_tool_use_failure_payload_produces_no_reminder(tmp_path):
    """The probed shape, verbatim: a top-level `error`, `is_interrupt`, and NO
    `tool_response`. Nothing registers this hook on that event — but the guard is
    what makes that a decision rather than an omission, and without it the payload
    falls straight through to the fragment branch, because the branch's trigger is
    "no structuredPatch" and a failure has none."""
    payload = _edit_with_turn("app.c")
    payload.pop("tool_response", None)
    payload["hook_event_name"] = "PostToolUseFailure"
    payload["error"] = ("EPERM: operation not permitted, rename "
                        "'app.c.tmp.20752.9ffd938e11dd' -> 'app.c'")
    payload["is_interrupt"] = False
    r = _run(tmp_path, payload, stub=_MEASURABLE)
    assert r.code == 0
    assert r.context is None, r.out


def test_an_error_alone_is_enough(tmp_path):
    """Each arm of the guard, alone. A failure whose event name were ever changed
    to `PostToolUse` — the shape this hook would then actually receive — must still
    be recognised as "not applied"."""
    payload = _edit_with_turn("app.c")
    payload.pop("tool_response", None)
    payload["error"] = "EPERM"
    r = _run(tmp_path, payload, stub=_MEASURABLE)
    assert r.context is None, r.out


def test_a_missing_event_name_still_reminds(tmp_path):
    """The event check defaults to `PostToolUse`, not to silence. Under-triggering
    loses a measurement invisibly; over-triggering costs one wasted analysis — the
    same asymmetry the `--content-kind` fallback is decided by, and the reason this
    guard cannot be written as "only proceed on a name I recognise"."""
    payload = _edit_with_turn("app.c")
    payload.pop("hook_event_name", None)
    r = _run(tmp_path, payload, stub=_MEASURABLE)
    assert r.context is not None, r.out


def test_a_null_error_field_is_not_a_failure(tmp_path):
    """`has("error")` alone would read an explicit `"error": null` as a failure and
    drop the reminder for a successful edit. JSON producers emit null fields all the
    time; this hook must not go silent on one."""
    payload = _edit_with_turn("app.c")
    payload["error"] = None
    r = _run(tmp_path, payload, stub=_MEASURABLE)
    assert r.context is not None, r.out


def test_a_failure_event_alone_is_enough(tmp_path):
    """Guard arm 3, in isolation. `test_a_post_tool_use_failure_payload_produces_no
    _reminder` sets a top-level `error` AND the event name, so arm 2 satisfied it on
    its own and deleting arm 3 survived all 64 tests — the arm added for
    future-proofing was the one with no test.

    A failure event carrying no `error` field is the shape this covers."""
    payload = _edit_with_turn("app.c")
    payload.pop("tool_response", None)
    payload["hook_event_name"] = "PostToolUseFailure"
    r = _run(tmp_path, payload, stub=_MEASURABLE)
    assert r.context is None, r.out


def test_an_unrecognised_event_still_reminds(tmp_path):
    """The arm is a DENY-list, and this is why. Naming the success event instead
    (`!= "PostToolUse"`) silences the hook on every edit the moment that event is
    spelled differently — on a field nobody in this repo controls, and against the
    asymmetry the rest of the file is built on: a lost measurement is invisible, a
    wasted analysis is not."""
    payload = _edit_with_turn("app.c")
    payload["hook_event_name"] = "PostToolUseSucceeded"
    r = _run(tmp_path, payload, stub=_MEASURABLE)
    assert r.context is not None, r.out
    assert "MUST invoke" in r.context


def test_a_null_error_inside_the_tool_response_is_not_a_failure(tmp_path):
    """Both `error` arms test `!= null`, not `has`. The top-level arm was written
    that way and the `tool_response` arm was not, so a successful edit whose response
    carried `"error": null` — ordinary for a JSON producer — dropped the reminder."""
    payload = _edit_with_turn("app.c")
    payload["tool_response"]["error"] = None
    r = _run(tmp_path, payload, stub=_MEASURABLE)
    assert r.context is not None, r.out


def test_a_real_tool_response_error_is_still_a_failure(tmp_path):
    """The other half of the null change: relaxing `has` to `!= null` must not stop
    a genuine error being recognised."""
    payload = _edit_with_turn("app.c")
    payload["tool_response"]["error"] = "EACCES"
    r = _run(tmp_path, payload, stub=_MEASURABLE)
    assert r.context is None, r.out
