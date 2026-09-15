"""Lint: the post-overhaul recipe regime — what the slimmed prose must not regrow.

T10–T12 deleted ~50 KB of detection prose: the compiler cascade, the `command -v`
ladders, the cross-compilation defaults, the four copies of one preamble. Nothing
stops that coming back one PR at a time except a test that fails when it does —
this repo's own history is the evidence (`test_freshness_contract.py` exists
because hostile review defeated 12 of its first 19 assertions with the suite
green).

**Why a second file rather than more of `test_freshness_contract.py`.** That suite
pins one guarantee — *a stale artifact must not be measured* — and carries ~1 000
lines of `detect-project.sh` integration tests alongside it. These pins are about a
different guarantee: *the recipe is the only source of build facts, and the prose
that used to guess them stays deleted*. Two of them (the byte ceilings) are not
about prose content at all. The shared helpers are imported rather than copied, so
there is still one definition of `_section`, `_prose_files` and the nine codes.

Every screen here is written to the rules that suite paid for:

* section-scoped, with markers whose **presence AND uniqueness** are asserted
  (`_section` / `_subsection` do that) — a prefix-ambiguous heading silently widens
  a slice;
* no `[^\\n]` against whitespace-collapsed text;
* registries are **self-liquidating** — an entry that stops being needed fails, so
  an exemption cannot outlive its debt;
* every negative screen says out loud what it cannot prove.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.unit.test_freshness_contract import (
    CONTRACT,
    NINE_CODES,
    PATTERN_B_SKILLS, VERB_OWNED_SKILLS,
    PLUGIN_ROOT,
    SKILLS,
    _model_prose_files,
    _PROSE_SUFFIXES,
    _injected_prose,
    _prose_files,
    _raw,
    _rel,
    _section,
    _subsection,
    _text,
)

#: The heading that owns the coded errors. Spelled once, used by every assertion
#: below, so a rename fails in one place with a message that names it.
CODED_ERRORS_HEADING = "When a `loci` call refuses: the nine coded errors"


def _collapsed(p: Path) -> str:
    return re.sub(r"\s+", " ", p.read_text(encoding="utf-8"))


def _screened() -> list[tuple[str, str]]:
    """(label, whitespace-collapsed text) for every string a model can read.

    Shipped documents **and** the prose the hooks inject. The split between them
    was a hole rather than a division of labour — a T12 reviewer reached the model
    through `session-init.sh`'s context block, which no `.md` scan can see, and put
    a write instruction and two ladder rungs in front of every session.
    """
    out = [(_rel(p), _collapsed(p)) for p in _prose_files()]
    out += [(rel, re.sub(r"\s+", " ", text)) for rel, text in _injected_prose()]
    return out


def _windows(text: str, needle: str, before: int, after: int):
    """Every ``before``/``after`` character window around ``needle``.

    A window, not a line: `_text()` collapses newlines, so a `[^\\n]*` proximity
    pattern spans the whole file and stops being a proximity check at all. That
    defeated a real assertion in the freshness suite.
    """
    for m in re.finditer(re.escape(needle), text):
        yield text[max(0, m.start() - before):m.end() + after]


# ---------------------------------------------------------------------------
# 1. The nine coded errors keep exactly one recovery each
# ---------------------------------------------------------------------------
#
# `test_the_coded_error_table_carries_all_nine_and_says_the_set_is_closed` in the
# freshness suite pins the table itself: nine rows, a recovery in each, no tenth.
# What it does not see is a SECOND recovery — a row for the same code under another
# heading, or a paragraph that softens the first one. Those are what these add.

#: The bullets that follow the table because one line was not enough. Registered by
#: their bolded subject, so a fifth bullet has to be read by a human before it
#: ships. Self-liquidating: a registered subject that stops appearing fails too.
#:
#: Three of the first four are codes; the fourth is the rule that binds three of
#: them together, and it is the one that carries the −52.4 % ROM defect, so it is
#: not optional and not a "code with a second treatment".
#:
#: The fifth IS a "code with a second treatment", which the line above rules out —
#: so it earns its place explicitly. Every other shape of `compdb_absent` is
#: something that broke and is fixed by regenerating; the artifact-only recipe is
#: a statement of the recipe's scope whose recovery is emphatically NOT
#: `/loci:init`, because init wrote that recipe for the same reason the code is
#: raised. The old one-line advice was therefore a loop (AAD-7589), and one line
#: can carry the fix or the anti-loop, not both.
EXTENDED_TREATMENTS = {
    "`arch_mismatch` after a mid-session target switch.",
    "`compdb_entry_missing`",
    "`compdb_absent` on an artifact-only recipe",
    "`outside_target`",
    "Never regenerate a compile database mid-turn.",
    "Never rebuild or relink the recorded artifact mid-turn either",
}

#: The lead-in that must agree with the count. It is prose the model reads as a
#: promise about how much is coming; a fifth bullet under "Four need more than a
#: line" is a paragraph the model is told does not exist.
_LEAD_IN = re.compile(
    r"\b(one|two|three|four|five|six|seven|eight|nine)\s+need\s+more\s+than\s+a\s+line",
    re.I)
_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
          "six": 6, "seven": 7, "eight": 8, "nine": 9}


def test_no_coded_error_gets_a_second_table_row_anywhere_in_shipped_prose():
    """One code, one recovery — counted across every document, not one section.

    The freshness suite's scan is bounded to the coded-error section, which is
    right for "are all nine here" and blind to "is one of them answered twice".
    A second row for `recipe_stale` under, say, `## Troubleshooting` in a skill is
    a second recovery the model can take, and the softer one wins whenever it is
    nearer the point of failure.

    Keyed on a row whose FIRST cell is the backticked code and nothing else —
    which is the shape of a recovery table, and not the shape of the several
    legitimate places a code is named inside a sentence or listed in a cell
    beside other codes.
    """
    homes: dict[str, list[str]] = {code: [] for code in NINE_CODES}
    for rel, _ in [(_rel(p), None) for p in _prose_files()]:
        raw = (PLUGIN_ROOT / rel).read_text(encoding="utf-8")
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped.startswith("|"):
                continue
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if not cells:
                continue
            # Strip every markdown emphasis character, not just the backticks.
            # Review shipped a second, softer `recipe_stale` recovery table in
            # `loci-post-edit` by writing the cell as ``**`recipe_stale`**``:
            # `strip("`")` is a no-op when the ends are `*`, so the row was
            # never counted. Two characters, one whole extra recovery, suite
            # green — in the file this suite's own docstring calls the one that
            # "wins whenever the two disagree".
            #
            # EVERY cell, not cell 0: a recovery table reads just as well with
            # its columns the other way round, and review shipped exactly that —
            # `| Refresh the recipe, then re-run … | `recipe_stale` |` — which
            # the scan never looked at. And the strip is a regex now, because
            # `.strip("`*_ ")` stops at the first character outside its set, so
            # `` | `recipe_stale` (mid-turn) | `` was not read as a code either.
            for cell in cells:
                # The cell's LEADING token, after any emphasis. Not a global
                # substitution: `[`*_()]+ → " "` also eats the underscore inside
                # `recipe_stale`, which made every code stop matching and the
                # whole screen vacuous — caught by the codes' own rows going to
                # zero, which is the failure mode this test is built to show.
                lead = re.match(r"[\s`*_]*([a-z][a-z_]*)`?", cell)
                if lead and lead.group(1) in homes:
                    homes[lead.group(1)].append(rel)
                    break
    offenders = []
    for code, where in homes.items():
        if len(where) != 1:
            offenders.append(f"{code}: {len(where)} recovery rows, in {where}")
        elif where[0] != _rel(CONTRACT):
            offenders.append(f"{code}: its only recovery row is in {where[0]}, "
                             f"not in the shared contract")
    assert not offenders, (
        "the nine codes must have exactly one recovery row each, in the shared "
        "contract:\n  " + "\n  ".join(offenders))


def test_the_extended_treatments_are_registered_and_the_lead_in_counts_them():
    """A fifth bullet is a second recovery paragraph wearing the table's clothes.

    Two halves, because either alone is defeatable. The count fails when a bullet
    is added and the lead-in is not updated; the registry fails when the lead-in
    IS updated, because the new subject has to be written down here — which is a
    human reading it, and that is the whole mechanism.
    """
    section = _subsection(_raw(CONTRACT), CODED_ERRORS_HEADING)
    m = _LEAD_IN.search(section)
    assert m, ("the lead-in that counts the extended treatments is gone — "
               "without it a fifth bullet contradicts nothing")
    promised = _WORDS[m.group(1).lower()]

    # The bullets, from the RAW section: a top-level `- **subject**` after the
    # table. Read off the raw text because a collapsed slice cannot tell a
    # top-level bullet from a nested one, and a nested bullet is not a treatment.
    raw_section = _subsection_raw(CODED_ERRORS_HEADING)
    # EVERY CommonMark bullet marker, and any emphasis on the subject. Review
    # added a fifth extended treatment for `recipe_stale` — a full paragraph
    # telling the model to finish the measurement in the same turn — using a `*`
    # bullet instead of a `-`, and both the count and the registry missed it.
    # `+` is the third legal marker and `_italic_` the other emphasis.
    subjects = []
    for line in raw_section.splitlines():
        # ANY indent. Anchoring at column 0 was defeated by indenting the new
        # bullet two spaces: a nested `- **\`recipe_stale\` when only a comment
        # moved.**` is a second recovery paragraph that the count and the
        # registry both missed, and indentation is exactly where one fits.
        bullet = re.match(r"\s*[-*+]\s+(?:\*\*|__)(.+?)(?:\*\*|__)",
                          line.rstrip())
        if bullet:
            subjects.append(bullet.group(1))
    assert len(subjects) == promised, (
        f"the section promises {promised} treatments needing more than a line "
        f"and carries {len(subjects)}: {subjects}")
    assert set(subjects) == EXTENDED_TREATMENTS, (
        f"unregistered extended treatment(s) "
        f"{sorted(set(subjects) - EXTENDED_TREATMENTS)}; "
        f"registered but gone: {sorted(EXTENDED_TREATMENTS - set(subjects))}. "
        f"Each of these is a second, longer recovery for a code that already has "
        f"a table row — add it here only after reading whether it softens the row.")


def _subsection_raw(heading: str) -> str:
    """`_subsection` without the whitespace collapse, for line-shaped reads."""
    raw = _raw(CONTRACT)
    m = re.search(r"^#{1,6} " + re.escape(heading) + r"[ \t]*$", raw, re.M)
    assert m, f"no heading is exactly {heading!r}"
    tail = raw[m.end():]
    nxt = re.search(r"^#{1,6} ", tail, re.M)
    return tail[:nxt.start()] if nxt else tail


#: The shapes a recovery takes when it is softened into a suggestion.
#:
#: ⚠ Read this as honestly as `_FORBIDDEN_PATTERNS` asks to be read: **a phrase
#: list catches instances, not the class.** A permissive sentence written with none
#: of these shapes — "the numbers are usually fine when only a header moved" — is
#: invisible here, and no prose lint can prove such a sentence is absent. What is
#: durable is on the CLI side: a build verb under a recipe *refuses*, and there is
#: no flag that makes it not refuse, so prose granting permission grants permission
#: to call something that will not answer. This screen raises the cost of writing
#: that prose; it does not make it impossible.
_SOFTENING_PATTERNS = (
    r"proceed\s+(?:anyway|with\s+a\s+warning)",
    r"(?:warn|note\s+it)\s+and\s+(?:continue|carry\s+on|go\s+on)",
    r"continue\s+with\s+(?:a\s+)?(?:warning|caveat)",
    r"non-?blocking",
    r"treat\s+(?:it|this|them)\s+as\s+(?:a\s+)?(?:warning|advisory|informational|hint)",
    r"is\s+not\s+fatal",
    r"does\s+not\s+(?:stop|block)\s+(?:you|the\s+measurement|the\s+report)",
    r"safe\s+to\s+ignore",
    r"best[-\s]effort",
    r"degrade\s+gracefully",
    r"soft\s+(?:failure|error|refusal)",
    r"you\s+may\s+still\s+(?:measure|compile|build)",
    r"measure\s+(?:it\s+)?anyway",
    r"only\s+(?:a\s+)?(?:warning|advisory)",
)


def test_no_coded_error_is_softened_anywhere():
    """A code whose recovery is "or you could just carry on" has no recovery.

    Scoped to a window around each code name, across every shipped document and
    every string the hooks inject. Measured before it shipped: on the tree this
    test was written against it matches NOTHING, so it costs no exemptions.

    **Partial in TWO ways, and the second is easy to miss.** The phrase list is
    partial by construction (see `_SOFTENING_PATTERNS`). The ±240-character
    window is a second limit nobody wrote down: a permissive paragraph 250
    characters from the nearest code name is invisible here however it is worded.
    The window is not widened to the section, because at section scope the screen
    would report a code named in one paragraph and a legitimate "may report" in
    another as an offence, and a screen that cries wolf gets deleted. The
    load-bearing guarantee is the CLI's refusal, not this screen.
    """
    offenders = []
    for rel, text in _screened():
        low = text.lower()
        for code in NINE_CODES:
            for window in _windows(low, code, 240, 240):
                for pat in _SOFTENING_PATTERNS:
                    m = re.search(pat, window)
                    if m:
                        offenders.append(
                            f"{rel}: {code} softened by {m.group(0)!r}")
    assert not offenders, (
        "a coded refusal is being turned into a suggestion:\n  "
        + "\n  ".join(sorted(set(offenders))))


def test_recipe_stale_refuses_measurement_in_both_places_that_govern_it():
    """`recipe_stale` means the recorded flags may not be this project's.

    Measuring anyway is the −52.4 % ROM defect with a different first step, so
    the two places that decide what happens on it — the table row and the
    mid-turn rule — must both end in a stop. Pinned structurally (a refusal verb
    in each) rather than by wording, so a rewrite that keeps the guarantee passes.

    **This is a shape check, not a proof.** It cannot show that no fourth
    document somewhere says "may proceed with a warning"; the screen above hunts
    those shapes, and the CLI's own refusal is what actually holds.
    """
    section = _subsection(_raw(CONTRACT), CODED_ERRORS_HEADING)
    rows = [w for w in _windows(section, "`recipe_stale` |", 0, 400)]
    assert rows, "the coded-error table has no `recipe_stale` row"
    row = rows[0]
    assert "Neither mid-turn" in row, (
        "`recipe_stale`'s row no longer says the repair is not a mid-turn one — "
        "a regeneration between the snapshot and the post-edit compile is the "
        "recorded -52.4% defect")
    assert "loci init --refresh" in row, (
        "`recipe_stale`'s row names no repair, so the model improvises one")

    # …and the rule that three codes share.
    midturn = _section(section, "Never regenerate a compile database mid-turn.",
                       "**Any other `error.code`")
    for code in ("recipe_stale", "compdb_absent", "compdb_entry_missing"):
        assert code in midturn, (
            f"{code} dropped out of the mid-turn rule, so nothing tells the "
            f"model to stop when it arrives during a change measurement")
    assert "report the code and its recovery and stop" in midturn, (
        "the mid-turn rule no longer ends in a stop")

    # The skill that meets these codes at the point of failure says the same —
    # and SECTION-SCOPED, because a whole-file `in` check is what `_section`
    # exists to replace. Review rewrote the governing sentence in Step 1 to
    # "finish the measurement and its report in this same turn" and parked the
    # pinned phrase in a `## Changelog` code fence at the bottom of the file;
    # this assertion never fired.
    step1 = _section(_text(SKILLS / "loci-post-edit" / "SKILL.md"),
                     "## Step 1: `loci analyse prepare` — compile, and get the statement of work",
                     "## Step 2: read the manifest")
    for code in ("recipe_stale", "compdb_absent", "compdb_entry_missing"):
        assert code in step1, (
            f"{code} no longer arrives anywhere in loci-post-edit's Step 1, "
            f"which is where a mid-turn refusal reaches the model")
    assert "report the code and its recovery, and stop" in step1, (
        "loci-post-edit's Step 1 no longer routes a mid-turn `recipe_stale` to a "
        "stop; the skill's own instruction is nearer the failure than the "
        "contract's and wins whenever the two disagree")


# ---------------------------------------------------------------------------
# 2. The deleted detection prose stays deleted
# ---------------------------------------------------------------------------

#: A compiler DRIVER named beside a compile flag — which is what a preference list
#: and a set of cross-compilation defaults both look like, and what neither the
#: skills nor the shared contract need any more: `loci build compile` replays the
#: recipe. Anchored on the flag so that format names (`gcc-ld`, `iar`, `armlink` in
#: memory-report's map-parser table) are not reported as compiler lines — that is
#: the cry-wolf direction this repo has deleted screens over.
_COMPILER_LINE = re.compile(
    r"\b(?:[a-z0-9_]+-)*(?:gcc|g\+\+|clang(?:\+\+)?|cc|armclang|iccarm|armcc)\b"
    r"(?=[^\n]{0,60}?(?:\s-[cogOmW]|\s--target|\s-std=|\s-march|\s-mcpu))", re.I)

#: The ISA knobs the cascade used to default. `loci build compile` takes them from
#: the recipe; prose that names them is prose someone can paste.
#:
#: The first version enumerated six spellings, and review put a
#: "Default flags for a bare-metal Cortex-M4F target" table back into `preflight`
#: using four it did not have (`-mfpu=`, `--specs=`, `-ffreestanding`, `-g3`).
#: Enumerating machine flags is a losing game, so this matches the FAMILY —
#: `-m<anything>=` and `-mno-…` are machine options by construction — plus the
#: freestanding/linking pair that carries the rest of a bare-metal default set.
_ISA_DEFAULTS = re.compile(
    r"-m[a-z][a-z0-9-]*=|-mthumb\b|-mno-[a-z-]+|-mlittle-endian\b|-mbig-endian\b"
    r"|--specs=|-ffreestanding\b|-nostdlib\b"
    r"|--target=(?:armv|thumb|riscv|aarch64)")

#: A compiler DRIVER name, as a name rather than as an invocation.
#:
#: Deliberately narrow at the boundaries: `gcc-ld` and `armlink` are *map-file
#: format* names in `memory-report`'s parser table, and reporting those is the
#: cry-wolf direction. `(?<![\w.-])`/`(?![\w-])` keep them out.
_DRIVER_NAME = re.compile(
    r"(?<![\w.-])(?:(?:[a-z0-9_]+-)+(?:gcc|g\+\+|clang\+\+?)"
    r"|gcc|g\+\+|clang\+\+?|armclang|iccarm|armcc)(?![\w-])", re.I)

#: A list item that BEGINS with a driver name — the shape a preference list
#: cannot avoid. `_COMPILER_LINE` needs a flag within 60 characters, so review's
#: four-rung list (``1. `arm-none-eabi-gcc` `` … ``4. `cc` ``) carried no flags
#: and matched nothing.
#:
#: "Begins with", not "is only": anchoring on `\s*$` was defeated by one sentence
#: of rationale per rung — *"1. `arm-none-eabi-gcc` — the recorded cross
#: toolchain for this target. 2. `armclang` — …"* — which is how anyone would
#: actually write the list.
_DRIVER_ONLY_ITEM = re.compile(
    r"(?m)^\s*(?:[-*+]|\d+[.)])\s+`?(?P<d>[A-Za-z0-9_+.-]+)`?(?:$|[\s`,.:;—-])")

#: Compiler lines that survive in shipped prose, by file and matched text.
#: Self-liquidating in both directions: an unregistered match fails, and a
#: registered one that disappears fails so the entry goes with it.
COMPILER_LINE_DEBT: dict[str, set[str]] = {
    # A provenance note on a worked example — "each measured against
    # `arm-none-eabi-gcc` 15.2 (Cortex-M4, `-O1 -g`)" — which says where a number
    # in the table came from. It instructs nothing and offers no alternative.
    "skills/_shared/loci-runtime-contract.md": {"arm-none-eabi-gcc"},
}

#: Empty on purpose. Not one ISA default survives in shipped prose, and the empty
#: dict is the claim: a single `-mcpu=cortex-m4` anywhere under `skills/` fails
#: here, quoted, until someone writes down why it is not a default being restored.
ISA_DEFAULT_DEBT: dict[str, set[str]] = {}


def _skill_prose():
    """Every prose file under `skills/` — SKILL files, the shared docs, and the
    `init` sub-documents.

    NOT an include list of the ten skills: T12's reviewer put seven ladder
    phrases into a file an include list did not name, and the lesson recorded
    there is that a new document must be screened by default. And not a
    directory EXCLUSION either — round 2 put the whole deleted regime into
    `skills/loci-post-edit/evals/build-detection.md`, which an `evals/`
    directory rule skipped whole. Eval fixtures are `.json`; the suffix is what
    excludes them.

    And **not `*.md`**, which was the same mistake one level down. Review created
    `skills/_shared/build-detection-notes.txt` carrying the compiler ladder, a
    cross-compilation defaults table, a nine-step cascade, a write instruction
    and two permissive sentences, linked it from `loci-post-edit`, and every
    screen in this file stayed green; renaming it `.md`, byte for byte, went red
    in five places. `_prose_files` in the freshness suite carries the shared
    suffix set and the assertion that nothing under `skills/` is skipped for its
    extension.
    """
    out = _model_prose_files()
    assert out, "no model prose found — every screen below would pass vacuously"
    return out


@pytest.mark.parametrize("rx,debt,what", [
    (_COMPILER_LINE, COMPILER_LINE_DEBT, "a compiler driver beside a compile flag"),
    (_ISA_DEFAULTS, ISA_DEFAULT_DEBT, "an ISA default flag"),
])
def test_no_skill_prose_carries_a_compiler_line_or_an_isa_default(rx, debt, what):
    """The cascade's two products, screened where they would come back.

    A "compiler preference list" and a "cross-compilation defaults" table are the
    same thing under two headings, and the deleted-heading check in the freshness
    suite cannot see either when they return inside a KEPT section — which is
    exactly how `F_ladder_returns_under_a_new_name` defeated that check. This one
    reads the text, not the headings.
    """
    unregistered, stale = [], []
    for p in _skill_prose():
        rel = _rel(p)
        found = {m.group(0) for m in rx.finditer(p.read_text(encoding="utf-8"))}
        allowed = debt.get(rel, set())
        for extra in sorted(found - allowed):
            unregistered.append(f"{rel}: {extra!r}")
        for gone in sorted(allowed - found):
            stale.append(f"{rel}: {gone!r} is registered and no longer present")
    assert not unregistered, (
        f"{what} in shipped skill prose:\n  " + "\n  ".join(unregistered)
        + "\n(register it above with the reason, or delete it — the recipe "
          "records the compiler and its flags)")
    assert not stale, (
        "the debt list outlived the debt — an exemption nobody needs is a hole "
        "nobody can see:\n  " + "\n  ".join(stale))


def test_no_skill_prose_enumerates_compiler_drivers_to_choose_between():
    """A "preference list" is a cascade with the word filed off.

    `_COMPILER_LINE` needs a driver beside a compile FLAG, and review walked past
    it with a flagless four-rung list — *"work down this list and use the first
    one present on this machine: 1. `arm-none-eabi-gcc` 2. `armclang` 3. `clang`
    4. `cc`"* — under a heading that never says "cascade". The recipe records one
    compiler; a second name offered as an alternative is the ladder, whatever it
    is called.

    Two shapes, because a list can be vertical or inline:
    * two or more list items / table cells that are *only* a driver name;
    * three or more DISTINCT drivers inside 200 characters.

    Measured before shipping: zero of either across `skills/`. The near misses
    are registered by being under the thresholds rather than by exemption —
    `memory-report` names `GCC` twice as a map-file format, and the contract
    names `arm-none-eabi-gcc` and `gcc` in one worked example about the same
    toolchain.
    """
    offenders = []
    for p in _skill_prose():
        raw = p.read_text(encoding="utf-8")
        # ANYWHERE in the item, not only at its start. Review put one word in
        # front of each rung — "1. Prefer `arm-none-eabi-gcc` — …", "2. Then
        # `armclang` — …" — and the leading-token capture read `Prefer` and
        # `Then`, which are not drivers, so the list vanished.
        listed = set()
        for line in raw.splitlines():
            if not re.match(r"\s*(?:[-*+]|\d+[.)])\s", line):
                continue
            listed |= {m.group(0).lower() for m in _DRIVER_NAME.finditer(line)}
        if len(listed) >= 2:
            offenders.append(f"{_rel(p)}: a list of drivers to choose between "
                             f"— {sorted(listed)}")
        # …and per FILE, not per section and not per 200-character window.
        # Both narrower scopes were walked around: 220-character gaps beat the
        # window, and splitting the four rungs across two sub-headings
        # ("Choosing the driver: the cross toolchains" / "… the host fallbacks")
        # beat the section. A document that names three different compiler
        # drivers is enumerating them wherever the headings fall.
        #
        # The registered debt is subtracted first, so the contract's one worked
        # example (`arm-none-eabi-gcc` and a prose "gcc" about the same
        # toolchain) and `memory-report`'s map-format names do not trip it.
        flat = re.sub(r"\s+", " ", raw)
        distinct = {m.group(0).lower() for m in _DRIVER_NAME.finditer(flat)}
        distinct -= {d.lower() for d in COMPILER_LINE_DEBT.get(_rel(p), set())}
        if len(distinct) >= 3:
            offenders.append(f"{_rel(p)}: {sorted(distinct)} named as "
                             f"alternatives in one document")
    assert not offenders, (
        "compiler drivers are being enumerated as alternatives:\n  "
        + "\n  ".join(offenders)
        + "\n(the recipe records the compiler; a missing one is `compiler_missing`)")


#: `command -v` is legitimate all over this plugin: it is how the skills probe for
#: `loci`, `jq` and `uv`. What was deleted is `command -v` aimed at a COMPILER, so
#: that is what is screened — the probe verb plus a compiler token, close enough
#: together to be one command.
_COMPILER_HUNT = re.compile(
    r"(?:command\s+-v|type\s+-p|\bwhich\b)\s+[^\s`]{0,30}?"
    r"(?:gcc|g\+\+|clang|\bcc\b|arm-none-eabi|aarch64|riscv|tricore|armclang|iccarm)",
    re.I)


def test_no_probe_verb_hunts_a_compiler():
    """`command -v arm-none-eabi-gcc` is the ladder's first rung.

    `_LADDER_PATTERNS` screens that exact string; this screens the shape, because
    the rung is one substitution away from it (`type -p`, `which`, a different
    triple) and a T12 reviewer defeated seven phrase patterns by exactly that
    kind of one-word miss.

    Screens shipped documents and injected prose, not the shell that legitimately
    runs the probe: `lib/detect-project.sh` executes `command -v arm-none-eabi-gcc`
    because detecting is its job, and reporting a detector for detecting is how a
    screen gets deleted.
    """
    offenders = []
    for rel, text in _screened():
        for m in _COMPILER_HUNT.finditer(text):
            offenders.append(f"{rel}: {m.group(0)!r}")
    assert not offenders, (
        "prose telling the model to go looking for a compiler:\n  "
        + "\n  ".join(offenders)
        + "\n(the recipe records the compiler; a missing one is `compiler_missing`)")


#: The mentions of the old flag cascade that survive, by file, with their count.
#: Every one is a PROVENANCE sentence — a report built on a CLI that predates
#: `--require-recipe` has to say its flags came from the cascade, which the shared
#: contract and `lib/compile-and-read-back.sh`'s own `NOTE` both require. A count
#: rather than a phrase, because the thing being prevented is not a wording: it is
#: the nine-step TABLE coming back, and a table is many mentions where a sentence
#: is one. Raising a number here means reading what the new mention says.
CASCADE_PROVENANCE = {
    "skills/_shared/loci-runtime-contract.md": 1,   # T14: the `--require-recipe` paragraph names it once, as gone,
    "skills/loci-post-edit/SKILL.md": 1,
}


def test_the_cascade_is_never_a_procedure_again():
    """The 9-step cascade table was a separately-maintained copy of the CLI's,
    and it had already drifted when T11 deleted it.

    Two halves. **Structural:** no heading, table row or numbered step in shipped
    prose mentions the cascade — those are the three shapes a procedure takes, and
    a procedure is something the model executes. **Counted:** the surviving
    sentences are registered per file, so a fourth one in preflight fails until
    someone has read it.
    """
    procedural, counts = [], {}
    for p in _prose_files():
        rel = _rel(p)
        raw = p.read_text(encoding="utf-8")
        # OCCURRENCES, not matching lines. A count of lines flips on a pure
        # reflow that moves the word across a wrap, which is the cry-wolf
        # direction; the number of times the word is written does not.
        n = len(re.findall(r"cascad", raw, re.I))
        for i, line in enumerate(raw.splitlines(), 1):
            if not re.search(r"cascad", line, re.I):
                continue
            if re.match(r"\s*(?:#{1,6}\s|\||[-*+]?\s*\d+[.)]\s|[-*+]\s+\*\*)", line):
                procedural.append(f"{rel}:{i}: {line.strip()[:100]}")
        if n:
            counts[rel] = n
    assert not procedural, (
        "the cascade is a heading, a table row or a numbered step again — which "
        "is a procedure the model can follow, not a note about where old flags "
        "came from:\n  " + "\n  ".join(procedural))
    assert counts == CASCADE_PROVENANCE, (
        f"cascade mentions moved: found {counts}, registered {CASCADE_PROVENANCE}. "
        f"Every registered one is a provenance sentence a pre-recipe CLI's report "
        f"must carry; a new one has to be read before it is written down here.")


#: Naming a Claude tool beside one of the recipe's files. `hooks/contract-guard.sh`
#: DENIES those writes, so prose instructing one sends the model into a refusal it
#: will then have to explain — and the sanctioned path (`loci init set`) exists.
_WRITE_TOOL = re.compile(
    r"\b(?:Write|Edit)\s+tool\b"
    r"|\b(?:use|using|via|with)\s+(?:the\s+)?(?:Write|Edit)\b"
    r"|\bWrite\(|\bEdit\(", re.I)
_RECIPE_NOUN = re.compile(
    r"\bbuild\.yaml\b|\bcontract\.yaml\b|\bflags\.json\b|\bthe\s+recipe\b"
    r"|\bcontract\s+envelope\b", re.I)


def test_no_prose_hands_a_recipe_file_to_the_write_tool():
    """Closes the exact evasion P78 records, which nothing else catches.

    T11's guarded-file screen is section-scoped and keyed on a `.loci` path: a
    reviewer wrote *"open the recipe with the Write tool…"* inside a section that
    pairs `.loci` with no basename, and it stayed green. This is keyed on the
    TOOL instead — the one word such an instruction cannot avoid — and on the
    recipe as a NOUN, so naming the file in words does not evade it.

    Measured before it shipped: zero matches across every shipped document and
    every injected string, so it costs no exemptions. It does not close the class
    — an instruction phrased without naming a tool ("just put the flags in
    `.loci/build.yaml`") is invisible here and is what the path-keyed screen is
    for. The durable guarantee is the hook: it denies the Edit or Write outright,
    and the recipe's integrity record answers `recipe_tampered` on the next
    compile.
    """
    offenders = []
    for rel, text in _screened():
        for sentence in re.split(r"(?<=[.!?])\s+|\|", text):
            if _RECIPE_NOUN.search(sentence) and _WRITE_TOOL.search(sentence):
                offenders.append(f"{rel}: {sentence.strip()[:180]}")
    assert not offenders, (
        "prose pointing a write tool at one of the recipe's files — which the "
        "contract guard denies:\n  " + "\n  ".join(offenders)
        + "\n(the sanctioned write is `loci init set <key>=<value>`)")


# ---------------------------------------------------------------------------
# 3. One preamble, four pointers
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 4. The provenance obligations cover every skill that incurs them
# ---------------------------------------------------------------------------

#: Files that NAME Pattern B without descending from it, with the reason. The
#: provenance parametrizations in the freshness suite run over `PATTERN_B_SKILLS`;
#: a fifth skill that starts selecting an artifact would inherit B4's obligation
#: and none of its tests, silently, because the tuple is hand-written.
PATTERN_B_REFERENCES = {
    # Reads the `Artifact:` line as diagnostic evidence — "which of the two it
    # was; if that line is [absent]" — and emits no report of its own.
    "skills/bug-report/SKILL.md": "reads the provenance line, never produces one",
    # Emits the `Artifact:` line from `loci analyse prepare`'s `provenance[]`; the
    # artifact ladder is the verb's (`--elf`), so B1-B4 prose no longer governs it.
    "skills/exec-trace/SKILL.md": "renders provenance from prepare's envelope",
}


def test_every_skill_that_names_pattern_b_is_covered_or_registered():
    """The coverage set is hand-written, so it has to be checked against reality.

    Fails in both directions: a new skill naming Pattern B is unregistered until
    someone decides whether it selects an artifact (add it to `PATTERN_B_SKILLS`,
    and it inherits the `Artifact:` and `Recipe:` pins) or merely refers to one
    (register it here). A registered file that stops naming Pattern B fails too,
    so the exemption cannot outlive its reason.
    """
    # Two signals, not one. Keying on the phrase "Pattern B" alone is a wording
    # away from useless: a new skill that selects an existing binary and reports
    # numbers without saying "Pattern B" inherits B4's obligation and none of its
    # tests, silently — the failure the registry exists to prevent. So a skill
    # that emits an `Artifact:` provenance line counts too, and that is the thing
    # the obligation actually IS.
    named = {_rel(p) for p in SKILLS.glob("*/SKILL.md")
             if re.search(r"Pattern B|`?Artifact:`?", p.read_text(encoding="utf-8"))}
    # `VERB_OWNED_SKILLS` are covered too: they emit the `Artifact:` line (B4, the
    # durable half) and their provenance pins are the envelope-sourced ones in
    # test_freshness_contract.py, not the context-file ones.
    covered = {f"skills/{n}/SKILL.md" for n in (*PATTERN_B_SKILLS, *VERB_OWNED_SKILLS)}
    unregistered = sorted(named - covered - set(PATTERN_B_REFERENCES))
    assert not unregistered, (
        f"{unregistered} name Pattern B but are neither in PATTERN_B_SKILLS (which "
        f"is what makes the `Artifact:` and `Recipe:` provenance tests run over a "
        f"skill) nor registered here as a reference. A skill that selects an "
        f"existing binary and is in neither set reports numbers with no pinned "
        f"provenance line at all.")
    stale = sorted(rel for rel in PATTERN_B_REFERENCES if rel not in named)
    assert not stale, (
        f"{stale} are registered as Pattern-B references and no longer mention "
        f"Pattern B or an `Artifact:` line — drop the entries so the screen "
        f"covers them again")
    assert covered <= named, (
        f"{sorted(covered - named)} are covered as Pattern-B skills but no longer "
        f"name Pattern B — either the selection rules left the skill (and the "
        f"provenance pins are now vacuous there) or the reference was renamed")


def test_no_skill_document_hides_a_rule_in_an_html_comment():
    """A pinned phrase parked in a comment reads as present to every test here
    and as absent to the model.

    `test_html_comments_cannot_hide_a_gutted_rule` bans them in eight files. Every
    positive assertion in THIS file — the preamble body, the extended treatments,
    the refusal verbs — can be satisfied out of a comment in any `.md` under
    `skills/`, so the ban is widened to all of them. Measured: zero today.

    Not widened past `skills/`: `authorization-flow/AuthorizationFlow.md` carries
    two `<!-- TODO: insert screenshot -->` lines that pin nothing and hide nothing,
    and a screen that reports those is one PR from deletion.
    """
    offenders = [_rel(p) for p in _skill_prose()
                 if "<!--" in p.read_text(encoding="utf-8")]
    assert not offenders, (
        f"HTML comments in {offenders} — delete them or make them live prose")


# ---------------------------------------------------------------------------
# 5. Byte ceilings — a regrowth alarm
# ---------------------------------------------------------------------------
#
# Set at ~110 % of the sizes measured at the end of T12, **not** at the report's
# estimates: P75 and P82 record why. The estimates (post-edit ≤ ~50 KB, preflight
# ≤ ~38 KB, the seven other skills −10 KB) were made against a model of these files
# that did not survive contact — post-edit's entire detection surface is ~9.3 KB,
# so deleting all of it including the compile invocation still leaves ~54 KB. A
# ceiling derived from those numbers would be red on arrival and would be raised
# within a day, which is how a budget stops meaning anything.
#
# LF-normalized on purpose. The working tree is CRLF on Windows and LF in WSL, so
# a raw byte count is ~1 000 B larger here than there — a ceiling that reads
# differently on two platforms is P68's defect in a new place.

#: EVERY model-facing prose file, with its own ceiling at ~110 % of the size
#: measured at the end of T13 (the measurement is in the comment beside it).
#:
#: **Every file, not three.** Review grew `exec-trace` from 24,450 B to 81,780 B
#: — the second-largest document in the plugin — and every ceiling stayed green,
#: because only three files had one. A ceiling on the three biggest bounds the
#: three biggest; what the model reads is all of them.
HOT_FILE_CEILINGS = {
    # Downloaded on its own, so it repeats what README says rather than linking
    # it; the ceiling is what keeps the repetition from growing into a second
    # README.
    "agents.txt": 6_900,                                    # 6,273 B
    # Added `57f6f9c` with no ceiling, which is the hole this table exists to
    # close; ~110 % of the size it arrived at, like every other new file here.
    "docs/turn-intent-note.md": 4_730,                      # 4,296 B
    "docs/turn-scoped-baseline.md": 3_700,                  # 3,354 B
    # Ceiling set after PR #259, when the file was 6,263 B. It reached
    # 7,132 B and was trimmed back under, rather than re-budgeted.
    "skills/_shared/contract-rationale.md": 6_900,          # 6,800 B
    # +1,400 B for AAD-7595's branch: the no-contract `STATUS` rule landing in
    # every judging skill. Taken on a promise — the trim back under is deferred,
    # not waived, so raise nothing further here without cutting first.
    "skills/_shared/loci-runtime-contract.md": 96_100,       # 86,087 B
    # Deliberately tighter than the ~110 % this table otherwise uses. `cfd4deb`
    # grew this file 3.6x, to 10,195 B, past a 3,200 B ceiling set when it was
    # 2,824 B; six skills load it on every invocation, so its size is paid on
    # every LOCI verdict. The rationale essay went to a pointer and the Measured
    # / Reasoned overlap was tightened, taking it to 7,849 B — and the ceiling is
    # tight rather than ~110 % because that headroom is what this file has earned,
    # not because a number is where it landed.
    # +450 B for 049: a third reasoned verdict (`no_opinion`), the words the panel
    # renders, and the rule that an agent's word can raise the run's verdict. Taken
    # rather than trimmed elsewhere — the file's job is to be the one place these
    # words are defined, and the escalation changes what every skill's verdict does.
    # +2,550 B for 051: the record call and the note rule, which five skills carried
    # five partial copies of. It is a MOVE, not growth — post-edit and preflight give
    # up more than this between them — and it is the point of the change: the one copy
    # that survives is the one every judging skill reads.
    # +157 B for 057: a flag does not raise a run the gate measured nothing on, which
    # is the one branch of the escalation rule the file did not state.
    # +860 B for 059, taken deliberately: `--agent-verdict`, the route for a reasoned
    # word on a run whose every entry computed and passed. Without it that word had no
    # landing place and the cockpit rendered the gate's `pass` beside a session that
    # said CAUTION — a correctness fix, and it needs the bytes in this file because the
    # record call is stated once and every judging skill reads it here.
    # +2,550 B for the verdict-composition change (ADR 04 D4/D8, ADR 08). This file
    # became the single home for the composition matrix, the two verdict columns, the
    # run's worst-of and the five-column table spec — content the judging skills used
    # to state five times each, and the one place a reader can find out what a word
    # rests on. `## What you may reason from` went the other way, to the runtime
    # contract's agent-verdicts section, which already said it. The raises below are
    # ~102 % of the size each file arrived at rather than the table's usual ~110 %:
    # this is room for prose the change added, not headroom the files have earned.
    # +500 B on 2026-09-11: an exec-trace run with no envelope printed its conclusion
    # as a stacked `ENTRY: … / FUNCTION: …` field list, because every skill said where
    # rows come from on a `project` envelope and none said the table is drawn either
    # way. The rule is stated once, here.
    # +3,400 B for AAD-7595's branch: the no-contract `STATUS` rule landing in
    # every judging skill. Taken on a promise — the trim back under is deferred,
    # not waived, so raise nothing further here without cutting first.
    "skills/_shared/verdicts.md": 18_900,                   # 15,362 B
    "skills/bug-report/SKILL.md": 35_500,                    # 32,187 B
    # +400 B for AAD-7595's branch: the no-contract `STATUS` rule landing in
    # every judging skill. Taken on a promise — the trim back under is deferred,
    # not waived, so raise nothing further here without cutting first.
    "skills/contract/SKILL.md": 13_200,                      # 11,550 B
    # +4,200 B on 2026-09-11: the skill stopped being a renderer. It now judges the
    # two structural signals the graph itself determines (recursion cycles, indirect
    # calls), so it carries a conclusion table, a row catalogue, a footer and the
    # envelope reads every other judging skill has. ~102 % of the size it arrived at.
    # +1,300 B on 2026-09-14: the table spec was a bare header with no example row,
    # so the file described a table it never showed one of — the same gap that made
    # `verdicts.md` state the drawn-either-way rule above. The template is fenced
    # (it read as documentation, not as output to reproduce) and carries a worked
    # example. ~102 % of the size it arrived at.
    # +500 B for AAD-7595's branch: the no-contract `STATUS` rule landing in
    # every judging skill. Taken on a promise — the trim back under is deferred,
    # not waived, so raise nothing further here without cutting first.
    "skills/control-flow/SKILL.md": 21_700,                  # 20,840 B
    "skills/exec-trace/SKILL.md": 26_900,                    # 24,450 B
    # +400 B on 2026-09-11: the verdict explainer this skill prints to the user now
    # describes two columns rather than one vocabulary — four `STATUS` values and
    # three assessment words, where there were three and two. The section was trimmed
    # to pay for most of it; this covers the rest.
    "skills/help/SKILL.md": 14_200,                          # 13,918 B
    "skills/init/SKILL.md": 17_600,                          # 15,912 B
    # 3,600 -> 6,000 for Step 0's CLI-version gate. Two states that used to be
    # handled inline (or not at all) now route through `/loci:setup` first, and the
    # routes are procedure, which is what this file exists to hold: a MISSING `loci`
    # (the skill checks the `uv` prerequisite and verifies with doctor — the inline
    # installer did neither and reported neither), and one BEHIND the
    # `LOCI_CLI_VERSION` pin, which needs the read of both numbers, the floor rule
    # that spares a dev or post-release CLI from a pointless reinstall, and the
    # unknown case. SKILL.md gained three lines for the gate and nothing else; the
    # procedure landed here, which is the trade this table is meant to encourage.
    # +300 B on 2026-09-14: the sign-in stop reads as a one-time step rather than a
    # failure, and says what happens next — with no promise of a resume this skill
    # does not perform.
    "skills/init/bootstrap.md": 6_300,                      # 6,205 B
    # +300 B on 2026-09-14: the consent ask opens in the user's terms — what the build
    # buys them, that it runs their own build, what it writes, that it leaves their
    # source alone — before the three specifics. The specifics are what they are
    # consenting TO and none of them moved; this is the sentence that was missing
    # above them.
    "skills/init/compdb.md": 13_900,                         # 13,753 B
    "skills/init/recovery.md": 11_300,                       # 10,197 B
    # T15, new: ceiling set the same way as its siblings, ~110 % of the size it
    # arrived at. A new prose file with no ceiling is the hole this table exists
    # to close — `exec-trace` grew 24 KB -> 82 KB with every ceiling green,
    # because only three files had one.
    # ~110 % of the size it arrived at, like its three siblings. The first cut
    # was 3_000 against a 2,161-byte file — 139 %, which is most of a page of
    # prose the alarm would not have noticed.
    "skills/init/go.md": 2_590,                             # 2,121 B
    # New on 2026-09-14: the messaging contract for `/loci:init` — the vocabulary that
    # goes second, the recognition line, the consent opener, progress as a checklist of
    # what completed, and Step 4's order (readiness, then every caveat, then the
    # setup). It exists as a file because those rules govern lines produced at four
    # different steps and SKILL.md had four bytes of headroom; ~104 % of the size it
    # arrived at, tighter than the ~110 % its siblings got, because a file about tone
    # is the easiest place in this plugin for prose to accumulate.
    # +800 B the same day: the progress checklist was a rule with no shape, so what
    # got printed was whatever each run improvised. It now carries the literal block,
    # the two moments it is printed at, and the offer that keeps the detail reachable
    # — the review's "keep detailed logs accessible" half, which the rule alone missed.
    # +500 B, same day, on the owner's call: the block is the review's five ticks
    # verbatim — including `LOCI execution model ready`, which is the same fact as the
    # readiness line below it, so the section also says which of the two says it in
    # words. Plus the noun rule, since the opening line is the one firmware-specific
    # string in a skill that also initializes crates and modules.
    # +550 B, same day, owner's call again: the list is printed WHOLE from the first
    # wait, pending stages included, and reprinted as ticks land — "how many stages are
    # coming" is the review's first named UX problem and a block that only grows
    # answers it last. The bytes are the two rules that keep it honest: nothing ticked
    # before the fact that earns it, and the lines settled at the first print from what
    # this run will actually do.
    "skills/init/voice.md": 6_600,                          # 6,496 B
    # +4,200 B on 2026-09-11, three things, all of them rules the skill cannot run
    # without: the `--signals` block (with no contract this skill has no request
    # source at all, so `measure` would have nothing to bill for), the no-contract
    # escalation rule (nothing proposes a child run, so the argument for one is the
    # whole gate), and Q8.6 — the parent names the escalated child in its cause
    # sentence only where that child moved the word. ~102 % of the size it arrived at.
    # +1,300 B for AAD-7595's branch: the no-contract `STATUS` rule landing in
    # every judging skill. Taken on a promise — the trim back under is deferred,
    # not waived, so raise nothing further here without cutting first.
    "skills/loci-post-edit/SKILL.md": 77_400,                # 74,610 B
    "skills/loci-preflight/SKILL.md": 56_500,                # 51,333 B
    "skills/memory-report/SKILL.md": 35_700,                 # 32,378 B
    "skills/setup/SKILL.md": 9_800,                         # 8,824 B
    "skills/stack-depth/SKILL.md": 34_300,                   # 31,133 B
    "skills/trends/SKILL.md": 4_000,                        # 3,611 B
}

#: The three the design named, spelled SEPARATELY from the dict above so that
#: deleting an entry fails instead of quietly shrinking the parametrized test.
CEILINGED_FILES = ("skills/loci-post-edit/SKILL.md",
                   "skills/loci-preflight/SKILL.md",
                   "skills/_shared/loci-runtime-contract.md")

#: The whole of it: 426,959 B over 19 files at the end of T13.
#:
#: The set is `_model_prose_files()` — everything screened, minus a registered
#: deny list — because every ALLOW list tried here was walked around. Scoped to
#: `skills/`, review put 28 KB in `docs/`; scoped to `skills/`+`docs/`, review put
#: 44 KB in a new `guides/`; and `LICENSE.md`+`DPA.md` were 53 KB of legal text no
#: model reads, whose removal freed room for 57 KB of real prose. A new root now
#: counts from the moment it is written.
#:
#: ~105 %, tighter than the per-file ceilings: at 110 % the headroom on a
#: half-megabyte total swallows a 28 KB relocation whole.
#
# 449,000 -> 450,500 on origin/main by the opt-in init policy: `loci init` no longer runs
# unless the user asks, and "no recipe -> name it and stop; recipe present with degraded
# state -> repair" is two halves taking opposite actions, restated in the shared row and
# the six skills that route to it.
#
# 450,500 -> 480,000 here by the verdict-composition change (ADR 04, ADR 08), which added
# ~25 KB across eleven files: the two-column table spec and the composition matrix in
# `verdicts.md`, a judging half for `control-flow`, `--signals` and the no-contract
# escalation rule in the two reflex skills, and the `ENTRY` / `FUNCTION` split in every
# conclusion table. The merged corpus measures 475,848 B over 22 files, so 480,000 is
# ~101 % of it rather than the ~105 % this number was first set at: room for what these
# changes added, not headroom for the next one.
#
# 480,000 -> 500,000 on 2026-09-15, and this one is a CORRECTION, not growth that
# earned room. The ceiling had been breached since v0.2.24 and nobody noticed,
# because the test that enforces it was already red: 478,616 (v0.2.23, the last
# green) -> 489,764 (v0.2.24) -> 490,056 (v0.2.25) -> 493,650 (v0.2.26) ->
# 494,739 (v0.2.27). Five releases shipped through a gate that could not fail any
# harder than it already had, which is the failure mode a permanently-red check
# has: it stops being a signal and starts being scenery, and the next change to
# genuinely blow the budget would have arrived silently.
#
# What crossed it was real and is staying: `skills/init/voice.md` (+6,496 B, the
# voice reference `/loci:init` reads at Step 1) plus `bootstrap.md`, and the
# verdict/table work in v0.2.26. None of it is redundant -- `init/SKILL.md`
# POINTS at `voice.md` in four places rather than repeating it, so there is no
# duplication to reclaim, and trimming to fit would mean deleting content that
# was added deliberately.
#
# 500,000 is ~101 % of the 494,739 the corpus measures now, the same ~101 % the
# 480,000 above was set at, and for the same reason: room for what has landed,
# not headroom for the next one. The next addition has ~5 KB and then has to
# argue for itself. Raise this number again ONLY with the same accounting --
# what grew, why it is not duplication, and what the new figure is a percentage
# of -- and never to make a red test green.
# Raised from 500_000 for AAD-7595's branch. The trim is owed, not waived.
SHIPPED_PROSE_CEILING = 550_000


def _lf_bytes(p: Path) -> int:
    return len(p.read_text(encoding="utf-8").replace("\r\n", "\n").encode("utf-8"))


@pytest.mark.parametrize("rel", sorted(HOT_FILE_CEILINGS))
def test_a_hot_file_stays_under_its_ceiling(rel):
    """~50 KB of detection prose was deleted across T10–T12; this is the alarm
    that goes off when it grows back.

    Deliberately not a straitjacket: the ceiling is ~10 % of headroom, and raising
    it means editing this dict, which is a decision someone makes on purpose
    rather than a limit that erodes. If you are here because a correctness fix
    needs the bytes, take the bytes and raise the number in the same commit — but
    say so, because three tasks running have ended with these files bigger than
    they started.
    """
    size = _lf_bytes(PLUGIN_ROOT / rel)
    assert size <= HOT_FILE_CEILINGS[rel], (
        f"{rel} is {size} B (LF-normalized), over its {HOT_FILE_CEILINGS[rel]} B "
        f"ceiling by {size - HOT_FILE_CEILINGS[rel]} B")


def test_the_shipped_prose_total_stays_under_its_ceiling():
    """The ceiling a relocation cannot get under.

    Per-file ceilings measure files; what costs the model context is the prose it
    is handed, wherever it lives. Review found three doors in the earlier
    versions and all three are shut here: a new `docs/*.md` (in scope now), a
    `.txt` or `.adoc` beside the shared contract (in `_PROSE_SUFFIXES`), and
    moving `LICENSE.md` / `DPA.md` out of the count to free 53 KB — they were
    never model-facing and are no longer counted, so there is nothing to free.
    """
    sizes = {_rel(p): _lf_bytes(p) for p in _model_prose_files()}
    total = sum(sizes.values())
    assert total <= SHIPPED_PROSE_CEILING, (
        f"model-facing prose totals {total} B (LF-normalized) over {len(sizes)} "
        f"files, past the {SHIPPED_PROSE_CEILING} B ceiling by "
        f"{total - SHIPPED_PROSE_CEILING} B. Largest: "
        + ", ".join(f"{k} {v}" for k, v in
                    sorted(sizes.items(), key=lambda kv: -kv[1])[:5]))


def test_every_model_facing_file_carries_its_own_ceiling():
    """A ceiling on the three biggest bounds the three biggest.

    Review grew `exec-trace` from 24,450 B to 81,780 B — the second-largest
    document in the plugin — with every ceiling green, because only three files
    had one. The registry is compared against what is ON DISK in both
    directions: a new document fails until it is given a ceiling, and a ceiling
    whose file is gone fails until it is removed.
    """
    on_disk = {_rel(p) for p in _model_prose_files()}
    registered = set(HOT_FILE_CEILINGS)
    assert registered == on_disk, (
        f"unceilinged: {sorted(on_disk - registered)}; ceilinged but absent: "
        f"{sorted(registered - on_disk)}. Every file a model can be handed "
        f"carries a ceiling, or the next 57 KB goes into whichever one does not.")
    # …and the three the design named are still among them, spelled separately so
    # that deleting an entry fails here instead of quietly shrinking the
    # parametrized test from three cases to two.
    missing = [rel for rel in CEILINGED_FILES if rel not in HOT_FILE_CEILINGS]
    assert not missing, (
        f"{missing} are the files the design named and they have no ceiling")
    for rel in CEILINGED_FILES:
        assert (PLUGIN_ROOT / rel).is_file(), (
            f"{rel} is ceilinged and does not exist — the ceiling is vacuous")


def test_bug_report_records_the_cli_version_the_contract_promises():
    """The contract says the CLI's own number is surfaced in ONE place.

    It said that while `/loci:bug-report` recorded only the plugin version — a promise
    nothing kept, in the same paragraph that forbids every other way of learning
    the number. T13 made it true, and then nothing pinned it: deleting the step
    and the table row left this suite and `test_bug_report_turn_recipe.py` green,
    with the only complaint coming from a section-digest registry whose remedy is
    to paste the new hashes in.

    Both ends, because either alone is the same lie in the other direction.
    """
    # SECTION-SCOPED, both ends. The first version was two whole-file `in`
    # checks — the shape this file's own docstring calls "what `_section` exists
    # to replace" — and review deleted the step AND the table row, parked the
    # strings in a `## Changelog` code fence, and it passed. Worse, ONE surviving
    # line satisfied both assertions at once, and a second unrelated `| loci CLI |`
    # row (the CLI's PATH, not its version) satisfied the table half on its own.
    bug_raw = _raw(SKILLS / "bug-report" / "SKILL.md")
    step1 = _subsection(bug_raw, "Step 1: Collect environment snapshot")
    assert "loci --version" in step1, (
        "bug-report's Step 1 no longer probes `loci --version`, so the CLI's "
        "number is collected nowhere — while the shared contract still says "
        "/loci:bug-report is the one place it is surfaced")
    # `_section`, not `_subsection`: the report TEMPLATE is a fenced markdown
    # block containing its own `## Versions` heading, so a "to the next heading"
    # slice ends two lines in. The bound is the next real step.
    step5 = _section(_text(SKILLS / "bug-report" / "SKILL.md"),
                     "## Step 5: Write report file",
                     "## Step 6: Present summary to user")
    assert re.search(r"\|\s*loci CLI\s*\|\s*<loci --version", step5), (
        "the Versions template's `loci CLI` row no longer carries the version "
        "output, so the number is collected and then dropped. (The other "
        "`| loci CLI |` row in that template is the binary's PATH — it is not "
        "this, and it satisfied the old whole-file check on its own.)")
    gate = _section(_text(CONTRACT),
                    "**Reading the CLI's version when a rule depends on it.**",
                    "## Prerequisites: `uv` (checked, never installed)")
    assert "/loci:bug-report` runs `loci --version`" in gate, (
        "the contract no longer names the exception, so 'never infer a CLI "
        "version any other way' now forbids the only route that exists")


# ---------------------------------------------------------------------------
# 6. Mirroring — neither surface shows a verdict or a sentence the other does not
# ---------------------------------------------------------------------------
#
# The rule is 040's and it has been re-broken twice, each time by a skill that
# printed a `Verdict:` line the cockpit never heard about. 051 closed three routes:
# a run that measured nothing recorded no sentence, an escalation's verdict reached
# neither surface as one run, and four of the five judging skills had no
# `--agent-note` instruction at all. What holds each of them is prose, so what holds
# the prose is here.

VERDICTS = SKILLS / "_shared" / "verdicts.md"

#: Every skill that closes on a verdict and must therefore record one.
#: `control-flow` joined them in PR #291 (2026-09-11), which gave it the two
#: structural signals its graph determines to judge — and with a verdict, the
#: sentence behind it. What is particular to it is that its verb wrote the run
#: record already, so its call PATCHES rather than appends; the last test pins
#: that.
RECORDING_SKILLS = ("loci-post-edit", "loci-preflight", "exec-trace",
                    "stack-depth", "memory-report", "control-flow")

#: The anchor the six link to. An explicit `<a id=…>`, so the link survives a
#: reworded heading — and `test_every_contract_link_resolves_to_a_file_and_an_anchor`
#: fails if it stops existing.
RECORDING_ANCHOR = "recording-the-verdict"


def _verdicts_recording() -> str:
    return _subsection(_raw(VERDICTS),
                       "Recording it: one call, on every run that printed a verdict")


def test_the_record_call_is_written_once_and_carries_both_halves():
    """One section, both flags. Before 051 the call lived in post-edit's footer with
    the note rule attached to it, and the four other skills each had a partial copy —
    `stack-depth`'s had no `--agent-note` at all, so the cockpit fell back to
    reconstructing a sentence and the two surfaces described one run differently."""
    section = _verdicts_recording()
    assert "loci stats record" in section and "--run" in section, (
        "the shared recording section no longer carries the call, so six skills "
        "route to a section that tells them nothing")
    for flag in ("--agent-note", "--agent-judged", "--parent-run"):
        assert flag in section, f"the shared recording section names no `{flag}`"
    assert "Copy the clause you printed; do not compose a second one" in section, (
        "the rule that makes the two surfaces agree is gone — a skill free to compose "
        "a second sentence is how the cockpit and the session drifted apart")
    assert "`--agent-note` is the cause clause only**" in section, (
        "the section no longer forbids a verdict token inside the note, which puts a "
        "second verdict on the row beside the real one")


def test_every_run_that_printed_a_verdict_records_one():
    """The gap 051 A closed, stated where every skill reads it. A run that measured
    nothing is the branch whose whole content IS the sentence, and it was the one
    branch that wrote none."""
    section = _verdicts_recording()
    assert "every branch that printed a `Verdict:` line" in section
    assert "a run that measured nothing included" in section, (
        "the section no longer says the no-measurement branch records too, which is "
        "the case it was written for")


@pytest.mark.parametrize("skill", RECORDING_SKILLS)
def test_every_judging_skill_routes_to_the_one_recording_section(skill):
    body = _text(SKILLS / skill / "SKILL.md")
    assert f"verdicts.md#{RECORDING_ANCHOR}" in body, (
        f"{skill} does not route to the shared recording section, so its sentence "
        f"never reaches the cockpit and the two surfaces read differently")
    assert "--agent-note" in body or "cause clause" in body, (
        f"{skill} names neither the flag nor what it carries, so the routing is a "
        f"pointer a model has no reason to follow")


def test_post_edit_records_off_the_footers_gate():
    """051 decision A. The call used to live inside `## LOCI footer`, whose whole
    section is gated on `N > 0` — so the one branch that measures nothing, and
    therefore prints no footer, wrote nothing at all. The printed footer keeps that
    rule; the record does not."""
    path = SKILLS / "loci-post-edit" / "SKILL.md"
    body = _text(path)
    step = _subsection(_raw(path),
                       "Step 7: record the verdict — one call, every branch")
    assert f"verdicts.md#{RECORDING_ANCHOR}" in step
    assert "Step 2a" in step, (
        "Step 7 no longer names the branch it exists for, which is how the call ended "
        "up inside the footer the first time")
    footer = _section(body, "## LOCI footer", "### Render the footer")
    assert "loci stats record" not in footer, (
        "the record call is back inside the footer section, which is gated on `N > 0` "
        "— the run that measures nothing then records nothing again")
    # …and the printed line keeps the gate it always had: the session output is
    # unchanged by 051, only the cockpit gains the sentence.
    assert "only if N > 0" in footer


def test_step_2a_no_longer_claims_nothing_is_recorded():
    """The sentence the defect was written down in. It said so outright — "nothing is
    recorded to `loci stats`" — while the same branch printed a verdict the user
    read."""
    step = _section(_text(SKILLS / "loci-post-edit" / "SKILL.md"),
                    "## Step 2a: no function changed",
                    "## Step 3: confirm or override the hot path")
    assert "nothing is recorded to `loci stats`" not in step, (
        "Step 2a still says its branch records nothing, which is the instruction 051 "
        "reversed")
    assert "**Step 7 still runs.**" in step
    # The half that stays true: this branch is free, and saying otherwise would put
    # the metered call back on it.
    assert "Nothing is metered on this branch" in step


def test_an_escalation_carries_the_parent_run_both_ways():
    """051 decision B. post-edit's Step 4a escalation closed on its own `Verdict:`
    line and the cockpit had no trace of it: the child's row carried the child's
    verdict, the parent's row was never written, and nothing linked the two."""
    parent = _text(SKILLS / "loci-post-edit" / "SKILL.md")
    assert "`--parent-run`" in parent, (
        "post-edit does not hand its manifest id to the skill it escalates into, so "
        "the two runs reach the cockpit as unrelated entries")
    for child in ("stack-depth", "memory-report"):
        body = _text(SKILLS / child / "SKILL.md")
        assert "--parent-run" in body, (
            f"{child} never passes `--parent-run`, so an escalation it was invoked "
            f"for is not linked to the run that caused it")


@pytest.mark.parametrize("skill", ("memory-report", "control-flow"))
def test_the_third_reasoned_verdict_is_named_where_the_two_were(skill):
    """049's open thread. `no_opinion` is the agent having READ an entry and declined
    — distinct from `pending`, which is one it never reached — and two skills named
    only `flagged` and `cleared`, so their models had no word for the case."""
    body = _text(SKILLS / skill / "SKILL.md")
    assert "no_opinion" in body, (
        f"{skill} still names two reasoned verdicts, so an entry it read and could "
        f"not judge is recorded as a `cleared` or left as a `pending`")


def test_control_flow_patches_the_run_its_own_verb_already_wrote():
    """The one recording skill whose call is a patch, and it has to say so.

    It was the skill outside the rule until PR #291 — `It records no
    `agent_note` either`, asserted here until 2026-09-13 — because a note
    explains a verdict and a renderer reached none. It reaches two now. What did
    not change is that `loci analyse cfg` appends the run record itself before
    it returns, so a second `stats record --skill` would put one run in the
    cockpit twice: the skill's only call names `--run` and patches the row that
    is already there.
    """
    body = _text(SKILLS / "control-flow" / "SKILL.md")
    assert "`loci analyse cfg` wrote the run record before it returned" in body, (
        "control-flow no longer says the verb recorded the run, which is the "
        "reason its own call is a patch rather than an append")
    assert ("the only `stats record` call this skill makes is Step 5's, which "
            "names `--run`") in body, (
        "nothing forbids a second recording call, so one run reaches the cockpit "
        "as two — the verb's and the skill's")
