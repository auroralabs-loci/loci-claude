"""Lint: LOCI's own counters never reach the user's report.

Two of them exist, both invented by LOCI and both meaningless outside the process
that made them:

* **candidate ids** — `p1`…`pN`, a manifest-local counter whose only job is to let
  `loci analyse measure --select p2` name exactly one path;
* **loop ids** — `L1`, `L2`, a per-artifact counter in `analyse cfg`'s loop table,
  whose only job is to let one `loci` call refer a loop to the next.

A reader of the report has never seen either and has no way to resolve one. They
were reaching the report from three places: post-edit's loop-bound template
(`` `<fn>` L1: 8 -> 16 ``), control-flow's loop table, and the `note` field that
`measure` writes and both timing skills render verbatim (`— on p1`, fixed in
loci-cli `analyse._row_note`). A loop is named by the source range its blocks map
to; a path is named "the ranked hot path" with that span.

The ids themselves stay — this is about prose, not about the flag.

Scope is every fenced block in `skills/**/*.md`, which is where the report
templates and worked examples live. Prose is not scanned: the rules below have to
name the ids in order to forbid them, and the `--select` bullet has to spell the
argument. `test_the_prohibition_is_still_written` is what keeps that untested half
honest — it fails if the rules are deleted.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
DOCS = sorted((PLUGIN_ROOT / "skills").rglob("*.md"))

#: `L1`, `p2` — a bare counter, standing alone. Word-bounded so `p1` inside an
#: identifier (`bb_0xp1`, `step1`) is not a hit, and case-sensitive so a prose
#: `P1` heading is not confused with the candidate id.
COUNTER = re.compile(r"(?<![\w.])(?:L\d+|p\d+)(?![\w-])")

#: Fenced blocks that are legitimately CLI text rather than report prose: a shell
#: invocation may spell `--select p2`, and a jq filter may name the field.
CLI_MARKERS = ("--select", "loci analyse", "jq ")


def _flat(path: Path) -> str:
    """The doc with its 80-column wraps collapsed, so a section name split across
    two lines still matches."""
    return re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))


def _offending_fences(text: str) -> list[tuple[int, str]]:
    """(line number, matched token) for every counter inside a fenced block."""
    out, in_fence, fence_start, buf = [], False, 0, []
    for lineno, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            if in_fence:
                body = "\n".join(buf)
                if not any(m in body for m in CLI_MARKERS):
                    for match in COUNTER.finditer(body):
                        out.append((fence_start, match.group(0)))
            in_fence, fence_start, buf = not in_fence, lineno, []
            continue
        if in_fence:
            buf.append(line)
    return out


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(PLUGIN_ROOT)))
def test_no_invented_counter_in_a_report_template(doc):
    hits = _offending_fences(doc.read_text(encoding="utf-8"))
    assert not hits, (
        f"{doc.relative_to(PLUGIN_ROOT)} prints a LOCI-internal counter in a fenced "
        f"block: {sorted({t for _l, t in hits})} (fence opening at line(s) "
        f"{sorted({l for l, _t in hits})}). A loop is named by the source range its "
        f"blocks map to; a path is 'the ranked hot path' plus that span. `p1` is for "
        f"`--select` and nowhere else.")


def test_the_prohibition_is_still_written():
    """Fails in the other direction: the rule is prose, so nothing else pins it."""
    post_edit = (PLUGIN_ROOT / "skills/loci-post-edit/SKILL.md").read_text(encoding="utf-8")
    control_flow = (PLUGIN_ROOT / "skills/control-flow/SKILL.md").read_text(encoding="utf-8")
    assert "Name the loop by where it is in the source, never by its `id`" in post_edit
    assert "they identify a loop to the next `loci` call, not to a reader" in control_flow


def test_the_shared_rule_is_the_one_place_it_is_stated():
    """Four skills report a path or a loop, and the rule drifted between them once
    already. It lives in the runtime contract now, anchored so the skills can link it."""
    shared = (PLUGIN_ROOT / "skills/_shared/loci-runtime-contract.md").read_text(encoding="utf-8")
    assert '<a id="naming-paths"></a>' in shared
    assert "## Naming a path or a loop in the report" in shared
    for rel in ("skills/exec-trace/SKILL.md", "skills/loci-preflight/SKILL.md",
                "skills/loci-post-edit/SKILL.md", "skills/control-flow/SKILL.md"):
        text = _flat(PLUGIN_ROOT / rel)
        assert "Naming a path or a loop in the report" in text, rel


def test_the_trip_count_is_not_suppressed_with_the_loop_id():
    """The id is noise; the iteration count is the assumption the figure rests on.
    Hiding one must never take the other with it."""
    shared = (PLUGIN_ROOT / "skills/_shared/loci-runtime-contract.md").read_text(encoding="utf-8")
    rule = shared[shared.index("## Naming a path or a loop in the report"):]
    rule = rule[:rule.index("\n## ")]
    assert "The trip count is not an identifier, and it is always reported" in rule
    assert "iterations not derivable (?)" in rule, "a `?` is reported, never filled in"
    assert "reported for every loop named, `?` included" in _flat(
        PLUGIN_ROOT / "skills/control-flow/SKILL.md")


def test_a_user_who_asks_for_the_id_gets_it():
    """The rule is about unasked-for prose. `which path did LOCI pick` is a question
    with an answer, and refusing to give the id is a worse failure than printing it."""
    shared = (PLUGIN_ROOT / "skills/_shared/loci-runtime-contract.md").read_text(encoding="utf-8")
    rule = shared[shared.index("## Naming a path or a loop in the report"):]
    rule = rule[:rule.index("\n## ")]
    assert "When the user asks which one, tell them" in rule
    assert "it does not withhold them from someone asking" in rule


def test_the_note_the_skills_render_verbatim_names_no_candidate_id():
    """Both timing skills quote `measure`'s note as an example, and render it
    verbatim. A stale quote here is how the id would come back without any template
    changing."""
    for rel in ("skills/loci-post-edit/SKILL.md", "skills/loci-preflight/SKILL.md"):
        text = (PLUGIN_ROOT / rel).read_text(encoding="utf-8")
        assert "— on the ranked hot path" in text, rel
        assert "`— on p1`" not in text, rel
