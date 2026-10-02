# When no function changed

Reference for `/loci:loci-post-edit`. Step 2a in full, with its three report templates: the questions to ask the pair when the differ reports nothing, and what each answer prints.

---

## Step 2a: no function changed — ask the pair what else moved

**Case A only** (a run with no Before has nothing to compare against), and only when
Step 2's function list came back empty. Steps 3, 4 and 4a are skipped in full, and
so are Steps 5 and 6 — their mandatory reasoning pass and conclusion table are about
per-function timing rows this branch does not produce. Step 7 is **not** skipped. No
manifest is measured, so nothing is billed. This step's own report, below, is the whole
output.

`<before>` and `<artifact>` below are the `before` and `artifact` fields of this
source's `data.provenance[]` entry from Step 1 — substitute the printed literal
values, and build neither path yourself.

**A source in `data.compiled_out` leaves this branch.** Its object holds no code (a
false `#if` around the file, or the file left out whole), so its pair has nothing to
answer. For it print `## Post-Edit: <source file> — not compiled`, then
`Verdict: **INCOMPLETE** — <source file> compiled to no code, so nothing was
measured.` and the entry's `reason` verbatim. Any other source goes on below. Step 7
sends `--agent-verdict no_opinion` with that cause clause as `--agent-note` when no
other source's report gave a word; otherwise that word, with this clause added to
its note.

**First, separate a deletion from a quiet edit.** `prepare` compares masked
instructions, so the empty list is not "the edit changed nothing" — and a deleted
function is filtered out of that list too, because it has no After to measure. Both
answers are already on the Step 1 envelope: **[A function this edit
deleted](../_shared/compile-route.md#removed-functions)**. Read
`data.removed_functions` and `data.orphaned_entries` there; run no diff of your own,
and do **not** widen to every function in the object instead.

Then run the two calls in **[What the differ does not answer](../_shared/compile-route.md#beyond-the-diff)**
— one Bash call, two local commands, no quota — and read `data.summary_delta`,
`data.symbol_deltas` and `data.frame_deltas` out of their envelopes. They exist
because `elf diff` compares masked instructions
inside functions, and a grown `const` table, a longer string literal, a grown static
array and a bigger stack frame all reach this branch reporting `{0,0,0,0}` — measured,
in that section's table.

Then one of two reports.

**Before either of them, read `data.unscoped_units`.** When it is present there was no
Before at all, so `prepare` named no function of its own — re-run it once with
`--functions` naming what you edited (see the rule above) and report on that manifest.
If you have nothing to name, this is not the quiet report either: say the unit went
unmeasured for want of a baseline, name it and the functions it lists, and relay the
`withheld` sentence.

**First, read `data.unattributed_changes` from the `prepare` envelope.** When it is
present, `prepare` is telling you the artifact **did change** and the differ could not
say which function — it matches by size, so a same-size edit (`ADD R1, R2, R2` becoming
`ADD R1<<1, R2, R2`) produces no entry at all. That is a different report from the quiet
one, and it is not optional: **do not write "no measurable change" when this field is
present.** Say that the binary changed, that the change could not be attributed to a
function at this scope, and name the artifact it lists. Everything below still applies —
a moved loop bound is still worth finding — but the closing report is the changed one.

**One more check before the quiet report: did a loop bound move?** A changed loop
bound is a masked immediate, so it reaches this branch reporting `{0,0,0,0}` — and it
is one of the most consequential timing edits there is. The CFG's trip count sees it
where the differ cannot. Two free calls, one per side, no quota:

```
loci analyse cfg --elf "<before>"   --turn "<turn-id>" --caller loci-post-edit
loci analyse cfg --elf "<artifact>" --turn "<turn-id>" --caller loci-post-edit
```

`data.loops` is keyed by function, and each function's `rows[]` is the loop table.
Compare the two printed envelopes row by row on `id`, `trips` and `trips_known`: a
`trips` that moved is the bound that moved, and a `trips_known: false` on either
side means the count is a lower bound, never a `1`. This verb renders the graph
itself to a file and puts only the summary in the envelope, so both calls print
small.

**No `--functions` here, deliberately — on an OBJECT.** Both paths are then the
object of the *one* translation unit this source compiles to, so every function in it
is a function this source defines, which is the set this check wants, and `analyse cfg`
renders all of them when `--functions` is omitted. There is no list to name: Step 2
found nothing changed, so `prepare` proposed nothing, and the diff file holds only
changed functions — it never carries an `unchanged` row to read names out of
(**[Diffing the pair](../_shared/compile-route.md#elf-diff)**).

> **On a LINKED artifact — `provenance[].kind: "elf"`, which is every Go project —
> do not run the two calls above.** The reasoning behind omitting `--functions`
> does not hold there: the paths are the whole program, not one translation unit,
> so "every function in it" is the Go runtime, its GC and its scheduler. Measured
> on a two-function Go module: 44 s + 46 s, 3649 functions, 484 MB resident, per
> edited file, on top of the rebuild. It is also the wrong question — none of
> those functions is one this source defines. Report from
> `data.unattributed_changes` instead (above) and stop; if you want a loop check
> on a Go binary, name the functions you actually care about with
> `--functions`.

Compare the two maps per function and loop `id`: the trip count is data (`trips`, with `trips_known: false`
when it reads `?`), not a text diff of two files. Both sides empty means neither has a
countable loop, and the quiet report stands as written. A changed `trips` on any loop is
a **measured finding**, not a caveat, and it leaves this branch:

```
## Post-Edit: <source file> — loop bound changed

| ENTRY                    | FUNCTION | STATUS | AGENT ASSESSMENT | NOTE |
|--------------------------|----------|:------:|:----------------:|------|
| `Performance (Hot-Path)` | `<fn>`   |   —    | Needs attention  | no function's instructions changed — the bound is a masked immediate; the loop at <file>:<lines> goes 8 → 16 trips (exact), so every block in it now runs twice |

Verdict: **CAUTION** — the cost of that loop's body doubles. No `hot_path_time`
bound in this repo, so this is an argument rather than a breach.
```

**Name the loop by where it is in the source, never by its `id`** — the shared
contract's **Naming a path or a loop in the report** is the rule, and it governs
candidate ids here too. The `L1`/`L2` in `analyse cfg`'s loop table and the `p1`/`p2`
in the manifest are counters LOCI made up for `--select`; the reader has never seen
either. Use the source range the loop's blocks map to, or — when no line map is
available — `the loop at <header>` or just `a loop in <fn>` when the function has only
one. Carry the trip counts with it, as the row above does: they are the assumption the
"runs 2x" claim rests on. If the user asks which loop or which path, name the id then.

Do NOT go on to Steps 3–5 for it: no function's instructions changed, so there is no
new per-block timing to fetch, and the trip-count ratio already states the effect.
`analyse cfg` writes each side's files under its own `dumps/<stem>-<hash>/` in the turn tree,
so the two calls never overwrite each other — pass no `--out-dir`. Each call records a
verdict-less `analyse cfg` run under `loci-post-edit`; that attribution is intended.

**Nothing moved** — the ROM and RAM deltas are both `0`, `data.frame_deltas` is
empty, **both calls answered `ok:true`**, `data.removed_functions` is absent, and the loop
check above found no changed trip count. All five, because a refusal means a check
did not run and an empty `frame_deltas` then proves nothing. Say what was checked,
and say what none of it can see:

```
## Post-Edit: <source file> — no measurable change

Verdict: **INCOMPLETE** — nothing was measured, so nothing is judged.

Four checks agree: no function's instructions changed, ROM/RAM in this translation
unit is unchanged, no worst-case frame moved, and no loop's trip count moved. No
table: every row would be `—` with nothing beside it, and `verdicts.md` draws no
INCOMPLETE row.

The first three compare shapes and sizes, never values, and the fourth reaches only a
bound whose trip count could be derived — so an edit that changes only a **value** at
the same size is invisible to all four: a constant in code (a threshold, a timeout, a
loop bound the trip count could not be derived from), or the contents of a `const`
table or an initialised array. If that is the edit you made, its cost is real and is
not in this report.
```

That last paragraph is not a hedge to be trimmed, and it is deliberately wider than
"constants" — narrower than it used to be by exactly one case, the derivable loop
bound, and no wider. Measured three ways: `return v + 4928u` → `v + 19840u` gives
`{0,0,0,0}`,
ROM 157 → 157, no frame delta; `const uint32_t coeff[8] = {1..8}` → `{9,9,…}` gives the
same three answers on objects differing in 216 bytes; so does the same edit in `.data`.
The differ never reads `.rodata`, and `memmap` compares each symbol's *size*, which did
not move. A retuned lookup table is one of the commonest embedded edits there is, and a
report claiming "nothing changed" is wrong for every one of them.

**Something moved** — report it, with the numbers as printed:

```
## Post-Edit: <source file> — no function changed

| ENTRY        | FUNCTION | BEFORE | AFTER | STATUS | AGENT ASSESSMENT | NOTE |
|--------------|----------|--------|-------|:------:|:----------------:|------|
| `Memory (ROM)` | —      | 137 B  | 361 B |  PASS  | Looks good       | +224 B in this translation unit, all of it in `lut`; no function's instructions changed, RAM static and worst-case frames unchanged |

No contract in this repo — every STATUS above is composed from the agent
assessment beside it, not from a bound. `/loci:contract` records the limits this
project actually has — a stack ceiling, a timing or energy budget, a ROM or RAM
region — and the next run judges these same figures against them.

Verdict: **OK** — +224 B ROM, all of it in `lut`; no `rom_size` bound in this
repo to judge it against.
```

Five rules for that report:

- **A deletion is not "no function changed".** A deleted function has no After
  to measure, which is exactly why this branch is the right one — but it is not the
  quiet answer, and the two must never be reported the same way. When
  `data.removed_functions` is present, title the report for those names and expect the
  ROM total to corroborate with a negative delta. When `data.orphaned_entries` is
  present too, add its line — the contract now bounds code that is gone.
- **Say which question each row answers.** A row that just reads "unchanged" next to a
  gate name is what made the differ's silence look like an answer in the first place.
- **The ROM/RAM numbers are this translation unit's**, never the firmware's — say so in
  the row label, and never send them to `loci contract check`. The contract's
  `rom_size` / `ram_size` bounds are firmware-scale.
- **A bound is what turns a delta into a verdict.** When something moved, ask whether
  the repo bounds it and let the owning skill measure the right artifact. Step 1's
  envelope already answered that — `data.requests[]` is the list, it is in this turn's
  transcript where `prepare` printed it, and no second `loci` call is made. Each entry
  carries `signal`, `gate` and, for a per-function bound, `fn`. Read it there; do not
  re-open the manifest, and never assemble a manifests path yourself.

  A `rom_size` / `ram_size` row and a moved ROM or RAM total → invoke `memory-report` once
  (it measures the linked binary, which is what the bound is about). A `stack_depth`
  row and a `data.frame_deltas` entry → invoke `stack-depth` once, passing that row's
  `fn` values as `--entry-functions`; a whole-binary row has **no `fn`** at all, and
  there `stack-depth` runs without the flag. Either way, hand it this run's manifest id
  as its `--parent-run` (Step 4a says why). With no contract there is no such row to
  read and the call is yours to argue for or skip — Step 4a's rule. Nothing coming
  back is the common case and needs no apology: report the delta and say plainly
  that no bound was checked.
- **A refusal means unmeasured, not unchanged** — carry its `error.message` into the
  report verbatim, and never let an empty `frame_deltas` beside a refusal be read as
  "no frame moved". Two causes are worth naming when you see them: `not signed in` is
  the auth gate (`loci elf` needs a session — tell the user to run `! loci login`), and if the
  CLI is below **0.1.107** — resolved through the shared contract's [Reading the CLI's
  version](../_shared/house-rules.md#cli-version-gate), never off the
  `loci command:` line, which carries no number — the frame half is uninformative on
  this install (every frame reads as the push size): say that and offer `/loci:setup`
  rather than reporting frames as unchanged.

Nothing is metered on this branch, so `M` is 0 and `N` is 0 and the footer is
suppressed by its own `N > 0` rule. **Step 7 still runs.** This branch printed a
`Verdict:` line and that line is the entire run, so the sentence under it is the one
the cockpit most needs; `--run` takes the manifest id Step 1 returned, and the run line
is written by that call because nothing measured one into existence. An escalated skill
invoked above emits its own report and footer; that is theirs, not this skill's. The
one other thing this branch prints beyond its own report is the qualified basis, when
there is one — **Say once when the basis is qualified**, below, is not gated on `N`.
