"""Lint: the documented way to read `loci elf diff` matches what the CLI returns.

`elf diff` answers with counts in `data.summary` and a **file** at `data.diff_file`.
There is no `data.modified` and no `data.added` — and until phase 09 no shipped
document said what is *inside* that file. Three skills told the model to read it and
then pass `<changed_funcs>` to the next command, so everything needed to cross that
gap was left to a guess. Four guesses fail, and all four fail quietly:

* the name is under **`symbol`**; `.function` and `.name` print the literal `null`,
  and `--functions null` is accepted by `elf asm` as a name matching nothing;
* the file carries **`removed`** entries — gone from the After, nothing to extract;
* the file carries **data symbols** (`STT_OBJECT`); `elf asm` answers `ok:true` with
  empty assembly and `timing_csv: null` for one, and `elf cfg` fails outright;
* an **empty result does not mean the edit had no effect** — the differ hashes masked
  instructions, so a constant-only edit produces no entry at all.

So the recipes are pinned here, and — where `jq` is available — actually **run**
against a fixture in the CLI's own written shape (`src/loci/cli/elf.py`'s diff entry;
`tests/unit/test_elf_handlers.py::test_diff_writes_file_summary_and_count` pins it
there). A regex can only say the prose still contains a filter; running it is what says
the filter still selects.

Two lessons from the mutation campaign are built into the shape of this file, because
the first version failed both. **Fixtures are derived, not listed** — emptying
`CHANGED`, `NOT_CHANGED` or `DIFF_CONSUMERS` used to leave the suite green, which is
the "a lint that passes with its own fixture emptied is testing nothing" failure in its
own test file. And **the executor reads the flag out of the document** rather than
hardcoding `-r`, because `jq -c` prints `"new_fn"` *with quotes* — a name that matches
nothing — and that mutation was invisible.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
SKILLS_DIR = PLUGIN_ROOT / "skills"
CONTRACT = SKILLS_DIR / "_shared" / "loci-runtime-contract.md"

# Skills whose own steps invoke `loci elf diff` and act on its answer. Declared here
# for legibility and checked against a scan below, so a fourth skill that starts
# calling the verb cannot stay invisible to every test in this file.
DIFF_CONSUMERS = ("loci-post-edit", "exec-trace", "control-flow")

# One entry per reachable shape, in the form `diff_elfs` writes, with
# `similarity_ratio` ordering them as the CLI's sort does.
#
# **Two** modified functions, deliberately: a one-element group is joined identically
# by `join(",")` and `join(";")`, so a single-entry fixture cannot tell a
# comma-separated list from any other separator — and `--functions` splits on commas
# alone. A mutation to `join(";")` survived the version of this fixture that had one.
#
# The `STT_OBJECT` row is a **changed global variable**. The differ diffs variables as
# well as functions and marks them only by `stt_type`, so a recipe that filters on
# `status` alone hands a data symbol to `elf asm`.
#
# The `unchanged` row is **defensive**, and is the one shape here that today's producer
# does not write: `asmslicer._bindiff_analysis` emits `added`, `removed` and `modified`
# rows only (verified against `arm-none-eabi-gcc` objects — three functions, one
# edited, `summary.unchanged: 0`). The CLI carries the status through regardless, so a
# differ that started emitting it would reach these recipes unannounced.
FIXTURE_ENTRIES = [
    {"status": "added", "symbol": "new_fn", "stt_type": "STT_FUNC",
     "similarity_ratio": 0.0, "reason": "new"},
    {"status": "added", "symbol": "new_global", "stt_type": "STT_OBJECT",
     "similarity_ratio": 0.0, "reason": "new"},
    {"status": "removed", "symbol": "gone_fn", "stt_type": "STT_FUNC",
     "similarity_ratio": 0.0, "reason": "gone"},
    {"status": "modified", "symbol": "adc_read", "stt_type": "STT_FUNC",
     "similarity_ratio": 0.42, "reason": "INSTRUCTION_MNEMONIC_MISMATCH"},
    {"status": "modified", "symbol": "spi_write", "stt_type": "STT_FUNC",
     "similarity_ratio": 0.77, "reason": "INSTRUCTION_MNEMONIC_MISMATCH"},
    {"status": "unchanged", "symbol": "untouched", "stt_type": "STT_FUNC",
     "similarity_ratio": 1.0, "reason": ""},
]

# Derived, never listed: a correct recipe yields exactly the changed *functions*.
CHANGED = {e["symbol"] for e in FIXTURE_ENTRIES
           if e["status"] in ("modified", "added") and e["stt_type"] == "STT_FUNC"}
NOT_CHANGED = {e["symbol"] for e in FIXTURE_ENTRIES} - CHANGED
STATUS_OF = {e["symbol"]: e["status"] for e in FIXTURE_ENTRIES}


def test_the_fixture_can_tell_a_right_recipe_from_a_wrong_one():
    """The derivation above is only useful while both sides are non-empty and the
    fixture still contains every shape the recipes have to discriminate."""
    assert len(CHANGED) >= 3, "too few changed functions to detect a bad join"
    assert {"removed", "unchanged"} <= {STATUS_OF[s] for s in NOT_CHANGED}
    assert any(e["stt_type"] == "STT_OBJECT" for e in FIXTURE_ENTRIES), (
        "without a data symbol nothing here can see an `stt_type` filter appear or go"
    )
    assert all(e["stt_type"] == "STT_FUNC" for e in FIXTURE_ENTRIES
               if e["symbol"] in CHANGED)


def _docs() -> list[tuple[str, str]]:
    """(label, text) for the contract and every diff consumer."""
    out = [("skills/_shared/loci-runtime-contract.md",
            CONTRACT.read_text(encoding="utf-8"))]
    for name in DIFF_CONSUMERS:
        path = SKILLS_DIR / name / "SKILL.md"
        out.append((f"skills/{name}/SKILL.md", path.read_text(encoding="utf-8")))
    return out


# A fenced block. The leading `[ \t]*` is load-bearing: in `control-flow` and
# `exec-trace` the recipe sits inside a numbered list item and is therefore indented,
# so an anchored ```` ^``` ```` found only the contract's and `loci-post-edit`'s
# fences and reported the other two as having no recipe at all.
_FENCE = re.compile(r"^[ \t]*```[a-z]*\n(.*?)^[ \t]*```$", re.S | re.M)

# A `jq` invocation in a fenced block: the flag group, then a single-quoted program
# that may span lines and never contains a single quote itself. The flags are captured
# rather than discarded — see the module docstring.
_JQ_PROGRAM = re.compile(r"jq\s+(-[a-zA-Z]+)\s+'([^']*)'", re.S)

# Inside such a fence, a program reading the *envelope* starts at `.data` or `.ok`;
# anything else is reading the diff file's array. Selecting diff readers this way,
# rather than by "it mentions `.status`", is deliberate: the property under test IS
# that they filter on status, so a discriminator that requires `.status` makes every
# per-program assertion vacuous for exactly the recipe that lost its filter. A
# mutation campaign proved that — deleting `select(…)` left the filter lint green and
# was caught only, and accidentally, by the coverage guard.
_READS_ENVELOPE = re.compile(r"\s*\.(?:data|ok)\b")


def _sentence_before(text: str, pos: int) -> str:
    """The text from the start of `pos`'s sentence up to `pos`.

    A sentence ends at `.`/`!`/`?`/`:` followed by whitespace, or at a blank line.
    Deliberately narrower than "the preceding N characters": a window wide enough to
    span a hard wrap is also wide enough to pick up an unrelated negation.
    """
    start = 0
    for m in re.finditer(r"[.!?:]\s|\n\s*\n", text[:pos]):
        start = m.end()
    return text[start:pos]


# A negation counts only when nothing but filler stands between it and the thing it
# denies. "There is no\n`data.modified`" qualifies; "There is no doubt that
# `data.modified` holds the list" does not — and that sentence defeated the earlier
# sentence-wide keyword search, asserting the field exists while reading as a denial.
#
# A field already denied earlier in the same run counts as filler, so one sentence can
# deny both at once — "There is no `data.modified` and no `data.added`" is the sentence
# this whole lint exists to keep, and it must not be the sentence that fails it.
_DENIAL = re.compile(
    r"\b(?:no|not|never|neither|nor)\b"
    r"(?:\s|`|\*|_|—|-|,|\band\b|\bor\b|\ba\b|\ban\b|\bany\b"
    r"|\bdata\.(?:modified|added)\b)*$",
    re.I,
)


def _is_denied(text: str, pos: int) -> bool:
    return bool(_DENIAL.search(_sentence_before(text, pos)))


def _fences(text: str) -> list[str]:
    return _FENCE.findall(text)


def _diff_programs() -> list[tuple[str, str, str]]:
    """(label, jq-flags, jq-program) for every documented read of the diff file."""
    out: list[tuple[str, str, str]] = []
    for label, text in _docs():
        for fence in _fences(text):
            if "data.diff_file" not in fence:
                continue        # not a diff fence at all
            for flags, prog in _JQ_PROGRAM.findall(fence):
                if not _READS_ENVELOPE.match(prog):
                    out.append((label, flags, prog))
    return out


# ── who the consumers are ────────────────────────────────────────────────────

def test_the_declared_consumers_are_the_skills_that_actually_run_the_verb():
    """`DIFF_CONSUMERS` feeds both sides of the coverage assertion below, so on its
    own it is self-referential: dropping a skill from it removed that skill from the
    expectation too, and two mutations exploited exactly that. Scanning for the
    invocation is what makes the list answerable to something."""
    found = set()
    for path in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        text = path.read_text(encoding="utf-8")
        if any("loci elf diff" in fence for fence in _fences(text)):
            found.add(path.parent.name)
    assert found == set(DIFF_CONSUMERS), (
        "the skills invoking `loci elf diff` in a runnable fence are not the ones "
        f"declared here. found={sorted(found)} declared={sorted(DIFF_CONSUMERS)}. A "
        "new consumer must be added to DIFF_CONSUMERS or every test in this file "
        "silently skips it."
    )


def test_every_diff_consumer_documents_a_way_to_read_the_file():
    """A guard that scans nothing passes for the wrong reason. If the fence
    convention or the regex drifts, fail here rather than lint an empty list."""
    found = {label for label, _flags, _prog in _diff_programs()}
    expected = {"skills/_shared/loci-runtime-contract.md"} | {
        f"skills/{n}/SKILL.md" for n in DIFF_CONSUMERS
    }
    assert found == expected, (
        "these documents invoke `loci elf diff` but never show how to turn "
        f"`data.diff_file` into a function list. found={sorted(found)} "
        f"expected={sorted(expected)}"
    )


def test_the_contract_carries_both_documented_forms():
    """The consumers link here for two different shapes — a flat list, and the
    labelled groups `loci-post-edit` needs because only a `modified` function has a
    Before. Coverage is counted per document, so deleting one of the two left the
    contract still "present" via the other."""
    progs = [p for label, _f, p in _diff_programs() if label.endswith("contract.md")]
    grouped = [p for p in progs if "group_by" in p]
    flat = [p for p in progs if "group_by" not in p]
    assert grouped and flat, (
        f"the contract must document both forms; found {len(flat)} flat and "
        f"{len(grouped)} grouped"
    )


# ── the recipes, as text ─────────────────────────────────────────────────────

def test_the_recipes_read_the_path_from_the_envelope():
    """`.loci-build/elf/<stem>/diff.json` is the CLI's to choose — it already keys
    that directory on the *pair*, so two comparisons of one Before do not collide.
    A spelled path is the same defect phase 02 removed from four skills.

    Two assertions with two different scopes, deliberately. Spelling `diff.json` is
    the defect, and it is a defect in **any** fence, so that half selects nothing.
    The other half — "a fence that reads entries knows where they are" — needs a
    discriminator for *reading entries*, and `.status` is no longer one: phase 11's
    footprint recipe reads `symbol_deltas[].status` out of `elf memmap`, which has no
    diff file at all and was reported here as a diff fence missing one. `stt_type` and
    `similarity_ratio` are keys only this producer writes. Selecting on the invocation
    instead is wrong in the other direction — the section opens by *showing* the
    command, with no jq at all."""
    offenders = []
    for label, text in _docs():
        for fence in _fences(text):
            if "diff.json" in fence:
                offenders.append(f"  {label}: spells diff.json instead of reading "
                                 f"`.data.diff_file`")
            if "jq" not in fence:
                continue    # the entry-shape sample is a fence too, and reads nothing
            if not any(k in fence for k in ("stt_type", "similarity_ratio")):
                continue
            if "diff_file" not in fence:
                offenders.append(f"  {label}: reads diff entries without reading "
                                 f"`.data.diff_file`, so their path is a guess")
    assert not offenders, (
        "these recipes name the diff file instead of taking its path from the "
        "envelope:\n" + "\n".join(sorted(set(offenders)))
    )


def test_every_diff_fence_also_reads_the_counts():
    """Since the empty-result branch is decided on `data.summary` — a non-zero
    `removed` means functions were deleted, all-zero means the differ saw nothing —
    a fence that prints only the list documents an answer the prose cannot
    interpret. Deleting the summary line while the prose still said "the second
    command prints the counts" left the suite green."""
    offenders = [
        label for label, fence in _diff_fences() if "data.summary" not in fence
    ]
    assert not offenders, (
        "these diff fences never read `data.summary`, so the model cannot tell "
        "'nothing changed' from 'everything was deleted': " + ", ".join(offenders)
    )


def test_every_fence_that_uses_env_also_sets_it():
    """Each fenced block is its own Bash call — the rule these skills state three
    times over. A fence that reads `$env` without setting it runs against an empty
    string and answers `jq: error: Could not open file : Invalid argument`, which is
    not the failure the surrounding prose predicts, so the model misdiagnoses a
    missing variable as a failed diff."""
    offenders = []
    for label, text in _docs():
        for fence in _fences(text):
            if '"$env"' in fence and "env=$(" not in fence:
                offenders.append(f"  {label}: {fence.strip().splitlines()[0][:70]}")
    assert not offenders, (
        "these fences consume `$env` without setting it:\n" + "\n".join(offenders)
    )


def test_every_documented_functions_placeholder_is_quoted():
    """`--functions <changed_funcs>` unquoted is not a style point. A monomorphized
    Rust generic contains `<` and `>`; bash reads them as redirections, consumes the
    following flag as a filename, and the command never runs — the contract names
    exactly that symbol shape two sections along.

    Scoped to **placeholders**, because that is where the hazard is: a placeholder is
    substituted with a value nobody has seen, while a literal name typed into an
    example already *is* the value and cannot surprise anyone.

    Scoped to **`loci elf`**, because `--functions` is two different flags. On
    `loci stats record` it takes an integer count (`--functions <N>`), where quoting
    is meaningless — an earlier version of this lint flagged six of those and would
    have had me edit five skills to no purpose.
    """
    offenders = []
    for label, text in _docs():
        for fence in _fences(text):
            for line in re.sub(r"\\\n\s*", " ", fence).splitlines():
                if "loci elf" not in line:
                    continue
                for m in re.finditer(
                        r"(--functions|--elf|--comparing-elf)\s+(\S+)", line):
                    flag, value = m.group(1), m.group(2)
                    if "<" not in value:
                        continue    # a literal example, not a substitution site
                    if not (value.startswith('"') or value.startswith("'")):
                        offenders.append(f"  {label}: {flag} {value}")
    assert not offenders, (
        "these documented commands leave a substituted value unquoted. `--elf` and "
        "`--comparing-elf` take paths, and a project directory containing a space is "
        "ordinary on Windows (`C:\\Users\\First Last\\…`):\n" + "\n".join(offenders)
    )


def test_no_document_claims_data_modified_or_data_added_exists():
    """The claim `elf diff` "returns lists of `modified` and `added`" shipped for
    months. `data.modified` is `null`, and `jq -r` prints that as the literal string
    `null`, which `elf asm` then accepts as a function name."""
    offenders = []
    for label, text in _docs():
        for m in re.finditer(r"`?data\.(?:modified|added)`?", text):
            if _is_denied(text, m.start()):
                continue    # prose denying the field
            offenders.append(f"  {label}:{text.count(chr(10), 0, m.start()) + 1}"
                             f" -> {m.group(0)}")
    assert not offenders, (
        "these documents assert a field `loci elf diff` does not return:\n"
        + "\n".join(offenders)
    )


def test_no_document_tells_the_model_to_widen_to_the_whole_object():
    """Every consumer's empty-result branch says what NOT to do, and the reason is
    metered: widening costs one `loci timing` call per function. A mutation that
    inverted the instruction — "no output means the diff failed, so widen" — left the
    suite green, so the negation is asserted rather than assumed."""
    offenders = []
    for label, text in _docs():
        for m in re.finditer(r"every function in the object", text):
            if _is_denied(text, m.start()):
                continue
            # `do not fall back to …` / `instead of widening to …`: the negation sits
            # before the verb, not immediately before this phrase, so accept a
            # negation anywhere earlier in the same sentence here.
            if re.search(r"\b(?:not|never|do not|don't|rather than|instead of)\b",
                         _sentence_before(text, m.start()), re.I):
                continue
            offenders.append(f"  {label}:{text.count(chr(10), 0, m.start()) + 1}")
    assert not offenders, (
        "these documents instruct the model to measure the whole object:\n"
        + "\n".join(offenders)
    )


def test_every_consumer_states_that_an_empty_result_is_not_no_effect():
    """The sharpest defect found in this phase's own review: an empty diff was
    documented, in bold, in four places, as "the edit changed no compiled function".
    It is not. `asmslicer._mask_instruction` replaces immediates before hashing, so
    changing a loop bound from 100 to 200 produces an envelope byte-identical to
    diffing an artifact against itself. Every document that tells the model what an
    empty result means has to carry the caveat."""
    missing = [label for label, text in _docs()
               if not re.search(r"\bmask(?:s|ed|ing)?\b", text, re.I)]
    assert not missing, (
        "these documents describe the empty-diff case without saying that the differ "
        "masks immediate values, so a constant-only edit reads as 'nothing "
        "changed': " + ", ".join(missing)
    )


def test_every_recipe_projects_symbol_and_not_a_guessed_key():
    offenders = []
    for label, _flags, prog in _diff_programs():
        if ".symbol" not in prog:
            offenders.append(f"  {label}: reads no `.symbol`")
        for guess in (".function", ".name"):
            if guess in prog:
                offenders.append(f"  {label}: projects `{guess}`, which is null")
    assert not offenders, (
        "the function name in a diff entry is under `symbol`; every other key "
        "yields null, which jq prints as the literal string `null`:\n"
        + "\n".join(offenders)
    )


def test_no_prose_names_a_key_the_entries_do_not_have():
    """The recipes are guarded; the sentences around them were not. A mutation that
    left every `jq` program correct and changed the prose to "the name is under
    `function`" survived — and the prose is what a model rewriting a recipe follows."""
    offenders = []
    for label, text in _docs():
        # `\s+`, not a literal space: these documents are hard-wrapped, and the phrase
        # this exists to catch breaks across the line exactly where `symbol` sits.
        for m in re.finditer(r"under\s+\*{0,2}`(function|name)`", text):
            offenders.append(f"  {label}:{text.count(chr(10), 0, m.start()) + 1}"
                             f" -> under `{m.group(1)}`")
    assert not offenders, (
        "these documents name the wrong key for a diff entry:\n" + "\n".join(offenders)
    )


def test_every_recipe_filters_on_status_and_on_symbol_type():
    """Two filters, both load-bearing. `removed` symbols are gone from the After;
    `STT_OBJECT` symbols are variables, and `elf asm` answers `ok:true` with empty
    assembly for one while `elf cfg` fails outright."""
    offenders = []
    for label, _flags, prog in _diff_programs():
        if "select" not in prog:
            offenders.append(f"  {label}: reads the file without a select()")
            continue
        for status in ("modified", "added"):
            if f'"{status}"' not in prog:
                offenders.append(f"  {label}: never selects `{status}`")
        if "STT_FUNC" not in prog:
            offenders.append(f"  {label}: does not filter on stt_type")
    assert not offenders, (
        "these recipes hand on symbols the edit did not change, or that are not "
        "functions at all:\n" + "\n".join(offenders)
    )


# ── the links the consumers deliver the contract through ─────────────────────

def test_every_contract_link_resolves_to_a_file_and_an_anchor():
    """Two of the three consumers deliver the entry shape by linking here rather than
    by restating it. Renaming the anchor, pointing at a missing fragment and pointing
    at a file that does not exist all left the suite green — and the repo has no
    markdown-link lint to fall back on."""
    offenders = []
    for name in sorted(p.parent.name for p in SKILLS_DIR.glob("*/SKILL.md")):
        path = SKILLS_DIR / name / "SKILL.md"
        text = path.read_text(encoding="utf-8")
        for target, anchor in re.findall(r"\]\(([^)#\s]+)#([^)\s]+)\)", text):
            dest = (path.parent / target).resolve()
            if not dest.is_file():
                offenders.append(f"  skills/{name}/SKILL.md -> {target} (no such file)")
                continue
            body = dest.read_text(encoding="utf-8")
            if f'id="{anchor}"' not in body:
                offenders.append(
                    f"  skills/{name}/SKILL.md -> {target}#{anchor} (no such anchor)")
    assert not offenders, (
        "these cross-document links are dead:\n" + "\n".join(offenders)
    )


# ── the recipes, actually run ────────────────────────────────────────────────
# A filter that lints clean can still select nothing: `.status` vs `.data.status`,
# `==` vs `=`, a `group_by` on a key that is not there. Only running it can tell.

requires_jq = pytest.mark.skipif(
    shutil.which("jq") is None,
    reason="jq required to execute the documented recipes",
)


def _write(tmp_path: Path, entries: list[dict], name: str = "diff.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def diff_file(tmp_path: Path) -> Path:
    return _write(tmp_path, FIXTURE_ENTRIES)


def _run_jq(flags: str, program: str, target: Path) -> str:
    """Run a documented program **with the flags the document specifies.**

    Hardcoding `-r` here hid a real defect: with `-c` the flat recipe prints
    `"new_fn"` *with quotes*, and a model pasting that into `--functions` asks for a
    name that cannot match.
    """
    proc = subprocess.run(
        ["jq", flags, program, str(target)],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode == 0, (
        f"a documented jq program failed to run:\njq {flags} '{program}'\n"
        f"{proc.stderr}"
    )
    return proc.stdout


def test_the_runner_fails_on_a_program_that_errors(tmp_path: Path):
    """`_run_jq`'s return-code assertion is the only thing separating "printed
    nothing" from "crashed", and both empty-output tests below read a crash as a pass
    without it. Dropping that assert left a genuine jq bug — indexing `.[0]` on an
    empty group — undetected."""
    if shutil.which("jq") is None:
        pytest.skip("jq required")
    with pytest.raises(AssertionError, match="failed to run"):
        _run_jq("-r", ".[] | .nope | error", _write(tmp_path, FIXTURE_ENTRIES))


def _parse(out: str) -> list[tuple[str | None, str]]:
    """A recipe's output as (label, symbol) pairs.

    Two documented shapes: one symbol per line, and `status<TAB>a,b` when a consumer
    needs the groups apart. Parsing both the same way lets one assertion cover them.
    """
    pairs: list[tuple[str | None, str]] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        label, _, rest = line.partition("\t")
        if not rest:
            label, rest = None, line
        for symbol in rest.split(","):
            pairs.append((label, symbol.strip()))
    return pairs


@requires_jq
def test_each_documented_recipe_selects_the_changed_functions_and_nothing_else(
        diff_file: Path):
    for label, flags, prog in _diff_programs():
        out = _run_jq(flags, prog, diff_file)
        pairs = _parse(out)
        named = {symbol for _, symbol in pairs}

        assert CHANGED <= named, (
            f"{label}: the documented recipe drops {sorted(CHANGED - named)}, which "
            f"changed:\njq {flags} '{prog}'\n-> {out!r}"
        )
        for symbol in sorted(NOT_CHANGED & named):
            pytest.fail(
                f"{label}: the documented recipe passes on `{symbol}` "
                f"({STATUS_OF[symbol]}). A `removed` symbol has no assembly in the "
                f"After; a data symbol makes `elf asm` answer ok:true with none; an "
                f"`unchanged` one costs a metered timing call to say nothing:\n"
                f"jq {flags} '{prog}'\n-> {out!r}"
            )

        # A recipe that labels its groups has to label them *correctly*. Membership
        # alone cannot see this: `group_by` on a key the entries do not have puts
        # every symbol in one group under the first one's status, so a `modified`
        # function arrives tagged `added` — and the skill then extracts assembly from
        # the After only, reporting a delta it never measured as a new function.
        if "group_by" in prog:
            assert any(lbl is not None for lbl, _ in pairs), (
                f"{label}: the grouped recipe printed no `status<TAB>…` labels, so "
                f"the two lists cannot be told apart:\njq {flags} '{prog}'\n-> {out!r}"
            )
        for status, symbol in pairs:
            if status is None:
                continue
            assert status == STATUS_OF[symbol], (
                f"{label}: the recipe labels `{symbol}` as `{status}`, but it is "
                f"`{STATUS_OF[symbol]}`. The two lists are used differently — only a "
                f"`modified` function has a Before to extract:\njq {flags} '{prog}'\n"
                f"-> {out!r}"
            )


# ── the fences, actually run ─────────────────────────────────────────────────
# Running the jq *program* leaves the shell around it unguarded, and that shell is
# most of what a model retypes. A campaign proved the gap: dropping the `\` from the
# flat fence's continuation left jq reading stdin and bash then executing the diff
# file's path as a command (`.loci-build/…/diff.json: line 1: [: missing ']'`), which
# a model reads as a corrupt diff; swapping the two `jq` lines, and unquoting the
# `$( )` that supplies the path, were likewise invisible. So the fence is run whole,
# against a stub `loci`, exactly as written.

def _find_bash() -> str | None:
    if sys.platform == "win32":
        for cand in (r"C:\Program Files\Git\usr\bin\bash.exe",
                     r"C:\Program Files (x86)\Git\usr\bin\bash.exe"):
            if Path(cand).is_file():
                return cand
    return shutil.which("bash")


def _to_bash_path(p: Path) -> str:
    s = Path(p).as_posix()
    m = re.match(r"^([A-Za-z]):/(.*)$", s)
    return f"/{m.group(1).lower()}/{m.group(2)}" if m else s


requires_bash = pytest.mark.skipif(
    _find_bash() is None or shutil.which("jq") is None,
    reason="bash and jq required to run the documented fences",
)

# Only `elf diff` is implemented: it writes the fixture where the envelope says it is,
# so the fence has to read the path out of the envelope to find it.
_SUMMARY = '{"added":2,"removed":1,"modified":2,"unchanged":0}'
_STUB_LOCI = """#!/usr/bin/env bash
if [ "$1 $2" != "elf diff" ]; then echo "unexpected: $*" >&2; exit 9; fi
cp "__FIXTURE__" "__OUT__"
echo '{"ok":true,"data":{"summary":__SUMMARY__,"count":6,"diff_file":"__OUT__"}}'
"""


def _diff_fences() -> list[tuple[str, str]]:
    return [(label, fence) for label, text in _docs() for fence in _fences(text)
            if "loci elf diff" in fence and "data.diff_file" in fence]


@requires_bash
def test_every_documented_diff_fence_runs_and_prints_what_the_prose_promises(
        tmp_path: Path):
    """The whole fence, verbatim, with only the placeholders substituted."""
    fixture = _write(tmp_path, FIXTURE_ENTRIES)
    # A directory with a space in it, deliberately: unquoting the `$( )` that supplies
    # the path is otherwise invisible, and a project under `C:\\Users\\First Last\\…`
    # is ordinary on Windows.
    out_json = tmp_path / "out dir" / "diff.json"
    out_json.parent.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "loci"
    stub.write_text(
        _STUB_LOCI.replace("__FIXTURE__", _to_bash_path(fixture))
                  .replace("__OUT__", _to_bash_path(out_json))
                  .replace("__SUMMARY__", _SUMMARY),
        encoding="utf-8", newline="\n")
    stub.chmod(0o755)

    jq_dir = _to_bash_path(Path(shutil.which("jq")).parent)
    env_path = f"{_to_bash_path(bindir)}:{jq_dir}:/usr/bin:/bin"

    for label, fence in _diff_fences():
        script = (fence.replace("<PREV>", "before.o")
                       .replace("<OBJ>", "after.o")
                       .replace("<loci_target>", "armv7e-m"))
        proc = subprocess.run(
            [_find_bash(), "-s"],
            input=f"export PATH={env_path}\nset -e\n{script}",
            capture_output=True, text=True, encoding="utf-8", cwd=tmp_path,
        )
        assert proc.returncode == 0, (
            f"{label}: the documented fence exits {proc.returncode}\n"
            f"--- fence ---\n{script}\n--- stderr ---\n{proc.stderr}"
        )
        assert not proc.stderr.strip(), (
            f"{label}: the documented fence writes to stderr, which a model reads as "
            f"a failure:\n{proc.stderr}"
        )
        lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
        assert lines, f"{label}: the documented fence printed nothing"

        # The counts lead. Every consumer's prose refers to them as what comes first
        # ("the second command prints the counts"), and `loci-post-edit` reads the two
        # answers positionally.
        if "data.summary" in fence:
            assert lines[0].startswith("{") and '"modified"' in lines[0], (
                f"{label}: the first line is not the summary — the prose says it is, "
                f"and the ordering is unguarded otherwise:\n{proc.stdout!r}"
            )
            lines = lines[1:]

        named = {sym for _lbl, sym in _parse("\n".join(lines))}
        assert CHANGED <= named and not (NOT_CHANGED & named), (
            f"{label}: run whole, the fence yields {sorted(named)}; expected exactly "
            f"{sorted(CHANGED)}\n--- fence ---\n{script}\n--- stdout ---\n"
            f"{proc.stdout!r}"
        )


@requires_jq
@pytest.mark.parametrize("shape", ["empty", "removed-only", "data-only"])
def test_a_recipe_prints_nothing_when_no_function_changed(tmp_path: Path, shape: str):
    """Empty output is the "no compiled function changed" answer every consumer is
    told to report (with the masking caveat). These are the shapes that reach it:
    the differ writes `[]` when it sees no difference, a delete-only edit yields
    `removed` rows alone, and an edit touching only a global yields `STT_OBJECT` rows
    alone. An earlier version tested an `unchanged`-only file — a shape the producer
    never writes.
    """
    entries = {
        "empty": [],
        "removed-only": [e for e in FIXTURE_ENTRIES if e["status"] == "removed"],
        "data-only": [e for e in FIXTURE_ENTRIES if e["stt_type"] == "STT_OBJECT"],
    }[shape]
    assert shape == "empty" or entries, "the fixture lost the shape under test"
    path = _write(tmp_path, entries)
    for label, flags, prog in _diff_programs():
        out = _run_jq(flags, prog, path)
        assert out.strip() == "", (
            f"{label}: printed {out!r} for a {shape} diff\njq {flags} '{prog}'"
        )
