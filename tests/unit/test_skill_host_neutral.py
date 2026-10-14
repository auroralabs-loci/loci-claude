"""Every skill, both hosts: the trigger clause and host-neutral wording (AAD-7835).

AAD-7785 moved each skill's trigger clause into `description`, because GitHub
Copilot CLI shows the model a skill's `description` and never its
`when_to_use`. AAD-7786 made the skill prose host-neutral: "the question tool"
instead of `AskUserQuestion`, the plan-mode hand-over named per host, no
Claude-only command. Both were pinned for `init` and `loci-preflight` alone, so
a regression in any of the other ten skills shipped unseen — the Epic's
post-ship review found three (`Glob`/`Grep` in help and bug-report, setup's
`Platform`/`OS Version` context fields). These lints walk every skill.

What counts as host-neutral: a Claude-only name may appear only where its own
paragraph says, within a few lines of it, what GitHub Copilot CLI does — a
host-gated sentence, like the house rules' definition of the question tool. One
"Copilot" at the far end of a long table or block does not gate a name at the
other. Naming Claude Code as a host is fine; telling the model to use a tool or
a field only Claude Code has is not.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
SKILLS = PLUGIN_ROOT / "skills"
SKILL_FILES = sorted(SKILLS.glob("*/SKILL.md"))
PROSE = sorted(SKILLS.rglob("*.md"))

# Claude Code's tool names with no Copilot mapping in the house rules' tool
# paragraph, its plan-mode tools, its `/mcp` command, its session-context
# fields, "Claude" as the one to ask, `$ARGUMENTS` (Copilot substitutes none,
# AAD-7785; `$1` is left out, as it is everyday shell) and `!` sold as a
# terminal command (it is typed at the agent's prompt, on both hosts). `Glob`
# and `Grep` are capitalised, so the `grep` command is not one; a sentence that
# opens "Grep the log" should say "grep" anyway.
CLAUDE_ONLY = re.compile(
    r"\b(?:AskUserQuestion|ExitPlanMode|EnterPlanMode|TodoWrite|WebFetch|WebSearch"
    r"|NotebookEdit|MultiEdit|Glob|Grep)\b"
    r"|\b(?:Ask|ask|tell|Tell) Claude\b|restart Claude Code|`/mcp`"
    r"|`Platform`|`OS Version`"
    r"|`! [^`]+` in (?:your|a) terminal"
    r"|\$ARGUMENTS\b")

# How near the gate must be: the two gated names today sit 30 and 260 bytes
# from their "Copilot".
GATE_REACH = 400
PARAGRAPH = re.compile(r"(?:[^\n]|\n(?![ \t]*\n))+")

# The shapes a trigger clause takes in this corpus: "Use when …", "Use for …",
# "Use on …", or the auto-run skills' "MANDATORY …".
TRIGGER = re.compile(r"\bUse (?:when|for|on)\b|\bMANDATORY\b")


def _text(p: Path) -> str:
    return p.read_text(encoding="utf-8").replace("\r\n", "\n")


def _frontmatter_field(p: Path, name: str) -> str:
    text = _text(p)
    assert text.startswith("---\n"), f"{p}: no frontmatter"
    fm = text[4:text.index("\n---\n", 4)]
    m = re.search(rf"^{name}:[ \t]*(?:[>|][-+]?)?[ \t]*\n?((?:.*\S.*\n?)*?)(?=^\S|\Z)", fm, re.M)
    assert m, f"{p.parent.name}: frontmatter has no `{name}`"
    return re.sub(r"\s+", " ", m.group(1)).strip()


def test_every_skill_is_walked():
    assert len(SKILL_FILES) == 12, [p.parent.name for p in SKILL_FILES]


@pytest.mark.parametrize("skill", SKILL_FILES, ids=lambda p: p.parent.name)
def test_the_trigger_clause_is_in_the_description(skill: Path):
    """Copilot reads `description` only: a trigger left in `when_to_use` is one
    Copilot's model never sees."""
    desc = _frontmatter_field(skill, "description")
    assert TRIGGER.search(desc), f"{skill.parent.name}: `description` carries no trigger clause: {desc[:160]!r}"


def _bare(text: str) -> list[str]:
    """`line: match` for each Claude-only mention no nearby Copilot sentence gates."""
    bare = []
    for para in PARAGRAPH.finditer(text):
        body = para.group(0)
        for m in CLAUDE_ONLY.finditer(body):
            near = body[max(0, m.start() - GATE_REACH): m.end() + GATE_REACH]
            if "Copilot" not in near:
                bare.append(f"{text.count(chr(10), 0, para.start() + m.start()) + 1}: {m.group(0)!r}")
    return bare


@pytest.mark.parametrize("path", PROSE, ids=lambda p: str(p.relative_to(SKILLS)))
def test_claude_only_wording_is_host_gated(path: Path):
    bare = [f"{path.relative_to(PLUGIN_ROOT)}:{b}" for b in _bare(_text(path))]
    assert not bare, ("Claude-only wording with no GitHub Copilot CLI counterpart "
                      "beside it:\n  " + "\n  ".join(bare))


def test_the_gate_is_the_sentence_beside_it_not_the_paragraph():
    gated = "The hand-over is `ExitPlanMode` under Claude Code; under GitHub Copilot CLI it is `exit_plan_mode`."
    assert _bare(gated) == []
    far = "| a | `AskUserQuestion` |\n" + "| row | filler text for the table |\n" * 30 + "| z | GitHub Copilot CLI |"
    assert _bare(far) == ["1: 'AskUserQuestion'"]
    # A repeated paragraph is reported where each copy is.
    twice = "Use Glob here.\n\nOther.\n\nUse Glob here."
    assert _bare(twice) == ["1: 'Glob'", "5: 'Glob'"]


def test_the_lint_catches_what_the_review_found():
    """The three regressions the post-ship review found, as they read."""
    for found in ("Use only: Read, Bash, Glob, Grep, plus the lightweight,",
                  "do not `find`, Glob or `ls` the project for ELF files.",
                  "The session context (`Platform`, `OS Version`) tells you the OS.",
                  "→ Run `! loci login` in your terminal, then re-run /loci:help."):
        assert CLAUDE_ONLY.search(found), found
    # And what is fine: a host named, a gated sentence's counterpart, plain Bash.
    for fine in ("Claude Code locates Git Bash itself", "Type `! loci login` at the prompt",
                 "Use only: Read, Bash, file search, plus", "grep the log",
                 "awk '{print $1}'"):
        assert not CLAUDE_ONLY.search(fine), fine


def test_user_facing_hook_text_names_no_one_host():
    """A hook's `systemMessage` is relayed verbatim to the model under Copilot,
    so "Ask Claude …" reached Copilot users (session-init's uv banner, AAD-7835)."""
    hits = []
    for p in sorted((PLUGIN_ROOT / "hooks").glob("*.sh")) + sorted((PLUGIN_ROOT / "lib").glob("*.sh")):
        for i, line in enumerate(_text(p).splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if re.search(r"\b(?:Ask|ask|tell|Tell) Claude\b", line):
                hits.append(f"{p.relative_to(PLUGIN_ROOT)}:{i}")
    assert not hits, hits
