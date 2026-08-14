"""Lint: the four structural invariants must keep a route from measurement to verdict.

`.loci/contract.yaml`'s starter set seeds four structural invariants
(`unbounded_recursion`, `recursion_cycles`, `unresolved_indirect_calls`,
`unknown_callees`). The hazards behind them were already measured — stack-depth
returns the soundness flags, control-flow counts cycles and indirect calls — but
nothing mapped a measurement onto a contract *signal*, so `loci contract check`
filed all four as "no measurement supplied" and the report dropped that as
routine. Two thirds of the starter contract was invisible.

The plugin's half of the fix is prose: the shared mapping table, the "one fact,
one row" precedence rule, and the fold-back line that carries the counts from
stack-depth into a parent skill's Safety row. Prose regresses silently on the
first reword, so each test below names the instruction whose loss puts the defect
back. They are structural (a section exists, a signal is named, a rule is stated)
rather than wording-exact — and, like every prose lint, they are a ratchet and not
a proof: the durable half is `ESCALATION_SKILLS` in the CLI.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
SKILLS = PLUGIN_ROOT / "skills"
CONTRACT = SKILLS / "_shared" / "loci-runtime-contract.md"

SIGNALS = ("unbounded_recursion", "recursion_cycles",
           "unresolved_indirect_calls", "unknown_callees")

# Every skill that renders a Safety row over these hazards, or measures them.
JUDGING_SKILLS = ("stack-depth", "control-flow", "loci-preflight", "loci-post-edit")


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


# ── the mapping itself ──────────────────────────────────────────────────────

def test_the_shared_contract_maps_every_structural_signal_to_a_measurement():
    section = _section(_text(CONTRACT),
                       "## Structural invariants: which measurement answers which signal",
                       "## When there is no contract")
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
    section = _section(_text(CONTRACT),
                       "## Structural invariants: which measurement answers which signal",
                       "## When there is no contract")
    assert "Report the zero" in section
    assert "unjudged is invisible" in section


def test_the_mapping_forbids_answering_a_structural_bound_from_an_object():
    """In a `.o` the call edges are unapplied relocations, so `has_unknown_callees`
    reads false for a binary whose callees were never linked. Reporting 0 from that
    is a confidently wrong answer, which is worse than no answer."""
    section = _section(_text(CONTRACT),
                       "## Structural invariants: which measurement answers which signal",
                       "## When there is no contract")
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


# ── precedence: one fact, one row ───────────────────────────────────────────

def test_the_contract_decides_a_row_it_covers():
    section = _section(_text(CONTRACT),
                       "## One fact, one row: the entry decides the status",
                       "## Structural invariants:")
    assert "the entry decides that row's status" in section
    assert "quotes its `text`" in section
    # The other half: a contract holding one budget must not silence built-in
    # judgement of every other signal.
    assert "applied per signal rather than per run" in section
    assert "Never emit both your own row and a contract row for one fact" in section


def test_a_soundness_caveat_survives_the_precedence_rule():
    """"This depth is a lower bound because a callee is missing" qualifies what a
    number means; it is not a competing verdict, so an entry never displaces it."""
    section = _section(_text(CONTRACT),
                       "## One fact, one row: the entry decides the status",
                       "## Structural invariants:")
    assert "A soundness caveat is not a verdict" in section


@pytest.mark.parametrize("skill", JUDGING_SKILLS)
def test_every_judging_skill_reads_the_two_shared_sections(skill):
    body = _skill(skill)
    assert "One fact, one row" in body, f"{skill} does not apply the precedence rule"
    assert "Structural invariants: which measurement answers which signal" in body, (
        f"{skill} does not apply the signal mapping")


# ── the route from a skill's numbers to a parent's Safety row ───────────────

def test_stack_depth_owns_all_four_signals():
    body = _skill("stack-depth")
    for signal in SIGNALS:
        assert signal in body, f"stack-depth does not report {signal}"
    assert "including the count `0` when the run was clean" in body


def test_stack_depth_hands_the_counts_back_to_a_parent_skill():
    """The `safety:` fold-back line is the only route these counts have into a
    preflight or post-edit conclusion table."""
    body = _skill("stack-depth")
    assert "safety: recursion_cycles" in body, "no structural fold-back line"
    for parent in ("loci-preflight", "loci-post-edit"):
        assert "`safety:` line" in _skill(parent), (
            f"{parent} does not read the fold-back line that carries the counts")


def test_control_flow_reports_a_breach_but_never_claims_the_invariant_holds():
    """Its scope is per-function and the invariants are whole-binary: one hazard is
    evidence of a breach, a clean set of CFGs is not the bound holding."""
    body = _skill("control-flow")
    assert "The Contract Envelope is input only" in body, "control-flow has no contract wiring"
    assert "Never report `0` against a whole-binary bound" in body
    assert "stack-depth" in body, "control-flow does not name what does measure them"


@pytest.mark.parametrize("skill", ("loci-preflight", "loci-post-edit"))
def test_a_clean_run_does_not_render_a_pass_it_did_not_measure(skill):
    body = _skill(skill)
    assert "the row rather than render ✅" in body, (
        f"{skill} may claim a structural invariant holds on a run that never "
        f"measured it")


def test_preflight_escalates_when_a_hazard_meets_an_enabled_invariant():
    """The CFG is per-function, so the count that judges a whole-binary entry has to
    come from stack-depth — that escalation is the mapping, in practice."""
    body = _skill("loci-preflight")
    assert "an enabled structural invariant bounds it" in body
