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

Todo 044 moved all four of those from the documents into the CLI. `elf diff` now
answers with **`data.functions`** — `added`, `removed` and `modified`, `STT_FUNC`
only — so the filtering is the producer's and the document's job is to say which
list to read. The executable half of this file went with it: the shapes are pinned
where the code is, in the CLI repo's
`tests/unit/test_elf_handlers.py::test_diff_function_names_exclude_the_variables_the_differ_also_diffs`.
What stays here is what only this repo can answer — that every document sends the
model to the right field, and none of them asserts a field the verb does not return.

One lesson from the mutation campaign is still built into the shape of this file:
**fixtures are derived, not listed** — emptying `CHANGED`, `NOT_CHANGED` or
`DIFF_CONSUMERS` used to leave the suite green, which is the "a lint that passes
with its own fixture emptied is testing nothing" failure in its own test file.
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
# for legibility and checked against a scan below, so a third skill that starts
# calling the verb cannot stay invisible to every test in this file. `control-flow`
# left when `analyse cfg` took its Incremental Path: the verb decides what to
# compile and the skill diffs nothing.
DIFF_CONSUMERS = ("loci-post-edit",)   # exec-trace runs the pair since T17

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


#: `data.functions.<group>`, wherever a document names one.
_FUNCTIONS_FIELD = re.compile(r"`?data\.functions(?:\.(added|removed|modified))?`?")


def _functions_groups(text: str) -> set[str]:
    """The `data.functions` groups a document names. `None` for a bare mention."""
    return {m.group(1) for m in _FUNCTIONS_FIELD.finditer(text)}


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


def test_every_diff_consumer_names_the_field_the_names_come_out_of():
    """A guard that scans nothing passes for the wrong reason. Every document that
    runs the verb has to send the model to `data.functions`; one that stops at
    `data.summary` has given it counts and no names, and the model then opens
    `diff_file` and re-derives the two filters the CLI already applied."""
    missing = [label for label, text in _docs()
               if not _functions_groups(text)]
    assert not missing, (
        "these documents invoke `loci elf diff` and never name `data.functions`, so "
        "the changed function names have no documented source: " + ", ".join(missing)
    )


def test_the_contract_names_all_three_groups_and_keeps_them_apart():
    """The consumers link here for the groups, not for a flat list: only a `modified`
    function has a Before to extract, an `added` one has none, and a `removed` one is
    not in the After at all. A contract naming `data.functions` and no group leaves
    the model to merge them."""
    text = CONTRACT.read_text(encoding="utf-8")
    groups = _functions_groups(text) - {None}
    assert groups == {"added", "removed", "modified"}, (
        f"the contract names {sorted(groups)}; all three groups have to be named, "
        f"because each is acted on differently"
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
    diff file at all and was reported here as a diff fence missing one."""
    offenders = [f"  {label}: spells diff.json instead of reading `data.diff_file`"
                 for label, text in _docs() for fence in _fences(text)
                 if "diff.json" in fence]
    assert not offenders, (
        "these fences name the diff file instead of taking its path from the "
        "envelope:\n" + "\n".join(sorted(set(offenders)))
    )


def test_every_diff_consumer_also_reads_the_counts():
    """The empty-result branch is decided on `data.summary` — a non-zero `removed`
    means functions were deleted, all-zero means the differ saw nothing. A document
    that names only the function lists documents an answer the prose cannot
    interpret: an empty `added`/`modified` pair is BOTH of those states."""
    offenders = [label for label, text in _docs() if "data.summary" not in text]
    assert not offenders, (
        "these documents run `loci elf diff` and never read `data.summary`, so the "
        "model cannot tell 'nothing changed' from 'everything was deleted': "
        + ", ".join(offenders)
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


def test_the_documented_entry_shape_is_the_one_the_cli_writes():
    """The sample entry in the contract is what a reader of `diff_file` matches
    against, and `symbol` is the key that has bitten: `function` and `name` are both
    absent, and a name read off an absent key is the string `null`, which `elf asm`
    accepts as a name matching nothing.

    Keyed on FIXTURE_ENTRIES so the fixture is answerable to the shipped prose rather
    than to itself — the failure this file's own docstring names."""
    text = CONTRACT.read_text(encoding="utf-8")
    samples = [f for f in _fences(text) if '"stt_type"' in f]
    assert len(samples) == 1, (
        f"the contract shows {len(samples)} diff-entry samples; a reader matching "
        f"the file against a sample needs exactly one")
    for key in sorted(set(FIXTURE_ENTRIES[0])):
        assert f'"{key}"' in samples[0], (
            f"the documented entry sample omits `{key}`, which every entry carries")
    for guess in ("function", "name"):
        assert f'"{guess}"' not in samples[0], (
            f"the documented entry sample names `{guess}`; the symbol is under "
            f"`symbol` and every other key yields null")


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


def test_the_contract_states_both_filters_the_cli_applies():
    """`data.functions` is filtered twice, and both filters are load-bearing: a
    `removed` symbol is gone from the After, and an `STT_OBJECT` symbol is a variable
    that `elf asm` answers `ok:true` with empty assembly for while `elf cfg` fails
    outright. The contract has to say the CLI applied them — a model that does not
    know the list is already filtered re-derives it from `diff_file`, which is the
    work this field exists to remove."""
    text = CONTRACT.read_text(encoding="utf-8")
    assert re.search(r"`data\.functions` (?:is already filtered|holds functions only)",
                     text), (
        "the contract does not state that `data.functions` excludes the variables "
        "the differ also diffs, so a model reads it as every changed symbol")
    assert "stt_type" in text, "the contract never names the type filter"
    assert re.search(r"`removed` function[\s\S]{0,120}?(?:own list|separate|apart)",
                     text), (
        "the contract does not say the `removed` group is kept apart, so a model "
        "sends a deleted function to `elf asm`, which cannot extract it")


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


# ── the fences, actually run ─────────────────────────────────────────────────
# What is left to run is the SHELL, not a filter: `elf diff` is one command whose
# whole answer is its envelope. The shapes `data.functions` groups are pinned where
# the code that groups them is, in the CLI repo's
# `tests/unit/test_elf_handlers.py`. What can still break here is the invocation —
# a dropped `\` on a continuation, an unquoted path — so the fence is run whole,
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
    _find_bash() is None, reason="bash required to run the documented fences")

_SUMMARY = '{"added":2,"removed":1,"modified":2,"unchanged":0}'
_FUNCTIONS = ('{"added":["new_fn"],"removed":["gone_fn"],'
              '"modified":["adc_read","spi_write"]}')
_STUB_LOCI = """#!/usr/bin/env bash
if [ "$1 $2" != "elf diff" ]; then echo "unexpected: $*" >&2; exit 9; fi
echo '{"ok":true,"data":{"summary":__SUMMARY__,"count":6,"functions":__FUNCTIONS__,"diff_file":"__OUT__"}}'
"""


def _diff_fences() -> list[tuple[str, str]]:
    return [(label, fence) for label, text in _docs() for fence in _fences(text)
            if "loci elf diff" in fence]


@requires_bash
def test_every_documented_diff_fence_runs_and_prints_the_envelope(tmp_path: Path):
    """The whole fence, verbatim, with only the placeholders substituted.

    One assertion, and it is the one that moved: the fence's entire output has to BE
    the envelope. A fence that captures it into a variable prints nothing, and the
    fields the prose then names are in a shell variable the next Bash call cannot
    reach — which is the defect todo 044 removed from every one of these documents."""
    out_json = tmp_path / "out dir" / "diff.json"
    out_json.parent.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "loci"
    stub.write_text(
        _STUB_LOCI.replace("__OUT__", _to_bash_path(out_json))
                  .replace("__SUMMARY__", _SUMMARY)
                  .replace("__FUNCTIONS__", _FUNCTIONS),
        encoding="utf-8", newline="\n")
    stub.chmod(0o755)

    env_path = f"{_to_bash_path(bindir)}:/usr/bin:/bin"
    fences = _diff_fences()
    assert fences, "no document runs `loci elf diff` in a fence any more"

    for label, fence in fences:
        script = (fence.replace("<PREV>", "before.o")
                       .replace("<OBJ>", "after.o")
                       .replace("<before>", "before.o")
                       .replace("<artifact>", "after.o")
                       .replace("<project_root>", _to_bash_path(tmp_path))
                       .replace("<turn-id>", "t1")
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
        printed = json.loads(proc.stdout)
        assert printed["data"]["functions"]["modified"] == ["adc_read", "spi_write"], (
            f"{label}: the fence did not put the envelope in front of the model; it "
            f"printed {proc.stdout!r}"
        )
