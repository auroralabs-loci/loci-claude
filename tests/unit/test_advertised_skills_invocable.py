"""Lint: skills advertised as `/<name>` slash commands must be invocable.

`disable-model-invocation: true` hides a skill from BOTH the model's
auto-invocation set AND the user-facing slash-command set, so an advertised
command silently stops working. Any skill whose `/<name>` is advertised in
session-init.sh or help/SKILL.md must not carry that flag.
"""

from __future__ import annotations

import re
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
SKILLS_DIR = PLUGIN_ROOT / "skills"
SESSION_INIT = PLUGIN_ROOT / "hooks" / "session-init.sh"
HELP_SKILL = SKILLS_DIR / "help" / "SKILL.md"


# Matches a /<skill-name> slash command in plugin docs. The skill name
# must match a real directory under skills/ — that's how we filter out
# unrelated forward-slash strings (e.g. "/tmp", "/plan").
_SLASH_CMD_RE = re.compile(r"/([a-z][a-z0-9-]*)")


def _existing_skill_dirs() -> set[str]:
    return {p.name for p in SKILLS_DIR.iterdir() if p.is_dir()}


def _advertised_slash_commands() -> set[str]:
    """Skills referenced as `/<name>` in user-facing docs.

    Source surfaces:
      - hooks/session-init.sh — the SessionStart context block
      - skills/help/SKILL.md  — the on-demand skills list

    Anything advertised here must be invocable. Anything else (e.g.
    skills only invoked transitively by another skill) can carry the
    `disable-model-invocation` flag without breaking a user promise.
    """
    skill_dirs = _existing_skill_dirs()
    found: set[str] = set()
    for path in (SESSION_INIT, HELP_SKILL):
        text = path.read_text(encoding="utf-8")
        for m in _SLASH_CMD_RE.finditer(text):
            name = m.group(1)
            if name in skill_dirs:
                found.add(name)
    return found


def _has_disable_model_invocation(skill_md: Path) -> bool:
    """True iff the skill's YAML frontmatter sets the flag truthy.

    Frontmatter is the leading `---`-delimited block. We only inspect
    that block so a stray mention in the prose body (e.g. quoting the
    flag name in a comment) does not count as enabling it.
    """
    text = skill_md.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return False
    end = text.find("\n---", 3)
    if end == -1:
        return False
    frontmatter = text[3:end]
    for line in frontmatter.splitlines():
        m = re.match(r"\s*disable-model-invocation\s*:\s*(\S+)", line)
        if m and m.group(1).lower() in ("true", "yes", "on"):
            return True
    return False


def test_advertised_slash_commands_are_invocable():
    advertised = _advertised_slash_commands()
    assert advertised, (
        "Expected at least one advertised /<skill> command in "
        "session-init.sh or help/SKILL.md; got none. The regex or "
        "the doc surfaces probably drifted."
    )

    offenders: list[str] = []
    for name in sorted(advertised):
        skill_md = SKILLS_DIR / name / "SKILL.md"
        if not skill_md.exists():
            continue  # `_advertised_slash_commands` already filters,
                       # but be defensive against rename races.
        if _has_disable_model_invocation(skill_md):
            offenders.append(name)

    assert not offenders, (
        "Skills advertised as `/<name>` in session-init.sh and/or "
        "help/SKILL.md but suppressed by `disable-model-invocation: true`, so "
        "the advertised command doesn't work. Remove the flag or stop "
        "advertising the command. Offenders:\n"
        + "\n".join(f"  skills/{n}/SKILL.md" for n in offenders)
    )
