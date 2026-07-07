"""Lint: the `/tmp` path-policy in docs must be unconditional, not OS-scoped.

Every doc surface mentioning `/tmp` (or `/var/tmp`) must carry an unconditional
prohibition — uppercase `NEVER` or `never write|use` — within a small window of
the mention. OS-conditional framing ("never on Windows") lets the LLM emit
`> /tmp/...` on macOS/Linux, which trips Claude Code's out-of-project permission
prompt and halts preflight/post-edit/eval automation.
"""

from __future__ import annotations

import re
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
SKILLS_DIR = PLUGIN_ROOT / "skills"
SESSION_INIT = PLUGIN_ROOT / "hooks" / "session-init.sh"

DOC_FILES = sorted(SKILLS_DIR.rglob("SKILL.md")) + [SESSION_INIT]

# Matches `/tmp` or `/var/tmp` as a leading path segment. Excludes
# substrings like `*.tmp`, `tmpdir`, `build_tmp`, `${TMP}` etc.
TMP_PATH_RE = re.compile(r"(?<![A-Za-z0-9_])/(?:tmp|var/tmp)\b")

# Unconditional prohibition. Lowercase "never" alone is not enough ("never on
# Windows" was the bug); require uppercase NEVER or the verb-form "never write|use".
UNCONDITIONAL_RE = re.compile(r"\bNEVER\b|\bnever\s+(?:write|use)\b")

# Window around each /tmp mention — tight enough that an unrelated "NEVER"
# elsewhere in the file can't satisfy the lint.
WINDOW_CHARS = 200


def test_tmp_path_policy_is_unconditional():
    offenders: list[tuple[str, int, str]] = []
    for path in DOC_FILES:
        text = path.read_text(encoding="utf-8")
        for m in TMP_PATH_RE.finditer(text):
            lo = max(0, m.start() - WINDOW_CHARS)
            hi = min(len(text), m.end() + WINDOW_CHARS)
            if not UNCONDITIONAL_RE.search(text[lo:hi]):
                lineno = text.count("\n", 0, m.start()) + 1
                offenders.append(
                    (str(path.relative_to(PLUGIN_ROOT)), lineno, m.group(0))
                )

    assert not offenders, (
        "/tmp (or /var/tmp) mentions must be bracketed by an unconditional "
        "prohibition — uppercase NEVER or 'never write|use' — within "
        f"{WINDOW_CHARS} chars. OS-conditional framing ('never on Windows') "
        "lets the LLM emit `> /tmp/...` on macOS/Linux and halts automation:\n"
        + "\n".join(f"  {f}:{ln} -> {t}" for f, ln, t in offenders)
    )
