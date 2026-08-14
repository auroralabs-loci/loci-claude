"""Lint: skill docs render status with exactly one icon set — ✅ / ⚠️ / ❌.

The trio dates to 10b533f (2026-04-28), which gave the warning its variation
selector so all three render in emoji presentation. It has regressed twice:
57fb0c1 (2026-08-12) reintroduced 23 bare `⚠` and 18 `✓` through a docs PR, and
9c9e32e (2026-08-14) swapped the whole set to 🟢/🟡/🔴. Both landed unnoticed
because nothing asserted the set.

Bare `⚠`, `✓` and `✗` are the dangerous half: they are text-presentation glyphs,
so they sit at a different width beside the emoji ones and knock table columns
out of alignment, and they carry no colour. `⚠` is the easiest to reintroduce by
accident — it looks identical to `⚠️` in most editors and differs only by a
trailing U+FE0F.

Scope is `skills/**/*.md`. `usage-examples.md` is deliberately excluded: its
narrow glyphs are correct inside ASCII-art CLI boxes, where emoji width would
break the borders.
"""

from __future__ import annotations

from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
SKILLS = PLUGIN_ROOT / "skills"

PASS, CAUTION, FAIL = "✅", "⚠️", "❌"

BANNED = {
    "⚠": "bare ⚠ (U+26A0 without U+FE0F) — use ⚠️",
    "✓": "✓ (U+2713) — use ✅",
    "✔": "✔ (U+2714) — use ✅",
    "✗": "✗ (U+2717) — use ❌",
    "✘": "✘ (U+2718) — use ❌",
    "\U0001f7e2": "🟢 — use ✅",
    "\U0001f7e1": "🟡 — use ⚠️",
    "\U0001f534": "🔴 — use ❌",
}

DOCS = sorted(SKILLS.rglob("*.md"))


def test_skill_docs_found():
    assert DOCS, f"no skill docs under {SKILLS} — the lint below would pass vacuously"


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(SKILLS)))
def test_no_offbrand_status_glyphs(doc: Path):
    hits = []
    for lineno, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
        for col, ch in enumerate(line):
            why = BANNED.get(ch)
            if why is None:
                continue
            # ⚠️ is the wanted glyph and starts with the banned bare ⚠.
            if ch == "⚠" and line[col + 1:col + 2] == "️":
                continue
            hits.append(f"  {doc.relative_to(PLUGIN_ROOT)}:{lineno}: {why}")
    assert not hits, (
        "off-brand status glyphs; the set is ✅ / ⚠️ / ❌:\n" + "\n".join(hits))


def test_canonical_trio_still_used():
    """Guards the lint above from passing on docs that dropped icons entirely."""
    corpus = "".join(d.read_text(encoding="utf-8") for d in DOCS)
    for icon in (PASS, CAUTION, FAIL):
        assert icon in corpus, f"{icon} appears in no skill doc"
