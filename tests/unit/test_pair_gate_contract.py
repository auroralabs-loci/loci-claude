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

So the contract carries a second recipe, and this file pins it the way the diff recipes
are pinned: the fence is **extracted from the shipped document and run**, against a stub
`loci`, from a directory with a space in it that is not where the artifacts are. A regex
can say the recipe still mentions `summary_delta`; only running it can say the recipe
still prints a number.

The expectations are **derived** from the same envelopes the stub serves, by building
the lines in Python rather than by listing them — a hand-written expectation can be
emptied by the same mutation that empties the fixture, which is the failure this repo's
own lints have hit twice.

Two shapes here are not hypothetical. `symbol_deltas` comes back `null` from a real
`loci elf memmap` on some pairs — the recipe guards that at two levels, and note that the
two guards are collectively necessary but individually removable (`null | (.rom // [])`
is already `[]`), so no test here pins either one alone; they are defence, not coverage.
And a frame
comparison against a CLI older than 0.1.107 reports every frame as the push size, so
both sides agree and "unchanged" means nothing — which is why a `NOTE` is a distinct
answer from a zero delta, and why the tests below never accept silence as a pass.
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
    assert "loci elf memmap --elf" in fence and "--comparing-elf" in fence
    assert fence.count("loci elf stack --elf") == 2, (
        "the frame half compares two artifacts; one call cannot answer it"
    )
    for metered in ("loci timing", "loci elf asm"):
        assert metered not in fence, f"the unmetered recipe calls `{metered}`"


def test_the_recipe_reads_no_variable_it_does_not_set():
    """A harness that supplies what the shipped text lacks defeats "run the fence" —
    `/bug-report`'s recipe read a `$PROJECT_ROOT` the skill assigned nowhere, and six
    tests passed over a program that reports nothing for every project. The
    environment the model has is `loci`, `jq` and the placeholders."""
    fence = _pair_fences()[0]
    assigned = set(re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)=", fence, re.M))
    # Single-quoted spans are the jq programs, and jq has `$s` / `$x` variables of its
    # own that the shell never sees. Scanning them as shell variables reported five
    # unset ones on a recipe that has none.
    shell_only = re.sub(r"'[^']*'", "", fence)
    read = set(re.findall(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)", shell_only))
    unset = read - assigned - {"PATH"}
    assert not unset, (
        f"the recipe reads {sorted(unset)}, which nothing in it sets and no skill "
        f"assigns; it will run against empty strings"
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
    assert 'status == "removed"' in gate, (
        f"{GATE_STEP} does not tell the model how to name the deleted functions — the "
        f"filter is the instruction, and 'mentions removed' is not")
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
    # A `NOTE` means a check did not run, and an absent `FRAME` line then proves
    # nothing — so the quiet condition has to exclude it too, or a failed `elf stack`
    # (a stripped baseline, an expired session) reads as "no frame moved".
    assert "NOTE" in condition, (
        "the quiet-answer branch's condition ignores `NOTE` lines, so a check that "
        f"never ran satisfies it:\n{condition}")


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
    reason="bash and jq required to run the documented fence",
)


def _memmap(rom: tuple[int, int], ram: tuple[int, int],
            symbols: list[dict] | None = None, ram_symbols: list[dict] | None = None,
            symbol_deltas_null: bool = False) -> dict:
    def totals(pair: tuple[int, int]) -> dict:
        return {"base": pair[0], "current": pair[1], "delta": pair[1] - pair[0],
                "delta_pct": 0.0}
    data: dict = {
        "mode": "delta",
        "summary_delta": {"rom_total": totals(rom), "ram_static_total": totals(ram)},
        "symbol_deltas": None if symbol_deltas_null else {
            "rom": symbols or [], "ram": ram_symbols or []},
    }
    return {"ok": True, "data": data}


def _stack(frames: dict[str, int]) -> dict:
    # `worst_case_depth` is deliberately NOT equal to `frame_size`. With the two set to
    # the same number no test could tell which key the recipe reads, and rewriting the
    # join to `worst_case_depth` — the one field choice the contract calls out as
    # load-bearing — survived a campaign against the version that did.
    return {fn: {"frame_size": size, "worst_case_depth": size + 1000, "warnings": []}
            for fn, size in frames.items()}


def _expected(mm: dict, prev: dict[str, int] | None,
              curr: dict[str, int] | None) -> list[str]:
    """What the recipe must print, built independently of the jq that prints it."""
    out: list[str] = []
    if mm.get("ok"):
        s = mm["data"]["summary_delta"]
        for label, key in (("ROM", "rom_total"), ("RAM", "ram_static_total")):
            t = s[key]
            out.append(f"{label}\t{t['base']}\t{t['current']}\t{t['delta']}")
        deltas = mm["data"].get("symbol_deltas") or {}
        for sym in (deltas.get("rom") or []) + (deltas.get("ram") or []):
            # A changed symbol carries `delta`; one that arrived or went carries `size`
            # and no `delta` at all — measured against a real `elf memmap`, and the
            # reason the recipe cannot simply print `\(.delta)`.
            bytes_ = sym.get("delta", sym.get("size", 0))
            out.append(f"SYM\t{sym['name']}\t{sym['status']}\t{bytes_}")
    else:
        out.append(f"NOTE\tfootprint not compared: {mm['error']['message']}")
    if prev is not None and curr is not None:
        for fn in sorted(set(prev) | set(curr)):
            # `-`, not 0: a function absent from one artifact has no frame there, and a
            # zero would read as "its frame shrank to nothing".
            before = prev.get(fn, "-")
            after = curr.get(fn, "-")
            if before != after:
                out.append(f"FRAME\t{fn}\t{before}\t{after}")
    return out


# The stub answers from its ARGUMENTS, not from a fixed file, and both halves are
# deliberately faithful to a real CLI in the ways that have bitten:
#
# * `elf memmap` serves a file named for the (--elf, --comparing-elf) pair, so swapping
#   the two — which inverts the sign of every delta, turning "+224 B ROM" into "-224" —
#   misses and fails loudly. A stub that `cat`s one fixed envelope cannot see that, and
#   that mutation survived the version of this file that had one.
# * `elf stack` writes its analysis INTO `--out-dir` and reports that path, falling back
#   to the released CLI's bare-stem default (`elf/<stem>/`) when the flag is absent. That
#   fallback is the point: every released CLI keys the default on the stem, so a Before
#   and an After sharing a basename collide, the second overwrites the first, and no
#   FRAME line can print. Dropping `--out-dir` from the recipe reproduces that here.
_STUB = r"""#!/usr/bin/env bash
verb="$1 $2"; shift 2
elf=""; comparing=""; outdir=""
while [ $# -gt 0 ]; do
    case "$1" in
        --elf|--comparing-elf|--out-dir)
            [ $# -ge 2 ] || { echo "$1 with no value" >&2; exit 8; }
            case "$1" in
                --elf) elf="$2" ;;
                --comparing-elf) comparing="$2" ;;
                --out-dir) outdir="$2" ;;
            esac
            shift 2 ;;
        *) shift ;;
    esac
done
side() { basename "$(dirname "$1")"; }     # the two artifacts share a basename
if [ "$verb" = "elf memmap" ]; then
    f="__DIR__/memmap-$(side "$elf")-$(side "$comparing").json"
    [ -f "$f" ] || { echo "no memmap fixture for ($elf, $comparing)" >&2; exit 7; }
    cat "$f"
    exit 0
fi
if [ "$verb" = "elf stack" ]; then
    err="__DIR__/stackerr-$(side "$elf").json"
    [ -f "$err" ] && { cat "$err"; exit 0; }
    src="__DIR__/frames-$(side "$elf").json"
    [ -f "$src" ] || { echo "no frame fixture for $elf" >&2; exit 7; }
    dir="${outdir:-__DIR__/elf/$(basename "${elf%.*}")}"
    mkdir -p "$dir"
    cp "$src" "$dir/stack-analysis.json"
    printf '{"ok":true,"data":{"stack_analysis_file":"%s/stack-analysis.json"}}\n' "$dir"
    exit 0
fi
echo "unexpected call: $verb $*" >&2
exit 9
"""


#: the basename both artifacts share, so the out-dir collision this recipe has to avoid
#: is the DEFAULT state of the fixture rather than a special case. The header route
#: produces exactly this: a reconstructed `turns/<key>/obj/<slot>/src/blink.o` against
#: `.loci-build/<target>/src/blink.o`.
SHARED_BASENAME = "blink.o"


def _run_fence(tmp_path: Path, mm: dict,
               prev_frames: dict[str, int] | None, curr_frames: dict[str, int] | None,
               *, prev_error: str | None = None, curr_error: str | None = None,
               ) -> subprocess.CompletedProcess:
    """Run the shipped fence with a stub `loci`, from a directory with a space in its
    name that is **not** where the artifacts are — the two CWD/quoting traps this repo
    has already paid for — with the Before and the After **sharing a basename**."""
    stub_dir = tmp_path / "stub dir"
    stub_dir.mkdir()
    before = tmp_path / "art dir" / "before" / SHARED_BASENAME
    after = tmp_path / "art dir" / "after" / SHARED_BASENAME
    for p in (before, after):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\x7fELF stub")
    # Keyed on (before, after) IN ORDER: swapping `--elf` and `--comparing-elf` inverts
    # the sign of every delta, and a stub serving one fixed envelope cannot see it.
    # A `str` is written verbatim so a caller can serve raw bytes — `""` is the
    # produced-no-envelope case, which must reach the recipe as genuinely empty stdout.
    (stub_dir / "memmap-before-after.json").write_text(
        mm if isinstance(mm, str) else json.dumps(mm), encoding="utf-8")
    for side, frames, error in (("before", prev_frames, prev_error),
                                ("after", curr_frames, curr_error)):
        if error is not None:
            (stub_dir / f"stackerr-{side}.json").write_text(
                json.dumps({"ok": False, "error": {"message": error}}), encoding="utf-8")
        elif frames is not None:
            (stub_dir / f"frames-{side}.json").write_text(
                json.dumps(_stack(frames)), encoding="utf-8")

    bindir = tmp_path / "bin dir"
    bindir.mkdir()
    stub = bindir / "loci"
    stub.write_text(_STUB.replace("__DIR__", _to_bash_path(stub_dir)),
                    encoding="utf-8", newline="\n")
    stub.chmod(0o755)

    elsewhere = tmp_path / "some where else"
    elsewhere.mkdir()
    root = tmp_path / "proj root"
    root.mkdir()

    fence = _pair_fences()[0]
    script = (fence.replace("<PREV>", _to_bash_path(before))
                   .replace("<OBJ>", _to_bash_path(after))
                   .replace("<project_root>", _to_bash_path(root))
                   .replace("<loci_target>", "armv7e-m"))
    jq_dir = _to_bash_path(Path(shutil.which("jq")).parent)
    path = f"{_to_bash_path(bindir)}:{jq_dir}:/usr/bin:/bin"
    return subprocess.run(
        [_find_bash(), "-s"],
        input=f'export PATH="{path}"\n{script}',
        capture_output=True, text=True, encoding="utf-8", cwd=elsewhere,
    )


# Each case: memmap envelope, the two frame maps (None = that call failed), and what
# the branch is *for*. Every one of the first four is a real measurement.
CASES = {
    "rodata table grew": (
        _memmap((137, 361), (0, 0), symbols=[{"name": "lut", "status": "changed",
                                              "delta": 224}]),
        {"frame_user": 72}, {"frame_user": 72}),
    "string literal grew": (
        _memmap((137, 181), (0, 0)), {"banner": 8}, {"banner": 8}),
    "static array grew": (
        _memmap((24, 24), (64, 16384),
                ram_symbols=[{"name": "pool", "status": "changed", "delta": 16320}]),
        {"pool_fill": 8}, {"pool_fill": 8}),
    "frame grew": (
        _memmap((137, 137), (0, 0)), {"frame_user": 72, "banner": 8},
        {"frame_user": 136, "banner": 8}),
    "nothing moved": (
        _memmap((137, 137), (0, 0)), {"frame_user": 72}, {"frame_user": 72}),
    "symbol_deltas is null": (
        _memmap((24, 280), (0, 0), symbol_deltas_null=True),
        {"f": 0}, {"f": 0}),
    "a function was added and removed": (
        _memmap((100, 120), (0, 0)), {"gone": 40}, {"fresh": 24}),
    # A removed symbol's entry has `size` and NO `delta`. Printing `\(.delta)` puts the
    # literal `null` in the report — the same shape phase 09 removed from the diff
    # recipes, reached here through a different producer.
    "a symbol went, so its entry has size and no delta": (
        {"ok": True, "data": {
            "summary_delta": {
                "rom_total": {"base": 157, "current": 149, "delta": -8},
                "ram_static_total": {"base": 64, "current": 64, "delta": 0}},
            "symbol_deltas": {"rom": [{"name": "scaled", "status": "removed",
                                       "size": 6},
                                      {"name": "banner", "status": "changed",
                                       "base_size": 16, "current_size": 14,
                                       "delta": -2}],
                              "ram": []}}},
        {"scaled": 0, "banner": 8}, {"banner": 8}),
}


def test_no_case_expects_a_null_in_the_report():
    """Belt and braces on the shape above: `jq -r` prints a missing key as the literal
    string `null`, and a model pasting `SYM scaled removed null` into a report has
    written a number nobody produced."""
    for name, (mm, prev, curr) in CASES.items():
        for line in _expected(mm, prev, curr):
            assert "null" not in line, f"{name}: expectation itself carries a null: {line}"


@requires_bash
@pytest.mark.parametrize("case", sorted(CASES))
def test_the_shipped_fence_prints_what_the_contract_says_it_prints(
        tmp_path: Path, case: str):
    mm, prev, curr = CASES[case]
    proc = _run_fence(tmp_path, mm, prev, curr)
    assert proc.returncode == 0, (
        f"{case}: the shipped fence exits {proc.returncode}\n{proc.stderr}")
    assert not proc.stderr.strip(), (
        f"{case}: the fence writes to stderr, which a model reads as a failure:\n"
        f"{proc.stderr}")
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert lines == _expected(mm, prev, curr), (
        f"{case}: the fence printed\n  {lines}\nexpected\n  {_expected(mm, prev, curr)}"
    )


@requires_bash
def test_an_unchanged_pair_prints_two_zero_deltas_rather_than_nothing(tmp_path: Path):
    """The all-quiet answer is the one the skill turns into "the edit changed
    nothing", so it has to be *stated*. A fence that printed nothing at all would be
    indistinguishable from a fence that crashed before its first command, and the
    report either way would be the silent skip this plan exists to remove."""
    mm, prev, curr = CASES["nothing moved"]
    proc = _run_fence(tmp_path, mm, prev, curr)
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 2 and all(ln.endswith("\t0") for ln in lines), (
        f"an unchanged pair must still print both totals with a zero delta: {lines}")
    assert not any(ln.startswith("FRAME") for ln in lines)


@requires_bash
@pytest.mark.parametrize("failing", ["before", "after"])
def test_a_failed_frame_call_is_a_note_and_not_an_unchanged_answer(
        tmp_path: Path, failing: str):
    """The distinction the whole branch rests on. A `NOTE` says nobody looked; an
    absent `FRAME` line says somebody looked and nothing moved. Collapsing the two
    hands back "frames unchanged" for a comparison that never ran — and the CLI this
    reaches first is the one that cannot size a frame at all."""
    mm, _prev, _curr = CASES["nothing moved"]
    proc = _run_fence(
        tmp_path, mm,
        None if failing == "before" else {"frame_user": 72},
        None if failing == "after" else {"frame_user": 72},
        prev_error=f"cannot analyze {failing}" if failing == "before" else None,
        curr_error=f"cannot analyze {failing}" if failing == "after" else None)
    assert proc.returncode == 0, proc.stderr
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    notes = [ln for ln in lines if ln.startswith("NOTE")]
    assert len(notes) == 1 and f"cannot analyze {failing}" in notes[0], (
        f"a failed `elf stack` on the {failing} side must name itself in a NOTE: {lines}")
    assert any(ln.startswith("ROM") for ln in lines), (
        "one half failing must not take the other half's answer with it")
    assert not any(ln.startswith("FRAME") for ln in lines)


@requires_bash
def test_a_failed_footprint_call_is_a_note_and_the_frames_still_answer(tmp_path: Path):
    mm = {"ok": False, "error": {"message": "not an ELF"}}
    proc = _run_fence(tmp_path, mm, {"frame_user": 72}, {"frame_user": 136})
    assert proc.returncode == 0, proc.stderr
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert lines == ["NOTE\tfootprint not compared: not an ELF",
                     "FRAME\tframe_user\t72\t136"], lines


@requires_bash
def test_a_footprint_call_that_prints_nothing_is_a_note_and_not_silence(tmp_path: Path):
    """`jq` over empty stdin prints nothing and exits 0 — so an `elf memmap` that
    produces no envelope at all (argparse error, `loci` absent, killed process) erased
    the entire footprint half without a word, and the branch's quiet condition was
    satisfied by a check that never ran. Reproduced against the recipe before the
    `[ -n "$mm" ]` guard existed."""
    proc = _run_fence(tmp_path, "", {"frame_user": 72}, {"frame_user": 72})
    assert proc.returncode == 0, proc.stderr
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert lines and lines[0].startswith("NOTE\tfootprint not compared:"), (
        f"an empty envelope must become a NOTE, not silence: {lines}")
    assert lines[0].strip() != "NOTE\tfootprint not compared:", (
        "the NOTE carries no message, so the report cannot say what failed")


@requires_bash
def test_each_frame_call_gets_its_own_out_dir(tmp_path: Path):
    """Every released CLI keys `elf stack`'s default output directory on the artifact's
    bare **stem**, so a Before and an After sharing a basename — which is exactly what
    the header route produces — both write `.loci-build/elf/<stem>/stack-analysis.json`.
    The second overwrites the first, both envelopes name one file, the comparison reads
    one side twice, and **no FRAME line can ever print**. Measured on 0.1.102.

    The stub reproduces that default when `--out-dir` is absent, so this test fails by
    the same mechanism the real CLI would."""
    mm, _prev, _curr = CASES["nothing moved"]
    proc = _run_fence(tmp_path, mm, {"frame_user": 72}, {"frame_user": 136})
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert "FRAME\tframe_user\t72\t136" in lines, (
        "the two frame calls collided: the pair shares a basename, so without a "
        f"per-side `--out-dir` the comparison reads one file twice. Got: {lines}")


@requires_bash
def test_the_stub_rejects_a_call_the_recipe_should_not_make(tmp_path: Path):
    """The positive control. Every assertion above reads "the fence printed the right
    lines" — none of them can tell that from a fence whose commands were never the
    ones the contract documents, because the stub would have answered anyway. Here the
    stub is loud, and this test proves it — including for an inverted pair, which the
    earlier fixed-envelope stub could not see at all."""
    mm, prev, curr = CASES["nothing moved"]
    for label, mangle in (
            ("an undocumented verb", lambda f: f.replace("elf memmap", "elf sections")),
            ("an inverted pair", lambda f: f.replace("--elf", "\x00")
                                            .replace("--comparing-elf", "--elf")
                                            .replace("\x00", "--comparing-elf")),
    ):
        stub_dir = tmp_path / f"stub {label}"
        stub_dir.mkdir()
        (stub_dir / "memmap-before-after.json").write_text(json.dumps(mm),
                                                          encoding="utf-8")
        for side, frames in (("before", prev), ("after", curr)):
            (stub_dir / f"frames-{side}.json").write_text(
                json.dumps(_stack(frames)), encoding="utf-8")
        bindir = tmp_path / f"bin {label}"
        bindir.mkdir()
        stub = bindir / "loci"
        stub.write_text(_STUB.replace("__DIR__", _to_bash_path(stub_dir)),
                        encoding="utf-8", newline="\n")
        stub.chmod(0o755)
        before = tmp_path / "art dir" / "before" / SHARED_BASENAME
        after = tmp_path / "art dir" / "after" / SHARED_BASENAME
        for p in (before, after):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"\x7fELF stub")
        jq_dir = _to_bash_path(Path(shutil.which("jq")).parent)
        script = mangle(_pair_fences()[0])
        proc = subprocess.run(
            [_find_bash(), "-s"],
            input=f'export PATH="{_to_bash_path(bindir)}:{jq_dir}:/usr/bin:/bin"\n'
                  + (script.replace("<PREV>", _to_bash_path(before))
                           .replace("<OBJ>", _to_bash_path(after))
                           .replace("<project_root>", _to_bash_path(tmp_path))
                           .replace("<loci_target>", "armv7e-m")),
            capture_output=True, text=True, encoding="utf-8", cwd=tmp_path,
        )
        assert proc.stderr.strip(), (
            f"the stub answered {label} without complaint, so every execution test "
            f"above proves nothing:\n{proc.stdout}")
