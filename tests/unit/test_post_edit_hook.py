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
* **The first-edit nudge fires once per project, or it is worse than absent.** A
  reminder to run `/loci:init` that repeats on every edit is noise the user learns
  to ignore, and one that fires in a project the CLI has already declared
  `unsupported` is a claim about a question that was asked and answered. The
  marker is created with `set -C`, so the test and the claim are one atomic
  operation — which is what makes "exactly one, across two sessions and two
  concurrent subagents" checkable rather than hopeful.
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


def _ctx_key(root: Path) -> str:
    """The keyed-context/marker name for `root`, from the plugin's OWN rule.

    `hash_cwd` is a device:inode key, so a Python reimplementation here would be a
    second spelling of the one thing these tests are checking — and it would agree
    with the hook right up until the day it stopped. Shelling out to the library
    the hook itself sources is the only version that cannot drift."""
    script = (
        'PLUGIN_DIR="$1"; export LOCI_STATE_DIR="$2"; '
        '. "$PLUGIN_DIR/lib/setup-steps.sh" >/dev/null 2>&1; hash_cwd "$3"'
    )
    proc = subprocess.run(
        [_find_bash(), "-c", script, "bash",
         _to_bash_path(PLUGIN_ROOT), _to_bash_path(root), _to_bash_path(root)],
        capture_output=True, text=True, timeout=30,
    )
    key = proc.stdout.strip()
    assert key, f"hash_cwd produced no key for {root}: {proc.stderr!r}"
    return key


def _markers(state_dir: Path) -> list[str]:
    """Every first-edit marker in a state directory, by name."""
    if not state_dir.is_dir():
        return []
    return sorted(p.name for p in state_dir.iterdir()
                  if p.name.startswith("init-nudge-"))


BS_CHAR = chr(92)
CR_CHAR = chr(13)

_UNSET = object()


def _run(home: Path, payload: dict, *, stub: str,
         drop_home: bool = False, expect_zero: bool = True,
         recipe: bool = True, init_status=_UNSET,
         project: Path | None = None, state_dir: Path | None = None) -> Result:
    """Run the hook against a stubbed `loci`.

    The hook prepends `$HOME/.local/bin` to PATH (where `uv tool install` puts the
    real CLI), so the stub goes there — pointing HOME at a tmp dir also keeps the
    developer's own installed `loci` from shadowing it and making the test
    silently exercise a different binary.

    `recipe=True` is the DEFAULT because the first-edit nudge would otherwise fire
    in every one of the forty tests that predate it: a `.loci/build.yaml` beside
    the project is initialization, whatever any state file says, and the hook
    stops there without touching the state directory at all. Pass `recipe=False`
    to reach the status lookup, and `init_status=` to seed what it finds (omit it
    for the no-file case, `None` for a context file that carries no such key)."""
    root = project if project is not None else home
    root.mkdir(parents=True, exist_ok=True)
    if recipe:
        (root / ".loci").mkdir(parents=True, exist_ok=True)
        (root / ".loci" / "build.yaml").write_text("version: 1\\n", encoding="utf-8")
    sdir = state_dir if state_dir is not None else home / "state"
    sdir.mkdir(parents=True, exist_ok=True)
    if init_status is not _UNSET:
        body = {} if init_status is None else {"init_status": init_status}
        (sdir / f"project-context-{_ctx_key(root)}.json").write_text(
            json.dumps(body), encoding="utf-8")

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
        "CLAUDE_PROJECT_DIR": _to_bash_path(root),
        # Pinned, so a developer's real `~/.loci/state` is never read or written
        # by a test — and so `_markers` looks where the hook writes.
        "LOCI_STATE_DIR": _to_bash_path(sdir),
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

def test_a_one_line_body_edit_reaches_the_verb_whole(tmp_path):
    """THE regression, at the boundary this hook owns since todo 044.

    Picking the -/+ lines out of the applied patch and labelling them `diff` is
    the CLI's job now — `_edit_code` in `hook.py`, pinned by
    `tests/unit/test_hook_edit_scan.py` in loci-cli. What this side must not
    break is that the patch ARRIVES: the payload goes over the pipe whole, and
    nothing on the argv claims to have classified it."""
    r = _run(tmp_path, _edit("/p/blink.c", patch=_ONE_LINE_PATCH), stub=_MEASURABLE)

    assert r.calls[0] == "hook edit-scan", f"got {r.calls[0]!r}"
    sent = json.loads(r.stdin())
    assert [ln for ln in sent["tool_response"]["structuredPatch"][0]["lines"]
            if ln[:1] in "+-"] == [
        "-        acc += i * 3;",
        "+        acc += i * 7;",
    ], f"got {r.stdin()!r}"
    assert r.context is not None and "loci-post-edit" in r.context


def test_the_payload_survives_as_raw_bytes(tmp_path):
    """Asserted on bytes, not text. `read_text`, `.splitlines()` and `.rstrip()`
    each normalise line endings away, so a text-only assertion is blind to payload
    corruption. The changed lines must arrive intact and unmerged; the line
    terminator is a platform wart, so it is normalised rather than asserted.

    Still worth pinning after the payload stopped being pre-chewed here: it now
    travels through one more shell variable than it did, and a `$(...)` that
    swallowed a line would be invisible to every other test in this file."""
    r = _run(tmp_path, _edit("/p/blink.c", patch=_ONE_LINE_PATCH), stub=_MEASURABLE)
    raw = r.stdin_bytes[0]
    assert b"-        acc += i * 3;" in raw
    assert b"+        acc += i * 7;" in raw
    assert re.search(rb"\* 3;.{0,4}\+        acc", raw), (
        f"lines merged or reordered: {raw!r}")


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

# The three payload shapes whose CLASSIFICATION used to be decided here — a
# create's whole `content`, an overwrite's patch, an Edit with no patch at all.
# That decision moved to `_edit_code` in the CLI (todo 044) and is pinned by
# `tests/unit/test_hook_edit_scan.py` in loci-cli, where the whole matrix lives
# including the shapes this file never reached. What stays here is the half this
# hook still owns: every shape reaches the verb whole, and one call answers it.
@pytest.mark.parametrize("payload,expect", [
    (_write("/p/newfile.c"), "int g(void){ return 1; }"),
    (_edit("/p/blink.c", patch=None, new_string="  acc += 1;"), "  acc += 1;"),
])
def test_every_edit_shape_reaches_the_verb_whole(tmp_path, payload, expect):
    r = _run(tmp_path, payload, stub=_MEASURABLE)
    assert r.calls == ["hook edit-scan"], f"calls={r.calls!r}"
    sent = json.loads(r.stdin())
    got = sent["tool_input"].get("content", sent["tool_input"].get("new_string"))
    assert got == expect
    assert r.context is not None


def test_a_blank_line_added_still_reaches_the_verb(tmp_path):
    """A bare `+` is an added blank line, and the shape most easily lost: a reader
    that strips markers turns it into an empty string, which reads as "this
    payload carried no patch". That it stays a CHANGE is pinned CLI-side; that it
    is still sent is pinned here."""
    r = _run(tmp_path, _edit("/p/blink.c", patch=[" f(){", "+", " }"]),
             stub=_NOT_MEASURABLE)
    assert r.calls == ["hook edit-scan"], f"calls={r.calls!r}"
    assert json.loads(r.stdin())["tool_response"]["structuredPatch"][0]["lines"] \
        == [" f(){", "+", " }"]


# ── malformed and hostile payloads ──────────────────────────────────────────

@pytest.mark.parametrize("response", [
    "Applied edit to /p/blink.c",              # a string
    [{"type": "text", "text": "done"}],        # an array
    None,                                      # JSON null
    {"structuredPatch": None},                 # null patch
    {"structuredPatch": [{"oldStart": 1}]},    # a hunk with no lines
])
def test_a_non_object_tool_response_is_handled_without_leaking_jq_errors(tmp_path, response):
    """A `tool_response` that is a string or an array used to die on the field
    access and leak a bare `jq: error` into the transcript on every edit — exactly
    the noise an advisory hook must not make. The whole class is gone with the jq
    reads, and nothing may take its place: this hook reads no part of
    `tool_response` at all now.

    That such a payload classifies as a fragment and never as a file is pinned
    CLI-side, in `test_hook_edit_scan.py`."""
    payload = _edit("/p/blink.c", response=response) if response is not None \
        else _edit("/p/blink.c", patch=None)
    if response is None:
        payload["tool_response"] = None
    r = _run(tmp_path, payload, stub=_MEASURABLE)
    assert r.code == 0
    assert r.stderr.strip() == "", f"stderr leaked: {r.stderr!r}"
    assert r.calls == ["hook edit-scan"], f"calls={r.calls!r}"


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
    # And the path is not on the argv at all any more — it reaches the CLI inside
    # the payload, so the `--path <value>` class of usage error cannot happen.
    assert r.calls == ["hook edit-scan"], f"calls={r.calls!r}"
    assert json.loads(r.stdin())["tool_input"]["file_path"] == "-weird.c"


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
    """Every other fixture here has ONE hunk, so a multi-hunk payload crossing this
    hook was never exercised end to end. The concatenation itself is the CLI's
    (`test_hook_edit_scan.py::test_every_hunk_contributes`); what this pins is that
    both hunks are still in the bytes the verb is handed."""
    r = _run(tmp_path, _edit("/p/blink.c", response={
        "filePath": "/p/blink.c",
        "structuredPatch": [
            {"oldStart": 3, "newStart": 3, "lines": [" a() {", "-    x = 1;", "+    x = 2;", " }"]},
            {"oldStart": 40, "newStart": 40, "lines": [" b() {", "-    y = 3;", "+    y = 4;", " }"]},
        ],
    }), stub=_MEASURABLE)
    assert r.calls == ["hook edit-scan"], f"calls={r.calls!r}"
    hunks = json.loads(r.stdin())["tool_response"]["structuredPatch"]
    assert [ln for h in hunks for ln in h["lines"] if ln[:1] in "+-"] == [
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
                                 ".go", ".S", ".s"])
def test_every_compilable_source_extension_is_handled(tmp_path, ext):
    """Extensions that emit an object of their own. `.S`/`.s` are here because they
    do too, and a header edit reaches an `.S` through its `#include` just as it
    reaches a `.c`.

    `.go` emits no object of its own — its unit is the linked binary — but it is
    on the same gate for the same reason: the hook decides only whether the CLI
    is asked, and the CLI decides what the artifact is."""
    r = _run(tmp_path, _edit(f"/p/mod{ext}", patch=_ONE_LINE_PATCH), stub=_MEASURABLE)
    assert r.calls, f"{ext} must reach the classifier"
    assert r.context is not None


@pytest.mark.parametrize("ext", [".h", ".hpp", ".hxx", ".h++", ".hh",
                                 ".inc", ".ipp", ".tcc", ".inl", ".tpp", ".def"])
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
    """Both hooks read `prompt_id` from their own payload. If they ever diverge, the
    compile's turn check rejects every baseline and the Before column disappears
    silently — a lost measurement is invisible, which is why this is pinned rather
    than left to inspection.

    The spelling is no longer jq's `.prompt_id`: todo 044 reads it through
    `loci_json_get`, and `loci hook edit-scan` reports it back as `turn` for a
    payload too large for the forkless prefix."""
    pre_hook = PLUGIN_ROOT / "hooks" / "pre-edit-hook.sh"
    post_hook = PLUGIN_ROOT / "hooks" / "post-edit-hook.sh"
    for hook in (pre_hook, post_hook):
        text = hook.read_text(encoding="utf-8")
        assert re.search(r"^[^#\n]*loci_json_get prompt_id", text, re.M), (
            f"{hook.name} no longer reads prompt_id outside a comment"
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
    assert "loci analyse prepare --source" in r.context, r.context
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


# ---------------------------------------------------------------------------
# The first-edit nudge
#
# Report §6.3 gives an uninitialized project a suggestion line in the session
# banner; this hook delivers the same fact to the user who does not read banners,
# at the first edit of compiled source. Everything below is a way the nudge could
# be worse than absent:
#
#   * repeating — a reminder on every edit is noise the user learns to ignore, and
#     "once per project" is only true if the marker really is keyed per project and
#     really does survive a session;
#   * lying — nudging `/loci:init` in a project the CLI already recorded as
#     `unsupported` (or is about to re-arm from `failed`) is a claim about a
#     question that was asked and answered;
#   * hiding — a nudge that only rides along with the post-edit reminder never
#     reaches the user whose first edit happened to be comment-only.
# ---------------------------------------------------------------------------

_C_EDIT = _edit("/p/blink.c", patch=_ONE_LINE_PATCH)
_NUDGE_TEXT = "run /loci:init"


def _nudged(result: Result) -> bool:
    return result.context is not None and _NUDGE_TEXT in result.context


def test_an_uninitialized_project_is_nudged_on_its_first_edit(tmp_path):
    r = _run(tmp_path, _C_EDIT, stub=_MEASURABLE, recipe=False,
             init_status="uninitialized")

    assert _nudged(r), f"no nudge: {r.context!r}"
    assert "LOCI is not initialized for this project" in r.context
    assert _markers(tmp_path / "state") == [
        f"init-nudge-{_ctx_key(tmp_path)}"
    ], "the marker is not keyed the way the context file is"


def test_the_nudge_fires_exactly_once_across_two_sessions(tmp_path):
    """Acceptance criterion 3. Two hook runs against one state directory is what
    two sessions look like from here: nothing else in a session is carried between
    them, which is precisely why the marker cannot live in the session."""
    first = _run(tmp_path, _C_EDIT, stub=_MEASURABLE, recipe=False,
                 init_status="uninitialized")
    second = _run(tmp_path, _C_EDIT, stub=_MEASURABLE, recipe=False,
                  init_status="uninitialized")

    assert _nudged(first)
    assert not _nudged(second), (
        "the nudge repeated; the marker is not being consulted or not being "
        f"written: {second.context!r}"
    )
    # …and the second run still did its real job.
    assert second.context is not None and "loci-post-edit" in second.context


def test_a_project_with_a_recipe_is_never_nudged_and_never_looked_up(tmp_path):
    """The cheap gate, asserted on its side effect. A `.loci/build.yaml` beside the
    session means the project IS initialized whatever the state file says — and the
    hook must stop there, because the alternative costs a `hash_cwd` (two `stat`
    probes and a `sha256sum`) on every edit of every initialized project."""
    state = tmp_path / "untouched"
    r = _run(tmp_path, _C_EDIT, stub=_MEASURABLE, recipe=True, state_dir=state)

    assert not _nudged(r)
    # No context file at all, which is the shape that DOES nudge — so a hook that
    # skipped the gate and went to the state directory would fail the assertion
    # above, and an empty marker list is the evidence it never got there.
    assert _markers(state) == []


def test_a_recipe_in_a_project_whose_state_says_uninitialized_still_wins(tmp_path):
    """The gate and the status test must not disagree. A recipe on disk with no
    recorded status is a real state (`initialized_degraded` — the state dir was
    wiped, or `loci init` has not seen this checkout), and "LOCI is not
    initialized" would be false in it."""
    r = _run(tmp_path, _C_EDIT, stub=_MEASURABLE, recipe=True,
             init_status="uninitialized")

    assert not _nudged(r), f"nudged beside a recipe: {r.context!r}"


@pytest.mark.parametrize("status", ["ok", "unsupported", "needs_user", "failed",
                                    "a_value_this_version_never_heard_of"])
def test_only_an_uninitialized_status_nudges(tmp_path, status):
    """An allow-list, and each exclusion has its own reason.

    `ok` is initialized. `unsupported` and `needs_user` are what the CLI records
    once it has already decided, so `/loci:init` sends the user at a question that
    was asked and answered. `failed` is the interesting one and the hostile brief
    asks for a decision: it is TRANSIENT (§6.2) — session-init re-arms the project
    and the next analysis routes whichever coded error the compile answers with,
    which will not be `not_initialized` — so a nudge naming `/loci:init` would be a
    guess about a state the CLI is about to correct. An unrecognised value takes
    the same quiet path, because the nudge is a claim and nothing in a hook can
    check it."""
    r = _run(tmp_path, _C_EDIT, stub=_MEASURABLE, recipe=False, init_status=status)

    assert not _nudged(r), f"init_status={status!r} nudged: {r.context!r}"
    assert _markers(tmp_path / "state") == [], (
        f"init_status={status!r} burned the marker without nudging, so the "
        "project can never be nudged once it becomes uninitialized"
    )


def test_a_context_file_with_no_init_status_nudges(tmp_path):
    """Not an oversight — the population the nudge exists for. session-init writes
    `init_status: uninitialized` only in the ARMED branch; a project the cheap gate
    declined (`no_project`, `multi_project`, a detector that could not run) gets a
    context file with no such key at all. Report §6.3 hands exactly that project to
    `/loci:init`, since a project with sources and no declared build no longer arms
    on its own."""
    r = _run(tmp_path, _C_EDIT, stub=_MEASURABLE, recipe=False, init_status=None)

    assert _nudged(r), f"no nudge for a status-less context: {r.context!r}"


def test_a_project_with_no_context_file_at_all_nudges(tmp_path):
    """A fresh checkout, or a state directory that was wiped. Reads the same way as
    an absent key, and for the same reason: nothing has recorded a decision."""
    r = _run(tmp_path, _C_EDIT, stub=_MEASURABLE, recipe=False)

    assert _nudged(r)


def test_the_nudge_reaches_an_edit_the_classifier_calls_unmeasurable(tmp_path):
    """The nudge is about the PROJECT, not about this edit. A user whose first
    edit is a comment change is exactly the user who has never seen a LOCI
    measurement, so making the nudge ride only on the reminder would lose it in
    the case it is most needed. Alone, it carries the `[loci]` prefix the reminder
    would otherwise have supplied."""
    r = _run(tmp_path, _C_EDIT, stub=_NOT_MEASURABLE, recipe=False,
             init_status="uninitialized")

    assert _nudged(r)
    assert r.context.startswith("[loci] LOCI is not initialized")
    # The half that must not regress: no reminder rode along with it.
    assert "loci-post-edit" not in r.context


def test_a_broken_cli_still_nudges_but_does_not_remind(tmp_path):
    """`rc != 0` and not the usage error: the CLI is unavailable or broken, so the
    skill could not have run either and the reminder stays withheld. Whether the
    project has a recipe is a question about the filesystem, which is still
    answerable."""
    r = _run(tmp_path, _C_EDIT, stub="exit 1", recipe=False,
             init_status="uninitialized")

    assert _nudged(r)
    assert "loci-post-edit" not in r.context


def test_an_unmeasurable_edit_in_an_initialized_project_is_still_silent(tmp_path):
    """The silence this hook has always kept. Nothing to measure and nothing to
    nudge about must still produce no output at all — the `remind=0` rework
    replaced two `exit 0`s and this is the one that had to survive it."""
    r = _run(tmp_path, _C_EDIT, stub=_NOT_MEASURABLE, recipe=True)

    assert r.out == "", f"expected silence, got {r.out!r}"


def test_the_nudge_is_appended_to_the_reminder_as_a_second_sentence(tmp_path):
    """Both at once is the common first edit. They must not run together into one
    word, and the reminder must keep its own opening."""
    r = _run(tmp_path, _C_EDIT, stub=_MEASURABLE, recipe=False,
             init_status="uninitialized")

    ctx = r.context
    assert ctx.startswith("[loci] blink.c was modified")
    assert "MUST invoke" in ctx
    assert "itself. LOCI is not initialized" in ctx, (
        f"the two sentences are not separated: {ctx[-160:]!r}"
    )
    # One `[loci]` prefix, not two.
    assert ctx.count("[loci]") == 1


@pytest.mark.parametrize("path", ["/p/notes.md", "/p/build.json", "/p/x.py"])
def test_a_non_source_edit_never_nudges(tmp_path, path):
    """The extension filter runs first and exits, so a markdown edit cannot burn
    the marker — which would silently consume the one nudge the project gets."""
    r = _run(tmp_path, _edit(path, patch=_ONE_LINE_PATCH), stub=_MEASURABLE,
             recipe=False, init_status="uninitialized")

    assert r.out == ""
    assert _markers(tmp_path / "state") == []


def test_a_failed_edit_never_nudges_and_leaves_the_nudge_available(tmp_path):
    """A `PostToolUseFailure` payload applied nothing, so there is no first edit
    yet. Burning the marker there would spend the project's one nudge on an edit
    that never happened."""
    payload = _edit("/p/blink.c", patch=_ONE_LINE_PATCH)
    payload["error"] = "EPERM: operation not permitted, rename"

    r = _run(tmp_path, payload, stub=_MEASURABLE, recipe=False,
             init_status="uninitialized")

    assert r.out == ""
    assert _markers(tmp_path / "state") == []

    # …and the next real edit still gets it.
    again = _run(tmp_path, _C_EDIT, stub=_MEASURABLE, recipe=False,
                 init_status="uninitialized")
    assert _nudged(again)


def test_an_absent_cli_never_nudges_and_leaves_the_nudge_available(tmp_path):
    """With no `loci` the hook kicks a background install and leaves. `/loci:init`
    needs the CLI it is still installing, so the nudge would be premature — and
    spending the marker on it would mean the project never gets nudged once the
    install lands."""
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    state = tmp_path / "state"
    state.mkdir()
    # The hook detaches `ensure-loci-cli.sh` on this path, which really does run
    # `uv tool install` against the network and write lock/status files into the
    # very state directory this test then inspects. Running the plugin from a copy
    # whose installer is a no-op keeps a unit test off the network and out of the
    # race \u2014 the hook under test is the real one.
    plugin = tmp_path / "plugin"
    (plugin / "hooks").mkdir(parents=True)
    (plugin / "lib").mkdir(parents=True)
    for name in ("post-edit-hook.sh",):
        (plugin / "hooks" / name).write_bytes((PLUGIN_ROOT / "hooks" / name).read_bytes())
    (plugin / "hooks" / "ensure-loci-cli.sh").write_text(
        "#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    for name in ("setup-steps.sh", "loci_log.sh"):
        (plugin / "lib" / name).write_bytes((PLUGIN_ROOT / "lib" / name).read_bytes())

    proc = subprocess.run(
        [_find_bash(), _to_bash_path(plugin / "hooks" / "post-edit-hook.sh")],
        input=json.dumps(_C_EDIT), capture_output=True, text=True, timeout=30,
        env={"PATH": _base_path(), "HOME": _to_bash_path(home),
             "CLAUDE_PROJECT_DIR": _to_bash_path(home),
             "LOCI_STATE_DIR": _to_bash_path(state)},
    )

    assert proc.returncode == 0
    assert proc.stdout.strip() == ""
    assert _markers(state) == []


def test_the_marker_is_keyed_per_project_not_per_machine(tmp_path):
    """Two projects, one state directory, two nudges. A marker keyed on anything
    machine-wide would give the second project none."""
    state = tmp_path / "state"
    a, b = tmp_path / "proj-a", tmp_path / "proj-b"

    first = _run(tmp_path, _C_EDIT, stub=_MEASURABLE, recipe=False,
                 project=a, state_dir=state, init_status="uninitialized")
    second = _run(tmp_path, _C_EDIT, stub=_MEASURABLE, recipe=False,
                  project=b, state_dir=state, init_status="uninitialized")

    assert _nudged(first) and _nudged(second)
    assert _markers(state) == sorted(
        [f"init-nudge-{_ctx_key(a)}", f"init-nudge-{_ctx_key(b)}"]
    )


def test_one_project_reached_by_two_path_spellings_is_nudged_once(tmp_path):
    """The hostile brief's device:inode question, made concrete.

    A hook payload carries the native `C:\\...` spelling and the plugin's own
    library runs under Git Bash, where the same directory is `/c/...`. Keying the
    marker on the path TEXT would make those two different projects and nudge
    twice for one; `hash_cwd` keys on device:inode, which cannot tell them apart
    because they are not apart."""
    state = tmp_path / "state"
    project = tmp_path / "proj"
    project.mkdir()

    first = _run(tmp_path, _C_EDIT, stub=_MEASURABLE, recipe=False,
                 project=project, state_dir=state, init_status="uninitialized")

    # The same directory, spelled the way the tool payload spells it, with a
    # trailing separator for good measure.
    home = tmp_path
    proc = subprocess.run(
        [_find_bash(), _to_bash_path(HOOK)],
        input=json.dumps({**_C_EDIT, "cwd": str(project) + "/"}),
        capture_output=True, text=True, timeout=30,
        env={"PATH": f"{_to_bash_path(home / '.local' / 'bin')}:{_base_path()}",
             "HOME": _to_bash_path(home),
             "LOCI_STATE_DIR": _to_bash_path(state)},
    )

    assert first.context is not None and _NUDGE_TEXT in first.context
    assert _NUDGE_TEXT not in proc.stdout, (
        "the same project nudged twice under two spellings of its path: "
        f"{proc.stdout!r}"
    )
    assert len(_markers(state)) == 1


def test_a_marker_path_that_is_a_directory_yields_no_nudge(tmp_path):
    """A create that cannot succeed must produce silence, not a nudge.

    This fixture pins the EXISTENCE half only, and saying so matters: a directory
    at the marker path defeats `O_EXCL` and satisfies a plain `[ -e ]` equally, so
    it cannot tell an atomic claim from a test-then-create. A hostile reviewer
    demonstrated exactly that — the whole file stayed green with `set -C` replaced
    by TOCTOU. `test_a_state_directory_that_denies_writes_yields_no_nudge_at_all`
    is the one that separates them; this stays because an unlinkable path is its
    own reachable state (a stale directory, a name collision) and the hook must
    not speak into that either."""
    state = tmp_path / "state"
    state.mkdir()
    (state / f"init-nudge-{_ctx_key(tmp_path)}").mkdir()

    r = _run(tmp_path, _C_EDIT, stub=_MEASURABLE, recipe=False,
             init_status="uninitialized", state_dir=state)

    assert not _nudged(r), f"nudged without claiming the marker: {r.context!r}"
    # The measurement half is untouched by it.
    assert r.context is not None and "loci-post-edit" in r.context


def test_two_concurrent_edits_produce_exactly_one_nudge(tmp_path):
    """Two subagents editing inside one turn is an ordinary shape, and both hooks
    run against the same state directory at the same time. `set -C` is `O_EXCL`, so
    the race has a winner rather than two."""
    from concurrent.futures import ThreadPoolExecutor

    state = tmp_path / "state"
    project = tmp_path / "proj"
    project.mkdir()
    home = tmp_path
    (home / ".local" / "bin").mkdir(parents=True, exist_ok=True)
    stub = home / ".local" / "bin" / "loci"
    stub.write_text("#!/usr/bin/env bash\ncat >/dev/null\n" + _MEASURABLE + "\n",
                    encoding="utf-8")
    stub.chmod(0o755)
    state.mkdir()

    def once() -> str:
        proc = subprocess.run(
            [_find_bash(), _to_bash_path(HOOK)],
            input=json.dumps({**_C_EDIT, "cwd": _to_bash_path(project)}),
            capture_output=True, text=True, timeout=60,
            env={"PATH": f"{_to_bash_path(home / '.local' / 'bin')}:{_base_path()}",
                 "HOME": _to_bash_path(home),
                 "LOCI_STATE_DIR": _to_bash_path(state)},
        )
        assert proc.returncode == 0, proc.stderr
        return proc.stdout

    with ThreadPoolExecutor(max_workers=2) as pool:
        outs = [f.result() for f in [pool.submit(once), pool.submit(once)]]

    assert sum(_NUDGE_TEXT in o for o in outs) == 1, (
        f"expected exactly one of two concurrent runs to nudge, got {outs!r}"
    )
    assert len(_markers(state)) == 1


def test_the_nudge_says_what_to_run_and_stays_short(tmp_path):
    """It is one line in a context block the model pays for on every edit. The
    task's byte budget is ~200 B added; the sentence has to name the skill or it is
    a complaint rather than a next step."""
    r = _run(tmp_path, _C_EDIT, stub=_NOT_MEASURABLE, recipe=False,
             init_status="uninitialized")

    line = r.context
    assert "/loci:init" in line
    # The task's budget is ~200 B ADDED; the shipped line is 87 B standalone and
    # adds 81 when appended. A 200 B ceiling here left 2.3x slack, so a sentence
    # that doubled in length would still have passed the test meant to stop it.
    assert len(line.encode("utf-8")) <= 120, f"{len(line)} bytes: {line!r}"


# ---------------------------------------------------------------------------
# The merged payload read
#
# `cwd`, `prompt_id` and `agent_id` come out of ONE jq now, because the nudge
# above needs a field the tail did not previously read and three spawns cost
# ~450 ms of a 5 s budget under Git Bash. Merging them is only safe if the field
# BOUNDARIES are, and there are two ways to get that wrong that both look right:
#
#   * `@tsv` + `IFS=$'\t' read -r a b c` — tab is IFS *whitespace*, so bash strips
#     it leading and collapses runs. A payload with no `cwd` shifts every field
#     left: the project root becomes the turn id and the subagent sentence
#     disappears. This was written, and the existing subagent tests caught it.
#   * no CR strip — the `jq` a Windows install puts on PATH writes CRLF, and
#     `read` keeps the `\r`. An `agent_id` of `$'\r'` is non-empty, so every
#     main-agent edit is told it is running as a subagent.
# ---------------------------------------------------------------------------


def test_a_payload_with_no_cwd_does_not_shift_the_other_two_fields(tmp_path):
    """The field-shift case, with all three consumers observable at once: the
    project root (through the nudge), the turn id, and the subagent sentence. A
    read that loses the leading empty field gets all three wrong in one go, and
    that is not hypothetical — `@tsv` with `IFS=$'\t' read -r a b c` was
    written here first and did exactly this."""
    payload = _edit("/p/app.c", patch=_ONE_LINE_PATCH)
    payload["prompt_id"] = "a19d2923-87c7-3a8c-2000-000000000001"
    payload["agent_id"] = "agent-7"
    assert "cwd" not in payload

    r = _run(tmp_path, payload, stub=_MEASURABLE, recipe=False,
             init_status="uninitialized")

    ctx = r.context
    assert "Pass turn id a19d2923-87c7-3a8c-2000-000000000001 " in ctx, ctx
    assert "subagent" in ctx, ctx
    assert _nudged(r), ctx


def test_a_project_root_with_a_space_survives_the_read(tmp_path):
    """`C:\\Users\\First Last\\proj` is ordinary on Windows, so the read has to
    carry it whole.

    This does NOT pin `IFS=` and must not claim to: `read -r var` with a single
    variable assigns the entire line and strips only LEADING and TRAILING IFS
    whitespace, so an interior space survives either way. The property `IFS=`
    actually buys is the test below."""
    project = tmp_path / "My Project"
    project.mkdir()
    payload = {**_edit("/p/app.c", patch=_ONE_LINE_PATCH),
               "cwd": _to_bash_path(project)}

    r = _run(tmp_path, payload, stub=_MEASURABLE, recipe=False,
             project=project, init_status="uninitialized")

    assert _nudged(r), r.context
    assert _markers(tmp_path / "state") == [f"init-nudge-{_ctx_key(project)}"]


def test_a_leading_space_in_a_field_is_not_stripped_by_the_read(tmp_path):
    """THIS is what `IFS=` buys, and nothing else in the file pins it.

    Without `IFS=`, `read -r` strips leading and trailing IFS whitespace from the
    line \u2014 so a `prompt_id` the harness happened to pad, or a project directory
    whose name begins with a space (legal on POSIX), silently arrives as a
    different value. A turn id that is not the one `pre-edit-hook.sh` stamped is
    the defect the turn id exists to prevent: the compile is handed a baseline
    from another turn and the delta spans two of them."""
    payload = {**_edit("/p/app.c", patch=_ONE_LINE_PATCH),
               "prompt_id": "  padded-turn-id"}

    r = _run(tmp_path, payload, stub=_MEASURABLE, recipe=True)

    assert "Pass turn id   padded-turn-id to the skill" in r.context, (
        f"the leading whitespace was stripped from the turn id: {r.context!r}")


def test_the_read_strips_the_carriage_return_a_windows_jq_writes(tmp_path):
    """Not hypothetical, and not a shim: the `jq` a Windows install puts on PATH
    really does write CRLF (verified on this machine, `jq-1.8.1` via chocolatey:
    `x\\r\\n`). The three fields used to arrive through `$(...)`, which strips
    the whole `\\r\\n`; they now arrive through `read`, which strips only the
    newline. So every field carries a trailing CR before `${_v%"$_CR"}` removes it.

    The consequence of dropping that strip is not cosmetic. `agent_id` becomes
    `$'\\r'` — non-empty — so EVERY main-agent edit is told it is running
    as a subagent, and `turn` carries a CR into the `--turn` value the skill is
    asked to pass on."""
    payload = {**_edit("/p/app.c", patch=_ONE_LINE_PATCH), "prompt_id": "turn-42"}
    assert "agent_id" not in payload

    r = _run(tmp_path, payload, stub=_MEASURABLE, recipe=True)

    ctx = r.context
    assert CR_CHAR not in ctx, f"a carriage return reached the model: {ctx!r}"
    assert "Pass turn id turn-42 to the skill" in ctx, ctx
    assert "subagent" not in ctx, (
        "a main-agent edit was told it is a subagent — the CR made `agent_id` "
        f"look non-empty: {ctx!r}")


# ---------------------------------------------------------------------------
# What round 1 of the hostile review found
# ---------------------------------------------------------------------------


def test_a_recipe_above_the_session_is_still_initialization(tmp_path):
    """The CRITICAL. A `.loci/build.yaml` two directories up is initialization,
    and the single-level `[ -f ]` could not see it.

    The state file cannot rescue that. `detect_and_write_context` records NO
    `init_status` in its `initialized_degraded`/`state` branch \u2014 a recipe on disk
    that `loci init` has not seen, or a wiped state directory \u2014 and its mirror
    only copies when a context file already exists under the recipe root. Add
    that `session-init.sh` is registered `startup` only, so a `--continue`
    session never repairs it, and a subdirectory session reads "nothing
    recorded" and is told its initialized project is not initialized.

    Reproduced against the pre-fix hook: nudged. This asserts the opposite."""
    project = tmp_path / "proj"
    (project / ".loci").mkdir(parents=True)
    (project / ".loci" / "build.yaml").write_text("version: 1\\n", encoding="utf-8")
    (project / ".git").mkdir()          # the walk's ceiling, as a real repo has
    session = project / "src" / "drv"
    session.mkdir(parents=True)

    payload = {**_C_EDIT, "cwd": _to_bash_path(session)}
    r = _run(tmp_path, payload, stub=_MEASURABLE, recipe=False, project=session)

    assert not _nudged(r), (
        f"nudged inside a subdirectory of an initialized project: {r.context!r}")
    # AND THE MARKER RECORDS WHICH FILE SAID SO. Recording nothing made the
    # authority run again on every edit; recording a bare "answered" would outlive
    # the recipe. The path is re-checkable with one `[ -f ]`, which is what makes
    # both properties true at once.
    marks = _markers(tmp_path / "state")
    assert marks == [f"init-nudge-{_ctx_key(session)}"], marks
    recorded = (tmp_path / "state" / marks[0]).read_text(encoding="utf-8").strip()
    assert recorded.endswith(".loci/build.yaml"), (
        f"the marker does not name the recipe that answered: {recorded!r}")


def test_the_walk_stops_at_the_git_ceiling_and_at_home(tmp_path):
    """The walk must not adopt a stray recipe from outside the project.

    `_loci_find_recipe` carries `$HOME` and `.git` ceilings precisely so a
    `~/.loci/build.yaml` cannot silence every project under the home directory.
    A hook that reimplemented the walk without them would go quiet everywhere and
    look exactly like a working feature."""
    home = tmp_path / "home"
    (home / ".loci").mkdir(parents=True)
    (home / ".loci" / "build.yaml").write_text("version: 1\\n", encoding="utf-8")
    project = home / "work" / "proj"
    project.mkdir(parents=True)
    (project / ".git").mkdir()

    payload = {**_C_EDIT, "cwd": _to_bash_path(project)}
    r = _run(home, payload, stub=_MEASURABLE, recipe=False, project=project,
             state_dir=tmp_path / "state")

    assert _nudged(r), (
        "the walk sailed past the ceilings and adopted ~/.loci/build.yaml, which "
        f"silences every project under the home directory: {r.context!r}")


@pytest.mark.parametrize("shape", ["truncated", "directory", "empty",
                                   "not-an-object", "a-number"])
def test_an_unreadable_context_is_not_the_same_answer_as_an_absent_one(
        tmp_path, shape):
    """`.init_status // ""` collapsed four different failures onto the one value
    that nudges.

    The worst of them: a context file truncated mid-write over a recorded
    `unsupported` produced exactly the line the task forbids outright. A
    directory at that path is not hypothetical either \u2014 `lib/setup-steps.sh`
    carries its own guard for it, so the authors have met it."""
    state = tmp_path / "state"
    state.mkdir()
    ctx = state / f"project-context-{_ctx_key(tmp_path)}.json"
    if shape == "truncated":
        ctx.write_text('{"init_status":"unsupported"', encoding="utf-8")
    elif shape == "directory":
        ctx.mkdir()
    elif shape == "empty":
        ctx.write_text("", encoding="utf-8")
    elif shape == "not-an-object":
        ctx.write_text('["init_status"]', encoding="utf-8")
    else:
        ctx.write_text('{"init_status":123}', encoding="utf-8")

    r = _run(tmp_path, _C_EDIT, stub=_MEASURABLE, recipe=False, state_dir=state)

    assert not _nudged(r), f"{shape} read as `nothing recorded`: {r.context!r}"
    assert _markers(state) == [], f"{shape} burned the marker"
    # The measurement half is untouched by any of it.
    assert r.context is not None and "loci-post-edit" in r.context


def test_a_recorded_null_status_still_nudges(tmp_path):
    """The one JSON shape that deliberately does NOT go quiet. A null is the same
    claim as an absent key \u2014 nothing was recorded \u2014 and the four shapes above are
    quiet because they mean "I could not read this", which is a different thing."""
    state = tmp_path / "state"
    state.mkdir()
    (state / f"project-context-{_ctx_key(tmp_path)}.json").write_text(
        '{"init_status":null}', encoding="utf-8")

    r = _run(tmp_path, _C_EDIT, stub=_MEASURABLE, recipe=False, state_dir=state)

    assert _nudged(r), r.context


def test_a_newline_in_the_cwd_costs_nothing_but_the_nudge(tmp_path):
    """One line per field survives a tab and a space; it does not survive a
    NEWLINE inside a value, which is legal in a POSIX directory name. `cwd` is
    the only one of the three that can contain one, so it is read LAST and a
    newline can only add trailing records.

    Read first, it shifted them: the turn id became `dir` and a MAIN-agent edit
    was told it was a subagent. The obvious repair \u2014 demand exactly three, else
    take nothing \u2014 fixed that and threw the turn id away with it, which is
    worse: the three separate reads this replaced delivered it correctly, and a
    missing `--turn` is what lets a PREVIOUS turn's capture be served as this
    edit's Before. So the two ids arrive intact and only the project root is
    lost, which costs an advisory line and no measurement."""
    payload = {**_edit("/p/app.c", patch=_ONE_LINE_PATCH),
               "cwd": "/c/weird\ndir",
               "prompt_id": "the-real-turn-id",
               "agent_id": ""}

    r = _run(tmp_path, payload, stub=_MEASURABLE, recipe=False,
             init_status="uninitialized")

    ctx = r.context
    assert "Pass turn id the-real-turn-id to the skill" in ctx, (
        f"the turn id was shifted or discarded: {ctx!r}")
    assert "subagent" not in ctx, (
        f"a main-agent edit was told it is a subagent: {ctx!r}")
    # The truncated root is DISCARDED rather than keyed. A prefix that happens to
    # exist is the dangerous shape: `hash_cwd` keys it happily and `[ -d ]` passes,
    # so the marker belonging to a different, real project would be claimed — that
    # project never nudged and this one nudged in its place. The next rung of the
    # ladder is the session's own directory, which is the right answer, so the
    # nudge here is correct and keyed where it belongs.
    assert _nudged(r), ctx
    assert _markers(tmp_path / "state") == [f"init-nudge-{_ctx_key(tmp_path)}"]



def _deny_writes(directory: Path) -> bool:
    """Make `directory` genuinely refuse a new file, on either platform.

    POSIX has `chmod`; Windows does not honour it, and a read-only bit does not
    stop a directory entry being created. An explicit DENY ace does, and it works
    for the current user without elevation."""
    if sys.platform == "win32":
        user = os.environ.get("USERNAME")
        if not user:
            return False
        proc = subprocess.run(
            ["icacls", str(directory), "/deny", f"{user}:(WD,AD)"],
            capture_output=True, text=True,
            env={**os.environ, "MSYS_NO_PATHCONV": "1"})
        return proc.returncode == 0
    directory.chmod(0o555)
    return True


def _allow_writes(directory: Path) -> None:
    if sys.platform == "win32":
        user = os.environ.get("USERNAME", "")
        subprocess.run(["icacls", str(directory), "/remove:d", user],
                       capture_output=True, text=True,
                       env={**os.environ, "MSYS_NO_PATHCONV": "1"})
    else:
        directory.chmod(0o755)


def test_a_state_directory_that_denies_writes_yields_no_nudge_at_all(tmp_path):
    """THE reason the claim is `set -C` and not `[ -e ]` then write.

    A test-then-create reads "absent" every time on a state directory that cannot
    be written, so it nudges on EVERY edit, for ever \u2014 the "worse than absent"
    outcome the whole once-per-project design exists to prevent. `set -C` is
    `O_EXCL`: the test and the claim are one syscall, and a create that cannot
    succeed simply produces no nudge.

    A directory sitting at the marker path (an earlier fixture for this) cannot
    tell the two designs apart, because a plain `[ -e ]` is satisfied by it too.
    A genuinely unwritable directory can, and does."""
    state = tmp_path / "state"
    state.mkdir()
    # Seeded BEFORE the deny, because the point is a state directory the hook can
    # READ and cannot WRITE — which is what a restored CI cache or a
    # wrong-ownership home directory actually looks like.
    (state / f"project-context-{_ctx_key(tmp_path)}.json").write_text(
        json.dumps({"init_status": "uninitialized"}), encoding="utf-8")
    if not _deny_writes(state):
        pytest.skip("could not make the state directory refuse writes")
    try:
        outs = [
            _run(tmp_path, _C_EDIT, stub=_MEASURABLE, recipe=False,
                 state_dir=state)
            for _ in range(3)
        ]
    finally:
        _allow_writes(state)

    nudged = [i for i, r in enumerate(outs) if _nudged(r)]
    assert nudged == [], (
        "the marker could not be claimed and the hook nudged anyway, so this "
        f"project is nudged on every edit for ever: runs {nudged}")
    # The measurement half still works — the nudge is advisory, the reminder is not.
    assert all(r.context is not None and "loci-post-edit" in r.context for r in outs)


def test_a_project_whose_recipe_is_removed_can_still_be_nudged(tmp_path):
    """The answer "there is a recipe above" can stop being true.

    `git clean -xdf` removes a gitignored `.loci/`; so does a branch switch, and a
    fresh clone of a machine-local recipe never had one. Recording that answer in
    the marker left a subdirectory session permanently silent while a session at
    the project root still nudged — two sessions of one project giving opposite
    answers. Nothing is recorded, so the scan simply reaches a different
    conclusion the next time."""
    project = tmp_path / "proj"
    (project / ".loci").mkdir(parents=True)
    (project / ".loci" / "build.yaml").write_text("version: 1\\n", encoding="utf-8")
    (project / ".git").mkdir()
    session = project / "src"
    session.mkdir()
    payload = {**_C_EDIT, "cwd": _to_bash_path(session)}

    before = _run(tmp_path, payload, stub=_MEASURABLE, recipe=False, project=session)
    assert not _nudged(before), before.context

    import shutil as _shutil
    _shutil.rmtree(project / ".loci")

    after = _run(tmp_path, payload, stub=_MEASURABLE, recipe=False, project=session)
    assert _nudged(after), (
        "the project is uninitialized again and this session can never be told: "
        f"{after.context!r}")


def _plugin_with_a_spying_walk(tmp_path: Path,
                               recipe_answers: bool = False) -> tuple[Path, Path]:
    """A plugin copy whose `_loci_find_recipe` records that it was called.

    The candidate scan's whole job is to keep the authority from running, and the
    only honest way to check that is to watch the authority. Timing cannot: it is
    the machine's answer, not the code's."""
    plugin = tmp_path / "plugin"
    (plugin / "hooks").mkdir(parents=True)
    (plugin / "lib").mkdir(parents=True)
    (plugin / "hooks" / "post-edit-hook.sh").write_bytes(
        (PLUGIN_ROOT / "hooks" / "post-edit-hook.sh").read_bytes())
    for name in ("loci_log.sh", "loci_json.sh"):
        (plugin / "lib" / name).write_bytes((PLUGIN_ROOT / "lib" / name).read_bytes())
    spy = tmp_path / "walk-was-called"
    lib = (PLUGIN_ROOT / "lib" / "setup-steps.sh").read_text(encoding="utf-8")
    # Redefined AFTER the original, so this is the definition the hook calls.
    if recipe_answers:
        # Answers the way the real one does — it PRINTS the recipe it found, which
        # is what the hook records. A spy that only refused could not exercise that.
        body = (
            '    printf x >> "%s"\n'
            '    d="$1"\n'
            '    while [ -n "$d" ]; do\n'
            '        [ -f "$d/.loci/build.yaml" ] && { printf %%s "$d/.loci/build.yaml"; return 0; }\n'
            '        d="${d%%/*}"\n'
            '    done\n'
            '    return 1\n') % _to_bash_path(spy)
    else:
        body = f'    printf x >> "{_to_bash_path(spy)}"\n    return 1\n'
    lib += "\n_loci_find_recipe() {\n" + body + "}\n"
    (plugin / "lib" / "setup-steps.sh").write_text(lib, encoding="utf-8", newline="\n")
    return plugin, spy


def _run_plugin(plugin: Path, payload: dict, home: Path, state: Path,
                stub: str = _MEASURABLE) -> str:
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / "loci").write_text(
        "#!/usr/bin/env bash\ncat >/dev/null\n" + stub + "\n", encoding="utf-8")
    (bin_dir / "loci").chmod(0o755)
    state.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [_find_bash(), _to_bash_path(plugin / "hooks" / "post-edit-hook.sh")],
        input=json.dumps(payload), capture_output=True, text=True, timeout=60,
        env={"PATH": f"{_to_bash_path(bin_dir)}:{_base_path()}",
             "HOME": _to_bash_path(home),
             "LOCI_STATE_DIR": _to_bash_path(state)},
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_the_authority_is_not_consulted_when_no_recipe_is_anywhere_above(tmp_path):
    """The budget fix, pinned on behaviour rather than on a stopwatch.

    `_loci_find_recipe` spends a `stat` per level on its ceilings. Called on every
    first edit it took this hook from 2.2 s to 3.6 s median and 5.0 s worst
    against a registered 5 s timeout — 3 of 15 runs killed, and a kill loses the
    MANDATORY post-edit reminder as well as the nudge. The candidate scan in front
    of it spawns nothing and answers "there is nothing to ask about" for every
    project that has never been initialized, which is the population the nudge
    exists for."""
    plugin, spy = _plugin_with_a_spying_walk(tmp_path)
    project = tmp_path / "proj"
    project.mkdir()

    out = _run_plugin(plugin, {**_C_EDIT, "cwd": _to_bash_path(project)},
                      tmp_path / "home", tmp_path / "state")

    assert _NUDGE_TEXT in out, out
    assert not spy.exists(), (
        "the upward walk ran even though no `.loci/build.yaml` exists anywhere "
        "above the session; that is a `stat` per level on every first edit")


def test_the_authority_is_consulted_when_a_candidate_exists(tmp_path):
    """The other half: the scan is deliberately more permissive than the rule, so
    it must hand every candidate to the authority rather than deciding itself. A
    scan that answered on its own would adopt `~/.loci/build.yaml` for every
    project under the home directory — the ceilings are exactly what
    `_loci_find_recipe` is for."""
    plugin, spy = _plugin_with_a_spying_walk(tmp_path)
    project = tmp_path / "proj"
    (project / ".loci").mkdir(parents=True)
    (project / ".loci" / "build.yaml").write_text("version: 1\\n", encoding="utf-8")
    session = project / "src"
    session.mkdir()

    out = _run_plugin(plugin, {**_C_EDIT, "cwd": _to_bash_path(session)},
                      tmp_path / "home", tmp_path / "state")

    assert spy.exists(), (
        "a recipe sits above the session and the authority was never asked; the "
        "scan decided on its own, without the $HOME and .git ceilings")
    # The stub authority returns 1 ("no recipe you may adopt"), so the nudge still
    # fires — which is the point: the scan does not get a vote.
    assert _NUDGE_TEXT in out, out


def test_the_candidate_scan_walks_a_native_windows_path(tmp_path):
    """The walk gets the payload's spelling, not the test harness's.

    A hook payload carries `C:\\Users\\...`; Git Bash resolves that in a FILE TEST but
    not in a parameter expansion, so a `/`-only step stopped the walk at the first
    level and found nothing above the session at all \u2014 which puts the CRITICAL
    this walk exists for straight back, on the only path spelling production ever
    sees. Every test above uses `_to_bash_path`, so every one of them would have
    stayed green.

    A bracket class does not rescue it either: inside a pattern, `[/\\\\]` reads the
    backslash as an escape for the `]` and matches nothing, silently."""
    project = tmp_path / "proj"
    (project / ".loci").mkdir(parents=True)
    (project / ".loci" / "build.yaml").write_text("version: 1\\n", encoding="utf-8")
    (project / ".git").mkdir()
    session = project / "src" / "drv"
    session.mkdir(parents=True)

    # The NATIVE spelling, exactly as a real payload carries it.
    native = str(session)
    if sys.platform == "win32":
        assert BS_CHAR in native, native
    r = _run(tmp_path, {**_C_EDIT, "cwd": native}, stub=_MEASURABLE,
             recipe=False, project=session)

    assert not _nudged(r), (
        "the walk could not step up a native path, so a subdirectory session of "
        f"an initialized project was told it is uninitialized: {r.context!r}")


def test_two_concatenated_objects_are_unreadable_not_silent(tmp_path):
    """`jq` without `-s` streams one result per object, so a context file holding
    `{}{}` produced two lines, matched no arm, and went quiet \u2014 where "nothing
    recorded" must nudge and "I cannot read this" must not.

    `lib/setup-steps.sh`'s own reader guards this exact hazard for the same file,
    which is why it is worth guarding here: it is a shape that reaches production
    (an interrupted write, a doubled append)."""
    state = tmp_path / "state"
    state.mkdir()
    (state / f"project-context-{_ctx_key(tmp_path)}.json").write_text(
        "{}{}", encoding="utf-8")

    r = _run(tmp_path, _C_EDIT, stub=_MEASURABLE, recipe=False, state_dir=state)

    assert not _nudged(r), f"a file jq cannot read as one object nudged: {r.context!r}"
    assert _markers(state) == []


def test_a_status_that_looks_like_the_sentinel_is_not_read_as_one(tmp_path):
    """The tag is a PREFIX, not a magic value.

    A bare `__none__` sentinel meant a context recording `init_status:
    "__none__"` was read as "nothing recorded" and nudged, where an unrecognised
    value must stay quiet. Nothing else in the file could tell the two apart."""
    state = tmp_path / "state"
    state.mkdir()
    (state / f"project-context-{_ctx_key(tmp_path)}.json").write_text(
        json.dumps({"init_status": "__none__"}), encoding="utf-8")

    r = _run(tmp_path, _C_EDIT, stub=_MEASURABLE, recipe=False, state_dir=state)

    assert not _nudged(r), (
        "a status this version does not recognise was read as the empty case: "
        f"{r.context!r}")


def test_a_recorded_empty_status_reads_as_nothing_recorded(tmp_path):
    """The other side of the tag: `init_status: ""` is what the writer produces
    for "no value", and it must keep behaving as it did before the tag existed."""
    state = tmp_path / "state"
    state.mkdir()
    (state / f"project-context-{_ctx_key(tmp_path)}.json").write_text(
        json.dumps({"init_status": ""}), encoding="utf-8")

    r = _run(tmp_path, _C_EDIT, stub=_MEASURABLE, recipe=False, state_dir=state)

    assert _nudged(r), r.context


def test_the_state_migration_carries_the_marker_with_the_context(tmp_path):
    """A key change must not buy the project a second first nudge.

    `_migrate_legacy_state` re-keys the context, the measurements and the stats.
    The marker means "this project has already been told" and is keyed the same
    way, so leaving it behind gives one project two nudges and orphans a file
    under a key nothing will ever look up again."""
    state = tmp_path / "state"
    state.mkdir()
    project = tmp_path / "proj"
    project.mkdir()
    legacy = "deadbeef1234"
    (state / f"project-context-{legacy}.json").write_text(
        json.dumps({"project_root": _to_bash_path(project),
                    "init_status": "uninitialized"}), encoding="utf-8")
    (state / f"init-nudge-{legacy}").write_text("", encoding="utf-8")

    script = (
        'PLUGIN_DIR="$1"; STATE_DIR="$2"; export LOCI_STATE_DIR="$STATE_DIR"; '
        'cd "$3" || exit 1; '
        '. "$PLUGIN_DIR/lib/setup-steps.sh" >/dev/null 2>&1; '
        '_migrate_legacy_state "$4" main'
    )
    key = _ctx_key(project)
    subprocess.run(
        [_find_bash(), "-c", script, "bash", _to_bash_path(PLUGIN_ROOT),
         _to_bash_path(state), _to_bash_path(project), key],
        capture_output=True, text=True, timeout=60)

    assert (state / f"project-context-{key}.json").is_file(), (
        "the fixture did not exercise the migration at all")
    assert _markers(state) == [f"init-nudge-{key}"], (
        "the marker did not move with the context, so this project will be "
        f"nudged again under its new key: {_markers(state)}")


def test_a_prefix_of_the_cwd_is_never_keyed_when_the_read_shifted(tmp_path):
    """The newline case with the prefix directory made REAL, which is when it
    bites.

    A `cwd` of `<proj>\nX` truncates to `<proj>`, and if `<proj>` exists then
    `hash_cwd` keys it and `[ -d ]` passes: the marker of a different, real
    project is claimed. That project is then never nudged and this one is nudged
    under its name — the opposite of the double-nudge the guard was written for.
    Discarding a root we know is truncated is the only answer that cannot pick
    the wrong project."""
    victim = tmp_path / "victim"
    victim.mkdir()
    state = tmp_path / "state"
    state.mkdir()

    r = _run(tmp_path, {**_edit("/p/app.c", patch=_ONE_LINE_PATCH),
                        "cwd": _to_bash_path(victim) + "\nX",
                        "prompt_id": "t"},
             stub=_MEASURABLE, recipe=False, state_dir=state)

    assert f"init-nudge-{_ctx_key(victim)}" not in _markers(state), (
        "a shifted read claimed the marker of a real project it was not editing; "
        "that project can now never be nudged")
    assert r.context is not None and "loci-post-edit" in r.context


def test_the_authority_is_asked_once_and_not_on_every_edit(tmp_path):
    """Round 2 stopped claiming the marker when a recipe answered, so the scan
    found the same candidate on every edit and the authority ran every time — a
    `stat` per level, for ever, in exactly the state the two-step exists to make
    cheap. Measured at +0.7 s to +1.2 s per edit: round 1's first-edit cost, made
    permanent.

    Behavioural, not timed: the plugin copy records every call."""
    plugin, spy = _plugin_with_a_spying_walk(tmp_path, recipe_answers=True)
    project = tmp_path / "proj"
    (project / ".loci").mkdir(parents=True)
    (project / ".loci" / "build.yaml").write_text("version: 1\n", encoding="utf-8")
    session = project / "src"
    session.mkdir()
    state = tmp_path / "state"

    for _ in range(5):
        out = _run_plugin(plugin, {**_C_EDIT, "cwd": _to_bash_path(session)},
                          tmp_path / "home", state)
        assert _NUDGE_TEXT not in out, out

    calls = spy.read_text(encoding="utf-8").count("x") if spy.exists() else 0
    assert calls == 1, (
        f"the authority ran {calls} times over 5 edits; it must be asked once and "
        "its answer recorded")


def test_a_recipe_removed_after_the_marker_was_written_re_opens_the_question(tmp_path):
    """The property recording nothing was reaching for, kept without its cost.

    `git clean -xdf` removes a gitignored `.loci/`; so does a branch switch. The
    marker names the file that answered, so the next edit re-checks it with one
    `[ -f ]` and finds the answer stale."""
    project = tmp_path / "proj"
    (project / ".loci").mkdir(parents=True)
    (project / ".loci" / "build.yaml").write_text("version: 1\n", encoding="utf-8")
    (project / ".git").mkdir()
    session = project / "src"
    session.mkdir()
    payload = {**_C_EDIT, "cwd": _to_bash_path(session)}

    before = _run(tmp_path, payload, stub=_MEASURABLE, recipe=False, project=session)
    assert not _nudged(before), before.context

    import shutil as _shutil
    _shutil.rmtree(project / ".loci")

    after = _run(tmp_path, payload, stub=_MEASURABLE, recipe=False, project=session)
    assert _nudged(after), (
        "the project is uninitialized again and the recorded answer outlived it: "
        f"{after.context!r}")


def _link_dir(link: Path, target: Path) -> bool:
    """A directory link, by whatever means this platform allows without a
    privilege. Returns False when neither works, so the caller can skip rather
    than pass for the wrong reason."""
    try:
        link.symlink_to(target, target_is_directory=True)
        return link.is_dir()
    except (OSError, NotImplementedError):
        pass
    if sys.platform == "win32":
        # A JUNCTION needs no privilege where a symlink does.
        proc = subprocess.run(
            ["cmd.exe", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, text=True,
            env={**os.environ, "MSYS_NO_PATHCONV": "1"})
        return proc.returncode == 0 and link.is_dir()
    return False


def test_the_candidate_scan_sees_through_a_linked_session_directory(tmp_path):
    """The scan must never be STRICTER than the authority — it is only allowed to
    be more permissive, because a scan that finds nothing never asks.

    `_loci_find_recipe` starts with `cd "$1" && pwd -P`, so it walks the PHYSICAL
    chain. Walking the logical one leaves the project entirely when a link points
    INTO it: `outer/link -> real/proj/src`, recipe at `real/proj/.loci/`, session
    at `outer/link/drv`. The authority finds the recipe; the logical walk climbs
    out through `outer` and never comes back — so an initialized project is told
    it is not, and burns its marker saying so. The CRITICAL this branch exists
    for, reached through a link."""
    real = tmp_path / "real" / "proj"
    (real / ".loci").mkdir(parents=True)
    (real / ".loci" / "build.yaml").write_text("version: 1\n", encoding="utf-8")
    (real / "src" / "drv").mkdir(parents=True)
    outer = tmp_path / "outer"
    outer.mkdir()
    if not _link_dir(outer / "link", real / "src"):
        pytest.skip("this platform will not create a directory link unprivileged")
    session = outer / "link" / "drv"

    r = _run(tmp_path, {**_C_EDIT, "cwd": _to_bash_path(session)},
             stub=_MEASURABLE, recipe=False, project=session)

    assert not _nudged(r), (
        "the scan walked the logical path, left the project, and never asked the "
        f"authority: {r.context!r}")


# ── AAD-7607: the reminder must not demand a skill that can only refuse ─────

_ARTIFACT_ONLY = (
    "echo '{\"ok\":true,\"data\":{\"measurable\":true,\"artifact_only\":true}}'")
_MEASURABLE_WITH_DB = (
    "echo '{\"ok\":true,\"data\":{\"measurable\":true,\"artifact_only\":false}}'")


def test_an_artifact_only_recipe_silences_the_reminder(tmp_path):
    """The edit is measurable and the project still cannot measure it.

    Under an artifact-only recipe — a linked binary recorded, no compile database
    — `loci analyse prepare` answers `compdb_absent` for every source, so
    `loci-post-edit` can do nothing but relay that refusal. Demanding it after
    every C edit is a refusal on repeat, and a refusal on repeat is what a session
    works around: in the run this came from, by rebuilding the artifact, which is
    the only pre-edit binary such a project has. `session-init.sh` states the
    scope once instead, with what DOES measure here.
    """
    r = _run(tmp_path, _C_EDIT, stub=_ARTIFACT_ONLY)

    assert r.context is None or "You MUST invoke" not in r.context, (
        f"the reminder survived an artifact-only recipe: {r.context!r}")


def test_the_reminder_stands_when_a_compile_database_exists(tmp_path):
    """The non-vacuity control for the test above.

    Same payload, same `measurable`, one field different — so a silence there is
    the new field's doing and not the fixture's.
    """
    r = _run(tmp_path, _C_EDIT, stub=_MEASURABLE_WITH_DB)

    assert r.context is not None and "You MUST invoke" in r.context


def test_a_cli_that_does_not_report_the_field_keeps_the_reminder(tmp_path):
    """Absent is not `true`, and the difference is a measurement.

    A `loci` older than this field reports no `artifact_only` at all. Reading that
    as "artifact-only" would silence post-edit for every project on that CLI —
    under-triggering loses a measurement invisibly, which is the trade this hook
    has always resolved the other way.
    """
    r = _run(tmp_path, _C_EDIT, stub=_MEASURABLE)

    assert r.context is not None and "You MUST invoke" in r.context


def test_the_nudge_is_not_what_silences_it(tmp_path):
    """An artifact-only project is INITIALIZED, so it was never in the nudge's
    population — the silence has to come from the reminder branch, not from a
    first-edit marker being burned."""
    r = _run(tmp_path, _C_EDIT, stub=_ARTIFACT_ONLY)

    assert not _nudged(r)
    assert _markers(tmp_path / "state") == [], (
        "an initialized project must not burn a first-edit marker")
