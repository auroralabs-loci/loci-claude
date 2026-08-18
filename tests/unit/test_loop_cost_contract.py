"""Lint: the loop-cost rule exists once, and both mandatory skills apply it.

`loci timing` prices a basic block **once**. A block inside a loop runs once per
lap, so a worst path summed block-by-block understates a function by a factor of
its trip count — the same class of error as treating a bare `bl` as a call site's
full cost, and usually the larger of the two. asmslicer 1.2.0 annotates every
in-loop block with `iters` (executions per call of the function, already
multiplied across nested loops) and `loci elf asm` / `loci elf cfg` report
a `data.loops` roll-up whose counts say whether the numbers can be exact.

There is deliberately no capability flag: one existed, was derived from the installed
CLI's version number, compared against a minimum that never matched the release which
shipped the feature, and so reported the capability as absent on builds that had it —
switching the whole feature off through the contract line that trusted it.

These tests pin the parts a future edit would quietly drop:

* the rule lives in the shared contract **once**, not copied into two skills that
  then drift — which is what happened to the `bl`-expansion prose;
* both skills link to it and both multiply by `iters` where they compute a total;
* the value sent to `contract check` is the multiplied one, since that is the
  number a budget is judged against;
* `iters=?` is never resolved by guessing, and produces a `>=` lower bound.

A regex cannot check that a model obeys prose. It can check that the prose is
still there, which is the failure mode with a track record in this repo.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
SKILLS = PLUGIN_ROOT / "skills"
CONTRACT = SKILLS / "_shared" / "loci-runtime-contract.md"
PREFLIGHT = SKILLS / "loci-preflight" / "SKILL.md"
POST_EDIT = SKILLS / "loci-post-edit" / "SKILL.md"

ANCHOR = "loop-cost"

pytestmark = pytest.mark.unit


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.fixture(params=[PREFLIGHT, POST_EDIT], ids=["preflight", "post-edit"])
def skill(request) -> Path:
    return request.param


# ── the rule lives in one place ──────────────────────────────────────────────

def test_the_contract_carries_the_loop_cost_section_with_an_anchor():
    text = _text(CONTRACT)
    assert f'id="{ANCHOR}"' in text, (
        f"the shared contract has no `{ANCHOR}` anchor, so nothing can link to the rule"
    )
    assert text.count(f'id="{ANCHOR}"') == 1, "two anchors means two rules, and one will drift"


def test_the_rule_is_not_copied_into_the_skills():
    """The `bl`-expansion prose is duplicated across both skills and has already
    diverged. The loop rule is referenced, not restated: a skill may name `iters`
    and the four cases, but the table of cases belongs to the contract alone."""
    for path in (PREFLIGHT, POST_EDIT):
        text = _text(path)
        assert text.count("| Annotation | Meaning |") == 0, (
            f"{path.name} restates the contract's case table instead of linking to it"
        )


# ── both skills reach the rule and the data ──────────────────────────────────

def test_the_skill_links_to_the_loop_cost_section(skill):
    text = _text(skill)
    assert f"#{ANCHOR}" in text, (
        f"{skill.name} never links to the loop-cost rule, so a run has no reason to "
        f"read it"
    )


def test_the_skill_reads_the_loops_field_for_triage(skill):
    """`data.loops` is read for the honesty counts — `unknown_trip_count` and
    `uncounted_cycles`, either of which makes a per-call total a lower bound."""
    text = _text(skill)
    assert ".data.loops" in text, (
        f"{skill.name} does not read `.data.loops`, so it has no advance warning that "
        f"a total on this run is a lower bound"
    )
    assert "unknown_trip_count" in text, (
        f"{skill.name} reads `.data.loops` but not the field that says whether the "
        f"numbers can be exact"
    )


def test_no_skill_gates_the_feature_on_a_capability_flag(skill):
    """The `annotated` flag is gone and must not come back.

    It was derived from the installed CLI's version number, and the minimum it compared
    against never matched the release that shipped the feature — so it read false on
    builds whose CFG said `loops: 1 (1 with a derived trip count)`. Because the contract
    told skills that false meant "do not use `iters`", one stale integer switched the
    whole feature off with correct `iters=64` values unread in the file. A block's
    `iters` is the evidence; there is nothing to ask permission for.
    """
    text = _text(skill)
    assert "loops.annotated" not in text and '"annotated"' not in text, (
        f"{skill.name} gates on a capability flag again"
    )
    assert "does not annotate loop iterations" not in text, (
        f"{skill.name} still carries the loop-blind fallback line that the flag drove"
    )


def test_the_contract_forbids_reintroducing_the_gate():
    text = _text(CONTRACT)
    section = text.split(f'id="{ANCHOR}"')[1]
    assert "no capability check" in section.lower(), (
        "the loop-cost rule does not state that there is no capability check, which is "
        "the instruction that stops the gate being added back"
    )


def test_the_skill_multiplies_a_block_cost_by_iters(skill):
    """The whole point. A total that sums bare block costs has counted every lap
    once, and the two skills are where the sum happens."""
    text = _text(skill)
    assert re.search(r"[x×*]\s*`?iters`?|`iters`\s*(?:multiplied|multiplication)"
                     r"|multiplied by (?:its |that block's )?`iters`", text), (
        f"{skill.name} mentions no multiplication by `iters`, so its worst-path total "
        f"is lap-collapsed"
    )
    assert "iters=?" in text, (
        f"{skill.name} never names the unknown case, which is the common one"
    )


def test_the_value_sent_to_the_contract_is_the_multiplied_one(skill):
    """A bound is judged against `curr`. An un-multiplied `curr` passes a ceiling
    the code breaches, which is worse than not gating at all."""
    text = _text(skill)
    # Paragraph-scoped, not line-scoped: the two skills wrap this sentence at
    # different columns and a per-line scan would pass on one and fail on the other
    # for a reason that has nothing to do with what either says.
    paragraphs = [par for par in re.split(r"\n\s*\n", text) if "`curr`" in par]
    assert paragraphs, f"{skill.name} never defines `curr`"
    assert any("iters" in par for par in paragraphs), (
        f"{skill.name} does not say that `curr` is the `iters`-multiplied total, so a "
        f"budget can be judged against a lap-collapsed number"
    )


# ── honesty: the unknown case stays unknown ──────────────────────────────────

def test_an_unknown_trip_count_makes_the_total_a_lower_bound(skill):
    """`?` is not 1. It is reported the way an unmeasured external callee already is
    — with `>=` — because a fabricated count is wrong in the same direction every
    time and wrong silently."""
    text = _text(skill)
    assert "≥" in text, f"{skill.name} has no lower-bound convention to fall back to"
    window = "\n".join(ln for ln in text.splitlines() if "iters" in ln)
    assert "≥" in window or "lower bound" in window, (
        f"{skill.name} never ties an unknown `iters` to a lower bound, so a `?` can be "
        f"silently read as 1"
    )


def test_the_contract_forbids_inventing_a_trip_count():
    text = _text(CONTRACT)
    assert re.search(r"[Nn]ever substitute a number", text), (
        "the loop-cost rule does not forbid supplying a trip count of your own — the "
        "one failure mode that is both silent and always in the same direction"
    )
    assert "stack-depth" in text.split(f'id="{ANCHOR}"')[1][:6000], (
        "the rule does not send recursion depth to stack-depth, so `R1` invites a "
        "fabricated iteration count"
    )


def test_expansion_is_ordered_before_multiplication(skill):
    """`iters x (bl_cost + callee_body)`, not `bl_cost + iters x body`. Multiplying
    first counts the callee once, and the wrong order reads as natural."""
    text = _text(skill)
    assert re.search(r"[Ee]xpand.{0,40}first.{0,60}multiply"
                     r"|expand(?:ed)?,? .{0,30}then multipl", text, re.S), (
        f"{skill.name} does not fix the order of `bl` expansion and `iters` "
        f"multiplication; the wrong order silently drops a callee body"
    )


def test_the_recorded_metric_says_it_includes_iters(skill):
    """`loci stats` compares response-time records with each other. A loop-aware
    value against a lap-collapsed one is the throughput-vs-response-time trap again,
    so the metric's definition has to name the multiplication."""
    text = _text(skill)
    block = text[text.index("response_time"):]
    assert "iters" in block[:2000], (
        f"{skill.name}'s response-time definition does not mention `iters`, so a "
        f"loop-aware number can be diffed against a lap-collapsed one"
    )
