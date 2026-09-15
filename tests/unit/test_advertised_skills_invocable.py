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


# Matches a slash command in plugin docs, in either spelling: the bare
# `/<skill>` the older lines use and the namespaced `/loci:<skill>` a user
# actually types for a plugin skill. The name must match a real directory under
# skills/ — that's how we filter out unrelated forward-slash strings (e.g.
# "/tmp", "/plan").
#
# The namespaced alternative is not decoration: `/loci:init` matched only the
# leading `/loci` under the bare pattern, `loci` is not a skill directory, and
# the newest advertisement in the session block would have been the one command
# this lint never checked.
_SLASH_CMD_RE = re.compile(r"/(?:loci:)?([a-z][a-z0-9-]*)")


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


#: What `/loci:help`'s own on-demand list has to offer, beyond being invocable. The
#: session block and the help list are written by hand in two files, and the test
#: above only checks that whatever they DO advertise works — so a skill dropped
#: from one of them passes silently. `init` is the one that cannot afford that: it
#: is the first thing a user runs in a checkout and everything else rests on the
#: recipe it writes, so a `/loci:help` that never names it leaves the user with four
#: skills that answer `not_initialized` and no advertised way out.
_MUST_BE_ADVERTISED = ("init",)


def test_help_advertises_the_skills_a_user_cannot_start_without():
    """The Step 2 list is hard-coded prose, so nothing else keeps it complete."""
    text = HELP_SKILL.read_text(encoding="utf-8")
    listing = text.split("## Step 2: Show Available Skills", 1)
    assert len(listing) == 2, (
        "help/SKILL.md no longer has a `## Step 2: Show Available Skills` "
        "section — this lint has nothing to read")
    body = listing[1].split("## Step 3", 1)[0]
    missing = [n for n in _MUST_BE_ADVERTISED
               if f"/{n}" not in body and f"/loci:{n}" not in body]
    assert not missing, (
        "help's on-demand skill list does not offer: "
        + ", ".join(f"/loci:{n}" for n in missing))

    # Being IN the list is not enough while another section can tell the model to
    # drop it before rendering. Review defeated the first version of this test with
    # one sentence in help's Fast path — "when the session shows a `recipe:` line
    # the project is already recorded, so drop the `/loci:init` entry from the Step
    # 2 list before rendering it" — which left the entry in place and removed it
    # from the only thing the user sees. Narrow by design: this screens the shape
    # that mutation had to take, and cannot prove no such sentence exists. What
    # holds underneath is Step 2's own standing instruction, asserted next.
    # A closed verb list, and review dodged it with a verb that is not one:
    # "render the Step 2 list WITHOUT the `/loci:init` entry". So the screen is
    # keyed on the two shapes a subsetting instruction can take — an omission
    # verb, or a preposition of exclusion — and it is still not a proof. What
    # carries the guarantee is Step 2's standing instruction, asserted below, and
    # `test_project_detection_gate.py::test_the_available_line_advertises_init`,
    # which RENDERS the session block rather than reading its source.
    removal = re.compile(
        r"(?:\b(?:drop|omit|remove|skip|leave|hide|suppress|exclude|elide)\w*\b"
        r"|\bwithout\b|\bminus\b|\ball but\b)"
        r"[^.]{0,140}(?:/loci:init|\bfrom the (?:Step 2 )?(?:skill )?list\b"
        r"|\bthe (?:Step 2 )?(?:skill )?list\b)", re.I)
    m = removal.search(text)
    assert not m, (
        "help tells the model to remove an entry from the skill list before "
        f"rendering it, so the list a user sees is not the list this test read: "
        f"{m.group(0)!r}")
    assert re.search(r"Always show the full skill list", body), (
        "Step 2 lost the standing instruction that the list is shown in full "
        "regardless of environment state — the only thing that makes an entry in "
        "it a promise rather than a candidate")

    # …and session-init's `Available:` line has to agree, or `/loci:help` advertises a
    # command the session block never mentions (or the reverse).
    session = SESSION_INIT.read_text(encoding="utf-8")
    # `findall`, not `search`: the first match anywhere used to win, so a commented
    # -out `# Superseded: _AVAILABLE='… /loci:init …'` above a live line that had
    # dropped it satisfied this. Every definition has to offer the command.
    definitions = re.findall(r"^\s*#?\s*_AVAILABLE='([^']*)'", session, re.M)
    assert definitions, "session-init.sh no longer defines _AVAILABLE"
    absent = [n for n in _MUST_BE_ADVERTISED
              for d in definitions
              if f"/{n}" not in d and f"/loci:{n}" not in d]
    assert not absent, (
        "an `Available:` line in session-init.sh does not offer: "
        + ", ".join(f"/loci:{n}" for n in sorted(set(absent))))

    # Reading the DEFINITION is not reading the value. Review left the definition
    # intact and emitted `_ctx_line "${_AVAILABLE/, \/loci:init/}"` — the rendered
    # line then offered seven commands instead of eight, which is exactly the
    # failure `_MUST_BE_ADVERTISED` exists to prevent, with this test green.
    #
    # The load-bearing guard for the rendered value is
    # `test_project_detection_gate.py::test_the_available_line_advertises_init`,
    # which runs the hook and reads its real `additionalContext`. It is skipped on
    # a host without bash and jq, so this cheap structural check stands beside it:
    # the variable must reach `_ctx_line` unmodified.
    used = re.findall(r"_ctx_line\s+\"\$\{?_AVAILABLE([^\"]*)\}?\"", session)
    mangled = [u for u in used if u.strip("}")]
    assert not mangled, (
        f"`_AVAILABLE` is edited at the point of use ({mangled}), so the line the "
        f"model receives is not the line this test read. Change the definition, "
        f"not the expansion.")


#: Control characters that reach a COMPILED pattern. `\b` in a non-raw string is a
#: backspace, and a backspace in a regex matches a byte that occurs in no prose —
#: so the assertion using it can never fail. Two ways in, and only one of them
#: leaves a trace in the source:
#:
#:   * a shell heredoc eating the backslash while writing the file (round 1 shipped
#:     exactly this, inside `r"…"`, invisible to grep and to a file dump);
#:   * a missing `r` prefix, which leaves the source looking perfectly ordinary and
#:     which is the way this normally happens.
#:
#: The first version scanned source text and so could only ever catch the first.
#: This reads what Python compiles: `compile()` resolves every escape and
#: concatenates adjacent literals — the same welding that made the positional
#: exemption unsafe — and needs no import, so a module with side effects cannot
#: bite.
_CONTROL_BYTES = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

#: The compiled control characters in this package that are deliberate, by
#: (file, char). ENUMERATE, do not classify: review defeated a positional rule
#: with a lone 0x08 in its own string literal, which Python then welded onto the
#: head of the pattern beside it. One entry, read and understood —
#: `test_compile_read_back._RS` is an ASCII record separator the compile stub
#: delimits its logged arguments with.
_DELIBERATE_CONTROL_CHARS = {
    # Four stubs log the arguments they were called with, one record per
    # call, delimited by ASCII RS — chosen because a shell argument may
    # contain any printable character, including newlines and tabs.
    ("tests/unit/test_compile_read_back.py", "\x1e"),
    ("tests/unit/test_post_edit_hook.py", "\x1e"),
    ("tests/unit/test_pre_edit_hook.py", "\x1e"),
    ("tests/unit/test_turn_clean_hook.py", "\x1e"),
    # A swap sentinel: `--elf` → NUL → `--comparing-elf` exchanges two
    # flags in one pass without the second replacement eating the first.
    ("tests/unit/test_pair_gate_contract.py", "\x00"),
    # `_win32_dots` (hooks/contract-guard.sh) held `.` and `..` aside in these
    # two bytes for one round, and returned a path already carrying one
    # UNCHANGED — which switched the whole Win32 reading off for that path. The
    # escape is a dot now, and the test feeds these back in to pin that no byte
    # in a path changes what the function does to the rest of it. `\x7f` rides
    # with them as the control that was never a sentinel.
    ("tests/unit/test_contract_guard.py", "\x01"),
    ("tests/unit/test_contract_guard.py", "\x02"),
    ("tests/unit/test_contract_guard.py", "\x7f"),
}


def _compiled_strings(source: str, filename: str) -> list[str]:
    """Every string constant Python ends up with, nested code objects included."""
    seen: list[str] = []

    def walk(code) -> None:
        for const in code.co_consts:
            if isinstance(const, str):
                seen.append(const)
            elif hasattr(const, "co_consts"):
                walk(const)

    walk(compile(source, filename, "exec"))
    return seen


def test_no_test_file_carries_a_mangled_escape():
    """An assertion that cannot fail is worse than no assertion."""
    offenders = []
    checked = 0
    for path in sorted((PLUGIN_ROOT / "tests").rglob("*.py")):
        rel = path.relative_to(PLUGIN_ROOT).as_posix()
        checked += 1
        for const in _compiled_strings(path.read_text(encoding="utf-8"), rel):
            for m in _CONTROL_BYTES.finditer(const):
                if (rel, m.group(0)) in _DELIBERATE_CONTROL_CHARS:
                    continue
                offenders.append(
                    f"  {rel}: {m.group(0)!r} inside {const[:60]!r} — a `\\b` or "
                    f"`\\f` a heredoc ate, or a missing `r` prefix")
    assert checked > 5, (
        f"the scan read {checked} test files — it is not reading this package")
    assert not offenders, (
        "a control character reaches a compiled string. Inside a regex that makes "
        "the pattern unmatchable and the assertion vacuous:\n"
        + "\n".join(sorted(set(offenders))))


def test_the_mangled_escape_guard_sees_a_missing_r_prefix():
    """The source-scanning version could not, and that is the ordinary shape."""
    mangled = _compiled_strings('x = "\\b(?:drop)"', "<mangled>")
    assert any(_CONTROL_BYTES.search(s) for s in mangled), (
        "a missing `r` prefix no longer shows up in the compiled constants")
    clean = _compiled_strings('x = r"\\b(?:drop)"', "<clean>")
    assert not any(_CONTROL_BYTES.search(s) for s in clean), (
        "the guard now flags a correctly-raw pattern — the cry-wolf direction")
    # …and adjacent-literal concatenation, which is how review welded a lone
    # exempt-looking byte onto the head of a live pattern.
    welded = _compiled_strings('x = "\\b" r"(?:drop)"', "<welded>")
    assert any(_CONTROL_BYTES.search(s) and "drop" in s for s in welded), (
        "adjacent literals are not being seen as the one string Python makes")
