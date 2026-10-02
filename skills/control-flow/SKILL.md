---
name: control-flow
description: >
  Annotated control-flow graphs for compiled C/C++/Rust/Go: per-function basic
  blocks, loops with derived trip counts, recursion and irreducible cycles, call
  sites, in text optimised for LLM analysis. Renders the graph and judges the two
  structural signals it determines, recursion cycles and indirect calls, and the
  contract's text-only rules on the functions it draws. Use when the user asks
  what the control flow of compiled code looks like: call dependencies, function
  impact, which branches and loops exist, where the hot path could run, or what
  a change did to the shape of a function.
when_to_use: >
  It judges the signals the graph itself determines; the rest belong elsewhere —
  a stack budget, whether a cycle is bounded, or a callee missing from the link
  is `/loci:stack-depth`, a timing or footprint bound is `loci-post-edit`, and
  authoring a new bound is `/loci:contract`.
---

# LOCI Control Flow Analysis

One free CLI call answers this skill. `loci analyse cfg` compiles the touched
translation units if they are stale, picks the artifact, cuts the annotated CFG
for the functions you name, and returns the file paths plus a per-function loop
table. You read the graph, judge the signals it determines and the text-only
rules on what it drew, and present it.

**This skill renders, and it judges the two signals the graph determines.**
`recursion_cycles` — a back-edge and an irreducible cycle are in the graph — and
`indirect_calls` — an unresolved call is visible at its call site. The other two
structural signals never become yours: `unbounded_recursion` asks whether
anything in the code bounds a cycle, which is the source reading `stack-depth`'s
classification step owns, and `unknown_callees` is a fact about the link, where an
object cannot tell an unlinked call from a resolved one. Never substitute a signal
you can see for one you cannot.

**Verdict vocabulary.** Two columns, `STATUS` and `AGENT ASSESSMENT`, and the
row verdict is the two composed — see `<plugin-dir>/skills/_shared/verdicts.md`,
which holds the matrix, the three assessment words and the display words they
render as. Step 3 is the branch that says what this run earned. You supply no
band and no threshold of your own: the graph supplies the number, a contract entry
supplies the `STATUS`, and where none covers the signal you supply the assessment
and the `STATUS` follows from it.

Apply the house rules' **The Contract Envelope is input only**, **A measurement
inherits a verdict from a bound, never from a band**, **[Your verdicts are
`flagged` / `cleared`](../_shared/house-rules.md#agent-verdicts)** and
**Structural invariants: which measurement answers which signal**.

**This verb computes no verdict, and `data.contract` says whose bounds apply.**
On `none`, every row's `STATUS` is the word your assessment composes to —
**Needs attention** → `CAUTION`, **Looks good** → `PASS`, **As reported** → `—` —
per [No contract: the agent fills `STATUS`](../_shared/verdicts.md#no-contract).
On `project`, an entry on these two signals is judged by **stack-depth** (Step 3
says why), and what comes to you is `data.agent_judged`: the text-only entries on
the functions this run drew.

**A hazard is provable at any scope; a zero is not.** A CFG is cut per function
while every structural invariant covers the whole binary, so a cycle or an
indirect call you found is evidence for the whole-binary entry and is reported as
one — a non-zero count holds however few functions were cut, including from a
single object. The reverse never holds, and a clean set of CFGs is not a scope
this skill can clear. **Say the scope in the Note, every time** — *0 cycles
across the N functions this run read* — and on an object say it is a single
translation unit. That sentence is what keeps a clean row honest: the word beside
it is your reading, and the reader can see exactly what it rests on. Never present
it as a zero over the binary, and never put it against a whole-binary bound —
that bound is stack-depth's to judge.

**Shared house rules.** Read `<plugin-dir>/skills/_shared/house-rules.md`
and apply its **Resolving the project**, **The turn id: one convention,
every skill**, **Output: the JSON envelope**, **[Naming a path or a loop in the
report](../_shared/house-rules.md#naming-paths)** and **[The three `loci`
commands a user ever sees](../_shared/house-rules.md#user-commands)**
sections. The target is the recipe's, and the verb reads it there — you never
pass or derive one. Artifact
selection is *not* yours either: the verb owns the freshness ladder,
refuses a stale binary rather than measuring it, and names what it read in the
envelope — [The artifact a run measures](../_shared/house-rules.md#the-artifact) says how it ranks
(the recipe's recorded artifact first), and you never re-run that ranking in
prose beside it.

**Tool boundary (reminder):** `loci` only — never `objdump`, `readelf`,
`addr2line`, or `nm`. This skill needs the annotated CFG that binutils cannot
produce.

## Step 1: one call

```
loci analyse cfg --turn "<turn-id>" --caller control-flow \
    --functions "<fn>[,<fn>]" [--project-root <the project the user named>] \
    [--source "<edited-source>"] [--elf <path>]
```

- `--turn` and `--caller` are **required**; without either the verb refuses and
  nothing is read. `<turn-id>` comes from the shared **The turn id: one
  convention, every skill** section — never invented here.
- `--functions` is what the user asked about. Keep it quoted: a Rust generic
  contains `<` and `>`, which bash reads as redirections. Omit it only when the
  request really is the whole artifact.
- `--source` only when you know which file was edited; left out, the verb
  compiles what `build snapshot --turn` captured. A translation unit whose object
  is already current is not recompiled.
- `--elf` only when the user named a binary. Otherwise the verb ranks the
  candidates itself, discards anything older than its sources, and falls back to
  an object.

**Branch on `.ok` first, and only then on the exit code** — a contract file that
fails to load refuses with a usage error, which is also `2`. On `ok:true`:
`0` — the graph was cut, and any entry that judged either passed or breached an
advisory `severity: caution` bound; `2` — an enabled entry with `severity: fail`
was breached on one of the two signals, which is the run's headline finding and
not metadata riding along with a graph. `1` — the analysis itself failed: no
artifact qualified, or a stage broke. `data.artifact.refused` names what was
skipped and why; surface that verbatim rather than hunting for a binary yourself.

A refusal is `ok: false` with an `error.code` from the shared **When a `loci` call
refuses: the eleven coded errors** — `not_initialized` (**it branches — follow its
row**), `recipe_stale`,
`recipe_tampered` and the rest, each with the one recovery that table names.
Relay the code and its recovery verbatim, and stop. The table is the procedure;
it is not restated here, and nothing in it sends you looking for anything.

The envelope carries:

- `data.files` — `control_flow` (the annotated CFG text), `assembly`, `arch`,
  `timing_csv`, `manifest`, and the `out_dir` they sit in.
- `data.loops` — the loop table, per function: `total`, `with_trip_count`,
  `unknown_trip_count`, `recursion`, `uncounted_cycles`, and one `rows` entry per
  loop (`id`, `kind`, `header`, `latch`, `depth`, `in`, `blocks`, `trips`,
  `trips_known`).
- `data.artifact` — the `Artifact:` line, as data.
- `data.compiled` — one line per touched translation unit, and whether it was
  rebuilt.
- `data.functions` / `data.not_found` — what was rendered, and what was asked for
  and is not in the artifact.
- The contract block, in `analyse stack`'s shapes: `data.contract`,
  `judgements`, `gates`, `verdict`, `unjudged`, `agent_judged`, `rows`. Nothing is
  measured, so `judgements`, `gates` and `rows` are empty, `verdict` is null and the
  exit is `0`. **`data.agent_judged`** is the part with work in it: each text-only
  entry on a function in `data.functions`. `data.unjudged` holds the rest, each with
  its reason — a function not drawn, a whole-binary rule, a structural entry
  `stack-depth` measures — and is routine, never a row.

## Step 2: read the graph

**One Read** of `data.files.control_flow`. That file is the analysis: blocks with
their source lines, edges with their conditions, `iters` per block, the `loops:`
summary and any `cycles:` line. Read `data.files.assembly` as well only when the
question is about instructions.

Do not re-derive anything the loop table already states, and do not count loops
by eye out of the text — `data.loops` is the same graph, already rolled up.

The two signals come off the same graph: `recursion_cycles` is `recursion` plus
`uncounted_cycles` summed across `data.loops`, and `indirect_calls` is the call
sites the CFG text leaves with no resolved target. Both are counts — the signals
have no unit — and both are the whole binary's, never a per-function figure.

## Step 3: judge the two signals, and route the two you cannot

**This skill observes the two signals the graph determines. It does not judge a
contract entry on either.** A structural signal is whole-binary, and a CFG is cut
per function: a cycle you find anywhere is real, but a zero over the functions you
read is not a zero over the binary, and a bound of `0` is what people write. The
CLI agrees and says so in data — `contract.ESCALATION_SKILLS` routes
`recursion_cycles`, `indirect_calls`, `unbounded_recursion` and `unknown_callees`
all to **`stack-depth`**, which reads the linked image and can discharge them.

So: **a contract entry on any of the four is stack-depth's**, and what you found
goes to it as evidence — the cycles, the call sites, the functions they are in.
That is already what you do for `unbounded_recursion` and `unknown_callees`; it is
now all four, which makes the hand-over consistent.

**Two branches here, and the run closes on a word in both.**

- **An entry covers the signal.** Its word is the row's, and that entry is judged
  by stack-depth, not here. Name the signal, the count, and the function it is in.
- **The count is clean, or this scope cannot prove it.** The row's word comes from
  your assessment: **Looks good** where you raise nothing, which composes to
  `PASS`, so the Note must name the gap — that a zero over the functions this run
  read is not a zero over the binary — or **Needs attention** where the graph gives
  you a specific concern you can point at, which composes to `CAUTION`.

**Where the composed word goes.** On `data.contract: none` it fills `STATUS`. On
`project` the row keeps `—` and your assessment beside it, and an entry on the
signal gets its word from stack-depth's run.

**Text-only entries: `data.agent_judged`.** Each is a rule on a function this run
drew. Judge it `flagged`, `cleared` or `no_opinion` against the graph and the
source lines its blocks name — a loop rule by `data.loops` (`trips`,
`trips_known`) and the loop's exit edges. `flagged` names the block, loop or call
site; `cleared` only where the graph shows the rule holds, never because nothing
looked wrong. Each is a row (Step 4).

The other two signals leave here the same way: hand `unbounded_recursion` and
`unknown_callees` to **`stack-depth`** with the cycles and call sites you
found. Timing, energy and ROM/RAM on an edit go to **`loci-post-edit`**, and a
bound the user is stating to **`contract`**.

## Step 4: report the CFG and the loop table

Report, per function:

```
## Control flow: <function>

Entry block:  bb_0x2010          Blocks: <N>
Loops:        <N> (<M> with a derived trip count)
Cycles:       <K> irreducible back-edge(s)     ← omit when 0

| Loop (source) | Kind | Depth | Blocks | Trips | Header |
|---|---|---|---|---|---|
| dsp.c:120-128 | loop | 1 | 2 | 16 | bb_0x202e |
| dsp.c:141-166 | loop | 1 | 5 | ? | bb_0x207e |
```

The `L1`/`L2` labels in `analyse cfg`'s own output are a per-artifact counter LOCI made
up — they identify a loop to the next `loci` call, not to a reader, so they never reach
the report. Identify a loop by the source range its blocks map to; fall back to the
header block only when there is no line map. The trip count is the exception that goes
in every time: the `Trips` column above is the assumed iteration count each figure
rests on, so it is reported for every loop named, `?` included. The shared contract's
**Naming a path or a loop in the report** is the rule, and it carries the one release
valve — a user who asks which loop is `L2`, or which of them LOCI ranked, gets the id
beside the source range.

Then the shape in prose: what the branches decide, which blocks are on the
straight-line path, where the calls go. `trips ?` means the trip count is **not
derivable from the code** — say so; it does not mean unbounded. `iters` is
executions per call of the function, and it is `?` whenever any enclosing loop's
count is.

A CFG cut from a `.o` **stops at the translation-unit boundary**: every call
leaving the unit is an unapplied relocation, so cross-TU call edges are absent,
not merely unlabelled. `data.artifact.scope` says so; when the question is about
call dependencies, quote it and say the answer is scoped to this translation
unit.

### Artifact provenance (mandatory)

Emit one line from `data.artifact`, at the end of the report body:

```
Artifact: build/app.elf (linked <build time>, sources current)
```

`freshness` is `current` / `unverified` (add `— <reason>`); a stale artifact
never reaches you, because the verb refused it. Never omit this line.

`data.artifact.via` says how the binary was chosen, and the line carries that
whenever it is not the ordinary case. `recipe` — the verb put the artifact the
recipe records first; this is the ordinary case and needs no remark.
`ranked` — nothing is recorded, or the recorded file is not on disk
(`data.artifact.recipe.recorded_artifact_on_disk` says which), so the newest fresh
candidate was taken: append `(ranked — no recorded artifact)`. `named` — the user
named it, and a binary no recipe vouches for gets `(named by you — build settings
unvouched)`. `data.artifact.recipe`, when present, is what the graph's shape rests
on — the target and validation tier it was built under. This skill prints no
`Recipe:` line: the two counts it reports are compared against a contract entry or
against nothing, not against a figure whose scale those flags set. But an `error` key there
(a recipe that exists and refused to load) is said once, with its code, beside
the `Artifact:` line, and `/loci:init` is what repairs it.

## Conclusion table

Build the rows from the counts Step 2 determined. Render judgement, gate and
machine-verdict payloads **only** when `data.contract` is `project`; on a `none`
envelope there are none to render and the run is uncontracted. A row an entry
decided quotes the requirement in that entry's own words.

The table is drawn either way. With no envelope the verb assembles no rows, so
you compose them — one per (signal, function) you reasoned about — and never fall
back to a stacked `ENTRY: … / FUNCTION: …` field list: a reader compares rows by
scanning a column, and a list of fields cannot be scanned.

Five columns, and the closed `ENTRY` vocabulary, exactly as [The conclusion table](../_shared/verdicts.md#conclusion-table) specifies
them — of the eleven names this skill reaches `Safety (Recursion)` and
`Safety (Indirect Calls)`:

```
| ENTRY | FUNCTION | STATUS | AGENT ASSESSMENT | NOTE |
|---|---|---|---|---|
| <qualified signal> | <fn or —> | <word or —> | <assessment> | <cause, with its number> |

Verdict: **<PASS|CAUTION|FAIL>** — <one sentence cause> [(<N> of <M> judged)]
```

`ENTRY` is the qualified signal name and `FUNCTION` is what the row is bounded
on, an em dash where the row covers every function this run read. `STATUS` is
`PASS` / `CAUTION` / `FAIL` for a compared bound or an observed hazard, and `—`
where nothing was computed. `AGENT ASSESSMENT` is **Needs attention**, **Looks
good** or **As reported**. A row that reached neither — no `STATUS` and no
assessment — is not drawn; it is counted beside the verdict.

### Row catalogue (order when present)

1. **Recursion cycles** — `Safety (Recursion)`, on the function an entry bounds,
   an em dash where the row is the count over the whole scope. Always, when the
   graph answered it. The Note carries the count and the functions the cycles are
   in, or `unmeasured` with its reason in place of a zero this scope cannot prove.
   `STATUS` is `PASS`/`CAUTION`/`FAIL` against an enabled entry bounding
   `recursion_cycles`, and otherwise `—` on a contracted run or your assessment's
   word on a `none` one. **Spend no caution on a cycle**: whether anything bounds it
   is `stack-depth`'s reading, so report the count and hand it over.
2. **Indirect calls** — `Safety (Indirect Calls)`, same rules against
   `indirect_calls`, with the Note naming the call sites by source range.

3. **Rules** — `<Gate> (Rule)` per text-only entry you judged, on its function:
   `STATUS` `—`, your assessment beside it, the Note quoting its `text` then the
   cause with its source range.

`unbounded_recursion` and `unknown_callees` get no row. A `—` beside either reads
as measured and clean, which is the one claim this skill cannot support.

Table footer, by what the run had to judge against:

- **An entry bounds the signal.** `Verdict: **PASS** — 0 recursion cycles and 0
  indirect calls, against the invariant <entry text> sets at 0`, or
  `Verdict: **FAIL** — <N> indirect call sites past the 0 that entry requires`.
- **No bound judged, something to raise.** `Verdict: **CAUTION** — <N> indirect
  call sites in <fn>; no bound judged them here, and unresolved dispatch hides
  call-graph depth`. The word is your assessment's, and the clause says what it rests on.
- **No bound judged, nothing to raise.** `Verdict: **PASS** — <K> loops across
  <N> functions; no bound judged recursion_cycles or indirect_calls here, and a
  zero over the functions this run read is not a zero over the binary`. The clause is
  not optional: it is what says the word rests on a reading, not on a bound. Where
  cycles were found, this is still the bullet — say the count instead of the zero,
  and name `stack-depth` as what reads the source: a cycle nobody has read the
  source for is not yet a hazard.
- **No bound judged, something to raise.** `Verdict: **CAUTION** — <cause naming
  the block, loop or call site>`.

The verdict line is the run's answer — the worst row verdict, with rows that
reached no word left out of the worst-of and counted beside it
(`(2 of 5 judged)`). It is never one row inside the table.

**Under the table**, by `data.contract`: on `none`, the shared caption from [No
contract](../_shared/verdicts.md#no-contract); on `project`:

```
No bound was computed here — an entry on recursion_cycles or indirect_calls is
judged by `/loci:stack-depth`, and a Rule row's word is the agent's reading.
```

### Example

```
Artifact: build/app.elf (linked <build time>, sources current)

| ENTRY                   | FUNCTION   | STATUS  | AGENT ASSESSMENT | NOTE |
|-------------------------|------------|:-------:|:----------------:|------|
| Safety (Recursion)      | —          |    —    | Looks good       | 0 cycles across the 12 functions this run read — not a zero over the binary |
| Safety (Indirect Calls) | dispatch   | CAUTION | Needs attention  | 3 call sites (sched.c:118, sched.c:121, sched.c:140), targets not in the graph |
| Safety (Rule)           | sched_tick |    —    | Looks good       | "every loop in sched_tick stays bounded by n_tasks": the loop at sched.c:94-99 exits on i < n_tasks |

No bound was computed here — an entry on recursion_cycles or indirect_calls is
judged by `/loci:stack-depth`, and a Rule row's word is the agent's reading.

Verdict: **CAUTION** — 3 indirect call sites in `dispatch`, targets not in the
graph; no bound judged indirect_calls here, so the word is this run's reading
```

The first row's `—` is a contracted run's: no bound was computed, and your
**Looks good** composes it to `PASS` — a reading of twelve functions, not a claim
about the binary. A repo that bounds `recursion_cycles` gets that answer from
stack-depth.

The `CAUTION` is a hazard observed with no entry behind it, which is why the word
is your **Needs attention** beside a `STATUS` the graph itself set. The Rule row's
word is yours alone, and its Note names the exit test the rule rests on.

## Step 5: record it

Apply **[Recording it: one call, on every run that reaches a verdict](../_shared/verdicts.md#recording-the-verdict)**.
`--run` is `data.run`, `--agent-note` carries the cause clause of the `Verdict:`
line you composed — copied, never recomposed — and `--agent-judged` carries
your per-row assessments, each `data.agent_judged` entry's by its `entry_key`. **The run's own word.** Where your verdict rests on something LOCI did not
compute — here, a zero this scope reports unmeasured — or the run is clean and no row says so, send
`--agent-verdict`. Both cases, and which value, are in **[the shared
rule](../_shared/verdicts.md#recording-the-verdict)**.

## Plumbing

`loci analyse cfg` is porcelain over `loci elf asm`, which stays public and
independently runnable for one artifact, functions already known and nothing to
compile:

```
loci elf asm --elf <path> --functions <fn>
```

Same address-sorted sections, same demangling, same `control-flow.txt` — but no
compile-if-stale step, no freshness gate and no run record. `loci elf cfg` is the
CFG text alone.

## LOCI voice remark

One line before the footer: [The voice remark](../_shared/voice.md#voice-remark).
Here the number comes from the graph — a trip count, a block count, a nesting
depth — and a shape worth a second look is stated as an **observation, never a
verdict**, because this skill judges two signals and the shape is not one of
them.

## LOCI footer

There is nothing further to record: `loci analyse cfg` wrote the run record before
it returned, under `--caller control-flow`, and Step 5 has already patched it. Do
NOT call `loci stats record --skill`, `loci stats measure` or `loci stats summary`
— the only `stats record` call this skill makes is Step 5's, which names `--run`.

Append the footer as the last thing printed, **only if N > 0**. If no functions
were rendered, do NOT emit the footer.

One form, always:

```
─── LOCI · control-flow ────────────────
  <N> fn · <shape>
  <icon> <PASS | CAUTION | FAIL | INCOMPLETE>
────────────────────────────────────────
```

- **N** — unique functions whose CFG was rendered — `data.functions`.
- `<shape>` — the graph in three words or fewer: `no loops`, `<K> loops`,
  `<K> loops · <U> unknown trips`, `<K> loops · <C> cycles`, `recursion`.
- `<icon>` — mirrors the run verdict: `✅` PASS, `🔶` CAUTION, `❌` FAIL.
  `INCOMPLETE` takes the word and no icon. A `PASS` no bound was compared for
  still takes ✅ — the Note and the run's `contract` field are what carry that,
  not a dimmer icon.

**The footer carries no sentence.** The run verdict is printed once, under the
conclusion table, with this same icon in front of it.

Worked example:
```
─── LOCI · control-flow ────────────────
  3 fn · 4 loops · 1 unknown trips
  ✅ PASS
────────────────────────────────────────
```
