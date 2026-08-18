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

import shutil
import subprocess
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
          expect_no_change: str = "false") -> tuple[str, str]:
    """Run `grade_bash_post_edit` and return (verdict, reason).

    The response is passed through a FILE and read back with `$(cat …)` rather
    than interpolated into the script: a report is many lines of markdown with
    quotes, backticks and `%` in it, and a version that pasted it into the
    command would be testing the harness's quoting instead of the grader.
    """
    return _run("grade_bash_post_edit", response,
                [should_trigger, expect_baseline, expect_no_change])


def _run(func: str, response: str, args: list[str]) -> tuple[str, str]:
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        rf = Path(td) / "response.txt"
        rf.write_text(response, encoding="utf-8")
        quoted = " ".join(f'"{a}"' for a in args)
        script = (
            f'set -euo pipefail\n'
            f'source "{LIB.as_posix()}"\n'
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


# A complete, well-formed post-edit report with a real delta. Every other fixture
# in this file is this one with exactly one thing changed, so a test that goes
# green for an unrelated reason has nowhere to hide.
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
    src = ("examples/rtos/LP_EM_CC2340R53/ble5stack/basic_ble_profiles/app/"
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
    script = (
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
