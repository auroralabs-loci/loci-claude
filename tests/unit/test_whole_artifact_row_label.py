"""AAD-7876: a row bounded on no function names its artifact in `FUNCTION`, never an em dash."""

from __future__ import annotations

import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
SKILLS = PLUGIN_ROOT / "skills"
VERDICTS = SKILLS / "_shared" / "verdicts.md"

_EM_DASH_FUNCTION = re.compile(
    r"^\|\s*(?:└ )?`?(?:Performance|Stack|Memory|Safety|Energy)\b[^|]*\|\s*—\s*\|", re.M)


def test_the_shared_rule_names_the_artifact_in_function():
    text = " ".join(VERDICTS.read_text(encoding="utf-8").split())
    start = text.index("**`FUNCTION` is what the row is bounded on.**")
    rule = text[start:text.index("Two columns", start)]
    for named in ("`data.artifact`", "`(object)`", "`(elf)`", "never an em dash"):
        assert named in rule, (named, rule)


def test_no_example_draws_a_whole_artifact_row_with_an_em_dash():
    hits = [f"{p.relative_to(PLUGIN_ROOT)}:{text[:m.start()].count(chr(10)) + 1}"
            for p in SKILLS.rglob("*.md")
            for text in [p.read_text(encoding="utf-8")]
            for m in _EM_DASH_FUNCTION.finditer(text)]
    assert not hits, hits
