"""Lint: the four structural invariants must keep a route from measurement to verdict.

`.loci/contract.yaml`'s starter set seeds four structural invariants
(`unbounded_recursion`, `recursion_cycles`, `indirect_calls`,
`unknown_callees`). The hazards behind them were already measured — stack-depth
returns the soundness flags, control-flow counts cycles and indirect calls — but
nothing mapped a measurement onto a contract *signal*, so `loci contract check`
filed all four as "no measurement supplied" and the report dropped that as
routine. Two thirds of the starter contract was invisible.

The plugin's half of the fix is prose: the shared mapping table, and the
fold-back line that carries the counts from stack-depth into a parent skill's
Safety row. Prose regresses silently on the first reword, so each test below
names the instruction whose loss puts the defect back. They are structural (a
section exists, a signal is named, a rule is stated) rather than wording-exact —
and, like every prose lint, they are a ratchet and not a proof: the durable half
is `ESCALATION_SKILLS` in the CLI.

**Retargeted 2026-09-08.** Twelve of these were red because the mechanism they
reached the rules through was deliberately removed and nobody moved the
assertions. `6b8b643` ("Remove default budgets from skill verdicts") deleted
`## One fact, one row: the entry decides the status` and `## When there is no
contract`; `cfd4deb` ("Two verdict vocabularies") replaced them with `## A
measurement inherits a verdict from a bound, never from a band` and `##
Conclusion rows`. What went with them was the *precedence* half — "your own
thresholds apply only where no enabled entry covers that signal", "Never emit
both your own row and a contract row for one fact" — and it is not coming back,
because a skill now has no threshold of its own to compete with an entry: a
`PASS`/`CAUTION`/`FAIL` requires a compared bound and everything else closes on
`⚑ flagged` / `○ cleared`, so a second verdict on one fact is unavailable by
construction rather than forbidden by instruction. The PROPERTIES the tests
carried are all still here, pinned against the sections that hold them now.

**Retargeted again 2026-09-13** (F21), for the same reason and by one PR.
#291 (`c59a6c7`, "Two verdict columns in every skill, and a table drawn
either way") gave control-flow a judging half: it judges the two structural
signals its own graph determines, `recursion_cycles` and `indirect_calls`,
and routes the two it cannot see. So `This skill renders. It never judges.`
is not a sentence that was reworded — the property is deliberately gone, and
the skill is one of the judging four now. The same PR renamed the shared `##
Conclusion rows` heading for the column split and left the slice marker below
pointing at the old one, which sliced nothing and took three assertions down
with it. The `Basis` rule survived that rename, in the body of the section
rather than in its heading, and is pinned there now.

One thing these assertions deliberately do NOT claim: that the CLI answers
the way the prose now reads. #291's own message says control-flow's judging
half was "in flight from another thread", and at the pinned CLI `analyse cfg`
still returns `ExitCode.OK` with no `contract`, `judgements`, `gates`, `verdict`
or `rows` key - it has a test of its own pinning that.
So the skill's contract-fed branch is unreachable prose
until that lands, and only its no-entry branch can fire. These lints pin what
the SKILL states, which is what they have always pinned; the divergence is
filed as its own task rather than carried as four red rows, because a red row
that means something else is how the fifth one goes unnoticed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
SKILLS = PLUGIN_ROOT / "skills"
CONTRACT = SKILLS / "_shared" / "loci-runtime-contract.md"

SIGNALS = ("unbounded_recursion", "recursion_cycles",
           "indirect_calls", "unknown_callees")

#: The mapping table's slice bounds. `## When there is no contract` was the old
#: end marker and `6b8b643` deleted it, which is what made the three tests below
#: fail inside `_section` before reaching an assertion.
MAPPING = ("## Structural invariants: which measurement answers which signal",
           "## Path cost is not yours")

#: The two shared sections a judging skill applies, under the names `cfd4deb`
#: gave them. Spelled here once, so a rename fails in one place.
VERDICT_SECTIONS = ("The Contract Envelope is input only",
                    "A measurement inherits a verdict from a bound, never from "
                    "a band",
                    "Your verdicts are `flagged` / `cleared`")

# Every skill that renders a Safety row over these hazards, or measures them.
# `control-flow` became one in PR #291: it judges the two signals its own
# graph determines, so it has a row to draw and an envelope to read as input
# like the other three. It was excluded here while `analyse cfg` judged
# nothing, which it no longer does.
JUDGING_SKILLS = ("stack-depth", "loci-preflight", "loci-post-edit",
                  "control-flow")


def _text(p: Path) -> str:
    """Whitespace-collapsed, so an assertion pins the instruction and not the line
    wrapping a future edit is free to change."""
    return re.sub(r"\s+", " ", p.read_text(encoding="utf-8"))


def _skill(name: str) -> str:
    return _text(SKILLS / name / "SKILL.md")


def _section(body: str, start: str, end: str) -> str:
    """Slice one section out. Both markers required and `end` must be unique in the
    remainder, or a renamed heading silently widens the slice to EOF and every
    assertion against it passes on a file that lost the rule."""
    assert start in body, f"missing section start {start!r}"
    tail = body.split(start, 1)[1]
    assert tail.count(end) == 1, (
        f"section end {end!r} is not unique after {start!r} — use a longer marker")
    return tail.split(end, 1)[0]


def _verdict_rule() -> str:
    return _section(_text(CONTRACT),
                    "## A measurement inherits a verdict from a bound, never "
                    "from a band",
                    "## Your verdicts are")


def _fold_back() -> str:
    return _section(_skill("stack-depth"),
                    "### Escalation fold-back", "## LOCI voice remark")


# ── the mapping itself ──────────────────────────────────────────────────────

def test_the_shared_contract_maps_every_structural_signal_to_a_measurement():
    section = _section(_text(CONTRACT), *MAPPING)
    for signal in SIGNALS:
        assert signal in section, f"{signal} has no measurement mapped to it"
    # The flags the mapping reads. Without these the table names signals and still
    # leaves the reader to guess what produces them.
    for flag in ("has_recursion", "has_indirect_calls", "has_unknown_callees"):
        assert flag in section, f"{flag} is not named as the source of a signal"
    assert "loci elf stack" in section, "the command that measures them is not named"


def test_the_mapping_requires_the_zero_to_be_reported():
    """A bound nothing measured is filed unjudged, and unjudged is invisible — which
    is how these four went unnoticed. A clean run has to say `0`."""
    section = _section(_text(CONTRACT), *MAPPING)
    assert "Report the zero" in section
    assert "unjudged is invisible" in section


def test_the_mapping_forbids_answering_a_structural_bound_from_an_object():
    """In a `.o` the call edges are unapplied relocations, so `has_unknown_callees`
    reads false for a binary whose callees were never linked. Reporting 0 from that
    is a confidently wrong answer, which is worse than no answer."""
    section = _section(_text(CONTRACT), *MAPPING)
    assert "cannot answer them" in section
    assert "Never report `0`" in section


def test_structural_bounds_are_whole_binary_only():
    """The CLI rejects a `function` on a structural signal (`scope_unexpected`), so
    prose that invites a per-function structural bound authors an entry that can
    never be accepted."""
    shared = _text(CONTRACT)
    assert "They are whole-binary, always" in shared
    assert "scope_unexpected" in shared
    contract_skill = _skill("contract")
    assert "a `function` on one is rejected" in contract_skill
    assert "`stack-depth` is what measures all four" in contract_skill


def test_the_mapping_is_routed_to_from_the_skill_that_measures_the_four():
    """A shared section nothing routes to is prose nobody is handed.

    stack-depth is the only skill that measures these four — preflight and
    post-edit receive them through the `safety:` fold-back — so the table is
    its to apply, and it has to name it to be handed it. Deliberately not
    asserted of all three judging skills: that was the old
    `test_every_judging_skill_reads_the_two_shared_sections`, and requiring a
    skill to read a mapping for measurements it never makes is how that
    assertion ended up pinning a heading none of the three cited.
    """
    assert MAPPING[0].removeprefix("## ") in _skill("stack-depth"), (
        "stack-depth does not name the mapping table, so the only skill that "
        "measures the four signals is not routed to the section saying which "
        "measurement answers which")


# ── the entry decides the row it covers ─────────────────────────────────────

def test_the_contract_decides_a_row_it_covers():
    """An entry's own judgement is what a row it covers says, and the skill's
    measurement is the evidence beneath it rather than a second verdict.

    Retargeted from `## One fact, one row`, which `6b8b643` deleted. One of
    that section's assertions has no successor here: "Your own thresholds apply
    only where no enabled entry covers that signal" is obsolete, because there
    are no skill-owned thresholds left to scope.

    **"The row quotes its `text`" landed on 2026-09-08** (F06), and is asserted
    below. It was NOT obsolete — `6b8b643` took the rule out of every skill and
    nothing brought it back, so a `FAIL` could be rendered without stating the
    requirement it breached. F02 filed it rather than smuggling it in here,
    because restoring it changes what the skills print; F06 restored it in the
    shared `## Conclusion rows` section and in each judging skill, together with
    the envelope reads that make an entry's `text` reachable at all
    (`test_contract_payloads_reach_the_report.py`).
    """
    verdicts = _verdict_rule()
    assert "are inputs, and you render them" in verdicts, (
        "the contract no longer says an entry's judgement payloads are the "
        "skill's to render, so a skill may re-decide a row the user bounded")
    assert "Never supply a band of your own" in verdicts, (
        "the prohibition on a skill-owned band is gone — and it is the only "
        "thing that makes a competing verdict unavailable rather than forbidden")

    # The heading is no longer `## Conclusion rows: no `Basis` column, and
    # mixed vocabularies in one table`: PR #291 renamed it for the column
    # split, and a start marker that is absent slices nothing — `_section`'s
    # own assertion is what caught it, before any rule below could be read.
    rows = _section(_text(CONTRACT),
                    "## Conclusion rows: five columns, the cockpit's two "
                    "among them",
                    "## Structural invariants:")
    # The `Basis` column is the rule the old heading carried, and it moved
    # into the body rather than going away. Pinned where it is now, or that
    # rename would have retired a rule by editing a title.
    assert "There is no `Basis` column" in rows, (
        "the rule that a row does not disclose which source supplied its bound "
        "is gone — it is the two verdict columns that say whether a bound or a "
        "reading reached the row")
    # One table, so two verdicts on one fact cannot arrive by the cheaper route
    # of two tables. `One table, both vocabularies` was how the section said
    # this before #291; the five columns are how it says it now, and the
    # separation is a column rather than a second table.
    assert "Do not add a second table" in rows
    assert "The two verdict columns are the separation." in rows, (
        "nothing says what separates a row a bound decided from a row you did, "
        "so the split the bullet above forbids has no replacement")
    assert "`ENTRY`, `FUNCTION`, `STATUS`, `AGENT ASSESSMENT`, `NOTE`" in rows, (
        "the five columns every judging skill's table carries are no longer "
        "named in the one place that fixes their order")
    # Was `The closing status is the worst row`. Same rule, in two clauses now:
    # the worst-of, and the one place it may not be printed.
    assert "the run verdict is the worst of them" in rows
    assert "never as one row inside the table" in rows, (
        "the run verdict may be rendered as a row again, where the worst-of "
        "reads as one more line instead of the run's answer")
    # The requirement, in the user's words, on the row it decided. `text` is the
    # field that carries it and `rows[].entries` maps a row back to the entries
    # that decided it, so both are named rather than left to be rediscovered.
    assert "quotes the requirement" in rows, (
        "the rule that a row an entry decided states what was required is gone "
        "again — a ❌ FAIL that does not quote the bound sends the user to look "
        "up their own requirement")
    assert "judgements[].text" in rows, (
        "the conclusion-rows section no longer names the field the requirement "
        "is quoted from, which is how the rule became unimplementable last time")
    # The other half of the old `One table, both vocabularies` - WHICH words
    # each column may carry - is in `verdicts.md` now, and this section is what
    # hands a skill over to it. Pin the pointer: a section that stops naming
    # the file holding the matrix leaves five columns with no defined contents.
    assert ("`verdicts.md` holds the column rules, the composition matrix and "
            "the run's worst-of") in rows, (
        "the conclusion-rows section no longer routes to the composition "
        "matrix, so the two vocabularies have a table and no rule for what "
        "may go in it")


def test_a_soundness_caveat_is_never_displaced_by_an_entry():
    """"This depth is a lower bound because a callee is missing" qualifies what a
    number means; it is not a competing verdict, so an entry never displaces it."""
    verdicts = _verdict_rule()
    assert "A soundness caveat is not a verdict" in verdicts
    # The half that does the work: a row an entry just decided is exactly where
    # the caveat is at risk of being dropped.
    assert "including on a row whose status an entry just decided" in verdicts


@pytest.mark.parametrize("skill", JUDGING_SKILLS)
def test_every_judging_skill_applies_the_shared_verdict_rules(skill):
    """Each judging skill routes to the shared vocabulary and to the two contract
    sections that decide which vocabulary it may use, rather than restating them
    — restating is how the three drifted apart before `cfd4deb`."""
    body = _skill(skill)
    assert "verdicts.md" in body, f"{skill} does not read the shared vocabulary"
    for section in VERDICT_SECTIONS:
        assert section in body, f"{skill} does not apply **{section}**"


# ── the route from a skill's numbers to a parent's Safety row ───────────────

def test_stack_depth_owns_all_four_signals():
    """Scoped to the fold-back section, where all four are named together with
    their counts. Whole-file, the four names occur in the envelope description
    and the row catalogue too, so the section that actually carries them across
    was deletable with this green."""
    fold = _fold_back()
    for signal in SIGNALS:
        assert signal in fold, f"stack-depth does not report {signal}"
    # `9f14402` rewrote "including the count `0` when the run was clean" into the
    # two clauses below. Same rule: a clean run states the zero, because an
    # unstated invariant is an unjudged one.
    assert "a zero is worth stating" in fold
    assert "name every signal the run measured, each with its count" in fold
    # …and the one case that is not a zero. A `0` read off an object is a
    # confidently wrong answer, so it has a word of its own.
    assert "write `unmeasured` in place of the count rather than `0`" in fold


def test_stack_depth_hands_the_counts_back_to_a_parent_skill():
    """The `safety:` fold-back line is the only route these counts have into a
    preflight or post-edit conclusion table."""
    fold = _fold_back()
    assert "safety: recursion_cycles" in fold, "no structural fold-back line"
    for parent in ("loci-preflight", "loci-post-edit"):
        assert "`safety:` line" in _skill(parent), (
            f"{parent} does not read the fold-back line that carries the counts")


def test_control_flow_judges_the_two_signals_it_sees_and_routes_the_two_it_cannot():
    """PR #291 split the four: two are in the graph this skill cuts, two are not.

    It rendered and judged nothing at all until then — `This skill renders. It
    never judges.`, asserted here until 2026-09-13. What replaced that is not a
    weaker rule but a sharper one, and the sharp half is the boundary: a cycle
    and an indirect call are in the graph, while `unbounded_recursion` is a
    reading of the source and `unknown_callees` a fact about the link, so
    answering either from a CFG is a confidently wrong answer rather than a
    missing one. The scope caveat is unchanged and is the other half — a hazard
    found in one function is evidence about the whole binary, and a clean
    function is not.
    """
    body = _skill("control-flow")
    assert ("This skill renders, and it judges the two signals the graph "
            "determines.") in body, (
        "control-flow no longer says which half of the work is its own, so it "
        "judges all four signals or none")
    assert "Never substitute a signal you can see for one you cannot." in body, (
        "the rule that keeps `unbounded_recursion` and `unknown_callees` off a "
        "CFG's evidence is gone — a back-edge would answer a bound about whether "
        "anything bounds it")
    # The routing half, scoped to the step that does it: whole-file, the two
    # names occur in the paragraph above and in the row catalogue too, so this
    # would pass on a skill that named them and handed them nowhere.
    route = _section(body,
                     "## Step 3 — judge the two signals, and route the two "
                     "you cannot",
                     "## Step 4 —")
    for signal in ("unbounded_recursion", "unknown_callees"):
        assert signal in route, (
            f"control-flow does not route {signal} out of Step 3, so a hazard "
            f"visible in the graph stops at the chat boundary")
    assert "stack-depth" in route, "control-flow does not name what does measure them"
    # And neither gets a row: a `—` beside either reads as measured and clean,
    # which is the one claim a per-function graph cannot support.
    assert "`unbounded_recursion` and `unknown_callees` get no row" in body
    assert "A hazard is provable at any scope; a zero is not." in body
    assert "Never report `0` against a whole-binary bound" in body


@pytest.mark.parametrize("skill", ("loci-preflight", "loci-post-edit"))
def test_a_clean_run_does_not_render_a_pass_it_did_not_measure(skill):
    body = _skill(skill)
    assert "the row rather than render ✅" in body, (
        f"{skill} may claim a structural invariant holds on a run that never "
        f"measured it")


def test_preflight_escalates_on_a_structural_hazard_whether_or_not_one_is_bounded():
    """The CFG is per-function, so the count that judges a whole-binary entry has to
    come from stack-depth — that escalation is the mapping, in practice.

    The trigger used to read "a structural hazard is in play **and** an enabled
    structural invariant bounds it". `6b8b643` dropped the conjunct and it must
    stay dropped: these four judge with no contract at all, because their
    invariant is zero by definition rather than by anyone's choice. Gating the
    escalation on a bound would leave an unbounded-recursion hazard in an
    unconfigured repo — the common case — escalating to nothing.
    """
    # Scoped to the trigger list, both ways. Whole-file, the negative would
    # also fire on a future sentence that merely describes what a bounded
    # invariant does — the cry-wolf failure this suite has deleted screens
    # over — and the positive would be satisfied by the phrase appearing
    # anywhere, including in prose about something else.
    triggers = _section(
        _skill("loci-preflight"),
        "*Escalate to `stack-depth`* when — increment R by 1 at trigger:",
        "*Escalate to `memory-report`* when — increment R by 1 at trigger:")
    assert ("A structural hazard (recursion, indirect call, unknown callee) is "
            "in play, OR") in triggers, (
        "preflight no longer escalates on a structural hazard, so the count that "
        "judges a whole-binary signal has no source")
    assert "an enabled structural invariant bounds it" not in triggers, (
        "the escalation is conditional on a bound again — these four judge "
        "without a contract, so a repo with none would escalate on nothing")
