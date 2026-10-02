"""One-hop reach: a skill's `SKILL.md` plus the reference files it links.

The rewrite split each skill into `SKILL.md` and sibling `.md` files, and the rule
is one hop — a skill links the file directly, never through another file. A lint
asserting a rule is PRESENT therefore reads the reach, not `SKILL.md` alone.

Only for presence. A lint asserting a rule is ABSENT from a step must keep reading
that step's own slice, or the widening is what hides the regression.
"""

from __future__ import annotations

import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
SKILLS_DIR = PLUGIN_ROOT / "skills"

_LINK = re.compile(r"\]\(([A-Za-z0-9_.-]+\.md)(?:#[^)]*)?\)")


def reference_files(skill: str) -> list[Path]:
    """The sibling `.md` files `SKILL.md` links, in the order it links them."""
    root = SKILLS_DIR / skill
    body = (root / "SKILL.md").read_text(encoding="utf-8")
    seen: list[Path] = []
    for name in _LINK.findall(body):
        p = root / name
        if p.is_file() and p not in seen:
            seen.append(p)
    return seen


def reach(skill: str) -> str:
    """`SKILL.md` and everything one hop from it, concatenated."""
    root = SKILLS_DIR / skill
    parts = [(root / "SKILL.md").read_text(encoding="utf-8")]
    parts += [p.read_text(encoding="utf-8") for p in reference_files(skill)]
    return "\n".join(parts)
