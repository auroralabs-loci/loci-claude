"""Lint: only three `loci` commands are ever put in front of the user.

`! loci login`, `loci cockpit` and `! loci contract accept`. Each is one the agent
is barred from running itself — a browser flow, a full-screen view that takes over
its terminal, and the moment a bound's authorship transfers. Everything else the
CLI does, the agent does, or a slash command does on the user's behalf.

The `! ` prefix is the enforceable half: in Claude Code it means *the user types
this*, so any `! loci <verb>` outside the two allowed verbs is a handover by
construction, whatever the surrounding prose claims. The rest of the rule is prose
and `test_the_rule_is_still_written` is what keeps it from being deleted quietly.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
DOCS = sorted((PLUGIN_ROOT / "skills").rglob("*.md"))
CONTRACT = PLUGIN_ROOT / "skills/_shared/loci-runtime-contract.md"

#: `! loci <verb>` — the "you type this" form.
HANDOVER = re.compile(r"!\s+loci\s+([a-z-]+(?:\s+[a-z-]+)?)")

ALLOWED = {"login", "contract accept"}


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(PLUGIN_ROOT)))
def test_no_cli_verb_is_handed_to_the_user(doc):
    hits = set()
    for lineno, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
        for match in HANDOVER.finditer(line):
            verb = match.group(1)
            if verb in ALLOWED or verb.split()[0] in ALLOWED:
                continue
            hits.add((lineno, verb))
    assert not hits, (
        f"{doc.relative_to(PLUGIN_ROOT)} hands the user a `loci` command: {sorted(hits)}. "
        f"Only `! loci login` and `! loci contract accept` are theirs to type "
        f"(`loci cockpit` is theirs to start). Run it yourself, or name the skill "
        f"that owns it.")


def test_the_rule_is_still_written():
    text = CONTRACT.read_text(encoding="utf-8")
    assert '<a id="user-commands"></a>' in text
    assert "## The three `loci` commands a user ever sees" in text
    section = text[text.index("## The three `loci` commands a user ever sees"):]
    section = section[:section.index("\n## ")]
    for allowed in ("! loci login", "loci cockpit", "! loci contract accept"):
        assert allowed in section
    assert "Not `loci elf …`" in section
    assert "/loci:init" in section and "/loci:setup" in section


def test_every_reporting_skill_carries_the_rule():
    for rel in ("skills/exec-trace/SKILL.md", "skills/loci-preflight/SKILL.md",
                "skills/loci-post-edit/SKILL.md", "skills/control-flow/SKILL.md",
                "skills/stack-depth/SKILL.md", "skills/memory-report/SKILL.md"):
        text = re.sub(r"\s+", " ", (PLUGIN_ROOT / rel).read_text(encoding="utf-8"))
        assert "The three `loci` commands a user ever sees" in text, rel


def test_the_unenforced_contract_entry_points_at_the_skill():
    """It used to close with `fix with: ! loci contract lint`, which is a verb the
    user has no reason to learn and the agent can run itself."""
    post_edit = (PLUGIN_ROOT / "skills/loci-post-edit/SKILL.md").read_text(encoding="utf-8")
    assert "fix with: /loci:contract" in post_edit
    assert "! loci contract lint" not in post_edit
