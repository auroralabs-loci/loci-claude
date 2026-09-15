"""Lint + execution: what the pipeline does when `elf diff` finds no changed function.

`loci elf diff` compares **masked instructions inside functions**. Its empty answer is
therefore about functions, and phase 09 made every consumer say so. Phase 11 is what
happens next: an empty function list gates the *metered* half — `elf asm` plus
`loci timing`, the only calls that spend the user's quota — and nothing else, because
four ordinary edits reach it having changed the compiled object. All four measured
against `arm-none-eabi-gcc` 15.2 (Cortex-M4, `-O1 -g`), and reproduced end to end by
`C:\\Playground\\loci-claude-tests\\repro-gate\\t-gate.sh`:

* `const uint32_t lut[8]` → `lut[64]` — +224 B ROM, `{0,0,0,0}`;
* a longer string literal — +44 B ROM, `{0,0,0,0}`;
* `uint32_t pool[16]` → `pool[4096]` — +16 320 B static RAM, `{0,0,0,0}`;
* `char scratch[64]` → `[128]` — worst-case frame 72 → 136 B, `{0,0,0,0}`.

So the contract carries a second recipe. Todo 044 made it two commands: `elf memmap`
already compared a pair, and `elf stack --comparing-elf` now does too, answering with
`data.frame_deltas` — the functions whose own frame moved, and only those. The
assembling that used to happen in a `jq` program in this document happens in the CLI,
where it is pinned by
`tests/unit/test_elf_handlers.py::test_stack_comparing_elf_reports_only_the_frames_that_moved`
in the CLI repo.

What this file pins is what only this repo can answer: that the recipe is those two
calls and nothing metered, that it passes no `--out-dir`, and that every field a
consumer is told to read is a field the pair actually returns — including the two
shapes that are not hypothetical. `symbol_deltas` comes back `null` from a real
`loci elf memmap` on some pairs, and a `removed` symbol's entry carries `size` and no
`delta` at all, so a document naming only `delta` puts a null in a report. And a frame
comparison against a CLI older than 0.1.107 reports every frame as the push size, so
both sides agree and `frame_deltas` comes back empty — which is why a refusal is a
distinct answer from an empty list, and why the prose below may never accept silence
as a pass.
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
POST_EDIT = SKILLS_DIR / "loci-post-edit" / "SKILL.md"

CONTRACT_ANCHOR = "beyond-the-diff"
GATE_STEP = "Step 2a"

_FENCE = re.compile(r"^[ \t]*```[a-z]*\n(.*?)^[ \t]*```$", re.S | re.M)


def _fences(text: str) -> list[str]:
    return _FENCE.findall(text)


def _section(text: str, heading_prefix: str) -> str:
    """The document from the heading starting with `heading_prefix` to the next
    heading at the same level. Used to assert what a step does *not* say, which is
    only meaningful when the slice really is that step.

    **Fence-aware, and that is the whole subtlety.** A step whose report template is a
    fenced block containing `## Post-Edit: …` ends, to a fence-blind reader, at its own
    example — so the slice was 19 lines of a 70-line step, and the two tests asserting
    that this step invokes nothing metered were passing on a slice that stopped before
    every command it has. Caught by a mutation campaign, not by review.
    """
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.startswith(heading_prefix)),
                 None)
    assert start is not None, f"no heading starting {heading_prefix!r}"
    level = len(lines[start]) - len(lines[start].lstrip("#"))
    in_fence = False
    for i in range(start + 1, len(lines)):
        ln = lines[i]
        if ln.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if ln.startswith("#") and len(ln) - len(ln.lstrip("#")) <= level:
            return "\n".join(lines[start:i])
    return "\n".join(lines[start:])


def _denied_before(text: str, pos: int) -> bool:
    """Is the mention at `pos` denied by its own sentence, *before* the mention?

    Scoped to the text preceding the token, because a negation keyword anywhere on the
    line is not an exemption — this repo has already shipped a lint that let
    `# never mind the sidecar` through on exactly that basis. "No `loci timing` call is
    made on this branch" is denied; "Run `loci elf asm` first, then the fence in *What
    the differ does not answer*" is not, though both lines carry a "not"."""
    sentence_start = 0
    for m in re.finditer(r"[.!?:]\s|\n\s*\n", text[:pos]):
        sentence_start = m.end()
    return bool(re.search(r"\b(?:no|not|never|neither|nor|skipped|without)\b",
                          text[sentence_start:pos], re.I))


def _pair_fences() -> list[str]:
    """Fences that compare the pair's footprint and frames — the phase 11 recipe."""
    return [f for f in _fences(CONTRACT.read_text(encoding="utf-8"))
            if "elf memmap" in f and "elf stack" in f]


# ── the recipe exists, once, where the consumers are sent ────────────────────

def test_the_contract_carries_exactly_one_pair_recipe():
    """A lint that scans nothing passes for the wrong reason. Two copies would be
    worse than one: the skills link to this anchor, and a second recipe elsewhere is
    the one that would drift."""
    found = _pair_fences()
    assert len(found) == 1, (
        f"expected exactly one footprint+frame recipe in the contract, found "
        f"{len(found)}"
    )
    assert f'id="{CONTRACT_ANCHOR}"' in CONTRACT.read_text(encoding="utf-8"), (
        f"the recipe has no `{CONTRACT_ANCHOR}` anchor, so nothing can link to it"
    )


def test_the_recipe_runs_both_halves_and_neither_is_metered():
    """Two questions, two verbs, and the whole point is that neither costs quota.
    `loci elf asm` / `loci timing` appearing here would put the gate's own escape
    hatch on the metered path."""
    fence = _pair_fences()[0]
    assert "loci elf memmap --elf" in fence
    assert "loci elf stack --elf" in fence
    assert fence.count("--comparing-elf") == 2, (
        "both halves compare a PAIR: two `elf stack` runs answer a different "
        "question, and reading their two analyses back is the work `--comparing-elf` "
        "exists to remove"
    )
    for metered in ("loci timing", "loci elf asm"):
        assert metered not in fence, f"the unmetered recipe calls `{metered}`"
    assert "--out-dir" not in fence, (
        "the recipe passes `--out-dir`; the CLI keys each dump directory on the "
        "artifact's full path, and a Before and an After sharing a basename then "
        "collide"
    )


def test_the_recipe_names_every_field_its_readers_are_sent_to():
    """The prose after the recipe is the whole instruction now — there is no jq
    projecting the answer into labelled lines. A field named in the reading rules and
    absent from the pair's envelopes is a model reading `null`."""
    section = _section(CONTRACT.read_text(encoding="utf-8"),
                       "## What the differ does not answer")
    for field in ("data.summary_delta", "rom_total", "ram_static_total",
                  "data.symbol_deltas", "data.frame_deltas"):
        assert field in section, (
            f"the recipe's reading rules never name `{field}`, so the model has no "
            f"documented source for that half of the answer")


def test_the_reading_rules_keep_the_three_traps_the_fields_carry():
    """Each was a real wrong report, and none of them is visible in the field name.

    A `removed` symbol's entry has `size` and no `delta`; `symbol_deltas` itself
    comes back `null`; and a function missing from one side reports `null`, which is
    not a zero frame."""
    section = _section(CONTRACT.read_text(encoding="utf-8"),
                       "## What the differ does not answer")
    assert re.search(r"`size`[^.]{0,80}no `delta`|no `delta`[^.]{0,80}`size`", section), (
        "the rules do not say an arrived/departed symbol carries `size` and no "
        "`delta`, so a report quoting `delta` prints a null")
    assert re.search(r"absent or `null`|`null`[^.]{0,40}absent", section), (
        "the rules do not say `symbol_deltas` is sometimes absent or null, so its "
        "absence reads as contradicting a non-zero total")
    assert re.search(r"`null` on either\s+side is \*\*not a zero frame\*\*", section), (
        "the rules do not say a `null` frame means the function is not in that "
        "artifact, so it reads as a frame that shrank to nothing")


def test_the_recipe_reads_no_variable_it_does_not_set():
    """A harness that supplies what the shipped text lacks defeats "run the fence" —
    `/loci:bug-report`'s recipe read a `$PROJECT_ROOT` the skill assigned nowhere, and six
    tests passed over a program that reports nothing for every project. The
    environment the model has is `loci` and the placeholders."""
    fence = _pair_fences()[0]
    assigned = set(re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)=", fence, re.M))
    read = set(re.findall(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)", fence))
    unset = read - assigned - {"PATH"}
    assert not unset, (
        f"the recipe reads {sorted(unset)}, which nothing in it sets and no skill "
        f"assigns; it will run against empty strings"
    )


def test_the_recipe_captures_nothing_into_a_shell_variable():
    """Each fenced block is its own Bash call. A recipe that captures its envelopes
    puts both answers somewhere the next fence cannot reach — and the reading rules
    below it then name fields nothing printed."""
    fence = _pair_fences()[0]
    assert "=$(" not in fence, (
        "the recipe captures an envelope instead of letting it print; the fields the "
        "rules below it name are then in a shell variable and not in the transcript"
    )


# ── the step that consumes it ────────────────────────────────────────────────

def test_the_gate_slice_is_the_whole_step():
    """Every assertion below is about what a step slice does **not** contain, and a
    slice that stops early contains nothing. The step ends with the sentence about the
    footer; if that has moved, the slicing is wrong and the two tests after this one
    are worthless rather than failing."""
    gate = _section(POST_EDIT.read_text(encoding="utf-8"), f"## {GATE_STEP}")
    assert len(_fences(gate)) >= 3, (
        f"{GATE_STEP} should carry both report templates and the escalation call; the "
        f"slice has {len(_fences(gate))} fenced blocks, so it stops short of them"
    )
    assert "footer" in gate, f"the {GATE_STEP} slice ends before its own footer rule"


def test_post_edit_routes_an_empty_function_list_to_the_gate_step():
    """The gate is a *route*, not a mood. Step 2's empty branch has to name the step
    it goes to, or the model falls through to Step 3 and extracts assembly for an
    empty function list — which is how `--functions ""` reaches `elf asm`.

    Asserted as a route — one sentence naming **both** destinations — and not as a
    mention, because the step mentions `Step 2a` twice and a mutation deleting the
    routing sentence left the other mention behind, green. Two notes sharing a phrase
    is a vacuous assertion; the token that differs is `Step 3`."""
    text = POST_EDIT.read_text(encoding="utf-8")
    step2 = _section(text, "## Step 2: ")
    sentences = re.split(r"[.!?:]\s|\n\s*\n", step2)
    routed = [s for s in sentences if GATE_STEP in s and "Step 3" in s]
    assert routed, (
        f"no sentence in Step 2 sends an empty function list to {GATE_STEP} rather "
        f"than Step 3; mentioning {GATE_STEP} is not routing to it"
    )
    assert f"## {GATE_STEP}" in text, f"{GATE_STEP} itself is not a step in this skill"


def test_the_gate_step_spends_nothing_and_delegates_the_measurement():
    """What makes this branch a gate is that it reaches neither metered call. What
    keeps it from being a silent skip is that it links to the recipe rather than
    describing it — one statement, in the file every consumer reads."""
    gate = _section(POST_EDIT.read_text(encoding="utf-8"), f"## {GATE_STEP}")
    for fence in _fences(gate):
        for metered in ("loci timing", "loci elf asm"):
            assert metered not in fence, (
                f"{GATE_STEP} runs `{metered}` in a fence; the branch exists to skip it"
            )
    for metered in ("loci timing", "loci elf asm"):
        for m in re.finditer(re.escape(metered), gate):
            assert _denied_before(gate, m.start()), (
                f"{GATE_STEP} names `{metered}` without denying it: "
                f"{gate[max(0, m.start() - 60):m.start() + 40]!r}"
            )
    assert f"#{CONTRACT_ANCHOR}" in gate, (
        f"{GATE_STEP} does not link the contract's recipe, so it has to restate it"
    )


def test_the_gate_step_never_sends_this_units_footprint_to_the_contract():
    """`rom_size` / `ram_size` bounds are firmware-scale. A translation unit's own
    361-byte ROM total judged against a 512 KB budget is a green row on a claim
    nobody made — the same shape as `data.unjudged` rendered as a pass.

    Asserted twice, because the fence half alone is not the instruction: deleting the
    whole prose rule — the sentence carrying "never send them to `loci contract check`"
    — left this green while removing the only thing a model reads."""
    gate = _section(POST_EDIT.read_text(encoding="utf-8"), f"## {GATE_STEP}")
    for fence in _fences(gate):
        assert "contract check" not in fence, (
            "the gate step pipes a measurement into `loci contract check`; this "
            "unit's ROM/RAM is not what the contract's bounds are about"
        )
    # Fenced blocks removed, not indented lines: the rule is a hard-wrapped bullet whose
    # continuation is indented, and dropping indentation dropped the sentence itself.
    prose = _FENCE.sub("", gate)
    assert re.search(r"never send.{0,40}contract check", prose, re.S | re.I), (
        "the step no longer forbids sending this unit's ROM/RAM to `contract check` — "
        "the fence being clean today is not the instruction a model follows tomorrow")


def test_the_gate_step_states_its_own_scope_and_what_it_skips():
    """Two sentences carry the whole "this is a gate" claim, and both were unpinned: a
    campaign deleting "Steps 3, 4, 4a and 4b are skipped in full" and widening
    "Case A only" to "Always" left the suite green. Without the first a model runs Step
    2a and then falls through to Step 3 with an empty function list — `--functions ""`
    reaching `elf asm`, the failure the routing test exists to prevent, arriving by the
    route that test does not cover. Without the second, Case B — which has no `PREV` —
    is routed into a fence that compares a pair."""
    gate = _section(POST_EDIT.read_text(encoding="utf-8"), f"## {GATE_STEP}")
    assert re.search(r"Case A only", gate), (
        f"{GATE_STEP} no longer scopes itself to Case A, so a run with no baseline can "
        f"reach a fence that needs one")
    skipped = re.search(r"Steps? [^.\n]*\bskipped\b[^.\n]*", gate)
    assert skipped, f"{GATE_STEP} never says which steps it skips"
    for step in ("3", "4"):
        assert step in skipped.group(0), (
            f"{GATE_STEP}'s skip sentence does not name Step {step}: "
            f"{skipped.group(0)!r}")


def test_the_quiet_answer_still_names_the_gap_all_four_checks_have():
    """The pair comparison narrows the blind spot; it does not close it. A changed
    constant compiles to the same instruction at the same size — measured,
    `v + 4928u` → `v + 19840u` gives `{0,0,0,0}`, ROM 157 → 157, no frame delta — so a
    template that says "nothing changed" is wrong for exactly that edit. An earlier
    draft of this step said the footprint and frame checks were "what make this a
    measurement rather than an inference", which is true of three causes and false of
    the fourth.

    The loop trip-count check (a fourth comparison, added with the CFG's `iters`
    annotations) removes ONE case from the blind spot — a loop bound the trip count can
    be derived from — and only that one. A derivable bound is now measured; an
    underivable one, a threshold, a timeout and a retuned `const` table are all still
    invisible, so the caveat stays and must still say that every check misses them."""
    gate = _section(POST_EDIT.read_text(encoding="utf-8"), f"## {GATE_STEP}")
    quiet = [f for f in _fences(gate) if "no measurable change" in f]
    assert len(quiet) == 1, (
        f"the no-change report template is not where this test can see it "
        f"({len(quiet)} candidates)")
    assert re.search(r"\bconstant", quiet[0], re.I), (
        "the quiet template does not mention a constant-only edit, so it reports a "
        "blind spot as an all-clear:\n" + quiet[0])
    assert re.search(r"\ball (three|four)\b|\bnone of\b|\binvisible to all\b",
                     quiet[0], re.I), (
        "the quiet template names the constant case but not that every check misses "
        "it, which reads as 'the differ missed it and the others caught it':\n"
        + quiet[0])
    # Both review lenses reported this independently: the gap is not "constants". A
    # `const` table or an initialised array whose VALUES change at unchanged size is a
    # second, mechanically different family — the differ never reads `.rodata` and
    # `memmap` compares sizes — and it is the commoner embedded edit of the two. A
    # template naming only scalar constants tells a user who retuned a lookup table
    # that the caveat does not describe their edit.
    assert re.search(r"\btable\b|\barray\b|initiali[sz]ed", quiet[0], re.I), (
        "the quiet template's blind-spot paragraph names only constants in code, so a "
        "changed lookup table or initialiser reads as covered by the all-clear:\n"
        + quiet[0])


def test_the_gate_step_separates_a_deletion_from_the_quiet_answer():
    """A delete-only edit reaches this branch with an EMPTY changed-function list —
    `removed` entries are filtered out of it, because a deleted function has no After to
    extract. Measured: removing one leaf gives `{"added":0,"removed":1,"modified":0}`
    and an empty list. Skipping the metered half is right; calling it "no function
    changed" is a lost measurement wearing the words of a clean run."""
    gate = _section(POST_EDIT.read_text(encoding="utf-8"), f"## {GATE_STEP}")
    assert "data.functions.removed" in gate, (
        f"{GATE_STEP} does not tell the model where the deleted function names are — "
        f"the field is the instruction, and 'mentions removed' is not")
    assert "summary.removed" in gate, (
        f"{GATE_STEP} never reads `data.summary.removed`, so a deletion cannot be told "
        f"from the quiet answer at all")
    quiet_branch = gate.split("**Nothing moved**", 1)
    assert len(quiet_branch) == 2, "the quiet-answer branch is not where this can see it"
    # The CONDITION is the first sentence, not the paragraph: the sentences after it
    # explain why each clause is there and mention `NOTE` and `removed` themselves, so
    # a slice of the whole branch is satisfied by the explanation of a clause that was
    # just deleted. Both mutations exploited exactly that.
    condition = re.split(r"\.\s", quiet_branch[1].split("```", 1)[0], maxsplit=1)[0]
    assert "removed" in condition, (
        "the quiet-answer branch's condition does not require `removed` to be zero, so "
        f"a delete-only edit satisfies it:\n{condition}")
    # A refusal means a check did not run, and an empty `frame_deltas` then proves
    # nothing — so the quiet condition has to exclude it too, or a failed `elf stack`
    # (a stripped baseline, an expired session) reads as "no frame moved".
    assert re.search(r"ok:true|answered", condition), (
        "the quiet-answer branch's condition ignores whether the two calls answered, "
        f"so a check that never ran satisfies it:\n{condition}")


def test_the_gate_step_states_the_frame_instruments_limit():
    """A comparison of two numbers from a producer that returns the same wrong number
    for both sides answers "unchanged" for every frame change there is. Measured: a
    528-byte frame reads as 4 B on CLI 0.1.102. The version the fix landed in is the
    fact a reader needs, so it has to be *in* the documents, not in this test."""
    docs = [CONTRACT.read_text(encoding="utf-8"),
            POST_EDIT.read_text(encoding="utf-8")]
    assert all("0.1.107" in d for d in docs), (
        "the frame-sizing threshold is missing from the contract or the skill, so an "
        "install that cannot answer the frame question reports it as unchanged"
    )


# ── the recipe, actually run ─────────────────────────────────────────────────
# Two commands and no filter, so what is left to run is the SHELL: a dropped `\` on
# a continuation, an unquoted path, a verb that is not the one the prose names. The
# numbers the pair reports are the CLI's, and are pinned there.

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
    _find_bash() is None, reason="bash required to run the documented recipe")


#: the basename both artifacts share, so the out-dir collision this recipe has to avoid
#: is the DEFAULT state of the fixture rather than a special case. The header route
#: produces exactly this: a reconstructed `turns/<key>/obj/<slot>/src/blink.o` against
#: `.loci-build/<target>/src/blink.o`.
SHARED_BASENAME = "blink.o"

# The stub answers from its ARGUMENTS, not from a fixed file: it records the pair each
# verb was given, in order, so an inverted `--elf` / `--comparing-elf` — which flips
# the sign of every delta — fails loudly rather than passing on a fixed envelope.
_STUB = r"""#!/usr/bin/env bash
verb="$1 $2"; shift 2
elf=""; comparing=""
while [ $# -gt 0 ]; do
    case "$1" in
        --elf) elf="$2"; shift 2 ;;
        --comparing-elf) comparing="$2"; shift 2 ;;
        --out-dir) echo "the recipe passed --out-dir" >&2; exit 8 ;;
        *) shift ;;
    esac
done
case "$verb" in
    "elf memmap"|"elf stack") ;;
    *) echo "unexpected call: $verb" >&2; exit 9 ;;
esac
[ -n "$elf" ] && [ -n "$comparing" ] || { echo "$verb got no pair" >&2; exit 7; }
printf '%s\t%s\t%s\n' "$verb" "$(basename "$(dirname "$elf")")" \
    "$(basename "$(dirname "$comparing")")"
"""


@requires_bash
def test_the_shipped_recipe_runs_and_asks_both_verbs_for_the_same_ordered_pair(
        tmp_path: Path):
    """Run whole, from a directory with a space in its name that is not where the
    artifacts are, with the Before and the After **sharing a basename** — the two
    CWD/quoting traps this repo has already paid for."""
    before = tmp_path / "art dir" / "before" / SHARED_BASENAME
    after = tmp_path / "art dir" / "after" / SHARED_BASENAME
    for path in (before, after):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x7fELF stub")

    bindir = tmp_path / "bin dir"
    bindir.mkdir()
    stub = bindir / "loci"
    stub.write_text(_STUB, encoding="utf-8", newline="\n")
    stub.chmod(0o755)

    elsewhere = tmp_path / "some where else"
    elsewhere.mkdir()
    root = tmp_path / "proj root"
    root.mkdir()

    script = (_pair_fences()[0]
              .replace("<PREV>", _to_bash_path(before))
              .replace("<OBJ>", _to_bash_path(after))
              .replace("<project_root>", _to_bash_path(root))
              .replace("<turn-id>", "t1")
              .replace("<loci_target>", "armv7e-m"))
    proc = subprocess.run(
        [_find_bash(), "-s"],
        input=f'export PATH="{_to_bash_path(bindir)}:/usr/bin:/bin"\nset -e\n{script}',
        capture_output=True, text=True, encoding="utf-8", cwd=elsewhere,
    )
    assert proc.returncode == 0, (
        f"the shipped recipe exits {proc.returncode}\n{proc.stderr}")
    assert not proc.stderr.strip(), (
        f"the recipe writes to stderr, which a model reads as a failure:\n"
        f"{proc.stderr}")
    assert [ln for ln in proc.stdout.splitlines() if ln.strip()] == [
        "elf memmap\tbefore\tafter",
        "elf stack\tbefore\tafter",
    ], (
        "both verbs must be asked for the same pair, Before first — swapping the two "
        f"inverts the sign of every delta. Got:\n{proc.stdout!r}")


@requires_bash
def test_the_stub_rejects_a_call_the_recipe_should_not_make(tmp_path: Path):
    """The positive control. The assertion above reads "the recipe printed the right
    lines", which it could not tell from a recipe whose commands were never the ones
    the contract documents — the stub would have answered anyway. Here the stub is
    loud, and this proves it."""
    before = tmp_path / "art dir" / "before" / SHARED_BASENAME
    after = tmp_path / "art dir" / "after" / SHARED_BASENAME
    for path in (before, after):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x7fELF stub")
    bindir = tmp_path / "bin ctl"
    bindir.mkdir()
    stub = bindir / "loci"
    stub.write_text(_STUB, encoding="utf-8", newline="\n")
    stub.chmod(0o755)

    for label, mangle in (
            ("an undocumented verb", lambda f: f.replace("elf memmap", "elf sections")),
            ("a dropped comparison", lambda f: f.replace("--comparing-elf", "--ignore")),
    ):
        script = (mangle(_pair_fences()[0])
                  .replace("<PREV>", _to_bash_path(before))
                  .replace("<OBJ>", _to_bash_path(after))
                  .replace("<project_root>", _to_bash_path(tmp_path))
                  .replace("<turn-id>", "t1")
                  .replace("<loci_target>", "armv7e-m"))
        proc = subprocess.run(
            [_find_bash(), "-s"],
            input=f'export PATH="{_to_bash_path(bindir)}:/usr/bin:/bin"\n{script}',
            capture_output=True, text=True, encoding="utf-8", cwd=tmp_path,
        )
        assert proc.stderr.strip(), (
            f"the stub answered {label} without complaint, so the execution test "
            f"above proves nothing:\n{proc.stdout}")
