"""059: the reasoned word a run has no entry to carry.

A project contract can cover every signal, every entry can compute, and every one can
pass — and the report still closes on a concern, because
`loci-runtime-contract.md`'s `lower_bound` rule keeps a `≥` figure off ✅ whatever
headroom the bound had. `check_entries` offers the agent only the entries it could not
judge, so on that run `data.agent_judged` is empty and `--agent-judged` has no
`entry_key` to take the word. It reached the record as prose in `--agent-note`, which
carries no level, and the feed row showed the gate's `pass` beside a session that had
just said CAUTION. Two live instances in one session, one from exec-trace and one from
preflight.

The CLI's answer is `stats record --run <id> --agent-verdict <word>`, keyed to the run.
What this file pins is that the plugin tells the skills to send it: the shared rule,
the `lower_bound` trigger that produced the report, and every judging skill's route to
both.
"""

from __future__ import annotations

from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
SKILLS = PLUGIN_ROOT / "skills"
VERDICTS = SKILLS / "_shared" / "verdicts.md"
CONTRACT = SKILLS / "_shared" / "loci-runtime-contract.md"

#: `control-flow` joined them in PR #291, which gave it the two structural
#: signals its own graph determines to judge; it routes to the recording
#: section like the other five. It is deliberately not in `LOWER_BOUND_SKILLS`
#: below: what it withholds is a zero whose SCOPE it cannot vouch for, which
#: it reports as `unmeasured` rather than as a `≥` figure.
JUDGING_SKILLS = ("loci-preflight", "loci-post-edit", "stack-depth",
                  "memory-report", "exec-trace", "control-flow")

RECORDING_ANCHOR = "verdicts.md#recording-the-verdict"

#: The skills whose figure can be a floor. A `≥` keeps the report off ✅ while every
#: bound passes, so these are the reports that close on a word the gate never reached.
#: `memory-report` is absent: a section size is exact, and it states no lower bound.
LOWER_BOUND_SKILLS = ("loci-preflight", "loci-post-edit", "stack-depth", "exec-trace")


def test_the_shared_rule_names_the_run_level_flag():
    text = VERDICTS.read_text(encoding="utf-8")
    assert "--agent-verdict" in text, (
        "the run-level route is stated once, here — a skill that closes on a word no "
        "offered entry carries has nowhere else to read it")
    assert "--agent-verdict <flagged|cleared|no_opinion>" in text, \
        "the run-level word takes the same three, and no measured one"


def test_a_run_level_flag_is_told_to_carry_its_reasoning():
    """The CLI refuses it without `--agent-note`, and a skill that learns that from an
    exit code learns it after the report is printed."""
    text = VERDICTS.read_text(encoding="utf-8")
    bullet = text.index("- **`--agent-verdict <flagged|cleared|no_opinion>`**")
    window = text[bullet:bullet + 900]
    assert "--agent-note" in window, (
        "`--agent-verdict flagged` needs the sentence: an alarm with no reasoning "
        "cannot be acted on or dismissed")


def test_the_lower_bound_rule_routes_to_the_record():
    """The trigger, at the rule that produces it. A `≥` figure keeps the report off ✅
    while every bound passes, so the gate word and the printed word part company right
    here — and this is where the skill has to be told to record the difference."""
    text = CONTRACT.read_text(encoding="utf-8")
    marker = "never claim a ✅\non a number that can only grow"
    assert marker in text, "the `lower_bound` rule moved; re-anchor this test"
    window = text[text.index(marker):text.index(marker) + 700]
    assert "--agent-verdict" in window, (
        "a `≥` that keeps the report off ✅ and leaves the run recorded `pass` is the "
        "same run described two ways — the cockpit shows the gate's word")


@pytest.mark.parametrize("skill", JUDGING_SKILLS)
def test_every_judging_skill_reaches_the_rule(skill):
    """Not one skill's defect: each routes its reasoned word through the same call, so
    each has to reach the section that now carries the run-level route."""
    text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
    assert RECORDING_ANCHOR in text, (
        f"{skill} does not link the recording section, so the run-level rule never "
        f"reaches it")


@pytest.mark.parametrize("skill", LOWER_BOUND_SKILLS)
def test_a_skill_that_can_close_on_a_floor_names_the_run_level_flag(skill):
    """Reaching the shared rule is not being told to use it.

    stack-depth linked the section and its own recording step never mentioned the flag,
    so a QA run that printed `worst-case ≥344 B` recorded the gate's `pass` — the feed
    then read `PASS` under a report that had closed on CAUTION.
    """
    text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
    assert "--agent-verdict" in text, (
        f"{skill} can print a `≥` figure that no bound explains, so its own recording "
        f"step must send `--agent-verdict flagged` — the link to the shared rule is not "
        f"the instruction")
