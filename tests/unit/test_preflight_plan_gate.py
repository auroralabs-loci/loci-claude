"""The preflight skill's plan-mode gate, as its frontmatter and body spell it.

AAD-7803. The first `--host copilot` eval run with `gpt-5.6-sol` failed all five
preflight criticals in the inverted direction. The transcripts said why, and it
was two defects, not one:

* pf-critical-4/-5 (not plan mode, must stay silent): the description said
  "Do NOT invoke for review/explain requests or direct edits outside plan mode"
  — two kinds of request, not a rule — while `when_to_use` listed "how should
  I" and "modify" as triggers. A model reading literally took the words as the
  gate and invoked on "How should I modify …" with plan mode off. The gate is
  the host's plan mode, so the frontmatter now says so, whatever the wording.
* pf-critical-1/-2/-3 (plan mode, must invoke): the skill DID run — prepare,
  measure, record, a full report — but the Plan-mode paragraph's Copilot
  sentence ended "and the reply's summary may stay brief", so the report went
  into Copilot's plan file and the reply carried one line. The reply is what the
  user reads in `-p` mode and what every reader of a transcript grades; the plan
  file is in addition to it, never instead.

AAD-8074. Under Claude Code the plan body was "the verdict box, then only the
adjusted steps". A preflight run measured hot path, worst path and energy, then
wrote that plan and handed it over with no report in the reply: the approval
dialog showed the verdict and none of the figures. The plan is what the user
approves on both hosts, so it carries the report on both.

These pins hold the wording that closes each hole, so a later trim cannot
reopen one without failing here.
"""

from __future__ import annotations

import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
SKILL = PLUGIN_ROOT / "skills" / "loci-preflight" / "SKILL.md"


def _text() -> str:
    return SKILL.read_text(encoding="utf-8").replace("\r\n", "\n")


def _frontmatter() -> str:
    text = _text()
    assert text.startswith("---\n")
    end = text.index("\n---\n", 4)
    return re.sub(r"\s+", " ", text[4:end])


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _section(heading: str) -> str:
    text = _text()
    start = text.index(f"\n{heading}\n")
    nxt = re.search(r"\n## ", text[start + 1:])
    return text[start: start + 1 + nxt.start()] if nxt else text[start:]


def test_the_description_gates_on_plan_mode_and_not_on_the_wording():
    fm = _frontmatter()
    assert "MANDATORY in plan mode (/plan; a [[PLAN]] prompt under GitHub Copilot CLI)" in fm
    assert "Outside plan mode do NOT invoke, whatever the words" in fm
    # The two phrasings the negative evals use, named as answered WITHOUT it.
    assert 'a "how should I …" question with plan mode off is answered without it' in fm
    assert "Plan mode is the host's mode, never inferred from the wording." in fm
    # The old two-kinds sentence is gone — it named exclusions, not a rule.
    assert "Do NOT invoke for review/explain requests or direct edits outside plan mode" not in fm


def test_the_trigger_words_are_scoped_to_plan_mode_in_when_to_use():
    fm = _frontmatter()
    m = re.search(r"when_to_use: > (.*)$", fm)
    assert m, "frontmatter needs `when_to_use`"
    wtu = m.group(1)
    assert wtu.startswith("In plan mode:")
    assert '"how should I"' in wtu and '"modify"' in wtu
    assert "The same words with plan mode off: never." in wtu


def test_the_body_opens_with_the_gate():
    """A literal reader that invoked the skill on its trigger words reads the body
    next, so the body's first paragraph is the gate, and it says what to do when
    plan mode is off: stop, no `loci` call, no report."""
    body = _flat(_text()[_text().index("# loci-preflight"):])
    assert body.startswith("# loci-preflight **Gate: plan mode only.** This skill applies when the host is in plan mode")
    assert "`/plan` under Claude Code; under GitHub Copilot CLI a `[[PLAN]]` prefix on the prompt" in body
    assert ("If plan mode is off, stop here, whatever the request says: answer it as asked, "
            "run no `loci` command for it and print no `## Preflight:` report.") in body


def test_when_to_run_opens_in_plan_mode():
    sec = _flat(_section("## When to run"))
    assert sec.startswith("## When to run In plan mode, run preflight as part of forming your plan")
    # The opening paragraph gates too: "run it during planning" read as a licence.
    assert "Run it in plan mode — while you are still deciding what to write" in _flat(_text())
    assert "Run it during planning" not in _text()


def test_the_reply_carries_the_full_report_on_every_host():
    sec = _flat(_section("## When to run"))
    plan = sec[sec.index("**Plan mode:**"):]
    assert "print the full report" in plan and "never a one-line summary" in plan
    assert ("The reply carries the full report on every host; the plan file is in "
            "addition to it, never instead, and a one-line summary is not it.") in plan
    # The licence that sent the report into the plan file alone is gone.
    assert "summary may stay brief" not in _text()
    # The plan carries it too, on both hosts: the plan is what the user approves.
    assert "On both hosts the plan is what the user approves" in plan
    assert "so it carries the full report after the verdict box and before the steps" in plan


def test_the_output_format_section_still_names_the_response_text():
    sec = _flat(_section("## Output format"))
    assert "Emit the preflight report in the **response text**" in sec
    assert "A one-line summary is not the report" in sec
    # Every host's plan body carries the report between the box and the steps.
    assert ("on every host the plan body opens with the verdict in the LOCI box, "
            "then the report, then the adjusted steps") in sec
    # The Claude-only exemption that hid the figures from the approval dialog is gone.
    assert "only the adjusted steps" not in _text()
    # No "all-clear shorthand" licence to replace the report with a line.
    assert "shorthand" not in _text()
