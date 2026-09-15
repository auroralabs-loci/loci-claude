"""Lint: skill docs render status with exactly one icon set — ✅ / 🔶 / ❌.

The middle glyph was `⚠️` until 2026-08-14, when it became 🔶 (U+1F536). Unlike
`⚠️` it needs no variation selector to render in emoji presentation, which is
what the old glyph kept regressing on. The set has regressed twice before:
57fb0c1 (2026-08-12) reintroduced 23 bare `⚠` and 18 `✓` through a docs PR, and
9c9e32e (2026-08-14) swapped the whole set to 🟢/🟡/🔴. Both landed unnoticed
because nothing asserted the set.

Text-presentation glyphs (`⚠`, `✓`, `✗`) are the dangerous half: they sit at a
different width beside the emoji ones and knock table columns out of alignment,
and they carry no colour.

Scope is `skills/**/*.md`, PROSE ONLY: fenced blocks and inline code spans are
skipped. The rule is about a glyph *rendered to the user as a status*, and a
glyph inside code is not that -- it is either sample output or the name of a
character. Both exemptions are load-bearing, and `skills/init/voice.md` needs
both at once:

* Its progress checklist is sample terminal output whose gutter mixes `✓` with
  `·`, and BOTH are one column wide. `✅` is two (east-asian-width W), so
  substituting it shifts every ticked line one column right of every pending
  one -- and that file has the block reprinted as each stage lands, so the
  misalignment would jitter down the screen on every reprint. This is the case
  the previous docstring already conceded, naming a `usage-examples.md` that no
  longer exists; the exemption it described was never implemented in code, so
  the concession had no mechanism behind it and the first file to rely on it
  failed.
* The prose *explaining* that block has to name `✓` and `✗` to be about them.
  Rewriting those to `✅`/`❌` would leave the text describing glyphs the block
  does not contain.

What this does NOT excuse is the regression the lint exists for: 57fb0c1
reintroduced 23 **bare** `⚠` and 18 **bare** `✓`, in running prose, and bare is
exactly what is still caught. Putting a status glyph in backticks to quiet this
lint changes what the file renders, which is the point.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
SKILLS = PLUGIN_ROOT / "skills"

PASS, CAUTION, FAIL = "✅", "\U0001f536", "❌"

BANNED = {
    "⚠": "⚠️ / ⚠ (U+26A0) — retired 2026-08-14, use 🔶",
    "✓": "✓ (U+2713) — use ✅",
    "✔": "✔ (U+2714) — use ✅",
    "✗": "✗ (U+2717) — use ❌",
    "✘": "✘ (U+2718) — use ❌",
    "\U0001f7e2": "🟢 — use ✅",
    "\U0001f7e1": "🟡 — use 🔶",
    "\U0001f534": "🔴 — use ❌",
    "\U0001f538": "🔸 small orange diamond (U+1F538) — use 🔶",
}

DOCS = sorted(SKILLS.rglob("*.md"))


def test_skill_docs_found():
    assert DOCS, f"no skill docs under {SKILLS} — the lint below would pass vacuously"


#: An inline code span: a run of backticks, the shortest content that reaches a
#: run of the SAME length, on one line. Matching the run length is what keeps
#: ``a `✓` and a `·` `` from reading as one span that swallows the text between
#: them -- which would exempt that prose instead of the two glyphs.
_CODE_SPAN = re.compile(r"(`+)(?:(?!\1).)*?\1")

#: A fence opener/closer: three or more backticks or tildes, optionally indented.
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")


def _prose_lines(text: str):
    """Yield ``(lineno, prose)`` with fenced blocks dropped and code spans blanked.

    The fence marker is tracked by its own character so a tilde fence cannot be
    closed by a backtick one, and by its run length so a longer fence survives a
    shorter one inside it.
    """
    fence = None
    for lineno, line in enumerate(text.splitlines(), 1):
        opener = _FENCE.match(line)
        if fence is None:
            if opener:
                fence = opener.group(1)
                continue
        else:
            if opener and opener.group(1)[0] == fence[0] and \
                    len(opener.group(1)) >= len(fence):
                fence = None
            continue
        yield lineno, _CODE_SPAN.sub(" ", line)


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(SKILLS)))
def test_no_offbrand_status_glyphs(doc: Path):
    hits = []
    for lineno, line in _prose_lines(doc.read_text(encoding="utf-8")):
        for ch in line:
            why = BANNED.get(ch)
            if why is None:
                continue
            hits.append(f"  {doc.relative_to(PLUGIN_ROOT)}:{lineno}: {why}")
    assert not hits, (
        "off-brand status glyphs in PROSE; the set is ✅ / 🔶 / ❌.\n"
        "Inside a fence or backticks this lint does not look — but backticking a "
        "glyph to silence it changes what the file renders.\n" + "\n".join(hits))


def test_the_exemptions_are_narrow():
    """The stripper must drop code and nothing else.

    A lint that quietly stopped looking at prose would pass for ever, and the
    regression it guards (bare glyphs reintroduced by a docs PR) is invisible the
    moment the scope is too wide. So both exemptions are pinned by example, and a
    bare glyph on a line that also carries a code span is still caught.
    """
    doc = (
        "prose ✓ one\n"                  # 1: bare, caught
        "```\n"                            # 2: fence opens
        "✓ sample output\n"                # 3: exempt
        "```\n"                            # 4: fence closes
        "a `✓` named and a ✗ bare\n"       # 5: span exempt, bare caught
        "~~~\n"                            # 6: tilde fence opens
        "✗ more output\n"                   # 7: exempt
        "~~~\n"                            # 8: closes
        "after ✘ fence\n"                   # 9: bare, caught
    )
    found = [(n, ch) for n, line in _prose_lines(doc) for ch in line if ch in BANNED]
    assert found == [(1, "✓"), (5, "✗"), (9, "✘")], found


def test_a_fence_is_closed_by_its_own_run():
    """A longer fence survives a shorter one inside it -- otherwise a block that
    shows fenced markdown would close early and its remainder be scanned as prose.
    A tilde fence is not closed by a backtick one either."""
    nested = "````\n```\n✓ still inside\n```\n````\n✓ outside\n"
    found = [(n, ch) for n, line in _prose_lines(nested) for ch in line if ch in BANNED]
    assert found == [(6, "✓")], found

    crossed = "~~~\n```\n✓ still inside\n~~~\n✓ outside\n"
    found = [(n, ch) for n, line in _prose_lines(crossed) for ch in line if ch in BANNED]
    assert found == [(5, "✓")], found


def test_canonical_trio_still_used():
    """Guards the lint above from passing on docs that dropped icons entirely."""
    corpus = "".join(d.read_text(encoding="utf-8") for d in DOCS)
    for icon in (PASS, CAUTION, FAIL):
        assert icon in corpus, f"{icon} appears in no skill doc"
