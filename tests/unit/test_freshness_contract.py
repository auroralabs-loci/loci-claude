"""Lint: the stale-artifact guard must stay in the skill docs and in detection.

A tester reported an execution trace based on a linked ELF older than the edit
that prompted it. The plugin-side half of that fix lives in prose — Pattern B's
selection rules, the freshness gate, and the mandatory `Artifact:` provenance line
— and prose regresses silently the first time someone rewords a skill. These tests
are the ratchet: each one names the specific instruction that, if it disappears,
puts the reported defect back.

They are deliberately structural (a section exists, a command is named, a rule is
stated) rather than wording-exact, so a rewrite that keeps the guarantee passes and
one that drops it fails.
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
SKILLS = PLUGIN_ROOT / "skills"
CONTRACT = SKILLS / "_shared" / "loci-runtime-contract.md"
DETECT = PLUGIN_ROOT / "lib" / "detect-project.sh"

# The skills that pick an existing binary to measure — the blast radius of the bug.
PATTERN_B_SKILLS = ("exec-trace", "control-flow", "stack-depth", "memory-report")


def _text(p: Path) -> str:
    """Whitespace-collapsed, so an assertion pins the instruction and not the
    line wrapping a future edit is free to change."""
    return re.sub(r"\s+", " ", p.read_text(encoding="utf-8"))


def _skill(name: str) -> str:
    return _text(SKILLS / name / "SKILL.md")


def _raw(p: Path) -> str:
    """Un-collapsed text, for assertions about literal shell syntax."""
    return p.read_text(encoding="utf-8")


def _section(body: str, start: str, end: str | None) -> str:
    """Slice one section out, so an assertion cannot be satisfied by a stray
    match elsewhere in the file — the way a whole-file `in` check can be.

    **Both markers are required.** Returning everything-to-EOF when `end` was absent
    silently turned every scoped assertion back into the whole-file check it was
    written to replace: renaming any one unpinned heading (`## LOCI voice remark` →
    `## Voice remark`, `## Full Compilation Path` → `## Full Compilation`) re-opened
    the hole, and eight assertions were defeated that way with the suite green. A
    renamed heading must break loudly here instead.
    """
    assert start in body, f"missing section start {start!r}"
    tail = body.split(start, 1)[1]
    if end is None:                      # deliberate: this section runs to EOF
        return tail
    # PRESENCE is not enough — the marker must be UNIQUE in the remainder. Asserting
    # only `end in tail` still let a rename slide when the marker was a prefix of two
    # headings: `## LOCI` matched both `## LOCI voice remark` and `## LOCI footer`, so
    # renaming the first silently re-anchored the slice on the second and widened it.
    # That defeated the mandatory-provenance assertions with the suite green.
    occurrences = tail.count(end)
    assert occurrences == 1, (
        f"section end {end!r} occurs {occurrences}x after {start!r} — a bound must "
        f"match exactly once, or a renamed heading re-anchors the slice on the next "
        f"match and every assertion in this test silently widens. Use a longer, "
        f"unique marker (or end=None when the section genuinely ends the file).")
    return tail.split(end, 1)[0]


def _near(body: str, anchor: str, phrase: str, window: int = 120) -> bool:
    """Is ``phrase`` within ``window`` characters of ``anchor``?

    Never write ``[^\\n]*`` against `_text()` output: it collapses newlines, so a
    "same line" pattern silently spans the whole file and the proximity check
    becomes no check at all. That defeated a real assertion here.
    """
    for m in re.finditer(re.escape(anchor), body):
        if phrase in body[m.end():m.end() + window]:
            return True
    return False


# The base commit this change is measured against. Every token an assertion pins
# must be ABSENT here, or the assertion is not about the change and cannot fail —
# three of the original assertions pinned flag names that already existed.
_BASE_COMMIT = "fcdc0d2"

# Wording that would re-permit what this change forbids.
#
# ⚠ Read this honestly: a phrase list catches *instances*, not the class. A reviewer
# defeated the previous fixed-string version with two rewrites that used none of its
# entries — an "escape hatch" paragraph permitting a labelled stale measurement, and
# a sentence calling the object's `worst_case_depth` "the best available estimate".
# Both left every pinned phrase intact and added permission alongside it. These are
# regex *patterns* now, aimed at the shapes those rewrites had to take (a modal verb
# near a forbidden object), which raises the cost of the next one — but no prose lint
# can prove the absence of a permissive sentence. The durable guarantee is B4's
# provenance line and the CLI's `source_provenance` field, not this screen.
_FORBIDDEN_PATTERNS = (
    r"optionally\s+emit",
    r"skipping\s+it\s+is\s+fine",
    r"this\s+is\s+the\s+preferred\s+path",
    r"are\s+advisory",
    r"do\s+not\s+let\s+(?:them|it)\s+block",
    r"any\s+of\s+them\s+will\s+do",
    r"when\s+you\s+have\s+time",
    r"usually\s+close\s+enough",
    r"prefer\s+whichever\s+is\s+fresher",
    r"escape\s+hatch",
    # "you may still present the measurement", "you can go on reporting the numbers".
    # Deliberately narrow to present/report: broadening it to use/measure/give
    # flagged an innocent "every skill may use …", and a screen that cries wolf gets
    # deleted by the next person.
    #
    # Two earlier flaws, both found by review: an adverb between the modal and the
    # verb defeated it (`may then report`, `may nonetheless present`), and so did the
    # gerund (`may go on reporting`). The `(?![^.]*\bnot\b)` lookahead — meant to
    # spare "may not report" — whitelisted every hedged permission that merely
    # mentioned a negation ("you may report the numbers, but do not omit the
    # caveat"), which is the exact sentence this is supposed to catch. The lookahead
    # is now immediate rather than sentence-wide.
    r"(?:may|can|could)\s+(?!not\b)(?:\w+\s+){0,3}(?:present|report)(?:ing)?\b",
    r"report\s+(?:it|them|the\s+numbers?)\s+anyway",
    r"(?:still|best)\s+(?:available\s+)?estimate",
    r"beats\s+refusing",
    r"better\s+than\s+(?:no\s+number|nothing)",
    r"good\s+proxy",
    r"treat\s+(?:it\s+)?as\s+fresh",
)


def _git_show(commit: str, path: str) -> str | None:
    # Explicit utf-8: these files carry em dashes, and `text=True` decodes with the
    # locale codec (cp1252 on Windows), which raised inside subprocess's reader
    # thread and made this whole guard skip — a skipped guard is a vacuous guard.
    try:
        r = subprocess.run(["git", "show", f"{commit}:{path}"], cwd=PLUGIN_ROOT,
                           capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# The shared contract
# ---------------------------------------------------------------------------
def test_pattern_b_has_all_four_selection_stages():
    body = _text(CONTRACT)
    # B4 ends the file, so its bound is EOF — stated explicitly, because the earlier
    # bound (`## Step 0 — Pattern A`) appears *before* Pattern B and so never matched,
    # which meant B4's length was silently measured to EOF. `_section` now refuses a
    # missing end marker, which is how that surfaced.
    bounds: list[tuple[str, str | None]] = [
        ("### B1 —", "### B2 —"),
        ("### B2 —", "### B3 —"),
        ("### B3 —", "### B4 —"),
        ("### B4 —", None),
    ]
    for start, _ in bounds:
        assert start in body, f"Pattern B lost {start!r}"
    # …with a body, not just a heading: emptying all four passed the heading-only
    # version of this test.
    for start, end in bounds:
        size = len(_section(body, start, end))
        assert size > 400, f"{start} is now a stub ({size} chars)"


def test_pattern_b_gates_on_the_cli_verdict_not_on_prose():
    # Scoped to B3: the phrase also appears in the old-CLI fallback and in several
    # skills, so a whole-file check would stay green with B3's own gate gutted.
    b3 = _section(_text(CONTRACT), "### B3 —", "### B4 —")
    # The invocation form, not the bare phrase: B3 also mentions `loci build fresh`
    # in its old-CLI fallback, so a looser check stays green with the gate itself
    # replaced by "ask the user".
    assert "loci build fresh --elf <candidate>" in b3, (
        "B3 no longer names the gate command it is supposed to run")
    # The branch must be on the machine-readable field. Branching on message text
    # is what the CLI field exists to replace.
    assert ".data.stale" in b3
    assert "source_provenance" in b3


def test_pattern_b_refuses_to_report_numbers_from_a_stale_artifact():
    # Scoped to B3, and paired with the hedge screen: a mutation kept both phrases
    # while adding "report the numbers anyway with a clear staleness caveat" and
    # parking the old rule in a "rejected alternative" block. The failure mode is
    # reporting numbers *with a caveat* — an engineer acts on the numbers and skips
    # the caveat — so the permissive wording must be absent, not merely outweighed.
    b3 = _section(_text(CONTRACT), "### B3 —", "### B4 —")
    assert "say that and stop" in b3
    assert "Do not report numbers for code that is not on disk." in b3


def test_pattern_b_never_treats_unknown_freshness_as_confirmed():
    # A bounded window, not `[^\n]*`: `_text` collapses newlines, so that pattern
    # spanned the whole file and stayed green with the rule replaced by "treat as
    # fresh and proceed silently" and the phrase parked in an HTML comment.
    b3 = _section(_text(CONTRACT), "### B3 —", "### B4 —")
    assert _near(b3, "`null`", "freshness is unknown, not confirmed", 60), (
        "B3's null branch no longer says freshness is unknown")


def test_pattern_b_degrades_instead_of_failing_on_an_older_cli():
    # `loci build fresh` landed with the CLI the plugin pins, but a stale `loci` on
    # PATH ahead of the pin is a real state (it has happened). A missing verb must
    # read as "unverified", never as "fresh", and never abort the skill.
    body = _text(CONTRACT)
    assert "If the installed CLI is too old to have the gate" in body
    assert "invalid choice" in body
    assert 'do not treat that as "fresh"' in body
    assert "/loci:setup" in body


def test_pattern_b_states_the_relocation_trap_for_objects():
    body = _section(_text(CONTRACT), "### B1 —", "### B2 —")
    # Preferring the fresh `.o` for a whole-program question is *worse* than the
    # stale ELF, and nothing in the envelope flags it. This is the one correction
    # a reader must not lose.
    assert "unapplied relocation" in body
    assert "R_ARM_THM_CALL" in body
    assert "has_unknown_callees" in body
    assert "4144" in body, "the measured contrast (8 B vs 4144 B) is the evidence"
    # …and the reason nothing warns you, which is what makes the fresh `.o` worse
    # than the stale ELF rather than merely different.
    assert "the soundness flags do not catch this" in body


def test_pattern_b_documents_the_loci_artifacts_context_key():
    body = _text(CONTRACT)
    # Both places have to name it: the placeholders section (where a skill learns
    # the key exists) and B2 (where it learns how to rank it).
    placeholders = _section(body, "## Session context placeholders",
                            "### Reporting versions")
    b2 = _section(body, "### B2 —", "### B3 —")
    for where, chunk in (("placeholders", placeholders), ("B2", b2)):
        assert "loci_artifacts" in chunk, f"{where} no longer names loci_artifacts"
    # `kind` matters: a skill must be able to tell a relink from a relocatable
    # object before choosing one, since only one of them resolves call edges.
    assert "`kind`" in placeholders
    assert "object" in b2 and "linked" in b2


def test_b2_filters_by_freshness_before_ranking():
    """Freshness must gate the candidate set, not break ties inside it.

    A provenance-first ranking is what produced the reported bug, and ranking
    `.loci-build` above the user's own build repeats it: a relink left there by a
    *previous* session outranks the `.elf` `make` just produced, and rebuilding with
    `make` never touches it — so the same stale artifact is re-nominated forever.
    """
    b2 = _section(_text(CONTRACT), "### B2 —", "### B3 —")
    assert "Freshness is a filter, not a tiebreak" in b2
    assert "discard the stale ones" in b2
    assert "session-start snapshot" in b2, (
        "B2 must say the context is stale for artifacts built this session")
    b3 = _section(_text(CONTRACT), "### B3 —", "### B4 —")
    assert "measure the artifact you just produced" in b3
    assert "Do **not** re-enter B2" in b3, "the re-pick loop is back"
    assert "stays rejected" in b3


def test_availability_alone_is_never_a_reason_to_measure():
    """"An existing binary is available" must not authorise measuring it.

    Three paragraphs said so and none was pinned: the contract's
    *Cross-compilation defaults* ("Prefer an existing binary whenever one is
    available") plus the identical Step 0 restatement in `exec-trace` and
    `control-flow`. Pattern B was rewritten around them while they kept telling the
    model the opposite — and the model follows whichever it reads last.
    """
    defaults = _section(_text(CONTRACT), "## Cross-compilation defaults",
                        "## Rust / Cargo projects")
    assert "that passes" in defaults and "B3" in defaults, (
        "Cross-compilation defaults no longer conditions reuse on the gate")

    for skill in ("exec-trace", "control-flow"):
        step0 = _section(_skill(skill), "## Step 0:", "## Incremental Path")
        assert "discard the" in step0 and "stale" in step0, (
            f"{skill} Step 0 no longer routes through the freshness filter")
        assert "is not on its own a reason to measure it" in step0, (
            f"{skill} Step 0 restored the availability-is-enough reading")


def test_no_permissive_wording_survives_anywhere():
    """Screen the shapes a gutted rule has to take to grant permission back.

    A partial guard, deliberately: see the note on `_FORBIDDEN_PATTERNS`. It raises
    the cost of re-permitting a stale measurement; it cannot prove no such sentence
    exists.
    """
    offenders = []
    for path in [CONTRACT, *(SKILLS / s / "SKILL.md" for s in PATTERN_B_SKILLS)]:
        low = _text(path).lower()
        for pat in _FORBIDDEN_PATTERNS:
            m = re.search(pat, low)
            if m:
                offenders.append(f"{path.name}: {pat!r} matched {m.group(0)!r}")
    assert not offenders, (
        "wording that re-permits what this change forbids:\n  "
        + "\n  ".join(offenders))


def test_html_comments_cannot_hide_a_gutted_rule():
    """A pinned phrase parked in a comment must not satisfy its assertion.

    Three defeated mutations kept every pinned phrase by moving it into an
    `<!-- historical note -->` block while the live text said the opposite. The
    phrases these tests pin are *instructions*; an instruction inside a comment is
    not one, so the simplest durable rule is that these files carry no HTML comments
    at all.
    """
    offenders = []
    for path in [CONTRACT, *(SKILLS / s / "SKILL.md" for s in PATTERN_B_SKILLS)]:
        raw = path.read_text(encoding="utf-8")
        if "<!--" in raw:
            offenders.append(path.name)
    assert not offenders, (
        f"HTML comments found in {offenders} — a rule parked in a comment reads as "
        f"present to this suite and as absent to the model. Delete it or make it "
        f"live prose.")


def test_every_pinned_phrase_is_new_in_this_change():
    """An assertion pinning pre-existing text can never fail.

    Three original assertions pinned `has_recursion` / `has_indirect_calls` /
    `has_unknown_callees`, which already appeared twice each in the base file — so
    the section they were meant to protect was deletable with the suite green. Each
    phrase below must be absent at the base commit, which is what makes the
    assertion that uses it a test *of this change*.
    """
    pinned = {
        "skills/_shared/loci-runtime-contract.md": [
            "Freshness is a filter, not a tiebreak",
            "Do not report numbers for code that is not on disk.",
            "freshness is unknown, not confirmed",
            "unapplied relocation",
            "If the installed CLI is too old to have the gate",
            "loci_artifacts",
            "measure the artifact you just produced",
        ],
        "skills/stack-depth/SKILL.md": [
            "A bare PASS requires a clean upper bound",
            "Never render a bare `PASS`",
            "It does not give worst-case depth.",
            "Artifact provenance (mandatory)",
            # Added 2026-08-13 with the carried-qualifier change (ADR 06 Q6.1/Q6.2),
            # a later change than the rest of this registry. Same guarantee holds:
            # fcdc0d2 predates both, so absent-at-base still proves non-vacuity.
            "lower bound: <cause>",
            "Never write a bare `<usage_pct>%` on a flagged run",
        ],
        "skills/exec-trace/SKILL.md": [
            "only for a known in-flight edit",
            "precondition, not a trigger",
            "Artifact provenance (mandatory)",
        ],
        "skills/control-flow/SKILL.md": [
            "only for a known in-flight edit",
            "Artifact provenance (mandatory)",
        ],
        "skills/memory-report/SKILL.md": ["Artifact provenance (mandatory)"],
        "skills/bug-report/SKILL.md": [
            "Analysed artifact is not stale",
            "Which binary was measured, and was it current?",
        ],
        "lib/detect-project.sh": ["find_loci_artifacts", "dotglob"],
    }
    checked = 0
    unavailable: list[str] = []
    for rel, phrases in pinned.items():
        base = _git_show(_BASE_COMMIT, rel)
        if base is None:
            # NOT a skip. `actions/checkout` defaults to `fetch-depth: 1`, so a
            # shallow clone cannot resolve the base commit — and skipping there made
            # the guard that keeps the other 30 assertions non-vacuous itself
            # vacuous, in precisely the CI configuration most likely to run it. This
            # suite's own rule is that a skipped guard is a vacuous guard. Collect
            # and fail at the end, so one missing path cannot mask the rest either.
            unavailable.append(rel)
            continue
        base_flat = re.sub(r"\s+", " ", base)
        for phrase in phrases:
            assert phrase not in base_flat, (
                f"{rel} already contained {phrase!r} at {_BASE_COMMIT} — an "
                f"assertion pinning it cannot fail")
            # …and it must be present now, or the pin is stale.
            assert phrase in _text(PLUGIN_ROOT / rel), f"{rel} lost {phrase!r}"
            checked += 1

    assert not unavailable, (
        f"cannot read {_BASE_COMMIT} for {unavailable} — this guard must not be "
        f"silently skipped. In CI, deepen the checkout (`fetch-depth: 0`) so the "
        f"base commit resolves; that is the only way the other assertions in this "
        f"file are known to be about the change rather than pre-existing text.")
    expected = sum(len(v) for v in pinned.values())
    assert checked == expected, (
        f"pinned {expected} phrases but only checked {checked} — a `>=` floor let "
        f"one be dropped unnoticed")


# ---------------------------------------------------------------------------
# Per-skill obligations
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("skill", PATTERN_B_SKILLS)
def test_every_pattern_b_skill_must_name_the_artifact_it_measured(skill):
    body = _skill(skill)
    assert "Artifact provenance (mandatory)" in body, (
        f"{skill} lost its mandatory provenance section — a stale run becomes "
        f"indistinguishable from a fresh one again")
    sec = _section(body, "Artifact provenance (mandatory)",
                   "## LOCI voice remark")
    assert "Artifact:" in sec
    assert "sources current" in sec
    # The obligation, not just the template. A mutation kept the section and the
    # example while downgrading the instruction to "Optionally emit …".
    assert "Never omit this line" in sec
    assert "Emit the" in sec or "Emit one" in sec


@pytest.mark.parametrize("skill", PATTERN_B_SKILLS)
def test_no_skill_may_claim_freshness_without_checking(skill):
    sec = _section(_skill(skill), "Artifact provenance (mandatory)",
                   "## LOCI voice remark")
    assert 'never write "sources current" without having run the check' in sec


@pytest.mark.parametrize("skill", ("exec-trace", "control-flow"))
def test_the_incremental_path_is_scoped_to_an_in_flight_edit(skill):
    # Step 0 / Pattern B is read first; an "Incremental Path (preferred)" that
    # triggers on any leftover .o is what made "reuse the existing binary" win.
    body = _skill(skill)
    assert "## Incremental Path — only for a known in-flight edit" in body
    inc = _section(body, "## Incremental Path", "## Full Compilation Path")
    # "preflight"/"post-edit" appear several times in the base file, so asserting
    # them whole-file could never fail; and the heading survived a mutation that
    # re-added "This is the preferred path" underneath it.
    assert "preflight" in inc and "post-edit" in inc
    assert "precondition, not a trigger" in inc
    assert "goes through freshness-gated" in inc, (
        "the standalone-request route back to Pattern B is gone")


def test_stack_depth_object_path_cannot_claim_worst_case_depth():
    body = _skill("stack-depth")
    assert "## Incremental Path — `.o` files (per-function frames only)" in body
    # Scoped to that section: 4144 also appears in a conclusion-table example, so a
    # whole-file check would stay green with the warning itself deleted.
    inc = _section(body, "## Incremental Path — `.o` files", "## Full ELF Path")
    assert "**It does not give worst-case depth.**" in inc
    assert "4144" in inc, "the .o warning lost the concrete inversion it rests on"
    assert "has_unknown_callees" in inc, "…and the reason nothing flags it"
    # The prohibition, not just the caveat: a mutation kept every phrase above and
    # still said the object's worst_case_depth "is a good proxy — report it, with a
    # budget verdict".
    assert "never present" in inc
    assert "Report only `frame_size`" in inc


def test_stack_depth_full_path_selects_through_pattern_b():
    body = _section(_skill("stack-depth"), "## Full ELF Path", "The envelope's")
    # The old text read "Cross-compile or use the existing linked binary" — the
    # exact hole. It must now route through the gate.
    assert not re.search(r"Cross-compile or use the existing linked binary", body)
    assert re.search(r"Select the binary per \*\*Step 0 — Pattern B\*\*", body)


def test_stack_depth_forbids_a_bare_pass_on_an_unsound_depth():
    # Scoped to the new section. The three flag names already appear twice each in
    # the base file (field list + row catalogue), so asserting them whole-file could
    # never fail — the section was deletable with the suite green.
    sec = _section(_skill("stack-depth"),
                   "### A bare PASS requires a clean upper bound",
                   "### Incremental comparison")
    for flag in ("has_recursion", "has_indirect_calls", "has_unknown_callees"):
        assert flag in sec, f"the soundness section no longer names {flag}"
    assert "Never render a bare `PASS`" in sec
    assert "unknown_callee_size" in sec
    # The specific claim: a flagged depth is a floor, not a ceiling. "lower bound"
    # alone also appears in the footer's expand-when list, so it survived alone.
    assert "the computed depth is a **lower bound**, not a worst case" in sec


def test_the_lower_bound_qualifier_survives_into_the_recorded_verdict():
    """The caveat is worthless on a surface that never receives it.

    `PASS (lower bound)` is body-only and the body is not persisted, so before this
    the recorded line was `CAUTION <usage>%` — level intact, reason gone. The
    cockpit could show amber with nothing to explain it, which is how one run ended
    up green on one surface and flagged on the other.
    """
    body = _skill("stack-depth")

    footer = _section(body, "### Row catalogue (order when present)", "### Example")
    assert "`Verdict: **CAUTION** ≥<usage_pct>% — lower bound: <cause>`" in footer, (
        "the footer no longer enumerates a flagged form, so a flagged run has only "
        "the bare `CAUTION <pct>%` to render")
    assert "worst-case ≥<N> bytes, lower bound: <cause>" in footer, (
        "the no-budget flagged form is gone")
    # The `≥` is the whole point: without it the figure reads as exact.
    assert "Never write a bare `<usage_pct>%` on a flagged run" in footer

    rec = _section(body, "**Record cumulative stats + verdict**",
                   "**Record per-function measurements**")
    assert "Verdict: CAUTION ≥<usage>% — lower bound: <cause>" in rec, (
        "the stats-record step no longer names the flagged form, so the qualifier "
        "stops at the chat boundary again")
    # Naming *why* it must be recorded, not just that it may be — a rewrite that
    # keeps the form but drops the reason is how this regresses.
    assert "is not persisted" in rec

    fold = _section(body, "### Fold-back to parent (escalation mode)",
                    "### Expand when...")
    assert "stack: ≥<worst_case_depth> B (≥<usage_pct>%) — CAUTION, lower bound:" in fold, (
        "the escalation fold-back drops the qualifier, so a parent skill's Stack "
        "row cannot know the depth was a floor")


def test_bug_report_precomputes_the_staleness_evidence():
    body = _skill("bug-report")
    assert "loci build fresh --elf" in body
    # In the *spec* table, not merely the report template — the row name appears in
    # both, and only the spec one tells the skill what to run.
    spec = _section(body, "| # | Check | How to test | PASS when |",
                    "## Step 3: Collect stats")
    assert "Analysed artifact is not stale" in spec, (
        "the diagnostics spec no longer defines the freshness check")
    assert "Analysed artifact is not stale" in _section(
        body, "| # | Check | Status | Detail |", "## Reasoning"), (
        "the report template no longer has a row to record it in")
    # The forensics must lead with "which binary", since that is the cheapest and
    # most common explanation for "the numbers are wrong".
    assert re.search(r"Which binary was measured, and was it current\?", body)


def test_bug_report_checklist_count_matches_its_rows():
    # Row structure, so this one reads the file unwrapped. Adding a check without
    # renumbering leaves a "7b" and an "N/10" that no longer add up — the report
    # is the artifact a human reads when LOCI is broken, so its arithmetic has to
    # hold.
    raw = (SKILLS / "bug-report" / "SKILL.md").read_text(encoding="utf-8")
    m = re.search(r"Run (\d+)-point diagnostics checklist", raw)
    assert m, "the checklist heading lost its count"
    claimed = int(m.group(1))
    spec = raw.split("| # | Check | How to test | PASS when |", 1)[1]
    spec = spec.split("\n\n", 1)[0]
    rows = re.findall(r"^\| (\S+) \|", spec, re.M)
    rows = [r for r in rows if r != "---"]
    assert rows == [str(i) for i in range(1, claimed + 1)], (
        f"{claimed}-point heading but rows are {rows}")
    assert f"**Result: <N>/{claimed} checks passed.**" in raw

    # The report template has to agree. Checking only the spec table let the
    # template be renumbered to 1..8b..10 while the footer still said "N/11" — and
    # the report is the artifact a human reads when LOCI is broken.
    tmpl = raw.split("| # | Check | Status | Detail |", 1)[1].split("\n\n", 1)[0]
    tmpl_rows = [r for r in re.findall(r"^\| (\S+) \|", tmpl, re.M) if r != "---"]
    assert tmpl_rows == rows, (
        f"spec table is {rows} but the report template is {tmpl_rows}")


# ---------------------------------------------------------------------------
# Detection: the dotglob trap
# ---------------------------------------------------------------------------
def _find_bash() -> str | None:
    if sys.platform == "win32":
        for cand in (r"C:\Program Files\Git\usr\bin\bash.exe",
                     r"C:\Program Files (x86)\Git\usr\bin\bash.exe"):
            if Path(cand).is_file():
                return cand
    return shutil.which("bash")


def test_detection_publishes_loci_artifacts():
    body = _text(DETECT)
    assert "find_loci_artifacts()" in body
    assert "_stage find_loci_artifacts" in body, "the stage is never run"
    assert "loci_artifacts: $loci_artifacts" in body, "the key is never emitted"


def test_detection_names_the_dotglob_trap_so_it_is_not_re_merged():
    body = _text(DETECT)
    assert "dotglob" in body
    # The specific warning, not just the word: folding this back into
    # find_elf_files' globs is exactly how the bug returns, and the next reader has
    # to be told why the separate walk exists.
    assert "DO NOT" in body
    assert "the dot prefix is the whole trap" in body
    # …and the other trap the same function fell into three rounds running: the cap
    # applied before the sort, and then GNU-only flags in the branch that exists
    # because the platform is not GNU. Both are gone because jq now does the framing,
    # the sort and the cap — pin that, and pin the warning that says why.
    assert "sort_by(-.mtime)" in body, "the sort left jq; the portability trap is back"
    assert "GNU-only" in body
    assert "sort -z" not in _raw(DETECT).replace("`sort -z`", ""), (
        "sort -z is back in the code")
    assert "head -z" not in _raw(DETECT).replace("`head -z`", ""), (
        "head -z is back in the code — BSD/macOS head has no -z")


@pytest.mark.skipif(_find_bash() is None, reason="bash not available")
def test_a_loci_build_object_reaches_the_context(tmp_path: Path):
    """The regression itself: an object under `.loci-build/` must be advertised.

    Before the fix, `find_elf_files`' `.o` sweep globbed `"$CWD"/*[Bb]uild*` and a
    few fixed names; with `dotglob` off, a leading-dot directory matches none of
    them, so the freshest artifact in the tree was invisible to the session context
    and only the (stale) linked ELF in the project root was offered.
    """
    proj = tmp_path / "proj"
    (proj / ".loci-build" / "armv6-m").mkdir(parents=True)
    obj = proj / ".loci-build" / "armv6-m" / "blink.o"
    obj.write_bytes(b"\x7fELF")
    (proj / ".loci-build" / "armv6-m" / "blink.o.prev").write_bytes(b"\x7fELF")
    (proj / "blink.c").write_text("int main(void){return 0;}\n", encoding="utf-8")

    res = subprocess.run(
        [_find_bash(), DETECT.as_posix()],
        cwd=proj, capture_output=True, text=True, timeout=180,
        env={**os.environ, "LOCI_STATE_DIR": (tmp_path / "state").as_posix()},
    )
    assert res.returncode == 0, res.stderr[-2000:]
    ctx = json.loads(res.stdout)

    arts = ctx["loci_artifacts"]
    paths = [a["path"] for a in arts]
    assert any(p.endswith("blink.o") and not p.endswith(".prev") for p in paths), (
        f"the .loci-build object is still invisible: {paths}")
    entry = next(a for a in arts if a["path"].endswith("blink.o"))
    assert entry["kind"] == "object"
    assert isinstance(entry["mtime"], int)
    # A pre-edit snapshot is the state *before* the current edit — never a candidate
    # for "measure this now". Enforced by the `find -name` patterns (nothing matches
    # a path ending in .prev), which is why detect-project.sh carries no separate
    # filter: this assertion IS the guard, and it fails if the patterns widen.
    assert not any(p.endswith(".prev") for p in paths)
    # And the user's own build list is untouched: existing consumers of
    # `elf_files` (flag_sources/linked_elf_dwarf.py) must not see LOCI's objects
    # appear there.
    assert not any(".loci-build" in p and p.endswith(".o")
                   for p in ctx["elf_files"])


@pytest.mark.skipif(_find_bash() is None, reason="bash not available")
def test_the_newest_artifact_survives_a_crowded_loci_build(tmp_path: Path):
    """More artifacts than the output cap must not lose the newest one.

    `find | head -60 | sort -by-mtime` capped in **traversal** order and only then
    sorted, so in a `.loci-build` with more than 60 artifacts the freshest — the one
    this list exists to advertise — could be truncated away before the sort saw it.
    Measured: 70 objects in, the newest absent and `[0]` an arbitrary older file.
    The other detection tests use 2 files and cannot see this.
    """
    proj = tmp_path / "proj"
    d = proj / ".loci-build" / "armv6-m"
    d.mkdir(parents=True)
    # Named so `find`'s traversal order puts the newest LAST, the worst case.
    for i in range(70):
        p = d / f"mod{i:02d}.o"
        p.write_bytes(b"\x7fELF")
        os.utime(p, (1_748_000_000 + i, 1_748_000_000 + i))
    newest = d / "zz-relinked.elf"
    newest.write_bytes(b"\x7fELF")
    os.utime(newest, (1_785_000_000, 1_785_000_000))

    res = subprocess.run(
        [_find_bash(), DETECT.as_posix()],
        cwd=proj, capture_output=True, text=True, timeout=180,
        env={**os.environ, "LOCI_STATE_DIR": (tmp_path / "state").as_posix()},
    )
    assert res.returncode == 0, res.stderr[-2000:]
    arts = json.loads(res.stdout)["loci_artifacts"]

    assert arts, "no artifacts advertised at all"
    assert arts[0]["path"].endswith("zz-relinked.elf"), (
        f"the newest artifact is not first: {arts[0]}")
    assert arts[0]["mtime"] == 1_785_000_000
    # And B2's "linked" rank must have a candidate, which is the whole point.
    assert any(a["kind"] == "linked" for a in arts)


def _bsd_shims(dirpath: Path) -> Path:
    """A PATH dir that makes GNU coreutils behave like BSD/macOS.

    `find` rejects `-printf`; `sort`/`head` reject `-z`; `stat` rejects `-c` and
    serves `-f %m` as the mtime. Faithfulness matters: a first attempt at this shim
    passed `-f %m` through to GNU `stat` (where `-f` means `--file-system`) and made a
    working fix look broken.
    """
    dirpath.mkdir(parents=True, exist_ok=True)
    reject = ("#!/bin/bash\n"
              'for a in "$@"; do [ "$a" = "%s" ] && '
              '{ echo "%s: illegal option" >&2; exit 1; }; done\n'
              'exec /usr/bin/%s "$@"\n')
    (dirpath / "find").write_text(reject % ("-printf", "find", "find"), encoding="utf-8")
    (dirpath / "sort").write_text(reject % ("-z", "sort", "sort"), encoding="utf-8")
    (dirpath / "head").write_text(reject % ("-z", "head", "head"), encoding="utf-8")
    (dirpath / "stat").write_text(
        "#!/bin/bash\n"
        'if [ "$1" = "-c" ]; then echo "stat: illegal option -- c" >&2; exit 1; fi\n'
        'if [ "$1" = "-f" ] && [ "$2" = "%m" ]; then shift 2; '
        'exec /usr/bin/stat -c %Y "$@"; fi\n'
        'exec /usr/bin/stat "$@"\n', encoding="utf-8")
    for f in dirpath.iterdir():
        f.chmod(0o755)
    return dirpath


@pytest.mark.skipif(_find_bash() is None, reason="bash not available")
def test_the_bsd_branch_produces_the_same_answer_as_the_gnu_one(tmp_path: Path):
    """Execute the BSD/macOS path, which no test had ever run.

    That is the structural reason this one function broke in three consecutive rounds:
    round 1 capped before sorting in both branches; round 2 fixed GNU and left BSD
    capping before the sort; round 3 found BSD requiring `sort -z`/`head -z`, which
    are GNU-only — so `loci_artifacts` was unconditionally `[]` on macOS, silently,
    because the detector is invoked with `2>/dev/null`. On macOS that made the
    ORIGINALLY REPORTED BUG live: the freshest artifact invisible, only the stale root
    ELF advertised. Every earlier test shelled out to Git Bash, i.e. GNU.
    """
    proj = tmp_path / "proj"
    d = proj / ".loci-build" / "armv6-m"
    d.mkdir(parents=True)
    for i in range(40):                       # more than the output cap
        p = d / f"mod{i:02d}.o"
        p.write_bytes(b"\x7fELF")
        os.utime(p, (1_748_000_000 + i, 1_748_000_000 + i))
    newest = d / "zz-relinked.elf"            # traversal order puts it LAST
    newest.write_bytes(b"\x7fELF")
    os.utime(newest, (1_785_000_000, 1_785_000_000))

    env = {**os.environ, "LOCI_STATE_DIR": (tmp_path / "state").as_posix()}
    gnu = subprocess.run([_find_bash(), DETECT.as_posix()], cwd=proj, env=env,
                         capture_output=True, text=True, timeout=180)
    assert gnu.returncode == 0, gnu.stderr[-2000:]

    shim = _bsd_shims(tmp_path / "bsdshim")
    bsd_env = {**env, "PATH": f"{shim.as_posix()}:{env.get('PATH', '')}"}
    bsd = subprocess.run([_find_bash(), DETECT.as_posix()], cwd=proj, env=bsd_env,
                         capture_output=True, text=True, timeout=180)
    assert bsd.returncode == 0, bsd.stderr[-2000:]

    a_gnu = json.loads(gnu.stdout)["loci_artifacts"]
    a_bsd = json.loads(bsd.stdout)["loci_artifacts"]

    assert a_bsd, f"the BSD branch published nothing (stderr: {bsd.stderr[-400:]})"
    assert a_bsd[0]["path"].endswith("zz-relinked.elf"), (
        f"BSD lost the newest artifact: {a_bsd[0]}")
    assert a_bsd[0]["kind"] == "linked"
    assert a_bsd == a_gnu, "the two branches disagree"


@pytest.mark.skipif(_find_bash() is None, reason="bash not available")
def test_loci_artifacts_is_newest_first_and_typed(tmp_path: Path):
    proj = tmp_path / "proj"
    d = proj / ".loci-build" / "armv6-m"
    d.mkdir(parents=True)
    old_obj = d / "old.o"
    new_elf = d / "relinked.elf"
    old_obj.write_bytes(b"\x7fELF")
    new_elf.write_bytes(b"\x7fELF")
    base = new_elf.stat().st_mtime
    os.utime(old_obj, (base - 500, base - 500))
    os.utime(new_elf, (base, base))

    res = subprocess.run(
        [_find_bash(), DETECT.as_posix()],
        cwd=proj, capture_output=True, text=True, timeout=180,
        env={**os.environ, "LOCI_STATE_DIR": (tmp_path / "state").as_posix()},
    )
    assert res.returncode == 0, res.stderr[-2000:]
    arts = json.loads(res.stdout)["loci_artifacts"]

    assert [a["kind"] for a in arts][:2] == ["linked", "object"]
    mtimes = [a["mtime"] for a in arts]
    assert mtimes == sorted(mtimes, reverse=True), "not newest-first"


# ── the mirrored object layout (phase 02c) ───────────────────────────────────

def _detect(proj: Path, tmp_path: Path, extra_env: dict | None = None):
    env = {**os.environ, "LOCI_STATE_DIR": (tmp_path / "state").as_posix()}
    env.update(extra_env or {})
    res = subprocess.run(
        [_find_bash(), DETECT.as_posix()],
        cwd=proj, capture_output=True, text=True, timeout=180, env=env,
    )
    assert res.returncode == 0, res.stderr[-2000:]
    return json.loads(res.stdout)


def _mirrored_tree(proj: Path) -> Path:
    """`.loci-build/<target>/<the source's own directories>/<stem>.o`, as
    `build compile` now writes it — six levels down, which is an ordinary depth
    for a vendor SDK example (the BLE fixture's `app_data.c` sits exactly there).
    """
    deep = (proj / ".loci-build" / "armv6-m" / "examples" / "rtos"
            / "LP_CC2652R7" / "ble5stack" / "basic_ble" / "app")
    deep.mkdir(parents=True)
    obj = deep / "app_data.o"
    obj.write_bytes(b"\x7fELF")
    os.utime(obj, (1_785_000_000, 1_785_000_000))
    return obj


@pytest.mark.skipif(_find_bash() is None, reason="bash not available")
def test_a_mirrored_object_deep_in_the_tree_reaches_the_context(tmp_path: Path):
    """Objects are keyed on the SOURCE's path now, so they are no longer flat.

    A translation unit six directories down lands six directories down under the
    target dir. At the old depth cap it is invisible to every skill that reads
    `loci_artifacts` — which is the same failure this list was added to fix, just
    reached from the other side.
    """
    proj = tmp_path / "proj"
    obj = _mirrored_tree(proj)

    paths = [a["path"] for a in _detect(proj, tmp_path)["loci_artifacts"]]
    assert any(p.endswith("app_data.o") for p in paths), (
        f"the mirrored object is invisible: {paths}")
    assert obj.is_file()


@pytest.mark.skipif(_find_bash() is None, reason="bash not available")
def test_the_cargo_dependency_cache_cannot_evict_the_real_objects(tmp_path: Path):
    """Depth alone is the wrong instrument — that is why the prune comes first.

    `.loci-build/cargo` is the private CARGO_TARGET_DIR. Walking deep without
    pruning it swept 201 build-script objects into a 30-entry list and pushed the
    crate's own object out of it: the same invisibility, arrived at by fixing the
    other half. The crate's real object is published up at `<target>/<stem>.o`, so
    nothing measurable is lost by never looking inside.
    """
    proj = tmp_path / "proj"
    real = _mirrored_tree(proj)
    cache = proj / ".loci-build" / "cargo" / "thumbv7em-none-eabi" / "release" / "build"
    cache.mkdir(parents=True)
    for i in range(60):                      # newer than the real object
        p = cache / f"build_script_build-{i:03d}.o"
        p.write_bytes(b"\x7fELF")
        os.utime(p, (1_790_000_000 + i, 1_790_000_000 + i))

    arts = _detect(proj, tmp_path)["loci_artifacts"]
    paths = [a["path"] for a in arts]
    assert not any("/cargo/" in p for p in paths), (
        f"the dependency cache is being advertised as measurable: {paths[:5]}")
    assert any(p.endswith(real.name) for p in paths), (
        f"the crate's own object was evicted by the cache: {paths[:5]}")


@pytest.mark.skipif(_find_bash() is None, reason="bash not available")
def test_the_text_dumps_and_turn_baselines_are_not_candidates(tmp_path: Path):
    """`elf/` holds CFG and timing text; `turns/` holds pre-edit baselines. A
    Before is by definition not "measure this now" — the same rule that keeps
    `.prev` out, made structural instead of resting on a filename suffix."""
    proj = tmp_path / "proj"
    real = _mirrored_tree(proj)
    for rel in (".loci-build/elf/app_data-abc123/relinked.elf",
                ".loci-build/turns/turn-A/armv6-m/app/app_data.o"):
        p = proj / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\x7fELF")
        os.utime(p, (1_790_000_000, 1_790_000_000))   # newer than the real one

    paths = [a["path"] for a in _detect(proj, tmp_path)["loci_artifacts"]]
    assert not any("/elf/" in p or "/turns/" in p for p in paths), paths
    assert any(p.endswith(real.name) for p in paths), paths


@pytest.mark.skipif(_find_bash() is None, reason="bash not available")
def test_a_staging_directory_a_killed_compile_left_is_not_a_candidate(
        tmp_path: Path):
    """`loci build compile` writes into `.loci-stage-<x>/` beside its destination
    and renames the object out of it (CLI phase 02d). A compile that is killed
    leaves the directory, and the file inside is called `<stem>.o` and IS a real
    object — the NEWEST one under `.loci-build`, since the compile that would
    have superseded it never finished. Unpruned it is advertised as the freshest
    thing to measure, and then `loci build reap` deletes it out from under the
    skill that was told to measure it.

    Two placements, because the directory sits beside its destination rather than
    at a fixed level: under a mirrored target directory (an ordinary compile) and
    inside a turn tree's `obj/` (a `--baseline` reconstruction). The second is
    already covered by the `turns/` prune; it is here so that widening one prune
    cannot quietly come to rely on the other.
    """
    proj = tmp_path / "proj"
    real = _mirrored_tree(proj)
    for rel in (".loci-build/armv6-m/app/.loci-stage-ab12cd34/app_data.o",
                ".loci-build/turns/tA/obj/9-x/armv6-m/.loci-stage-ff00ff00/app_data.o"):
        p = proj / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\x7fELF")
        os.utime(p, (1_790_000_000, 1_790_000_000))   # newer than the real one

    # And the same object in the place `--output build/app.o` puts one, which is
    # the user's own build tree — where `loci build reap` never reaches, so a
    # leftover there is permanent rather than collected within the hour.
    build = proj / "build"
    (build / ".loci-stage-99887766").mkdir(parents=True)
    (build / ".loci-stage-99887766" / "app.o").write_bytes(b"\x7fELF")
    (build / "app.o").write_bytes(b"\x7fELF")

    ctx = _detect(proj, tmp_path)
    paths = [a["path"] for a in ctx["loci_artifacts"]]
    assert not any(".loci-stage-" in p for p in paths), (
        f"a killed compile's staging object is offered as measurable: {paths}")
    assert any(p.endswith(real.name) for p in paths), (
        f"the real object went with the prune: {paths}")
    # `elf_files` is a candidate list too — `detect_architecture`'s last fallback
    # reads its first entry and `/bug-report` check 7 reads the whole thing — and
    # a staged path sorts BEFORE the real one, so it would head the list.
    assert not any(".loci-stage-" in p for p in ctx["elf_files"]), (
        f"the staged object reached elf_files: {ctx['elf_files']}")
    assert any(p.endswith("build/app.o") for p in ctx["elf_files"]), (
        f"the project's own object went with the prune: {ctx['elf_files']}")


@pytest.mark.skipif(_find_bash() is None, reason="bash not available")
def test_a_source_directory_named_elf_is_still_walked(tmp_path: Path):
    """The prune is by PATH, not by name. `-name elf` would also prune a mirrored
    source directory called `elf` — which is a perfectly ordinary thing to call
    one — and silently drop every object compiled from it."""
    proj = tmp_path / "proj"
    d = proj / ".loci-build" / "armv6-m" / "src" / "elf"
    d.mkdir(parents=True)
    obj = d / "reader.o"
    obj.write_bytes(b"\x7fELF")

    paths = [a["path"] for a in _detect(proj, tmp_path)["loci_artifacts"]]
    assert any(p.endswith("reader.o") for p in paths), (
        f"a source directory named `elf` was pruned as if it were LOCI's: {paths}")


@pytest.mark.skipif(_find_bash() is None, reason="bash not available")
def test_the_prune_survives_a_glob_metacharacter_in_the_project_path(tmp_path: Path):
    """`-path` takes an fnmatch PATTERN, not a literal.

    A project at `C:\\work\\proj[v2]\\` — legal on Windows, and `[`, `*` and `?` are
    all legal on POSIX — turns `[v2]` into a character class, so the pattern matches
    nothing and the prune silently does nothing. Survivable while this walked to
    depth 4; at the depth it walks now it is exactly the failure the prune exists to
    prevent, with the cargo cache filling the list.

    The project path is passed as an ARGUMENT here on purpose. `_detect` runs the
    script with none, so `CWD` defaults to `.` — no metacharacters, and the bug is
    unreachable. Production passes `"$(pwd)"` (`lib/setup-steps.sh`), so the
    argument is the faithful call.
    """
    proj = tmp_path / "proj[v2]"
    real = _mirrored_tree(proj)
    cache = proj / ".loci-build" / "cargo" / "rel" / "build"
    cache.mkdir(parents=True)
    for i in range(20):
        p = cache / f"build_script_build-{i:03d}.o"
        p.write_bytes(b"\x7fELF")
        os.utime(p, (1_790_000_000 + i, 1_790_000_000 + i))

    env = {**os.environ, "LOCI_STATE_DIR": (tmp_path / "state").as_posix()}
    res = subprocess.run(
        [_find_bash(), DETECT.as_posix(), proj.as_posix()],
        cwd=proj, capture_output=True, text=True, timeout=180, env=env,
    )
    assert res.returncode == 0, res.stderr[-2000:]
    paths = [a["path"] for a in json.loads(res.stdout)["loci_artifacts"]]

    assert not any("/cargo/" in p for p in paths), (
        f"the prune matched nothing — the pattern was not escaped: {paths[:5]}")
    assert any(p.endswith(real.name) for p in paths), paths


@pytest.mark.skipif(_find_bash() is None, reason="bash not available")
def test_the_bsd_stat_loop_stops_at_its_bound_and_still_answers(tmp_path: Path):
    """The BSD branch's `stat`-per-file loop runs OUTSIDE the find's `timeout`, so
    it carries its own deadline. Deleting that deadline, and making it unable to
    fire, both survived a mutation campaign: nothing reached it, because at the
    shipped 6 s a fixture would need thousands of files.

    The clock starts at the first record, not at function entry — set up front it
    is shared with the concurrent `find`, and a first record arriving after it
    expires breaks the loop with nothing collected and publishes `[]`. So this
    asserts BOTH halves: the walk is bounded, and the answer is still non-empty.
    """
    proj = tmp_path / "proj"
    d = proj / ".loci-build" / "armv6-m"
    d.mkdir(parents=True)
    for i in range(12):
        p = d / f"mod{i:02d}.o"
        p.write_bytes(b"\x7fELF")
        os.utime(p, (1_748_000_000 + i, 1_748_000_000 + i))

    shim = _bsd_shims(tmp_path / "bsdshim")
    # …and make each `stat` cost a second, so 12 files cannot fit in a 3 s budget.
    (shim / "stat").write_text(
        "#!/bin/bash\nsleep 1\n"
        'if [ "$1" = "-c" ]; then echo "stat: illegal option -- c" >&2; exit 1; fi\n'
        'if [ "$1" = "-f" ] && [ "$2" = "%m" ]; then shift 2; '
        'exec /usr/bin/stat -c %Y "$@"; fi\n'
        'exec /usr/bin/stat "$@"\n', encoding="utf-8")
    (shim / "stat").chmod(0o755)

    arts = _detect(proj, tmp_path, {
        "PATH": f"{shim.as_posix()}:{os.environ.get('PATH', '')}",
        "LOCI_STAT_BUDGET_SECONDS": "3",
    })["loci_artifacts"]

    assert arts, "the loop broke before collecting anything and published []"
    assert len(arts) < 12, (
        f"the deadline never fired — all {len(arts)} files were stat'd")


@pytest.mark.skipif(_find_bash() is None, reason="bash not available")
def test_the_bsd_branch_prunes_and_walks_the_same_way(tmp_path: Path):
    """Every property above, on the branch that exists because macOS is not GNU.

    Three consecutive rounds of repair to this function broke that platform, each
    time invisibly — the detector runs under `2>/dev/null`. Pruning and depth are
    written twice, once per branch, so they are exactly the kind of thing that
    lands in one of them.
    """
    proj = tmp_path / "proj"
    real = _mirrored_tree(proj)
    cache = proj / ".loci-build" / "cargo" / "release" / "build"
    cache.mkdir(parents=True)
    for i in range(20):
        p = cache / f"build_script_build-{i:03d}.o"
        p.write_bytes(b"\x7fELF")
        os.utime(p, (1_790_000_000 + i, 1_790_000_000 + i))

    gnu = _detect(proj, tmp_path)["loci_artifacts"]
    shim = _bsd_shims(tmp_path / "bsdshim")
    bsd = _detect(proj, tmp_path,
                  {"PATH": f"{shim.as_posix()}:{os.environ.get('PATH', '')}"})["loci_artifacts"]

    assert bsd, "the BSD branch published nothing"
    assert not any("/cargo/" in a["path"] for a in bsd)
    assert any(a["path"].endswith(real.name) for a in bsd)
    assert bsd == gnu, "the two branches disagree about the mirrored layout"
