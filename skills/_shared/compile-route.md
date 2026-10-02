# The compile route (shared)

What a run does when it **builds or diffs an artifact**: compiling the source, the
header route, what `elf diff` answers with, and what the differ cannot see. Read it
beside [the house rules](house-rules.md), not instead of them.

**Who reads this:** `loci-post-edit`, `loci-preflight`, `exec-trace` and `bug-report`.
A skill that measures an artifact somebody else built — `control-flow`, `stack-depth`,
`memory-report` — never comes down this path and does not read this file.

**Not here:** how a *language* changes the names you get back. That is every skill's
business, so [Rust / Cargo](house-rules.md#rust-projects) and
[Go / TinyGo](house-rules.md#go-projects) stay in the house rules.

---

<a id="loop-cost"></a>
## Path cost is not yours

`loci analyse measure` computes it, once, and identically on the Before and the
After sides of a comparison: each block multiplied by the laps it runs, and every
call priced at its own block — the callee's body is never counted, only named in
`reasons`, so the figure is exact for the function's own code. Only a trip count that
could not be derived (`iters=?`), recursion or a `cycles:` line makes the total a `≥`
lower bound. Three copies of that arithmetic used
to live in prose here and in the two reflex skills, and they drifted.

**Never re-derive a figure by hand.** A number you computed yourself is a different
measurement from the one the run record holds, and the two will disagree in the
report. Read `data.paths.<fn>` from `measure`: `ns`, the `blocks` the figure was
computed on, `lower_bound` and its `reasons`, and `energy_uws` where it is present.
Energy is reported only where the contract bounds it, or where the project has no
contract at all — an absent `energy_uws` is a signal nobody asked for, not a
measurement that failed, and it is never reconstructed from the timing figure.

`lower_bound: true` is reported, never resolved: prefix the figure with `≥`, put the
`reasons` entry in the Note of every row whose path includes it, and never claim a ✅
on a number that can only grow. That reading is yours and not the gate's — every bound
can pass on a `≥` figure — so **record it with `--agent-verdict`**, per the shared
**Recording it** section; a `≥` that keeps the report off ✅ and leaves the run
recorded `pass` is the same run described two ways. **Never substitute a number of your own** for a trip
count the evaluator could not derive — not from the source, not from a plausible
buffer size, not from "typically". A fabricated count is wrong in the same direction
every time, and it is wrong *silently*, which a `≥` is not. If a loop's bound is
knowable but not from the instruction stream — a `#define`, a caller-supplied length
the project fixes elsewhere — that is a fact for the repository's Contract Envelope
to declare, not for you to assume. A recursive cycle is not a loop with a big trip
count: depth is `stack-depth`'s question.

**There is no capability check.** Nothing here is gated on whether the build
"supports" loop annotation, and no such flag may be reintroduced: one existed, was
derived from the installed CLI's version number, compared against a minimum that
never matched the release which shipped the feature, and switched the whole feature
off on builds that had it.

The entry-point skills (`stack-depth`, `memory-report`, `control-flow`) measure
unmetered signals and have no path cost to compute at all.

---

<a id="pattern-a"></a>

## Step 0: Pattern A — compile the source

For skills that compile the analyzed source themselves (preflight, post-edit).

The compile call resolves the project from `--source` and compiles for its recipe's
target (house rules, **Resolving the project**): no call before it, no
`--loci-target`. Take `<project_root>` and `<project-context>` from its answer for
the calls after it. `not_resolved`: ask which project. Any other code
(`not_initialized`, `recipe_invalid`, `recipe_tampered`, …): its row.

Compile the affected source(s) with `loci build compile` — do **not** reuse an
existing `.o`/`.elf` from the project's own build. LOCI needs the compiler,
flags and version the recipe pins, so that the pre/post rebuild diffs
apples-to-apples:

    loci build compile --source <file> --phase preflight --require-recipe

**`--require-recipe` is accepted and ignored since the flip (T14).** The refusal
it used to demand is unconditional now: with no recipe, every compile route
answers the coded `not_initialized` whether or not the flag is passed — there is
no cascade left to fall back to, so nothing is ever built on guessed flags, and
`loci analyse prepare` — the compile route every skill runs — needs no flag to
refuse. The flag stays in the fence for one release. A `loci` that rejects it
(`unrecognized arguments`, exit 2) predates the recipe entirely and is the
version-skew case: say the CLI is too old, offer `/loci:setup`, and print no
`Recipe:` line — nothing would stand behind it.

With it, compiler and flags come from the **recipe** — you do not pass
`--compiler`/`--flags`/`--arch`, and there is nothing here for you to detect. It
writes the object under `.loci/build/objects/<target>/` (in a subdirectory
mirroring the source's own path), plus a sidecar `<output>.meta.json`, and
returns both paths in the envelope as `data.output` and `data.meta_file`. Every
refusal is one of the coded errors above.

**Pass `--project-root` where a call names no file in the project** (`measure
--prepared <id>`, `stats`): the `project_root` the first call returned.

**Take every path from the envelope. Never assemble one.** Where the object lands
is the CLI's choice — a Rust crate's object is named after the crate target, and the
C/C++ scheme keys on the source's own path — so a path built by hand breaks silently
the next time the layout moves.

**Do not pass `--meta-prev` by hand.** It names a pre-edit sidecar, so using it
means constructing exactly the path the rule above forbids — and on a cargo crate it
overrides a deliberate refusal (rule 2 of **Rust / Cargo projects**). Pairing the
baseline is `loci analyse prepare`'s job: both sides are compiled under the one
recipe, and it reports the pair in `provenance[]`.

**If you are measuring a change**, do not use the bare call above — run
`loci analyse prepare --source <file> --turn <id>` (post-edit, exec-trace). It reaches
flag parity with the pre-edit baseline by construction and names every artifact it
measured. Preflight is the exception: it *establishes* the flags a later post-edit
inherits, so the bare call above with `--phase preflight` is correct there.

---

## When there is no Before: `provenance[].withheld`

**The pre-edit snapshot is armed by the Edit/Write tools.** `hooks/pre-edit-hook.sh`
runs `loci build snapshot` on `PreToolUse` for `Edit|Write` and nothing else. A source
changed any other way — a shell redirection, an in-place stream edit, a heredoc, `git checkout`, a generator —
has no snapshot for this turn, so its `kind: regression` entries go **unjudged** this
turn. That is by design: a bound with no Before is neither held nor breached, and a
percentage invented for it would be a fabricated regression.

What changes is that the report now says why. When `build compile` finds no comparable
pre-edit pair it returns `baseline_withheld {code, reason}`; `analyse prepare` carries it
as `provenance[].withheld` and `manifest.artifacts.withheld` (keyed by the after
object), and `analyse measure` puts the same `withheld` on every baseline-less
regression row in `data.unjudged`, with its `reason` ending in `— <code>: <reason>`.
**Relay that sentence verbatim.** Do not reason backwards from the missing number to a
cause of your own; the code is the cause. `not_captured` is the shell-edit case, and its
reason carries the remedy for next time. `source_new` is a file this turn created: its
functions **are** measured and only regression bounds wait, so it is not the quiet
answer. A line with `flags_borrowed` compiled a file the compile database lacks using a
neighbour's command — relay its `reason`.

**No Before also means no scope** (`source_new` aside). The differ is what names functions, and with no
Before it names none — the edit knows which functions it reached, the artifacts do not.
So `prepare` narrows the touched set to what `--functions` names, and to nothing when it
names nothing: per-function requests are dropped rather than fanned out over every
function in the unit, which used to judge untouched neighbours against their own
scoped bounds. The unit is listed in `data.unscoped_units` and in the manifest as
`{artifact, source, functions}` — the functions that live in it, none of which was
measured — and every entry scoped to one of them says so, its `reason` ending in the
`withheld` sentence. A caller that knows the names states them: `--functions` is scope,
not a measurement request. Naming a function the contract does not bound requests
nothing unless `--signals` names the signal too — so no reflex run invents a demand the
project never made.
Whole-artifact requests are unaffected — they never needed a touched set.

**`--phase preflight` has no edit, so it never scopes by one.** It measures the code as
it stands: the functions `--functions` names, or with none named, the ones the
contract's entries bound. It withholds every Before, whether a `.prev` or a rebuild
from HEAD, as `withheld.code: preflight`, because the Before of an unedited source is
the After again. So a regression bound is unjudged there, beside today's figure, and
the post-edit run judges it. `data.unscoped_units` still lists a unit where neither
the names nor the contract reached a function.

Two rules follow:

- A step that must run through the shell — a generator, a stream-edit pass — is preceded by
  `loci build snapshot --source <f> --turn <id>` for each source it will change. After
  the fact there is nothing to recover: the pre-edit bytes are gone.
- To recover in the same turn: restore the source, then make the edit with Edit. The
  object on disk was built from the edited source, so `snapshot` refuses to freeze it
  (`snapshotted: false`, reason names both hashes) — but the overlay captures the
  restored text, and `analyse prepare` rebuilds the Before from it
  (`provenance[].before_kind: reconstructed`, `verified: true`). Never delete a
  `.prev` by hand to get there.

---

---

<a id="header-edits"></a>
## Measuring a header edit

A header emits no object, so there is nothing to compile and nothing to diff for the
file the user actually touched. What a header *does* have is text, and the
translation units that `#include` it do have objects — so the measurement is: **the
header as it was, plus a rebuild of each affected translation unit against it.**

`loci analyse prepare --source <header> --turn <t>` does all of it. Nothing about the
call differs from a `.c`; the envelope grows two fields, and its `provenance[]` lines
say where each Before came from:

- **`data.headers[]`** — one per edited header: `source`, `reached`, `measured[]`,
  `unaffected[]`, `unmeasured[]` (`{source, reason}`), `coverage_complete`,
  `confidence`, `coverage`, `warnings[]`. `reached` counts every unit the header
  reaches; `measured` is the first `--units` (default 3) that compiled, in the CLI's
  order — exact evidence first. The gap between the two is stated, never implied.
- **`data.units`** — `{fn: unit}` for every function in `data.functions`.
- **`provenance[].before_kind`** — `reconstructed` (rebuilt against the turn
  overlay's captured header text) or `snapshot` (the unit was itself edited this
  turn; `note` says the delta is the turn's, not the header's alone). `verified:
  false` on a rebuild means the CLI could not prove it read the captured copies — an
  unverified rebuild that lost the include search is the current build, and
  comparing a build against itself is exactly what produces a confident zero. A line
  with no `before` and a `note` is a unit whose Before could not be rebuilt: the
  After alone, with the CLI's reason. A relative quoted include
  (`#include "../inc/x.h"`) is the usual one — the preprocessor resolves it in the
  including file's own directory ahead of any include path.

Two facts a reader has to hold apart, and the envelope keeps them apart:

- **`unaffected`** — this unit genuinely does not depend on what changed (commonly a
  header edit inside an `#ifdef` it does not take). It is an answer about the code,
  not a failure and not a gap, and it did not use one of the N slots.
- **`unmeasured`** — reached, and not measurable: assembly units (`.S`/`.s` are real
  translation units a header reaches, but `loci build compile` does not take them),
  or a unit whose compile failed, with the message. One awkward unit does not end
  the run and does not use a slot either.

**An empty `measured` is not automatically "nothing is affected".** It means that
only when `coverage_complete` is true *and* `confidence` is `exact`. Any other
combination means the search could not see the whole project; `warnings[]` says
which bound bit, and the honest report carries it.

Plumbing, for `/loci:bug-report` and for reading `.loci/build/turns/<t>/`: `loci build
affected --source <header>` is what names the units, and `loci build compile
--baseline --turn <t>` on the unit is what rebuilds one Before into
`turns/<t>/obj/`. Neither is a skill's call.

---

<a id="elf-diff"></a>
## Diffing the pair: what `elf diff` answers with

```
loci elf diff --elf "<PREV>" --comparing-elf "<OBJ>" \
    --project-root "<project_root>" --turn "<turn-id>"
```

The counts are in `data.summary`. The changed FUNCTION names are in
`data.functions`, grouped as `added`, `removed` and `modified`. The per-symbol
entries — every symbol, with its similarity ratio and the differ's reason — are
**in a file**, at `data.diff_file`. `data` also carries `count` and `warnings`,
and the two freshness blocks when the CLI can resolve the artifacts' sources — a
diff of two bare objects outside a project has none, so their absence is not a
malfunction. There is no `data.modified` and no `data.added` at the top level:
the three lists are under `data.functions`. Never write `null` into a
`--functions` argument for a list you did not find — `elf asm` accepts it as a
name that matches nothing, and answers with an empty measurement.

The file is a JSON array, most-changed-first:

```
{"status": "modified", "symbol": "adc_read", "stt_type": "STT_FUNC",
 "similarity_ratio": 0.42, "reason": "…"}
```

`status` is `added` | `removed` | `modified` | `unchanged`, and the name is under
**`symbol`** — not `function`, not `name`.
`symbol` is the differ's own spelling, which is **not** always the name the CFG,
the contract and the user use. A C `static` arrives file-qualified
(`analyze.c_quicksort` for `quicksort`); every C++ symbol arrives mangled
(`_ZN3sigL7mean_ofEPKii` for `sig::mean_of`). Do not try to derive one from the
other — for C++ nothing can. **You do not have to**: `prepare` holds the pairing
and applies it itself, so `data.functions` already comes back in the printed
spelling. That is the translation, and it is the only one on offer — no envelope
carries the map for a skill to join by hand.

**`unit`** rides beside `symbol` when the symbol has internal linkage: the
translation unit that owns it. The printed name drops the file, so `unit` is the
one thing the pairing cannot give back — two units may each define a
`static helper`, and it is what tells those rows apart.

The file lists what *changed*, and only that. The differ writes an `added`, `removed`
or `modified` row and nothing else, so `summary.unchanged` is a status the envelope can
carry rather than one you will see, and **the file's length is not a symbol count** —
do not read "3 entries" as "this object has 3 functions". It follows that the file can
never answer *which functions this unit defines*: there are no `unchanged` rows to read
them from. When you need that set — a quiet edit, where nothing changed and there is no
changed list at all — omit `--functions` from `loci analyse cfg` and let it render the
whole artifact, which for a single translation unit's object is exactly that set.

**`data.functions` is already filtered on the two things that matter**, so the
file is not where the names come from:

- **`status`**, because a `removed` function is gone from the After.
  `elf asm --elf <OBJ>` cannot extract it, so it is its own list.
- **`stt_type`**, because the differ diffs **variables too**. A changed global
  arrives as an ordinary entry in the file, and `elf asm` answers `ok:true` with
  `function_count: 1`, empty assembly and `timing_csv: null` — success-shaped
  and empty. `elf cfg` fails outright on one. `data.functions` holds functions
  only; the variable's row stays in the file, where it is evidence rather than
  a measurement target.

So the call is the whole answer:

```
loci elf diff --elf "<PREV>" --comparing-elf "<OBJ>" \
    --project-root "<project_root>" --turn "<turn-id>"
```

`data.summary` is `{"added":N,"removed":N,"modified":N,"unchanged":N}`.
`data.functions.added` and `data.functions.modified` are the list `--functions`
takes, comma-separated **and quoted**, in the *next* fence you run: copy the
names across yourself, because nothing but the transcript survives between
fences. Read `ok` first as always — a failed envelope has **no `data` key at
all**, and the `error.message` is what tells you the diff failed.

<a id="elf-diff-empty"></a>
**An empty list does not mean the edit had no effect.** The differ hashes **masked**
instructions — immediate values are replaced before comparison — so an edit that
changes only constants (a loop bound, a buffer size, a threshold, a timeout) produces
**no entry at all**, and the envelope is byte-identical to diffing an artifact against
itself. What an empty list means is *no structural change this differ can see*.

So read `data.summary` before concluding anything, and report accordingly:

- `removed` non-zero, `added` and `modified` empty → **functions were deleted.**
  Name them from `data.functions.removed`.
- every count zero → say the differ saw no change, **and say that constant-only edits
  are invisible to it**. That is an answer about *functions*, not about the artifact:
  go on to [the two questions it does not answer](#beyond-the-diff) before concluding
  that the edit changed nothing. If the user named a function, measure that function
  anyway rather than reporting nothing.
- Do not widen to every function in the object instead — for exec-trace that is one
  metered `loci timing` call per function, spent to say nothing.

When the two groups have to stay apart — extracting a Before only makes sense for
a function that already existed — keep them apart. `data.functions` already does:
`added` and `modified` are separate lists, and an empty one is a group with
nothing in it. There is no second call to make.

---

<a id="removed-functions"></a>
## A function this edit deleted

`prepare` runs the differ itself, so **never run one to ask whether something was
deleted**. Two fields on its envelope carry it, present only when it happened:

- `data.removed_functions` — the names. Absence is the answer; an empty list is
  never printed.
- `data.orphaned_entries` — the enabled contract entries those names leave bounding
  nothing, in the shape `data.unjudged` rows carry.

**A removed function is never measured and never requested.** It has no After —
nothing to time, no frame to read, no block to rank — so it is absent from
`data.functions` on purpose, and naming it on `--functions` cannot bring it back.

**An orphaned entry is a line, never a row.** Nothing was measured, so it carries no
`STATUS` and it does not move the run verdict: deleting code is not a breach of a
bound. What it *is* is a blind spot the edit just created — that entry will read
clean for ever over a function that is gone. Say so, quote the entry's `text`, and
give the `reason`'s two exits: remove the entry, or point it at the code that
replaced it.

---

<a id="beyond-the-diff"></a>
## What the differ does not answer: footprint and frames

`elf diff` compares **masked instructions inside functions**, so its silence is scoped
to exactly that. Four edits that changed the compiled artifact and still produced
`{"added":0,"removed":0,"modified":0,"unchanged":0}`, each measured against
`arm-none-eabi-gcc` 15.2 (Cortex-M4, `-O1 -g`):

| The edit | What it did to the object |
| --- | --- |
| `const uint32_t lut[8]` → `lut[64]` | +224 B ROM |
| a string literal got longer | +44 B ROM |
| `uint32_t pool[16]` → `pool[4096]` | +16 320 B static RAM |
| `char scratch[64]` → `[128]` | worst-case frame 72 → 136 B |

The last row is the one that reads as safe and is not: `sub sp, #68` and
`sub sp, #132` are the same instruction with a masked operand. The *bigger* version of
that same edit (`[256]`) **was** visible, because gcc happened to emit an extra
instruction with it — so whether a frame change surfaces is an accident of encoding,
never something to gate on.

An empty function list therefore licenses skipping the **metered** half — `elf asm`
plus `loci timing`, the only calls that spend the user's quota — and licenses nothing
else. Ask the pair the other two questions before concluding. Both calls are local,
unmetered, and in every released CLI; this is one Bash call:

```
loci elf memmap --elf "<PREV>" --comparing-elf "<OBJ>" \
    --project-root "<project_root>" --turn "<turn-id>"

loci elf stack --elf "<PREV>" --comparing-elf "<OBJ>" \
    --project-root "<project_root>" --turn "<turn-id>"
```

**The pair is the two OBJECTS, never the linked image.** A run that recompiled one
translation unit has not relinked, so the linked artifact predates the edit and
`stale: true` says so. A whole-binary bound read off it measures the previous binary
and reports it against the current one, so such an entry stays **`unjudged`** on a
single-TU run, named with the link as its reason — escalating to the stale image to
fill the row in is the answer ruled out. Two runs of this resolved it opposite ways.

**`--comparing-elf` on both.** Each verb compares the pair itself and prints one
envelope for the pair. Two separate `elf stack` runs answer a different question:
their per-function analyses are keyed by every function with its per-call-chain
paths, and the frame comparison is a handful of rows out of two of those.

**`--project-root` and `--turn` on every `elf` call, and never `--out-dir`.** The CLI
keys each dump directory on the artifact's full path, so a Before and an After that share
a basename — a reconstructed `…/turns/<key>/obj/<slot>/src/blink.o` against
`…/objects/<target>/src/blink.o` — never collide. With the root and the turn passed, the
dumps land in that turn's tree under the project and go when the turn does; without them
the verb writes under the shell's own directory, which for a fence run outside the
project root is a second `.loci/build/` that nothing reads and nothing cleans.

Four fields carry the answer, and **only differences are listed**:

- **`data.summary_delta.rom_total`** and **`data.summary_delta.ram_static_total`**
  from `memmap`, each `{base, current, delta}` in bytes. Both are there whenever
  the call answered; a `delta` of `0` is the real "unchanged".
- **`data.symbol_deltas`** from `memmap` — `{rom: [...], ram: [...]}`, the symbols
  behind that delta, when the CLI attributed it. Each entry carries `name` and a
  `status` of `changed` | `added` | `removed`. A changed symbol carries `delta`; one
  that arrived or went carries `size` and **no `delta` at all**, so quote the field
  the entry actually has. The whole block is sometimes absent or `null`, and its
  absence does not contradict a non-zero total.
- **`data.frame_deltas`** from `stack` — `{function, base, current}`, one per
  function whose own frame moved. An empty list means none moved. A `null` on either
  side is **not a zero frame**: it means that function is not in that artifact at all.
- **`ok: false` on either call** — a check that did not answer. Report it as
  unmeasured, never as unchanged, and quote its `error.message`. An
  `auth_required` there is the sign-in gate below, not a broken artifact.

Six things to know before you trust a quiet answer:

- **All three checks compare shapes and sizes, never values — so an edit that changes
  only a value is invisible to every one of them.** Three measured families, one
  mechanism each: a **constant in code** (`return v + 4928u` → `v + 19840u` — same
  `add.w`, so the differ's masked hash is identical and the object is the same size); the
  **contents of an initialised table** (`const uint32_t coeff[8] = {1..8}` → `{9,9,…}` —
  `.rodata` bytes are not instructions, so the differ never looks, and `memmap` compares
  the symbol's *size*, which did not move); and the same again in `.data`. All three give
  `{0,0,0,0}`, a zero ROM/RAM delta and no frame line, on objects that differ in hundreds
  of bytes. A quiet answer therefore means "no change these three can see", and a report
  of it must say so — a retuned lookup table is one of the commonest embedded edits there
  is, and the pair comparison cannot see it. What the comparison buys is the *narrowing*
  of the gap from "any change to code, data or stack" to "a change of values at unchanged
  size"; it does not close it.
- **Both verbs need a signed-in session.** They are local and unmetered — no model call,
  no quota — but `loci elf` is behind the CLI's login gate, so an expired session answers
  `{"ok":false,…,"code":"auth_required"}` and neither half answers. That is
  the one case where neither the quiet answer nor a delta applies: say the pair could not
  be compared and tell the user to run `! loci login`.
- **The ROM/RAM totals here are this translation unit's, not the firmware's.** Report them as
  such, and never send them to `loci contract check` as a `rom_size` / `ram_size`
  measurement: those bounds are firmware-scale, and a 361-byte object judged against a
  512 KB budget produces a green row on a claim nobody made. When the contract does
  bound ROM/RAM, escalate to `memory-report`, which measures the linked binary.
- **Frame sizes are only as good as the installed CLI.** Before **0.1.107** every frame
  came back as the push size — measured: a 528-byte frame reported as 4 B on 0.1.102 —
  so both sides agree, `frame_deltas` comes back empty, and "unchanged" is
  uninformative rather than true. Resolve the
  installed version through [Reading the CLI's version](house-rules.md#cli-version-gate); below
  0.1.107, report the frame question as unanswerable on this install and offer
  `/loci:setup`.
- **A `frame_deltas` entry is not a stack-depth verdict.** It is one function's own frame
  (`frame_size`), not the worst-case depth through a call graph (`worst_case_depth`,
  which the recipe deliberately does not read). It says re-measurement is warranted; the
  `stack-depth` skill is what answers.
- **`symbol_deltas` attributions are only as good as both symbol tables.** Compare against a
  *stripped* artifact and every symbol on the other side reads as `added` — measured, and
  it arrives beside a ROM delta of zero, which is the tell. Attribute a delta to names
  only when the two totals actually moved.
- **Nothing checks that the two artifacts are the same architecture.** `elf memmap` takes
  no `--arch` and does not compare `e_machine`: an ARM object against an AArch64 one
  answers `ok:true` with confident, meaningless numbers. What stands between you and that
  pair is `loci analyse prepare` building both sides under the one recipe; this fence
  assumes it did.

<a id="elf-diff-unrequestable"></a>
**Quoting, and the one symbol shape that still cannot be requested.** Quote the
value — `--functions "<changed_funcs>"` — because a monomorphized Rust generic
contains `<` and `>`, which bash reads as redirections, and the command then never
runs. Quoting fixes that.

A **comma inside a symbol** used to be the half quoting could not fix; since CLI
0.1.126 it is fixed. The CLI splits `--functions` at **bracket depth zero**, so
`drop_in_place<Ring<u8, 4>>` and `gix::pair::<u32, u64>` arrive as one name — and so
do a C++ parameter list (`calculate(int, double)`) and a `[crate#hash]`
disambiguation tag. What still splits is a query whose brackets are
**unbalanced** (a stray `)` or `>`): the depth never goes below zero, so such a
query degrades to plain comma-splitting and its fragments match nothing. Report
that symbol as changed-but-unmeasurable and name it; do not present a number for
it. On an install older than 0.1.126 the original rule holds — every comma splits —
so resolve the version through [Reading the CLI's version](house-rules.md#cli-version-gate)
before deciding which case you are in.

Rust symbols reach you demangled unconditionally on 0.1.126 and later: there is no
`PATH`-dependent path and no mangled fallback to round-trip through, so query with
the readable name.

---
