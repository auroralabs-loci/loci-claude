"""The eval suite's graders — the code that decides whether an eval passed.

Nothing tested these until now, which is an odd blind spot for a test suite to
have: a grader that says PASS for the wrong reason makes every eval above it
decorative, and the eval suite is what stands between a skill regression and a
release. `grade_bash_post_edit` had exactly that shape — it returned PASS the
moment a report said it had no pre-edit baseline, so an eval written to prove the
baseline SURVIVES a multi-edit turn (the defect this whole branch exists to fix)
would have passed by reporting that it had none.

They were also unreachable: `run_evals.sh` parses arguments and `cd`s into the
fixture root at the top level, so it cannot be sourced. The graders now live in
`lib/eval-graders.sh`, which has no side effects, and this file drives them the
way `test_compile_read_back.py` drives the compile script — as bash, with real
strings, not by re-implementing the regexes in Python.

The responses below are shaped like the real thing. Where one is a fragment, the
test says which structural element it is exercising and why the rest is absent.
"""
from __future__ import annotations

import re
import shutil
import os
import subprocess
from shlex import quote as shlex_quote
import types
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
LIB = PLUGIN_ROOT / "lib" / "eval-graders.sh"


def _find_bash() -> str | None:
    if sys.platform == "win32":
        for cand in (
            r"C:\Program Files\Git\usr\bin\bash.exe",
            r"C:\Program Files (x86)\Git\usr\bin\bash.exe",
        ):
            if Path(cand).is_file():
                return cand
    return shutil.which("bash")



def _to_bash_path(p: Path) -> str:
    """`C:/x/y` → `/c/x/y`. Git Bash ignores a drive-letter entry in PATH."""
    import re
    s = Path(p).as_posix()
    m = re.match(r"^([A-Za-z]):/(.*)$", s)
    return f"/{m.group(1).lower()}/{m.group(2)}" if m else s

pytestmark = pytest.mark.skipif(_find_bash() is None, reason="bash required")


def grade(response: str, should_trigger: str = "true",
          expect_baseline: str = "false",
          expect_no_change: str = "false",
          tool_calls: str | None = None) -> tuple[str, str]:
    """Run `grade_bash_post_edit` and return (verdict, reason).

    The response is passed through a FILE and read back with `$(cat …)` rather
    than interpolated into the script: a report is many lines of markdown with
    quotes, backticks and `%` in it, and a version that pasted it into the
    command would be testing the harness's quoting instead of the grader.
    `tool_calls` is what the harness exports as LOCI_EVAL_TOOL_CALLS — the
    transcript's tool calls, one per line — and None leaves the grader on its
    text-only fallback, as a grader driven by hand is.
    """
    return _run("grade_bash_post_edit", response,
                [should_trigger, expect_baseline, expect_no_change],
                tool_calls=tool_calls)


def grade_preflight(response: str, should_trigger: str = "true",
                    tool_calls: str | None = None) -> tuple[str, str]:
    """Run `grade_bash` — the preflight grader — and return (verdict, reason)."""
    return _run("grade_bash", response, [should_trigger], tool_calls=tool_calls)


def _run(func: str, response: str, args: list[str],
         tool_calls: str | None = None) -> tuple[str, str]:
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        rf = Path(td) / "response.txt"
        rf.write_text(response, encoding="utf-8")
        quoted = " ".join(f'"{a}"' for a in args)
        env_line = ""
        if tool_calls is not None:
            tf = Path(td) / "tools.txt"
            tf.write_text(tool_calls, encoding="utf-8")
            env_line = f'export LOCI_EVAL_TOOL_CALLS="$(cat "{tf.as_posix()}")"\n'
        script = (
            f'set -euo pipefail\n'
            f'source "{LIB.as_posix()}"\n'
            f'{env_line}'
            f'R=$(cat "{rf.as_posix()}")\n'
            f'{func} "$R" {quoted}\n'
        )
        proc = subprocess.run([_find_bash(), "-c", script],
                              capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"grader exited {proc.returncode}: {proc.stderr!r}"
    out = proc.stdout.strip()
    assert "|" in out, f"grader printed no verdict: {out!r}"
    verdict, _, reason = out.partition("|")
    return verdict, reason


# A complete, well-formed post-edit report with a real delta, in the shape the
# skill renders: no `Recipe:` line — post-edit reports a delta and never prints
# the absolute verbs' provenance line (its SKILL.md; its own eval pins it). Every
# other fixture in this file is this one with exactly one thing changed, so a
# test that goes green for an unrelated reason has nowhere to hide.
FULL = """## Post-Edit: aes_encrypt

| Metric | Before | After | Note |
| --- | --- | --- | --- |
| Performance | 4862 ns | 5104 ns | +5.0% |
| Energy | 12.1 uWs | 12.7 uWs | +4.9% |

Verdict: **CAUTION**

LOCI post-edit · aes_encrypt +5.0%
"""

NO_BASELINE = """## Post-Edit: aes_encrypt

| Metric | Before | After | Note |
| --- | --- | --- | --- |
| Performance | — | 5104 ns | no pre-edit artifact — absolute values only |

Verdict: **PASS**

LOCI post-edit · aes_encrypt (first measurement)
"""

NO_CHANGE = """## Post-Edit: aes_encrypt

`loci elf diff` reports 0 changed functions — the compiled functions are
unchanged, so there is no timing delta to report. ROM and RAM are identical and
no stack frame moved.

Verdict: **PASS**

LOCI post-edit · aes_encrypt (no net change)
"""


assert "Recipe:" not in FULL + NO_BASELINE + NO_CHANGE, "post-edit prints no Recipe: line"


# ── the states that already worked, so a regression here is visible ──────────

def test_a_complete_report_passes():
    verdict, reason = grade(FULL)
    assert verdict == "PASS", reason


def test_prose_about_running_the_skill_is_not_a_report():
    """The whole reason the grader anchors on a real header: "I'll run the
    post-edit analysis now" used to be scored as an invocation."""
    verdict, _ = grade("I'll run the post-edit analysis on your change now.")
    assert verdict == "FAIL"


def test_a_report_with_no_verdict_fails():
    verdict, _ = grade(FULL.replace("Verdict: **CAUTION**", "")
                           .replace("LOCI post-edit · aes_encrypt +5.0%", ""))
    assert verdict == "FAIL"


def test_should_not_trigger_is_graded_the_other_way():
    assert grade(FULL, should_trigger="false")[0] == "FAIL"
    assert grade("Here is the refactor you asked for.",
                 should_trigger="false")[0] == "PASS"


# ── the recipe flow (T14): what auto-init owes ────────────────────────────────
#
# Since Phase 4 every measurement rests on a recipe. The one thing about that a
# bash grader can judge is the flow: a coded `not_initialized` in the transcript
# is followed by the `loci init` CALL the init rule prescribes — relayed as text
# and left there is exactly the failure the rule exists to prevent. The `Recipe:`
# provenance line is NOT graded here: neither skill these graders grade prints
# it (preflight's "One exception, one line"; post-edit "reports a delta … Never
# print the full absolute-verb line"), and a gate on it failed every correct
# report (review round 3). The absolute verbs' claude-graded evals assert it.

def test_a_post_edit_report_owes_no_recipe_line():
    """The skill's own shape passes as it is; a report that does print one is
    not this grader's business either — its own eval pins the absence."""
    assert grade(FULL)[0] == "PASS"
    with_line = FULL.replace("| Metric |", "Recipe: .loci/build.yaml (target armv6-m, validated compile-check)\n\n| Metric |", 1)
    assert grade(with_line)[0] == "PASS"


#: The CLI's own refusal, as `recipe_flags.for_compile` words it — recovery
#: sentence included. Pasting it is the recorded failure mode.
_REFUSAL = ("Error: this project is not initialized for LOCI — no .loci/build.yaml governs "
            "/tmp/x. Run `/loci:init` (or `loci init --auto`) to record how this project "
            "builds; LOCI has no build knowledge for it until then. (error.code: not_initialized)")


# The verdicts below are INVERTED from what they were, and the detection they
# rest on is not: `_names_an_init_call` still answers "did a bash call run
# init", and every review finding these tests encode is still pinned here.
# What changed is which answer is a failure. Adopting a project is the user's
# decision, so relaying `not_initialized` and naming `/loci:init` is the
# correct flow, and an init call nobody asked for is the defect.


def test_a_pasted_refusal_with_no_init_call_passes():
    """The refusal names `loci init --auto` itself, so a text match on those words
    passed the exact transcript this check exists for (review round 1, c1). With
    the tool calls known, init counts only as a call — which is what lets the
    relay-and-stop flow pass instead of being mistaken for having run it."""
    verdict, reason = grade(_REFUSAL + "\n\n" + FULL,
                            tool_calls="Bash: loci analyse prepare --project-root /tmp/x")
    assert verdict == "PASS", reason


def test_an_init_that_ran_as_a_tool_call_fails():
    """The whole point: a skill that adopted the project unasked."""
    verdict, reason = grade(_REFUSAL + "\n\n" + FULL,
                            tool_calls="Bash: loci analyse prepare --project-root /tmp/x\n"
                                       "Bash: loci init --auto --project-root /tmp/x\n"
                                       "Bash: loci analyse prepare --project-root /tmp/x")
    assert verdict == "FAIL" and "RAN `loci init`" in reason, reason


def test_narrated_init_does_not_count_when_the_tool_calls_are_known():
    """Prose about running init is not running it, so this transcript is clean."""
    verdict, reason = grade(
        "The CLI answered `not_initialized`; running `loci init --auto` first…\n\n" + FULL,
        tool_calls="Bash: loci analyse prepare --project-root /tmp/x")
    assert verdict == "PASS", reason


def test_a_transcript_with_no_tool_calls_at_all_is_not_an_init_either():
    """The harness writes `(none)` for a transcript that called nothing, and that
    is evidence too — evidence that init did not run, which is what is owed."""
    verdict, reason = grade(_REFUSAL + "\n\n" + FULL, tool_calls="(none)")
    assert verdict == "PASS", reason


def test_without_tool_calls_the_pasted_refusal_is_still_not_an_init():
    """A grader driven by hand has only the text. The refusal's recovery sentence
    is stripped before the text is read, so pasting the refusal alone is not
    read as an init and passes; a separate claim of having run init is the most
    the text can show, and that is the failure."""
    verdict, reason = grade(_REFUSAL + "\n\n" + FULL)
    assert verdict == "PASS", reason
    verdict, reason = grade(_REFUSAL + "\n\nRan `loci init --auto`, then measured.\n\n" + FULL)
    assert verdict == "FAIL" and "RAN `loci init`" in reason, reason


def test_a_not_initialized_answer_left_as_text_passes():
    """Left as text with no numbers IS the flow now — it used to be the failure."""
    verdict, reason = grade(
        "The CLI answered `not_initialized` for this project, so no numbers.\n\n" + FULL)
    assert verdict == "PASS", reason


def test_a_wrapped_pasted_refusal_is_still_not_an_init():
    """A pasted envelope wraps where the model's renderer likes; the recovery
    sentence has to be stripped across the wrap (review round 2, c-r2-3)."""
    wrapped = _REFUSAL.replace("to record how this project", "to record how this\nproject")
    verdict, reason = grade(wrapped + "\n\n" + FULL)
    assert verdict == "PASS", reason


def test_probe_set_and_add_file_are_not_an_init_call():
    """`loci init probe` is read-only and the init skill's own first step; `set`
    records one knob, `add-file` one entry. A transcript that probed and stopped
    has not initialized anything (review round 2, c-r2-1) — and probing to
    answer a question is what the relay flow may legitimately still do."""
    for call in ("Bash: loci init probe --project-root /tmp/x",
                 "Bash: loci init set rust.features=max-pure",
                 "Bash: loci init add-file src/new.c --project-root /tmp/x"):
        verdict, reason = grade(_REFUSAL + "\n\n" + FULL, tool_calls=call)
        assert verdict == "PASS", f"{call!r}: {reason}"


def test_the_console_script_spelled_with_exe_is_an_init_call():
    verdict, reason = grade(_REFUSAL + "\n\n" + FULL,
                            tool_calls="Bash: loci.exe init --auto --project-root C:/x")
    assert verdict == "FAIL" and "RAN `loci init`" in reason, reason


def test_an_init_call_is_recognised_however_the_console_script_is_spelled():
    """Path-qualified, quoted, `.exe`, two spaces, a trailing separator, after
    `cd … &&`, or on the second line of a multi-line command (which the harness
    joins with ` ; `) — all real init calls, all were graded "no call" once
    (review round 3, c-r3-2). Every one must still be SEEN — a spelling is not
    a way around the rule."""
    for call in ("Bash: ~/.local/bin/loci init --auto",
                 "Bash: ./.venv/Scripts/loci.exe init --auto --project-root C:/x",
                 'Bash: "loci" init --auto',
                 "Bash: loci  init --auto",
                 "Bash: loci init --auto; echo done",
                 "Bash: loci init --auto | jq .",
                 "Bash: cd /tmp/x && loci init --auto",
                 "Bash: cd /tmp/x ; loci init --auto"):
        verdict, reason = grade(_REFUSAL + "\n\n" + FULL, tool_calls=call)
        assert verdict == "FAIL" and "RAN `loci init`" in reason, f"{call!r}: {reason}"


def test_what_merely_mentions_init_is_not_an_init_call():
    """A subcommand after global options, `--help`, a Grep for the words, an echo
    of them — each names init and runs none (review round 3, c-r3-1)."""
    for call in ("Bash: loci init --project-root /tmp/x probe",
                 "Bash: loci init -f yaml probe",
                 "Bash: loci init --project-root /tmp/x set build.compdb.select.prefer_output=app",
                 "Bash: loci init --project-root /tmp/x add-file src/new.c",
                 "Bash: loci init --help",
                 "Grep: loci init",
                 'Bash: echo "loci init --auto"',
                 "Read: /tmp/x/README.md ; loci init --auto is documented there"):
        verdict, reason = grade(_REFUSAL + "\n\n" + FULL, tool_calls=call)
        assert verdict == "PASS", f"{call!r}: {reason}"


def test_the_pasted_refusal_without_backticks_is_still_not_an_init():
    """The fallback strips the recovery sentence however the model rendered it
    (review round 3, c-r3-3)."""
    plain = _REFUSAL.replace("`", "")
    verdict, reason = grade(plain + "\n\n" + FULL)
    assert verdict == "PASS", reason


# ── the preflight grader (T14): the init flow, and NO provenance gate ─────────
#
# Preflight prints no `Recipe:` line by design — "One exception, one line" in its
# SKILL.md, pinned by pf-recipe-1 — so the provenance gate that every absolute
# report owes would fail every correct preflight. Round 1 put it there with no
# test driving `grade_bash` at all (review round 2, b1); these are those tests.

PREFLIGHT = """## Preflight: aes_encrypt

| Callee | Safety | Performance | Energy |
| --- | --- | --- | --- |
| aes_round | ok | 812 ns | 2.1 uWs |

Execution fit: **PASS**
"""


def test_a_correct_preflight_passes_without_a_recipe_line():
    assert "Recipe:" not in PREFLIGHT
    verdict, reason = grade_preflight(PREFLIGHT)
    assert verdict == "PASS", reason


def test_a_preflight_that_ran_init_unbidden_fails():
    """The preflight grader carries the same inverted assertion, and preflight is
    where it bites hardest: MANDATORY in `/plan` is the strongest auto-run wording
    in the plugin, and it still does not license adopting someone's repo."""
    verdict, reason = grade_preflight(_REFUSAL + "\n\n" + PREFLIGHT, tool_calls="(none)")
    assert verdict == "PASS", reason
    verdict, reason = grade_preflight(_REFUSAL + "\n\n" + PREFLIGHT,
                                      tool_calls="Bash: loci init --auto --project-root /tmp/x")
    assert verdict == "FAIL" and "RAN `loci init`" in reason, reason


def test_the_argued_verdict_vocabulary_is_a_verdict_too():
    """Since 2026-09-03 a skill with no contract bound to judge by ARGUES —
    `⚑ flagged —` / `○ cleared —` — instead of rendering PASS/CAUTION/FAIL. The
    graders knew only the judged vocabulary, so every correct run with no
    contract (the fixtures' common case) was "invoked but produced no real
    verdict line"; found by the third real eval pass of T14 on the BLE project."""
    cleared = PREFLIGHT.replace("Execution fit: **PASS**",
                                "Execution fit: ○ cleared — 136.9 ns / 21.21 mWs measured; no contract covers this function")
    assert "PASS" not in cleared.split("Execution fit")[1]
    verdict, reason = grade_preflight(cleared)
    assert verdict == "PASS", reason
    flagged = PREFLIGHT.replace("Execution fit: **PASS**",
                                "Execution fit: ⚑ flagged — unbounded recursion in parser_descend")
    assert grade_preflight(flagged)[0] == "PASS"
    # …and a sentence that merely contains the words is still not a verdict line.
    assert grade_preflight(PREFLIGHT.replace("Execution fit: **PASS**",
                                             "The path was cleared of obstacles."))[0] == "FAIL"

    argued = FULL.replace("Verdict: **CAUTION**",
                          "Verdict: ⚑ flagged — hot path +147%, in the new block bb_0x1ea on p1")
    argued = argued.replace("LOCI post-edit · aes_encrypt +5.0%", "")
    assert "CAUTION" not in argued
    verdict, reason = grade(argued)
    assert verdict == "PASS", reason


def test_preflight_outside_plan_mode_is_graded_the_other_way():
    assert grade_preflight(PREFLIGHT, should_trigger="false")[0] == "FAIL"
    assert grade_preflight("Here is the plan.", should_trigger="false")[0] == "PASS"


# ── what the tail changed: a required baseline that is missing is a FAILURE ──

def test_a_missing_baseline_passes_when_the_eval_does_not_require_one():
    """The first edit of a file the fixture never built has no `.o` to snapshot,
    so "no baseline" is the truth. Failing it would turn an environment gap into
    a skill regression, which is how a suite trains people to ignore it."""
    verdict, reason = grade(NO_BASELINE)
    assert verdict == "PASS", reason


def test_a_missing_baseline_fails_when_the_eval_requires_one():
    """THE change. An eval that edits an already-built file three times in one
    turn exists to prove the baseline survives — and it used to pass by
    REPORTING THAT IT HAD NONE, which is the defect, not the pass."""
    verdict, reason = grade(NO_BASELINE, expect_baseline="true")
    assert verdict == "FAIL", reason
    assert "baseline was required" in reason


def test_requiring_a_baseline_does_not_change_a_run_that_has_one():
    """The positive control for the pair above: the flag must fail the missing
    case without also failing the ordinary one, or it is just an off switch."""
    assert grade(FULL, expect_baseline="true")[0] == "PASS"


def test_only_the_literal_true_requires_a_baseline():
    """Stated as the positive literal, never as an inclusion. The recorded
    `role`-is-null defect is exactly this shape: a criterion phrased around a
    field that may be absent passed vacuously on every install that lacked it."""
    for spelling in ("", "false", "no", "1", "TRUE", "yes"):
        verdict, _ = grade(NO_BASELINE, expect_baseline=spelling)
        assert verdict == "PASS", f"{spelling!r} was treated as true"


# ── edit-and-revert: the report must say nothing changed, and invent nothing ──

def test_edit_and_revert_passes_when_the_report_says_no_net_change():
    verdict, reason = grade(NO_CHANGE, expect_baseline="true",
                            expect_no_change="true")
    assert verdict == "PASS", reason


def test_edit_and_revert_fails_when_the_report_invents_a_delta():
    """The failure that matters most here. The object is byte-for-byte the
    baseline, so a signed percentage is a fabricated number — worse than a
    missing one, because the user acts on it."""
    verdict, reason = grade(FULL, expect_baseline="true", expect_no_change="true")
    assert verdict == "FAIL", reason


def test_edit_and_revert_fails_when_the_report_is_merely_silent():
    """"Nothing changed" has to be SAID. A report that reaches a verdict without
    mentioning the comparison has not established anything — and phase 11 gave
    that state its own shape precisely so it could be stated."""
    quiet = """## Post-Edit: aes_encrypt

Verdict: **PASS**

LOCI post-edit · aes_encrypt
"""
    verdict, reason = grade(quiet, expect_baseline="true", expect_no_change="true")
    assert verdict == "FAIL", reason


def test_edit_and_revert_fails_when_it_had_no_baseline():
    """With no Before, "nothing changed" is not a finding — there is nothing it
    could have been compared against. Reachable: the pre-edit hook is killed,
    the skill reports absolute values, and the model writes "no change" about a
    single measurement."""
    both = NO_BASELINE.replace("Verdict: **PASS**",
                               "The functions are unchanged.\n\nVerdict: **PASS**")
    verdict, reason = grade(both, expect_baseline="true", expect_no_change="true")
    assert verdict == "FAIL", reason


# ── the percentage test, which used to match any `%` anywhere ────────────────

def test_a_percent_sign_in_unrelated_text_is_not_a_delta():
    """`grep -qE '%'` matched a `printf("%d")` in a quoted diff, "100% of the
    callees", and the model saying it was "90% sure" — so "the report carries a
    Before→After delta", the one thing this grader exists to assert, was pinned
    by nothing at all."""
    decoy = """## Post-Edit: aes_encrypt

I rewrote the loop and the `printf("%d bytes\\n", n)` call it contained. This
covers 100% of the callees.

Verdict: **PASS**
"""
    verdict, reason = grade(decoy)
    assert verdict == "FAIL", reason
    assert "no signed % diff" in reason


def test_a_real_delta_in_either_shape_counts():
    """Both spellings the skill actually emits: the Note column's `+5.0%` and
    the footer's `(-17%, …)`. A tightened pattern that only accepted one would
    fail every real report of the other kind."""
    for note in ("+5.0%", "-17%", "+0.4 %"):
        body = FULL.replace("+5.0%", note)
        assert grade(body)[0] == "PASS", note


def test_an_unchanged_object_is_not_graded_as_a_missing_delta():
    """Phase 11: an empty changed-function list is an ANSWER and carries no
    percentage. Before that distinction the grader would have failed the honest
    report for the absence of a number it correctly did not have."""
    verdict, reason = grade(NO_CHANGE)
    assert verdict == "PASS", reason
    assert "unchanged" in reason


# ── the library is a library ─────────────────────────────────────────────────

def test_sourcing_the_graders_has_no_side_effects(tmp_path):
    """It was extracted from `run_evals.sh` so a test could reach it. If it ever
    grows top-level work — an argument parse, a `cd`, a mkdir — this file starts
    testing that instead, which is how the original became untestable."""
    marker = tmp_path / "before"
    marker.write_text("x", encoding="utf-8")
    script = (
        f'set -euo pipefail\n'
        f'cd "{tmp_path.as_posix()}"\n'
        f'source "{LIB.as_posix()}"\n'
        f'ls -A | tr "\\n" " "\n'
    )
    proc = subprocess.run([_find_bash(), "-c", script],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.split() == ["before"], proc.stdout
    assert proc.stderr.strip() == "", proc.stderr


def test_run_evals_still_sources_the_library():
    """The move is only safe while the caller actually calls it. A stale copy
    left behind in `run_evals.sh` would make every test here grade a function
    the suite does not use — the same shape as verifying a copy of the shipped
    text instead of the shipped text."""
    text = (PLUGIN_ROOT / "run_evals.sh").read_text(encoding="utf-8")
    assert "source \"$SCRIPT_DIR/lib/eval-graders.sh\"" in text
    assert "grade_bash_post_edit() {" not in text, (
        "run_evals.sh still defines its own copy of the grader")


# ── the field has to REACH the grader ────────────────────────────────────────

def test_expect_baseline_travels_from_the_eval_file_to_the_grader(tmp_path):
    """Everything above tests the grader in isolation. This tests the wiring, and
    it exists because the wiring was broken when it was only read: the patch that
    added the two arguments to the `run_one_eval` call site left a literal `\n`
    between them, which bash parses as an escaped `n` — an extra positional
    argument that shifted `expect_baseline` into `expect_no_change` and put the
    string "n" where the flag belonged. `bash -n` was happy; the suite was happy;
    the field simply never arrived.

    So this drives the real `run_evals.sh` against the real `critical_evals.json`
    with a STUB `claude` that emits a canned transcript. No model call, no
    toolchain — the only thing under test is that a `true` in the eval file comes
    out as a FAIL in the verdict.
    """
    src = ("examples/rtos/LP_EM_CC2340R5/ble5stack/basic_ble/app/"
           "app_data.c")
    ble = tmp_path / "ble"
    (ble / src).parent.mkdir(parents=True)
    (ble / src).write_text("int GATT_EventHandler(void){return 0;}\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # The stub emits its transcript from a HEREDOC rather than a printf with
    # nested quoting. The printf version produced a line jq parsed to nothing —
    # `response 0 chars`, which the harness reports as ERROR "empty response",
    # i.e. a failure with no relation to what is under test.
    import json as _json
    line = _json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text",
         "text": NO_BASELINE}]}})
    stub = bin_dir / "claude"
    stub.write_text("#!/usr/bin/env bash\ncat <<'JSON'\n" + line + "\nJSON\n",
                    encoding="utf-8")
    stub.chmod(0o755)

    # Two things about PATH here, both of which produced a run that looked like a
    # grader bug and was not:
    #
    #  * it is prepended INSIDE bash. On Windows `os.environ["PATH"]` is
    #    `;`-separated with drive-letter entries, and handing that to Git Bash as
    #    an explicit env var leaves it unable to find `jq` at all.
    #  * the stub's directory is converted to `/c/…`. **A `C:/…` entry in PATH is
    #    silently ignored by Git Bash** — the already-recorded trap — so the
    #    prepend succeeded, the stub was never consulted, and the REAL `claude`
    #    ran the eval for 40 s and answered it properly.
    # `LOCI_EVALS_ALLOW_PLUGIN_SKEW=1` is load-bearing, not noise. `pe-13` is a
    # `flow: edit` eval, and those run the INSTALLED plugin with no SKILL.md
    # injected — so run_evals.sh skips them whenever the installed commit is not
    # HEAD, to stop a verdict about the released plugin being read as one about
    # the working tree. That guard is right and this test is the exception it was
    # given: the subject here is whether `expect_baseline` reaches the grader, the
    # `claude` on PATH is a stub emitting a canned transcript, and no claim is
    # made about any plugin. Without the flag this test skips its own subject and
    # passes for the wrong reason. It is also the only coverage the hatch has.
    # `LOCI_STATE_DIR`: this drives the real harness without `--dry-run`, so its
    # BLE block runs a real `loci init --auto` on the tmp tree — whose failed-init
    # context belongs to the test, not to `~/.loci/state` (two were found there).
    script = (
        f'LOCI_EVALS_ALLOW_PLUGIN_SKEW=1 '
        f'LOCI_STATE_DIR="{(tmp_path / "state").as_posix()}" '
        f'PATH="{_to_bash_path(bin_dir)}:$PATH" '
        f'"{(PLUGIN_ROOT / "run_evals.sh").as_posix()}" '
        f'--ble-root "{ble.as_posix()}" --skill loci-post-edit --eval-id pe-13'
    )
    proc = subprocess.run(
        [_find_bash(), "-c", script],
        cwd=PLUGIN_ROOT, capture_output=True, timeout=300,
        # utf-8 with replacement, NOT `text=True`: the suite's own output carries
        # box-drawing characters and a `·`, and the Windows locale decoder raises
        # on them inside the pipe reader — which surfaces as `stdout is None` and
        # a TypeError three lines later, nowhere near the cause.
        encoding="utf-8", errors="replace")
    out = proc.stdout + proc.stderr
    assert "VERDICT: FAIL" in out, out[-2000:]
    assert "a baseline was required for this eval" in out, out[-2000:]

# ---------------------------------------------------------------------------
# The plugin-skew guard
# ---------------------------------------------------------------------------
# `flow: edit` / `two-turn` evals load the INSTALLED plugin and inject no SKILL.md —
# the real plugin auto-invoking is the behaviour under test. During an initiative the
# installed plugin is the last released version, so those verdicts are facts about it
# and not about the working tree; T11 added a gate that SKIPS them instead. The gate
# shipped with no test at all, and the one test that reaches it sets
# `LOCI_EVALS_ALLOW_PLUGIN_SKEW=1`, so it would pass with the gate ripped out.


def _run_evals(tmp_path, *, extra_env="", eval_id="pe-13", installed_sha=None,
               script_dir=None, skill="loci-post-edit", ble_out=False,
               loci_stale=None, flags=()):
    """Drive the real run_evals.sh with a stub `claude` and a fake plugin registry.

    ``flags`` are extra command-line flags. The skew tests pass
    ``--installed-plugin``: since F01 the DEFAULT loads this working tree with
    `--plugin-dir`, where there is nothing to skew against, so the guard those
    tests are about only speaks in the QA mode it now belongs to.

    ``ble_out`` puts a `basic_ble.out` where the fresh-BLE staging looks for one,
    and ``loci_stale`` stubs the `loci build fresh` verdict that staging checks —
    so the fixture gate can be driven in BOTH directions without depending on
    which `loci` (if any) the host has on PATH. A gate only ever observed
    rejecting is indistinguishable from one that always rejects.
    """
    src = ("examples/rtos/LP_EM_CC2340R5/ble5stack/basic_ble/app/"
           "app_data.c")
    ble = tmp_path / "ble"
    (ble / src).parent.mkdir(parents=True)
    (ble / src).write_text("int GATT_EventHandler(void){return 0;}\n", encoding="utf-8")
    if ble_out:
        out = (ble / "examples" / "rtos" / "LP_EM_CC2340R5" / "ble5stack"
               / "basic_ble" / "freertos" / "ticlang" / "basic_ble.out")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x7fELF" + b"\0" * 64)
        (out.parent / "basic_ble.map").write_text("MEMORY CONFIGURATION\n",
                                                  encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    if loci_stale is not None:
        # Answers only what the staging asks. Anything else exits 1, so a stub that
        # starts being relied on for a second thing fails loudly rather than
        # inventing an answer.
        loci = bin_dir / "loci"
        verdict = "true" if loci_stale else "false"
        loci.write_text(
            "#!/usr/bin/env bash\n"
            'if [ "$1" = "build" ] && [ "$2" = "fresh" ]; then\n'
            "  echo '{\"ok\":true,\"data\":{\"stale\":" + verdict + "}}'\n"
            "  exit 0\n"
            "fi\n"
            "exit 1\n",
            encoding="utf-8", newline="\n")
        loci.chmod(0o755)
    import json as _json
    line = _json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": NO_BASELINE}]}})
    stub = bin_dir / "claude"
    stub.write_text("#!/usr/bin/env bash\ncat <<'JSON'\n" + line + "\nJSON\n",
                    encoding="utf-8")
    stub.chmod(0o755)

    # A HOME whose plugin registry we control. `run_evals.sh` reads
    # `~/.claude/plugins/installed_plugins.json`, and `~` follows HOME.
    home = tmp_path / "home"
    (home / ".claude" / "plugins").mkdir(parents=True)
    if installed_sha is not None:
        (home / ".claude" / "plugins" / "installed_plugins.json").write_text(
            _json.dumps({"plugins": {"loci@loci": [
                {"gitCommitSha": installed_sha, "version": "0.0.0"}]}}),
            encoding="utf-8")

    # `LOCI_STATE_DIR`: since T14 the stale-fixture staging runs a real
    # `loci init` (when the host has the cross-compiler and a real `loci`), and
    # its escrow and context records belong to the test, not to `~/.loci/state`.
    script = (
        f'{extra_env} HOME="{home.as_posix()}" '
        f'LOCI_STATE_DIR="{(tmp_path / "state").as_posix()}" '
        f'PATH="{_to_bash_path(bin_dir)}:$PATH" '
        f'"{_to_bash_path(Path(script_dir or PLUGIN_ROOT) / "run_evals.sh")}" '
        f'--ble-root "{ble.as_posix()}" --skill {skill} --eval-id {eval_id}'
        + ("".join(" " + f for f in flags))
    )
    # Explicit utf-8, NOT text=True: the banner carries em dashes and `text=True`
    # decodes with the locale codec (cp1252 here), which raises inside subprocess's
    # reader thread and turns a real result into an unrelated crash. Exactly the trap
    # `_git_show` in test_freshness_contract.py already carries a comment about.
    proc = subprocess.run([_find_bash(), "-c", script], capture_output=True,
                          timeout=600, cwd=str(script_dir or PLUGIN_ROOT))
    return types.SimpleNamespace(
        returncode=proc.returncode,
        stdout=proc.stdout.decode("utf-8", "replace"),
        stderr=proc.stderr.decode("utf-8", "replace"))


def _head_sha():
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(PLUGIN_ROOT),
                          capture_output=True, text=True, timeout=60).stdout.strip()


@pytest.fixture(scope="module")
def clean_tree(tmp_path_factory):
    """A snapshot of the CURRENT tree, committed into a throwaway repo.

    Two properties are both needed and pull against each other. The tests below need
    `git status` to be empty — keying them on the real repo made them SKIP whenever it
    was dirty, i.e. exactly when someone is changing the thing they guard. And they
    need to exercise the WORKING TREE: a `git worktree add HEAD` is clean but is the
    last commit, so it tests the gate as it was, not as it is.

    Copying the live `run_evals.sh`, `skills/`, `hooks/` and `lib/` into a fresh repo
    and committing them once satisfies both.
    """
    import shutil as _shutil
    dest = tmp_path_factory.mktemp("cleantree") / "snap"
    dest.mkdir()
    for rel in ("run_evals.sh", "lib", "hooks", "skills"):
        src = PLUGIN_ROOT / rel
        if src.is_dir():
            _shutil.copytree(src, dest / rel)
        else:
            _shutil.copy2(src, dest / rel)
    (dest / "run_evals.sh").chmod(0o755)
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "commit.gpgsign=false", "commit", "-qm", "snapshot"]):
        r = subprocess.run(["git", *args], cwd=str(dest), capture_output=True,
                           timeout=300, env=env)
        if r.returncode != 0:
            pytest.skip("could not build the snapshot repo: "
                        + r.stderr.decode("utf-8", "replace")[:200])
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(dest),
                         capture_output=True, text=True, timeout=60).stdout.strip()
    return dest, sha


def test_an_edit_flow_eval_is_skipped_when_the_installed_plugin_is_not_this_tree(tmp_path):
    """The whole point: a verdict about the released plugin must not be reported as
    one about the branch. Skipped, named, and exit 2 — not run and not silently
    dropped.

    `--installed-plugin` since F01. The guard did not change; what changed is
    that it is no longer the default path, because the default now loads this
    tree and a skip there would mean no edit-flow eval ever runs on a branch —
    which is exactly what happened for the whole of the 0.2.0 overhaul.
    """
    proc = _run_evals(tmp_path, installed_sha="0" * 40,
                      flags=("--installed-plugin",))
    out = proc.stdout + proc.stderr
    assert "SKIP loci-post-edit:pe-13" in out, out[-2500:]
    assert "installed plugin" in out, out[-2500:]
    assert proc.returncode == 2, (
        f"expected exit 2 (guards did not run), got {proc.returncode}\n{out[-2500:]}")


def test_the_skip_reaches_the_report_not_only_the_console(tmp_path):
    """`report.md` is the artifact that gets cited. It used to say 'fixture
    unavailable on this host' for every skip and name none of them."""
    results = PLUGIN_ROOT / "eval-results"
    before = set(results.glob("*/report.md")) if results.is_dir() else set()
    proc = _run_evals(tmp_path, installed_sha="0" * 40,
                      flags=("--installed-plugin",))
    fresh = set(results.glob("*/report.md")) - before
    # Not "the newest on disk". `_run_evals` runs the real run_evals.sh with
    # cwd=PLUGIN_ROOT, so the reports of every other caller land in this same
    # directory. Taking the newest gave two wrong answers: under a concurrent
    # writer it graded a stranger's report, and — the reason this is a
    # correctness fix rather than hygiene — if run_evals.sh stopped writing
    # report.md ALTOGETHER, the newest left on disk could be the one the skip
    # test above wrote, same `installed_sha`, so it carries `pe-13` and
    # `installed plugin` and lacks `fixture unavailable` too. Measured on a
    # developer machine: 4 of 8 accumulated reports satisfied all three
    # assertions, so the old guard was reliable only on a fresh CI checkout,
    # where `eval-results/` is empty. A report that did not appear during THIS
    # call is not evidence about this call.
    assert fresh, (
        "this run wrote no report.md of its own; the ones already on disk belong "
        "to other runs and cannot stand in for it\n" + proc.stdout[-2000:])
    body = max(fresh, key=lambda p: p.stat().st_mtime).read_text(encoding="utf-8")
    assert "pe-13" in body, body
    assert "installed plugin" in body, body
    assert "fixture unavailable" not in body, (
        "the report still attributes every skip to a missing fixture:\n" + body)


def test_the_guard_lets_the_eval_run_when_the_installed_plugin_is_this_tree(
        tmp_path, clean_tree):
    """A guard that always skips is not a guard. With the registry naming HEAD and a
    clean tree, the eval must actually run."""
    tree, sha = clean_tree
    proc = _run_evals(tmp_path, installed_sha=sha, script_dir=tree,
                      flags=("--installed-plugin",))
    out = proc.stdout + proc.stderr
    assert "SKIP loci-post-edit:pe-13" not in out, out[-2500:]
    assert "matches HEAD" in out, out[-2500:]


def test_a_dirty_tree_is_skew_even_when_the_sha_matches(tmp_path, clean_tree):
    """The sha is a proxy for the wrong thing. The installed plugin is a frozen copy;
    an uncommitted `SKILL.md` edit — the normal state mid-initiative — cannot be in
    it, so 'matches HEAD' would vouch for a verdict about prose without the change."""
    tree, sha = clean_tree
    marker = tree / "skills" / "_shared" / "_skew_probe.md"
    marker.write_text("probe\n", encoding="utf-8")
    try:
        proc = _run_evals(tmp_path, installed_sha=sha, script_dir=tree,
                          flags=("--installed-plugin",))
        out = proc.stdout + proc.stderr
        assert "SKIP loci-post-edit:pe-13" in out, out[-2500:]
        assert "modified" in out, out[-2500:]
    finally:
        marker.unlink()


def test_the_override_runs_them_anyway_and_says_so(tmp_path):
    """The hatch exists for someone deliberately testing the released plugin. It must
    run the eval AND leave a trace, so a PASS obtained that way is not mistaken for
    one about the tree."""
    proc = _run_evals(tmp_path, installed_sha="0" * 40,
                      extra_env="LOCI_EVALS_ALLOW_PLUGIN_SKEW=1",
                      flags=("--installed-plugin",))
    out = proc.stdout + proc.stderr
    assert "SKIP loci-post-edit:pe-13" not in out, out[-2500:]
    assert "ALLOW_PLUGIN_SKEW" in out, out[-2500:]


def test_the_default_runs_an_edit_eval_against_this_tree_instead_of_skipping_it(tmp_path):
    """The other half of the guard, and the one that was missing.

    With the installed plugin at some other commit — the normal state on any
    branch — the default must RUN the eval against this working tree rather than
    skip it. Running it under the stub `claude` is enough: what is under test is
    that the eval reaches the runner at all, and that the banner says which
    plugin answered.
    """
    proc = _run_evals(tmp_path, installed_sha="0" * 40)
    out = proc.stdout + proc.stderr
    assert "SKIP loci-post-edit:pe-13" not in out, out[-2500:]
    assert "Working tree under test" in out, out[-2500:]
    assert "pe-13" in out, (
        "the eval was never reached, so 'it was not skipped' means nothing:\n"
        + out[-2500:])


# ---------------------------------------------------------------------------
# The BLE fixture gates (P79/P83)
# ---------------------------------------------------------------------------
# Seven evals measure `basic_ble.out`. On the reference checkout that ELF is stale,
# so Pattern B refuses — correctly — and those evals were red at every baseline
# since the freshness gate shipped, asserting behaviour the design forbids. Two
# gates replace that: a re-stamped copy the CLI certifies as fresh, and a skip for
# any eval naming a fixture file this host does not have.


def test_an_eval_whose_declared_fixture_is_absent_is_skipped_not_failed(tmp_path):
    """mr-3 asks for a delta against `CC2340R5/basic_ble.out`, a second BLE build
    the reference checkout does not carry.

    It was RUN anyway and graded FAIL — the model took a long diagnostic path
    around a missing file and the grader scored the confusion as a skill
    regression (P79). An eval that cannot test what it claims to test is skipped
    and named; that rule already governed the two staged fixtures and now governs
    the files an eval DECLARES it needs.
    """
    # The fresh fixture is STAGED here (`ble_out` + a stub CLI) so the skip under
    # test is the declared-file one and not the staging one. Without that this
    # test decided on the host: on Linux the `loci` on PATH is behind the pin,
    # staging fails first, and mr-3 skips for a different reason with the
    # assertion below still green-ish — the same "passes or fails on an
    # environment fact" defect this task fixed in the block-budget test (P68).
    proc = _run_evals(tmp_path, skill="memory-report", eval_id="mr-3",
                      installed_sha=_head_sha(), ble_out=True, loci_stale=False)
    out = proc.stdout + proc.stderr
    assert "fresh BLE fixture unavailable" not in out, (
        "the staging gate fired first, so this is not testing the declared-file "
        "skip:\n" + out[-2500:])
    assert "SKIP memory-report:mr-3" in out, out[-2500:]
    assert "fixture missing on this host" in out, out[-2500:]
    assert "CC2340R5/basic_ble.out" in out, (
        "the skip must NAME the file, or the operator cannot fix it:\n"
        + out[-2500:])


def test_an_eval_that_declares_nothing_is_never_skipped_for_a_missing_path(tmp_path):
    """The over-skip direction, which is the one that loses coverage silently.

    The first cut of this screen scanned the prompt for paths and skipped on any
    that was absent. On this checkout that is FOURTEEN evals — most of them
    reasoning evals whose grade does not depend on the file existing ("grade only
    this reasoning; the agent need not execute any loci command") — so it would
    have traded a visible red for an invisible loss. `pf-critical-5` names a
    source file the tmp fixture below does not create, and declares no
    `requires_files`, so it must still run.

    "Declares nothing" means `requires_files` specifically. Since F01 this eval
    DOES carry a `source_file` — that key says "back this up and restore it if
    the run touches it", not "skip me when it is absent", and the single-turn
    flow logs a NOTE and carries on when the file is not there. Two keys, two
    jobs; only one of them is a skip. (Until F01 the path it named did not exist
    on ANY checkout: `LP_EM_CC2340R53/…`, a board directory with a stray `3` in
    it. The property under test is the same either way — a path the host lacks
    is not a reason to skip an eval that declared no requirement.)
    """
    proc = _run_evals(tmp_path, skill="loci-preflight", eval_id="pf-critical-5",
                      installed_sha=_head_sha(), ble_out=True, loci_stale=False)
    out = proc.stdout + proc.stderr
    # A POSITIVE control first. Asserting only the ABSENCE of a SKIP line passed
    # when the harness aborted before reaching the eval at all — proved by
    # corrupting an unrelated eval file so the `jq empty` guard exited first:
    # `pf-critical-5` appeared nowhere in stdout or stderr, and the assertion
    # below was still green.
    assert "pf-critical-5" in out, (
        "the eval was never reached, so 'it was not skipped' means nothing:\n"
        + out[-2500:])
    assert "SKIP loci-preflight:pf-critical-5" not in out, (
        "an eval that declares no required fixture was skipped for naming a "
        "missing path:\n" + out[-2500:])


def test_the_declared_fixture_list_covers_the_evals_that_need_one():
    """A declaration nobody wrote is a screen that does nothing.

    Both directions, the way this repo's registries are asked to fail: the three
    evals P79/P83 name must declare the build they cannot run without, and an eval
    that declares a file must actually name it in its prompt — a declaration that
    has drifted from the prompt skips for a reason that is no longer true.
    """
    import json as _json
    declared = {}
    for path in sorted(PLUGIN_ROOT.glob("skills/*/evals/*evals.json")):
        doc = _json.loads(path.read_text(encoding="utf-8"))
        for e in doc.get("evals", []):
            if e.get("requires_files"):
                declared[e["id"]] = (e["requires_files"], str(e.get("prompt", "")))
    # `mr-3` only. `pf-critical-2` and `pf-critical-3` were declared here in the
    # first pass and review was right that it was the wrong call: it silenced two
    # *critical* invocation guards in the same commit that staged an ELF holding
    # every function they name. They are repointed at it instead. mr-3 stays,
    # because it compares two DISTINCT builds and the staged copy is one of them.
    assert "mr-3" in declared, (
        "mr-3 no longer declares the second BLE build its delta needs, so it runs "
        "on a host that lacks the file and is graded on the confusion (P79)")
    for eid in ("pf-critical-2", "pf-critical-3"):
        assert eid not in declared, (
            f"{eid} declares a fixture again — it is a critical invocation guard "
            f"and the staged ELF carries its functions; skipping it trades an "
            f"intermittent red for permanent silence")
    for eid, (files, prompt) in sorted(declared.items()):
        for f in files:
            assert f in prompt, (
                f"{eid} declares {f!r} but its prompt never names it — the "
                f"declaration has drifted from the eval and now skips for a "
                f"reason that is not true")


def test_the_fresh_ble_fixture_is_rejected_when_the_cli_still_calls_it_stale(tmp_path):
    """The staging's verdict is the CLI's, not the harness's optimism.

    Copy-and-touch is not a guarantee: a copy that lands with a preserved mtime,
    a `touch` the filesystem ignores, or an ELF whose DWARF names sources in the
    future all leave the artifact stale — and an eval that then runs measures
    something Pattern B is supposed to refuse, which is the state this whole gate
    exists to end. So `loci build fresh` is asked, and anything but `stale=false`
    rejects the fixture.
    """
    proc = _run_evals(tmp_path, skill="memory-report", eval_id="mr-1",
                      installed_sha=_head_sha(), ble_out=True, loci_stale=True)
    out = proc.stdout + proc.stderr
    assert "fresh fixture rejected" in out, out[-3000:]
    assert "SKIP memory-report:mr-1" in out, out[-3000:]
    assert "fresh BLE fixture unavailable" in out, out[-3000:]


def test_the_fresh_ble_fixture_stages_when_the_cli_certifies_it(tmp_path):
    """The other direction, because a gate only ever seen rejecting is
    indistinguishable from one that always rejects — and one that always rejects
    turns six evals into six permanent skips, which is no better than six
    permanent reds."""
    proc = _run_evals(tmp_path, skill="memory-report", eval_id="mr-1",
                      installed_sha=_head_sha(), ble_out=True, loci_stale=False)
    out = proc.stdout + proc.stderr
    assert "fresh fixture rejected" not in out, out[-3000:]
    assert "Fresh BLE fixture: " in out and "stale=false" in out, out[-3000:]
    assert "SKIP memory-report:mr-1" not in out, out[-3000:]


#: The one path under `$LOCI_TEST_BLE_ROOT` the freshness gate refuses on the
#: reference checkout: the linked ELF two SDK headers are newer than. Naming it is
#: how six evals came to assert behaviour the design forbids.
_STALE_TICLANG_ELF = ("$LOCI_TEST_BLE_ROOT/examples/rtos/LP_EM_CC2340R5/ble5stack"
                      "/basic_ble/freertos/ticlang/basic_ble.out")


def test_no_eval_still_measures_the_stale_ble_artifact():
    """The repointing has to be complete, or one straggler keeps a permanent red.

    Scoped to the ticlang ELF specifically, not to `$LOCI_TEST_BLE_ROOT` in
    general: that root is still the right way to name a SOURCE file to compile
    (mr-4 does exactly that, and a source is not something the freshness gate can
    refuse), and `CC2340R5/basic_ble.out` is a MISSING build rather than a stale
    one — the declared-fixture skip handles those.
    """
    import json as _json
    stragglers = []
    for path in sorted(PLUGIN_ROOT.glob("skills/*/evals/*evals.json")):
        doc = _json.loads(path.read_text(encoding="utf-8"))
        for e in doc.get("evals", []):
            blob = " ".join(str(e.get(k, "")) for k in ("prompt", "expected_output"))
            if _STALE_TICLANG_ELF in blob:
                rel = path.relative_to(PLUGIN_ROOT).as_posix()
                stragglers.append(f"{rel}: {e.get('id')}")
    assert not stragglers, (
        "these evals still measure the stale BLE artifact, which Pattern B's "
        "freshness gate refuses — so they assert behaviour the design forbids and "
        "are red at every baseline:\n  " + "\n  ".join(sorted(set(stragglers)))
        + "\n(point them at $LOCI_TEST_BLE_FRESH/basic_ble.out)")
    # …and the replacement is actually in use, or this screen passes because
    # nothing measures anything.
    users = []
    for path in sorted(PLUGIN_ROOT.glob("skills/*/evals/*evals.json")):
        doc = _json.loads(path.read_text(encoding="utf-8"))
        users += [e["id"] for e in doc.get("evals", [])
                  if "$LOCI_TEST_BLE_FRESH" in str(e.get("prompt", ""))]
    assert sorted(users) == ["cmb-1", "cmb-2",
                             "mr-1", "mr-2", "mr-3",
                             "pe-10", "pe-11", "pe-7", "pe-8", "pe-9",
                             "pf-critical-1", "pf-critical-2", "pf-critical-3",
                             "pf-critical-4",
                             "sd-1", "sd-2", "sd-3", "sd-4"], (
        f"the evals measuring the staged fresh artifact are {sorted(users)}; the "
        f"eleven repointed by T13 plus the seven F01 repointed are the expected "
        f"set — a change here needs a look at whether the new eval really wants a "
        f"re-stamped ELF")
    # The seven F01 added are the edit and two-turn evals, which named a
    # top-level `CC2340R5/basic_ble.out` no checkout carries. They could not
    # have been noticed sooner: the skew guard skipped both flows entirely, so
    # none of them had run since the path went in.


def test_every_eval_carries_assertions():
    """Without `assertions[]`, run_evals.sh emits no "ALL must be met to pass" block
    and a ten-clause `expected_output` is weighed holistically — a reviewer caught the
    grader inventing a `[PARTIAL/PASS]` verdict and waiving the one clause an eval had
    been rewritten to assert. Every eval carries them today; this is what stops the
    next one silently reverting.
    """
    import json as _json
    missing = []
    for path in sorted(PLUGIN_ROOT.glob("skills/*/evals/*evals.json")):
        doc = _json.loads(path.read_text(encoding="utf-8"))
        for e in doc.get("evals", []):
            if not e.get("assertions"):
                rel = path.relative_to(PLUGIN_ROOT).as_posix()
                missing.append(f"{rel}: {e.get('id')}")
    assert not missing, (
        "these evals have no `assertions[]`, so the grader weighs their whole "
        "expected_output holistically and can waive any clause:\n  "
        + "\n  ".join(missing))


# ── `set -euo pipefail` hazards in the harness itself (P81) ──────────────────
#
# The suite aborts silently under `set -euo pipefail` whenever a command
# substitution's pipeline returns non-zero: `pipefail` hands the status to the
# assignment and `set -e` kills the script before it prints anything. That is not
# hypothetical — it is how a host with no `~/.claude/plugins/cache/loci` was
# unable to run the eval suite and was told nothing (fixed in T11, parked as P81
# with the note that the CLASS deserved a sweep). This is the sweep.

def _run_evals_text() -> str:
    return (PLUGIN_ROOT / "run_evals.sh").read_text(encoding="utf-8")


def _grade_extraction_lines() -> list[str]:
    """The five shipped lines that turn a grader's text into a verdict.

    Taken as a contiguous BLOCK — the write, the two `sed` reads and the two `:-`
    fallbacks — because each is half the behaviour of the next. A filter that took
    only the `$( … )` lines reported the shipped code as dropping `UNKNOWN` when
    it was the harness that had dropped it; and one that started at the first
    `sed` left the extraction's own PRECONDITION untested, since the reads take
    their input from `$GRADE_FILE` and the write is what puts it there. Moving or
    deleting that write kept all six extraction tests green while the shipped run
    died on an unreadable file. `VERDICT=` and `REASON=` are assigned in six other
    places in this script, so the block is anchored rather than grepped.
    """
    lines = _run_evals_text().splitlines()
    start = next((i for i, ln in enumerate(lines)
                  if ln.strip().startswith("VERDICT=$(")), None)
    assert start is not None, (
        "run_evals.sh no longer extracts a verdict with a command substitution — "
        "this test drives the shipped lines and cannot find them any more")
    # A generous window: the two are 17 lines apart today, most of it the comment
    # explaining why the read takes a file. What matters is that the write is the
    # nearest one ABOVE the read, not how much prose sits between them.
    write = next((i for i in range(start - 1, max(-1, start - 40), -1)
                  if lines[i].strip().startswith('echo "$GRADE" >')), None)
    assert write is not None, (
        "nothing writes $GRADE_FILE in the lines before the extraction that "
        "reads it — the extraction's precondition is gone, and every test below "
        "would be supplying the file itself")
    block = [lines[write].strip()] + [ln.strip() for ln in lines[start:start + 4]]
    assert block[2].startswith('VERDICT="${VERDICT'), block
    assert block[3].startswith("REASON=$("), block
    assert block[4].startswith('REASON="${REASON'), block
    return block


def _drive_extraction(grade: str, tmp_path: Path) -> subprocess.CompletedProcess:
    """Run the shipped extraction over `grade`, under the suite's own shell flags.

    The lines read `$GRADE_FILE`, so the harness writes it the way `run_evals.sh`
    does — through a file, never interpolated into the command, which is how a
    200 KB grade full of quotes and backticks stays a test of the extraction
    rather than of this function's quoting.
    """
    # `$GRADE`, not a pre-written file. The shipped block's own
    # `echo "$GRADE" > "$GRADE_FILE"` is part of what `_grade_extraction_lines`
    # returns now, so the write is under test too. Writing the file here instead
    # left the extraction's precondition outside every test: moving or deleting
    # that line kept all six green while the shipped run died on an unreadable
    # file — strictly worse than the pipe it replaced.
    # `$GRADE` is loaded from a file rather than interpolated into the command:
    # a 256 KB grade is past Windows' command-line limit (`WinError 206`), and a
    # grade full of quotes and backticks would otherwise be a test of this
    # function's quoting rather than of the extraction.
    gf = tmp_path / "grade.txt"
    src = tmp_path / "grade.src"
    src.write_text(grade, encoding="utf-8", newline="")
    script = ("set -euo pipefail\n"
              f'GRADE=$(cat {shlex_quote(src.as_posix())})\n'
              f'GRADE_FILE={shlex_quote(gf.as_posix())}\n'
              + "\n".join(_grade_extraction_lines()) + "\n"
              'printf "%s|%s\\n" "$VERDICT" "$REASON"\n')
    return subprocess.run([_find_bash(), "-c", script],
                          capture_output=True, text=True, timeout=120)


def test_nothing_pipes_into_head():
    """`cmd | head -N` is a silent abort waiting for input size.

    `head` exits after N lines and closes the pipe; the writer takes SIGPIPE and
    exits 141; `pipefail` promotes that to the pipeline and `set -e` ends the
    run — with the grade already in hand, no message, and only when the input
    happens to have more than N matching lines. It is a race, so it fails
    intermittently, which is the worst way for a harness to fail.

    Structural rather than behavioural on purpose: a behavioural test of a race
    passes whenever the writer wins. `sed -n '…{p;q;}'` and `awk 'NR==1{…;exit}'`
    are the shapes that need no second process.

    **A pipe INTO head, anywhere — not one inside `$( … )`.** The first version of
    this screen was `\\$\\([^)]*\\|\\s*head\\b`, and the mutation it exists to catch
    walked straight past it: the real line is
    `$(echo "$GRADE" | sed -n 's/…\\([^[:space:]]*\\).*/\\1/p' | head -1)`, whose sed
    expression contains a `)`, so `[^)]*` stopped there and never reached `head`.
    Scoping to a command substitution was wrong twice over — a top-level
    `writer | head` under `set -e` kills the script just as dead. `head` as the
    FIRST command of a pipeline (`head -15 "$0" | tail -14`) is fine and common
    here: nothing is writing into it.
    """
    offenders = []
    for i, line in enumerate(_run_evals_text().splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue            # prose about the hazard is not the hazard
        if re.search(r"\|\s*head\b", line):
            offenders.append(f"run_evals.sh:{i}: {line.strip()[:120]}")
    assert not offenders, (
        "something pipes into `head` under `set -euo pipefail`:\n  "
        + "\n  ".join(offenders)
        + "\n(let the reader quit by itself — `sed -n '…{p;q;}'` — so there is no "
          "pipe to race)")


def test_every_find_pipeline_is_guarded():
    """`find` exits non-zero on an unreadable directory, and the four `find`
    pipelines in this script are all assignments whose emptiness is ALREADY the
    error path — the abort takes that path away and reports nothing instead."""
    unguarded = []
    for i, line in enumerate(_run_evals_text().splitlines(), 1):
        if re.search(r"=\$\(\s*find\b", line) and not re.search(
                r"\|\|\s*(true|echo)", line):
            unguarded.append(f"run_evals.sh:{i}: {line.strip()[:120]}")
    assert not unguarded, (
        "these `find` substitutions can kill the run before it prints anything:\n  "
        + "\n  ".join(unguarded))


def test_the_verdict_extraction_takes_the_first_line_of_a_repeated_grade(tmp_path):
    """Drives the two real extraction lines, not a copy of them.

    A grader that repeats itself — two `VERDICT:` lines, a `REASON:` quoted back
    inside its own explanation — must still resolve to "first one wins", under
    the same `set -euo pipefail` the suite runs with.
    """
    grade = ("VERDICT: PASS\n"
             "REASON: the report names the artifact\n"
             "VERDICT: FAIL\n"
             "REASON: ...and then contradicts itself\n")
    proc = _drive_extraction(grade, tmp_path)
    assert proc.returncode == 0, (
        f"the extraction killed the run (exit {proc.returncode}): {proc.stderr}")
    assert proc.stdout.strip() == "PASS|the report names the artifact", proc.stdout


@pytest.mark.parametrize("kb", [64, 256])
def test_a_large_grade_does_not_kill_the_run(kb, tmp_path):
    """The regression the first fix INTRODUCED, and the reason it is a file read.

    `echo "$GRADE" | sed …q` makes `echo` the loser of the SIGPIPE race: `sed`
    quits at the first match and `echo` is still writing, so past the pipe buffer
    it takes signal 13 and the assignment exits 141 — `pipefail` plus `set -e`
    ends the whole eval run, after the grade is in hand, with nothing printed.
    Measured at ~128 KB on Windows and ~200 KB on Linux, so 256 KB is over both
    and 64 KB is the control that passes either way.

    A grader quoting a long report produces a grade this size; it is not a
    contrived input.
    """
    grade = ("VERDICT: PASS\nREASON: fine\n"
             + ("x" * 79 + "\n") * (kb * 1024 // 80))
    proc = _drive_extraction(grade, tmp_path)
    assert proc.returncode == 0, (
        f"a {kb} KB grade killed the run (exit {proc.returncode}) — "
        f"exit 141 is SIGPIPE: {proc.stderr[:200]}")
    assert proc.stdout.strip() == "PASS|fine", proc.stdout


@pytest.mark.parametrize("grade,expected", [
    # The ordinary shape.
    ("VERDICT: FAIL\nREASON: no `Artifact:` line\n", "FAIL|no `Artifact:` line"),
    # A reason full of `/`, which is the `s///` delimiter — the extraction is a
    # substitution, so an unescaped delimiter in the DATA is a real hazard and a
    # `head`-free rewrite is the moment to check it.
    ("VERDICT: PASS\nREASON: measured .loci/build/objects/armv7e-m/app.o, 12/12 blocks\n",
     "PASS|measured .loci/build/objects/armv7e-m/app.o, 12/12 blocks"),
    # No REASON at all — the fallback has to survive `set -e`, which is where a
    # `sed` that exits non-zero on no-match would take the suite down.
    ("VERDICT: BLOCKED\n", "BLOCKED|could not extract reason"),
    # Nothing the grader recognises: both fallbacks, and still no abort.
    ("the model rambled and never emitted a verdict\n",
     "UNKNOWN|could not extract reason"),
])
def test_the_verdict_extraction_survives_the_grades_it_actually_meets(grade, expected, tmp_path):
    """The extraction is two `sed` calls in the hot path of every eval.

    Rewriting them (P81) is exactly when a delimiter bug or a no-match exit
    status gets introduced, and neither shows up until a real run: the first
    draft of this fix put `{p;q;}` after an `s///` and produced
    `sed: unknown option to 's'` for every grade in the suite — caught here, not
    by review.
    """
    proc = _drive_extraction(grade, tmp_path)
    assert proc.returncode == 0, (
        f"the extraction killed the run (exit {proc.returncode}): {proc.stderr}")
    assert proc.stdout.strip() == expected, proc.stdout


# ── the harness must say why it cannot run, not just stop ────────────────────

def _tree_copy(tmp_path):
    """A runnable copy of the harness and its eval files, for breaking."""
    import shutil as _shutil
    dest = tmp_path / "tree"
    dest.mkdir()
    for rel in ("run_evals.sh", "lib", "hooks", "skills"):
        src = PLUGIN_ROOT / rel
        if src.is_dir():
            _shutil.copytree(src, dest / rel)
        else:
            _shutil.copy2(src, dest / rel)
    (dest / "run_evals.sh").chmod(0o755)
    return dest


def _run_harness(tree, tmp_path, *args, stage_fresh=False):
    """Drive a broken copy of the harness.

    `stage_fresh` puts a `basic_ble.out` and a `loci` stub in place so the
    fresh-BLE gate passes — otherwise its skip fires first and the failure under
    test is never reached.
    """
    ble = tmp_path / "ble"
    ble.mkdir(exist_ok=True)
    env_prefix = ""
    if stage_fresh:
        out = (ble / "examples" / "rtos" / "LP_EM_CC2340R5" / "ble5stack"
               / "basic_ble" / "freertos" / "ticlang" / "basic_ble.out")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x7fELF" + b"\0" * 64)
        (out.parent / "basic_ble.map").write_text(
            "MEMORY CONFIGURATION\n", encoding="utf-8")
        binp = tmp_path / "hbin"
        binp.mkdir(exist_ok=True)
        loci = binp / "loci"
        loci.write_text(
            "#!/usr/bin/env bash\n"
            'if [ "$1" = "build" ] && [ "$2" = "fresh" ]; then\n'
            "  echo '{\"ok\":true,\"data\":{\"stale\":false}}'\n"
            "  exit 0\n"
            "fi\n"
            "exit 1\n",
            encoding="utf-8", newline="\n")
        loci.chmod(0o755)
        env_prefix = f'PATH="{_to_bash_path(binp)}:$PATH" '
    # The state directory is the test's: the harness's BLE block runs `loci init`,
    # and a real `claude -p` from here runs the installed plugin's session-init,
    # which writes a context for the copied tree — neither belongs in `~/.loci`.
    script = (env_prefix + f'LOCI_STATE_DIR="{(tmp_path / "state").as_posix()}" '
              f'"{_to_bash_path(tree / "run_evals.sh")}" '
              f'--ble-root "{ble.as_posix()}" ' + " ".join(args))
    proc = subprocess.run([_find_bash(), "-c", script], capture_output=True,
                          timeout=600, cwd=str(tree))
    return types.SimpleNamespace(
        returncode=proc.returncode,
        out=proc.stdout.decode("utf-8", "replace")
            + proc.stderr.decode("utf-8", "replace"))


def test_a_malformed_eval_file_is_named_instead_of_killing_the_run(tmp_path):
    """One bare `jq` line and exit 5 is P81's original symptom, still reachable.

    `run_evals.sh` reads each eval file through ~20 unguarded `X=$(jq …)`
    assignments. Under `set -euo pipefail` the first of them inherits jq's parse
    failure and `set -e` ends the run — the operator sees jq's one-line complaint,
    no harness message, no eval list. Reproduced by review before the fix: exit 5,
    zero eval IDs listed.
    """
    tree = _tree_copy(tmp_path)
    victim = tree / "skills" / "memory-report" / "evals" / "evals.json"
    victim.write_text('{"skill_name": "memory-report", "evals": [', encoding="utf-8")
    proc = _run_harness(tree, tmp_path, "--list")
    assert proc.returncode == 1, (
        f"expected a named failure (exit 1), got {proc.returncode}\n{proc.out[-1500:]}")
    assert "is not valid JSON" in proc.out, proc.out[-1500:]
    assert "memory-report/evals/evals.json" in proc.out.replace("\\", "/"), (
        "the failure does not name the file, so the operator cannot fix it:\n"
        + proc.out[-1500:])


@pytest.mark.parametrize("entry,needle", [
    # A placeholder the resolver does not know: left literal, fails the existence
    # test, skips the eval on EVERY host forever. That is a typo silently
    # retiring an eval — the same shape the `$LOCI_TEST_STALE_ROOT` guard's own
    # comment records ("a typo'd `requires` fell straight through").
    ("$LOCI_TEST_BOARD_ROOT/basic_ble.out", "LOCI_TEST_BOARD_ROOT"),
    # …and an entry with NO placeholder at all, which the first version of this
    # guard missed entirely because it only inspected entries beginning with `$`.
    # It resolves to itself, is never found, and skips forever — the exact
    # failure the guard was written for.
    ("examples/rtos/LP_EM_CC2340R5/basic_ble.out", "examples/rtos"),
])
def test_a_requires_files_entry_that_can_never_resolve_fails_loudly(entry, needle,
                                                                    tmp_path):
    """It has to be an error, not a skip: a skip is indistinguishable from a
    fixture the host genuinely lacks, and it is permanent."""
    import json as _json
    tree = _tree_copy(tmp_path)
    victim = tree / "skills" / "memory-report" / "evals" / "evals.json"
    doc = _json.loads(victim.read_text(encoding="utf-8"))
    for ev in doc["evals"]:
        if ev["id"] == "mr-1":
            ev["requires_files"] = [entry]
    victim.write_text(_json.dumps(doc, indent=2), encoding="utf-8")
    proc = _run_harness(tree, tmp_path, "--skill memory-report --eval-id mr-1",
                        stage_fresh=True)
    assert proc.returncode == 1, (
        f"expected exit 1, got {proc.returncode}\n{proc.out[-2000:]}")
    assert "can never resolve" in proc.out, proc.out[-2000:]
    assert needle in proc.out, proc.out[-2000:]


@pytest.mark.timeout(300)
def test_a_bare_known_placeholder_is_accepted(tmp_path):
    """The over-rejection direction, and it took the whole run down.

    `requires_files: ["$LOCI_TEST_BLE_FRESH"]` — the directory, no trailing `/` —
    matched none of the `…/*` arms, fell through to the error arm, and exited 1.
    One eval's perfectly valid declaration killed the suite.

    Its own timeout, because the suite-wide 120 s does not fit it. Measured on
    an **idle** machine in a full green run: **116.13 s** — a 3.9 s margin, 3.2 %.
    So any concurrent work at all reports this as a failure, and it did: an
    otherwise-green suite went red here twice, once beside a second test suite
    and once beside nothing heavier than a few file edits and a `git commit`.
    A red that means "the machine was busy" is indistinguishable from a red that
    means "the harness broke", which is exactly the ambiguity the platform-failure
    cleanup removed everywhere else.

    Raising the budget does not make it faster, and the number is not the real
    story: a 116 s subprocess test living under a 120 s unit-test default is
    integration-shaped in a unit-test file. If it ever grows another 60 %, the
    fix is to make it faster or move it, **not** to raise 300 to 600. Its
    siblings on the same `_run_harness` helper take ~7 s and are deliberately
    left on the default, so they still catch a hang.
    """
    import json as _json
    tree = _tree_copy(tmp_path)
    victim = tree / "skills" / "memory-report" / "evals" / "evals.json"
    doc = _json.loads(victim.read_text(encoding="utf-8"))
    for ev in doc["evals"]:
        if ev["id"] == "mr-1":
            ev["requires_files"] = ["$LOCI_TEST_BLE_FRESH"]
    victim.write_text(_json.dumps(doc, indent=2), encoding="utf-8")
    proc = _run_harness(tree, tmp_path, "--skill memory-report --eval-id mr-1",
                        stage_fresh=True)
    assert "can never resolve" not in proc.out, (
        "a valid known placeholder was rejected:\n" + proc.out[-2000:])
