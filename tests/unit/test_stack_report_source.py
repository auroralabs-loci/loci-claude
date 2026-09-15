"""Lint: stack-depth's report has a source in the one call it makes, and the path is
not a row.

Two field defects from one QA session (`session-60984982`), both in the same skill.

**The figures had no source.** `SKILL.md`'s Step 4 requires a worst-case depth, an
average, a frame size and the per-function frames along the worst path. `loci analyse
stack` returned none of them: it computed the whole per-function analysis and published
only the four structural counts, so the "one free CLI call" the skill opens with could
not answer its own report. The agent grepped the plugin for the field names it had been
told to print, read the skill's own `evals/evals.json` looking for the output shape,
re-ran the same call twice more, then fell through to `loci elf stack` — a second full
disassembly of a binary the first call had already read — to get the numbers. The CLI
now carries them in `data.detail.stack_analysis`; this pins that the skill is told
where they are, because a figure a skill cannot source is a figure it goes hunting for.

**The worst-case path was a row.** It is a call chain, and the envelope's signal
vocabulary has nothing for it — no `stack_path` to bound — so its conclusion-table row
could only ever carry `—`. Every neighbouring row answers a signal a bound can cover,
and the QA report duly printed `⚑` on it: an icon claiming a judgement nothing made.
The chain stays in the per-function report, which is where evidence for the depth goes.
"""
from __future__ import annotations

from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
SKILL = PLUGIN_ROOT / "skills" / "stack-depth" / "SKILL.md"

#: The figures Step 4's per-function report prints. Each is a key of one entry of
#: `data.detail.stack_analysis`.
REPORT_FIGURES = ("worst_case_depth", "average_depth", "frame_size",
                  "per_function_frames", "worst_case_path")


def _text() -> str:
    return SKILL.read_text(encoding="utf-8")


def test_the_envelope_read_names_where_the_figures_are():
    text = _text()
    assert "data.detail.stack_analysis" in text, (
        "the report's figures live in `data.detail.stack_analysis` and the skill must "
        "be shown the key — unshown, it hunts: a grep of the plugin, the skill's own "
        "eval fixtures, and a second disassembly through `loci elf stack`")
    for figure in REPORT_FIGURES:
        assert figure in text, (
            f"Step 4 reports {figure} and the skill never names the field it comes "
            f"from, so the number has no source in the call the skill makes")


def test_the_skill_does_not_send_itself_to_a_raw_elf_verb():
    """`loci elf stack` is the plumbing under `analyse stack`. Running it for a figure
    the envelope now carries costs a second full disassembly and records nothing."""
    text = _text()
    assert "loci elf stack" not in text or "not this skill's to call" in text, (
        "stack-depth must not route the model to `loci elf stack`; the envelope "
        "carries the figures and the `elf` verbs record no run")


def test_the_worst_case_path_is_not_a_conclusion_row():
    text = _text()
    assert "| Worst-case path" not in text, (
        "the examples must not show a `Worst-case path` row — a report copies the "
        "example, and the QA run copied its icon onto a row nothing can judge")

    catalogue = text[text.index("### Row catalogue"):]
    catalogue = catalogue[:catalogue.index("Table footer")]
    assert "**Worst-case path**" not in catalogue, (
        "no row for the path: the signal vocabulary has nothing to bound it with, so "
        "the row can only carry `—` and an icon there asserts a judgement")
    assert "worst-case path is not a row" in catalogue.lower(), (
        "state the rule where the catalogue is read, or the row comes back")


def test_the_path_is_still_reported_as_evidence():
    """Dropping the row does not drop the chain: it is how a reader sees which callees
    own the bytes, and the skill has required it since the first stack-depth release."""
    text = _text()
    assert "Worst-case path:" in text, "the per-function report keeps the chain"
    assert "always include `Worst-case path`" in text
