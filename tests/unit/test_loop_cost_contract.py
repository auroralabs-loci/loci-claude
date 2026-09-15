"""Lint, inverted: the path-cost arithmetic is the CLI's, and the prose says so.

This file used to pin the opposite. It asserted that both reflex skills carried
the loop-cost arithmetic in prose — multiply each block by its `iters`, expand a
`bl` before multiplying, send the multiplied value to `contract check` — because
three copies of that arithmetic lived in prose and drifted, and a regex could at
least check the prose was still there.

The prepare/measure split moved the arithmetic into code. `loci analyse measure`
runs the path-cost evaluator once, identically on the Before and the After side:
call-site expansion, recursion through in-binary callees, each block multiplied by
the laps it runs, external callees tainting the total as a `≥` lower bound. The
skills read `data.paths.<fn>` and never re-derive a figure. So the assertions flip:
the failure mode is no longer prose gone missing, it is prose coming *back* — a
fourth copy of an evaluator that already exists, which would then disagree with the
run record in the report.

**Where the old invariant is pinned now.** The arithmetic guarantees this file used
to assert live in the CLI's own tests, against the evaluator rather than against a
sentence: `loci-cli/tests/unit/test_pathcost.py` and
`test_pathcost_fixtures.py` (T3) — `iters` multiplication, `bl` expansion order,
callee recursion, the cycle guard, and the `≥` taint for an unknown trip count.

What stays here:

* the loop-cost rule still exists **once**, in the shared contract, with its
  anchor, and both skills still link to it;
* the contract still forbids inventing a trip count and still states there is no
  capability check — the gate that was derived from a version number and read
  false on builds that had the feature;
* the skills invoke the pair (`analyse prepare` → `analyse measure --prepared`)
  and patch their judgments through `stats record --run … --agent-judged`;
* the skills carry no arithmetic: no multiply-by-`iters`, no expansion order, no
  `bl` pricing.

Two things legitimately survive in the skills and must not be read as arithmetic:
`iters=?` (a real field value from the lower-bound reporting rule — "`iters=?` is a
lower bound, never a `1`") and post-edit's "expanded form" (the report layout).
Both are asserted present below, so an over-broad absence check fails here rather
than quietly passing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
SKILLS = PLUGIN_ROOT / "skills"
CONTRACT = SKILLS / "_shared" / "loci-runtime-contract.md"
VERDICTS = SKILLS / "_shared" / "verdicts.md"
PREFLIGHT = SKILLS / "loci-preflight" / "SKILL.md"
POST_EDIT = SKILLS / "loci-post-edit" / "SKILL.md"

ANCHOR = "loop-cost"

pytestmark = pytest.mark.unit


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.fixture(params=[PREFLIGHT, POST_EDIT], ids=["preflight", "post-edit"])
def skill(request) -> Path:
    return request.param


# ── the rule lives in one place, and both skills reach it ────────────────────

def test_the_contract_carries_the_loop_cost_section_with_an_anchor():
    text = _text(CONTRACT)
    assert f'id="{ANCHOR}"' in text, (
        f"the shared contract has no `{ANCHOR}` anchor, so nothing can link to the rule"
    )
    assert text.count(f'id="{ANCHOR}"') == 1, "two anchors means two rules, and one will drift"


def test_the_skill_links_to_the_loop_cost_section(skill):
    text = _text(skill)
    assert f"#{ANCHOR}" in text, (
        f"{skill.name} never links to the path-cost rule, so a run has no reason to "
        f"read it"
    )


def test_the_rule_is_not_copied_into_the_skills():
    for path in (PREFLIGHT, POST_EDIT):
        assert _text(path).count("| Annotation | Meaning |") == 0, (
            f"{path.name} restates the contract's case table instead of linking to it"
        )


def test_the_contract_hands_path_cost_to_measure():
    section = _text(CONTRACT).split(f'id="{ANCHOR}"')[1][:6000]
    assert "loci analyse measure" in section, (
        "the path-cost rule does not name the verb that computes it, so the arithmetic "
        "has no owner and the prose will grow one back"
    )
    assert re.search(r"[Nn]ever re-derive", section), (
        "the rule does not forbid re-deriving a figure by hand — the one thing that "
        "puts a second, disagreeing number in the report"
    )


# ── the arithmetic is gone from the skills ───────────────────────────────────

MULTIPLY = re.compile(
    r"[x×*]\s*`?iters`?"
    r"|`?iters`?\s*[x×*]"
    r"|multipl\w*\s+(?:\w+\s+){0,4}`?iters`?"
    r"|`?iters`?[- ]multiplied",
    re.I)


def test_the_skill_gives_no_multiply_by_iters_instruction(skill):
    """`measure` multiplies. A skill that also multiplies produces a number that
    disagrees with the run record it is reporting."""
    hit = MULTIPLY.search(_text(skill))
    assert hit is None, (
        f"{skill.name} carries loop arithmetic again: {hit.group(0)!r} — path cost is "
        f"`loci analyse measure`'s, and a fourth copy will drift like the first three"
    )


def test_the_skill_gives_no_expansion_order_prose(skill):
    text = _text(skill)
    order = re.search(r"[Ee]xpand.{0,60}multipl|multipl.{0,60}expand", text, re.S)
    assert order is None, (
        f"{skill.name} fixes the order of call-site expansion and `iters` "
        f"multiplication again: {order.group(0)!r} — that ordering is the evaluator's"
    )


def test_the_skill_gives_no_bl_expansion_instruction(skill):
    """The `bl`-expansion prose is the copy with the longest drift history."""
    text = _text(skill)
    hit = re.search(r"`?\bbl[x]?\b`?|bl_cost|prices the branch", text)
    assert hit is None, (
        f"{skill.name} instructs on call-site (`bl`) pricing again: {hit.group(0)!r}"
    )


def test_no_skill_gates_the_feature_on_a_capability_flag(skill):
    """The `annotated` flag is gone and must not come back.

    It was derived from the installed CLI's version number, and the minimum it
    compared against never matched the release that shipped the feature — so it read
    false on builds whose CFG said `loops: 1 (1 with a derived trip count)`, and one
    stale integer switched the whole feature off.
    """
    text = _text(skill)
    assert "loops.annotated" not in text and '"annotated"' not in text, (
        f"{skill.name} gates on a capability flag again"
    )
    assert "does not annotate loop iterations" not in text, (
        f"{skill.name} still carries the loop-blind fallback line that the flag drove"
    )


def test_the_contract_forbids_reintroducing_the_gate():
    section = _text(CONTRACT).split(f'id="{ANCHOR}"')[1]
    assert "no capability check" in section.lower(), (
        "the path-cost rule does not state that there is no capability check, which is "
        "the instruction that stops the gate being added back"
    )


# ── what the prose invokes instead: the pair, and the typed patch ────────────

def test_the_skill_invokes_prepare_then_measure_on_the_prepared_id(skill):
    text = _text(skill)
    assert "loci analyse prepare" in text, (
        f"{skill.name} never runs `loci analyse prepare`, so there is no manifest to "
        f"measure"
    )
    assert re.search(r"loci analyse measure\s+--prepared", text), (
        f"{skill.name} does not call `loci analyse measure --prepared <id>` — the "
        f"metered half only ever executes a prepared manifest"
    )


def test_the_skill_reads_the_figures_it_reports(skill):
    """`data.paths.<fn>` is the figure. Reading it is the replacement for computing
    it, so its absence would leave the arithmetic with nowhere to have gone."""
    text = _text(skill)
    assert "data.paths" in text, (
        f"{skill.name} never reads `data.paths`, so it has no measured figure to "
        f"report and will compute one"
    )


def test_the_skill_patches_its_judgments_through_the_typed_sink(skill):
    """The command itself moved to `_shared/verdicts.md` in 051 — five skills were
    carrying five copies of one rule and only one of them had the `--agent-note` half.
    What each skill must still do is ROUTE there, so both ends are checked: the skill
    names the shared section and its `--run` patch, and the shared file holds the call
    with both flags on it."""
    text = _text(skill)
    assert "verdicts.md#recording-the-verdict" in text, (
        f"{skill.name} does not route to the shared recording section, so nothing "
        f"tells it to patch the run at all"
    )
    assert "--run" in text, (
        f"{skill.name} no longer says which id its patch names, and the shared "
        f"section cannot know that for it"
    )
    shared = _text(VERDICTS)
    assert re.search(r"stats record\b.{0,200}--run\b", shared, re.S), (
        "the shared recording section no longer carries `stats record --run <id>`"
    )
    for flag in ("--agent-judged", "--agent-note"):
        assert flag in shared, (
            f"the shared recording section names no `{flag}`, so the verdicts or the "
            f"sentence never enter the record"
        )


# ── honesty: the unknown trip count is still reported, not resolved ──────────

def test_the_unknown_trip_count_stays_a_lower_bound(skill):
    """`iters=?` survives on purpose: it is a field value in the reporting rule, not
    arithmetic. `?` is not 1, and the number is prefixed `≥`."""
    text = _text(skill)
    assert "iters=?" in text, (
        f"{skill.name} dropped the `iters=?` case from the lower-bound reporting rule, "
        f"so an underivable trip count can be read as a `1`"
    )
    assert "≥" in text, f"{skill.name} has no lower-bound convention to fall back to"
    window = "\n".join(ln for ln in text.splitlines() if "iters" in ln)
    assert "lower bound" in window or "≥" in window, (
        f"{skill.name} never ties an unknown `iters` to a lower bound"
    )


def test_the_contract_forbids_inventing_a_trip_count():
    text = _text(CONTRACT)
    assert re.search(r"[Nn]ever substitute a number", text), (
        "the path-cost rule does not forbid supplying a trip count of your own — the "
        "one failure mode that is both silent and always in the same direction"
    )
    assert "stack-depth" in text.split(f'id="{ANCHOR}"')[1][:6000], (
        "the rule does not send recursion depth to stack-depth, so a cycle invites a "
        "fabricated iteration count"
    )


def test_the_report_layout_is_not_mistaken_for_expansion_prose():
    """post-edit's "expanded form" is the multi-line report. The absence checks above
    must not be tightened into anything that trips on it."""
    assert "expanded form" in _text(POST_EDIT), (
        "post-edit lost the expanded report form — if an assertion above was widened "
        "to catch the word 'expand', widen it back"
    )
