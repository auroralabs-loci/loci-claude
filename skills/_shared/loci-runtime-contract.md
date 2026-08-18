# LOCI runtime contract (shared)

Canonical instructions shared by the LOCI analysis skills. A skill's `SKILL.md`
points here and names the sections it needs; read those sections, then return to
the skill body for its specifics.

This file is **not** a skill — it has no frontmatter and no `SKILL.md`, so it is
never auto-invoked or advertised as a slash command. It is a reference document
the skills read on demand.

The detection of compiler / flags / build system / architecture happens **once**
at session start (`hooks/session-init.sh` → `lib/detect-project.sh`), and the
results are persisted and injected into the session. Skills **consume** those
results — they never re-detect. Deeper build-flag discovery, when a skill needs
it, is done on demand via `loci build compile` (which runs the flag cascade).

---

## Session context placeholders

All analysis runs through the **`loci`** command — a single executable on PATH,
installed by the session bootstrap. Always invoke it as a bare `loci …`; there is
no script path or venv Python to substitute.

Read these values from the LOCI session context (the `system-reminder` block
emitted at session start) and substitute them wherever the placeholders appear:

- `LOCI target: <arch>` → use as `<loci_target>` (one of `aarch64`, `armv7e-m`, `armv6-m`, `tc399`)
- `plugin dir: <path>` → use as `<plugin-dir>` (to locate shared docs like this contract)

All skills read this one; the Pattern B skills depend on it:

- `project context: <path>` → use as `<project-context>` (the persisted detection
  JSON). The compile-the-source skills (preflight, post-edit) pass it to
  `loci build compile`; the Pattern B skills read `elf_files` and `loci_artifacts`
  out of it when choosing a binary.

The Pattern B skills read two lists out of that same JSON when picking a binary
(see **Step 0 — Pattern B**):

- `elf_files` — linked binaries and objects found in the project's own build.
- `loci_artifacts` — artifacts **LOCI** produced under `.loci-build/`, each as
  `{path, mtime, kind}` with `kind` one of `linked` / `object`, newest first. Kept
  separate from `elf_files` on purpose: these are the ones whose provenance LOCI
  knows exactly, and the two are ranked differently.

### Reporting versions to the user

The context carries two versions, and they are released separately:

    loci version: 0.1.105                     ← the plugin
    loci command: loci (on PATH, v0.1.104)    ← the CLI

Report the plugin version as *the* LOCI version, and never show the two side by
side. Surface the CLI version only in `/bug-report`. If the CLI is genuinely too
old, say so as an action (run `/loci:setup`), not as a number.

---

## Prerequisites: `jq` and `uv` (checked, never installed)

`jq` and `uv` are host tools the plugin **detects but does not install** — they
are prerequisites for the scripts and `loci` commands the skills run. `jq` parses
every loci envelope (below); `uv` installs the loci CLI.

**Check them up front, before running a loci command — don't wait for a failure.**
Probe with `command -v jq` / `command -v uv`. If either is absent, **you**
determine the install command for the user's OS and package manager, give it to
them as `! <command>`, and stop the current loci path until they have run it. Do
**not** install jq or uv yourself. Pick the command by platform:

- **jq** — `sudo apt-get install -y jq` (Debian/Ubuntu), `sudo dnf install -y jq`
  (Fedora/RHEL), `sudo pacman -S jq` (Arch), `brew install jq` (macOS),
  `winget install jqlang.jq` (Windows).
- **uv** — NOT in Debian/Ubuntu apt. Use `curl -LsSf https://astral.sh/uv/install.sh | sh`
  (Linux/macOS) or `pipx install uv`; `sudo pacman -S uv` (Arch),
  `brew install uv` (macOS), `winget install astral-sh.uv` (Windows).

---

## Tool boundary: `loci elf` only

All assembly, CFG, symbol, section, and ELF inspection goes through
`loci elf …`. Do **not** use `objdump`, `readelf`, `addr2line`, `nm`, or
`size` as substitutes — `loci elf` produces the LOCI-ready output (annotated
CFG, per-block timing CSV, symbol map, frame and section data) that binutils
cannot. If a `loci` command returns an error envelope (`{"ok": false}`), surface
its `error.message` and stop; do not fall back to objdump or other disassemblers.

Always pass `--arch <loci_target>` on every `loci elf` call, reading the value
verbatim from the SessionStart `LOCI target:` line. Do not guess or retry with
alternative architecture names — the pipeline expects exactly one of `aarch64`,
`armv7e-m`, `armv6-m`, `tc399`.

**Exception:** `loci elf memmap` auto-detects architecture from the ELF and
does **not** accept `--arch` (used only by memory-report). Every other `loci elf`
subcommand requires `--arch <loci_target>`.

---

## Output: the JSON envelope

Every `loci` command prints **one JSON document on stdout**:

- success → `{"ok": true, "data": {…}}`
- failure → `{"ok": false, "error": {"message": "…", "code"?: "…"}}`

Parse it with `jq` and branch on `.ok` — never on substrings of the human text.
Diagnostic/progress logs go to **stderr**; captured stdout is always the envelope.
Two error `code`s are stable and must be handled deterministically:

- `auth_required` (exit 3) — not signed in / token expired. Tell the user to run
  `! loci login`, then stop the current path cleanly (see each skill's auth gate).
- `quota_exceeded` (exit 4) — usage limit reached; surface `error.message`
  verbatim and stop the backend path.

Bulky text (assembly, CFG, diffs) is **written to files** somewhere under
`.loci-build/elf/` — the exact directory name depends on the installed CLI (newer
ones append a hash so two binaries with the same stem cannot share it), so it is
never one to spell; the envelope's `data` carries the paths (e.g.
`data.control_flow`, `data.timing_csv`, `data.diff_file`). Read those files by path
rather than expecting the text inline. Some verbs are **size-adaptive**: `elf
symbols` returns the table inline under `data.symbols` when it is small and only
spills to `data.symbols_file` when it exceeds `--inline-threshold` — branch on
`data.payload` (`"inline"` vs `"file"`). Either way the answer is in the single
envelope you already have; never re-run a verb to re-read its own output.

---

## The Contract Envelope is input only

`.loci/contract.yaml` holds the bounds this repository requires — stack, timing,
energy, memory, and structural invariants. Read it with `loci contract show` and
judge your findings against the **enabled** entries, quoting an entry's `text`
when you report a verdict so the user hears their own words back.

**You never change it.** Not with Edit/Write, and not with the CLI verbs that
write it (`accept`, `init`, bare `edit`/`disable`/`enable`) — a hook denies all
of them. Only `/loci:contract` drafts changes, and only the **user** applies one.

A breach is **reported, never resolved by moving the bound.** If a measurement
exceeds a budget, say so with the numbers; do not propose loosening the entry, do
not suggest disabling it, and do not mention that either is possible. The entry
is the requirement — your job is to report against it, not to negotiate it.

Fields you will read: `text` (the intent, verbatim), `kind`
(`budget`/`regression`/`invariant`), `function` (absent ⇒ whole binary), `signal`,
`bound` (`max`/`min`/`max_delta`, with `unit`), `severity` (absent ⇒ `warn`), and
`enabled` (`false` ⇒ skip it entirely). An entry with no `signal`/`bound` is a
sentence — judge it yourself and say that you did. An entry whose `signal` you do
not recognise is the same case; never substitute a signal you do know.

## One fact, one row: the entry decides the status

Where an enabled entry covers a fact you would also have classified on your own —
a recursion or indirect-call hazard under `Safety`, a depth you would have judged
against your own 50%/80% thresholds — **the entry decides that row's status**, and
the row quotes its `text`. Your measurement is the evidence in the Note, not a
second verdict on it. Take the icon from `severity`, of which there are exactly
two: `fail` ⇒ ❌, `warn` (and absent) ⇒ 🔶. An entry carrying any other severity
is malformed — `contract check` reports it under `unjudged` with the reason, and
you report that rather than guessing a status for it.

Your own thresholds apply only where **no** enabled entry covers that signal. That
is the **When there is no contract** rule below, applied per signal rather than per
run: a contract holding one stack budget does not silence your built-in judgement
of everything else.

Never emit both your own row and a contract row for one fact. Two verdicts on one
fact is how a 🔶 of your own and a ❌ from the user's stated requirement end up in
the same table with nothing saying which of them gates the run.

**A soundness caveat is not a verdict** and is never displaced by an entry: "this
depth is a lower bound because a callee is missing" qualifies what the number
means. Keep those caveats whatever the contract says — including on a row whose
status an entry just decided.

## Every row says where its bound came from

A conclusion table mixes bounds the **user stated** with bounds **LOCI brought**,
and the two are not the same claim: one is a requirement this repository
committed, the other is a default that happens to be reasonable. Rendered
identically they read as one authority, and the reader cannot tell which of their
own requirements is failing from which of ours.

So every conclusion table carries a **`Basis`** column, one value per row:

| `Basis` | The bound is | Set by |
|---|---|---|
| `contract` | an enabled entry in this repo's `.loci/contract.yaml` | the user |
| `starter` | LOCI's starter bounds — the repo has no contract, and `contract check` fell back to them (`data.contract.source == "starter"`) | LOCI, stated |
| `LOCI` | the skill's own built-in threshold; **no** entry covers this signal | LOCI, internal |

Rules:

- **It describes the BOUND, not who did the arithmetic.** An entry you judged
  yourself under `agent_judged` is still `contract` — the requirement is the
  user's either way, and that the model read it is already carried by the 🔶 cap
  and the block/callee/instruction the Note has to cite.
- **`starter` and `contract` are never mixed in one run.** The fallback is
  whole-envelope: either the file exists and every checked row is `contract`, or
  it does not and every checked row is `starter`. A run showing both is a bug.
- **`LOCI` rows are per signal, not per run.** A contract holding one stack
  budget does not make the timing row `contract` — see the rule above.
- **Do not add a second table, a heading or a blank-line group** to separate
  them. The rows are ordered by gate and the verdict is the worst of them; a
  split makes that read as two verdicts. The column is the separation. (The
  cockpit's contract panel reached the same conclusion and dropped its gate
  groupings for it: with rows already named by their gate, the headings restated
  the first column and cost more lines than the table had rows.)
- Keep the column even when every row shares a value — a table of all-`LOCI`
  rows is exactly the case where the reader most needs to know that none of it is
  theirs.

## Structural invariants: which measurement answers which signal

The four structural signals are the ones nothing was mapping to a measurement, so
`loci contract check` filed them as "no measurement supplied" and the report
dropped them as routine. They come from one `loci elf stack` run over a **linked**
binary, and each one's `curr` is a **count** — the signal has no unit:

| Signal | Read from | `curr` is |
|---|---|---|
| `unbounded_recursion` | a recursion warning whose cycle has no visible exit condition — `--max-recursion-depth` bounded it by fiat, not by the code | cycles that could not be bounded |
| `recursion_cycles` | `has_recursion`, and the recursion `warnings` | distinct cycles, bounded ones included |
| `unresolved_indirect_calls` | `has_indirect_calls`, and the indirect-call `warnings` | call sites with no statically resolved target |
| `unknown_callees` | `has_unknown_callees`, and the unknown-callee `warnings` | distinct symbols missing from the binary |

Three rules make the mapping usable:

- **Report the zero.** A clean run measures `0` and must say so against the entry.
  A bound nothing measured is filed as unjudged, and unjudged is invisible — which
  is exactly how these four went two-thirds of the starter contract unnoticed.
- **They are whole-binary, always.** The contract rejects a `function` on a
  structural signal (`scope_unexpected`), so there is no per-function structural
  bound to judge. A hazard you found in one function is evidence *for the
  whole-binary entry*; name the function in the Note, not in the scope.
- **A `.o` cannot answer them.** In a relocatable object the call edges are
  unapplied relocations, so `has_unknown_callees` reads `false` for a binary whose
  callees were simply never linked (Pattern B, B1). From a `.o`, the invariant is
  **unmeasured** — say that. Never report `0` for it.

<a id="loop-cost"></a>
## Loop cost: a block on the hot path does not run once

`loci timing` costs a block **once**. A block inside a loop runs once per lap, so
a worst path summed block-by-block understates a function by a factor of its trip
count — a 16-tap filter reports about a sixteenth of its real cost. This is the
same class of error as treating a bare `bl` as a call site's full cost, and it is
usually larger.

The CFG answers it. `loci elf asm` / `loci elf cfg` annotate every block inside a
loop with **`iters`** — how many times it runs per single call of the function,
already multiplied out across nested loops — plus a per-function `loops:` block
carrying each loop's header, latch, nesting depth, trip count and the evidence
behind it:

```
loops: 2 (1 with a derived trip count; max nesting depth 2)
  L1  loop  header bb_0x1c  latch bb_0x3c  depth 1  blocks 3  trips 16 exact  (init r5=0 @bb_0x14; step +1 …)
  L2  loop  header bb_0x48  latch bb_0x58  depth 2  in L1  blocks 2  trips ?  (no single induction step for r3)

[fir_0x28] 0x28-0x38  (in L1, iters=16)
```

### The rule

**Expand `bl` / `blx` sites first, then multiply.** A callee called inside a loop
costs `iters × (bl_cost + callee_body)`, not `bl_cost + iters × …`. Doing it the
other way round counts the callee once and is the commonest way to get this wrong.

Then, per hot-path block:

```
block_contribution = (execution_time_ns [+ std_dev_ns where the skill uses worst]) × iters
```

Four cases, and the annotation tells you which:

| Annotation | Meaning | What you report |
|---|---|---|
| no `iters` on the block | in no loop — runs once per call | the block cost as-is |
| `iters=16` | derived count | cost × 16 |
| `iters<=16` | the loop has another exit, so 16 bounds it from above | cost × 16, and say it is a max — a ceiling is the right number for a budget |
| `iters=?` | no trip count is derivable from the code | **the total becomes a lower bound** |

### `?` is not 1

An unknown trip count makes every total that crosses it a lower bound, and it is
reported exactly like an unmeasured external callee: prefix the figure with `≥` and
append `(≥ <total> ns — loop <L#> trip count unknown)` to the Note of every row
whose path includes that block. **Never substitute a number of your own** — not
from the source, not from a plausible buffer size, not from "typically". A
fabricated count is wrong in the same direction every time, and it is wrong
*silently*, which a `≥` is not. The `loops:` line names the reason (`no single
induction step for r3`, `no constant initialiser for r2 reaching bb_0x610`); quote
it when the Note has room.

If the loop's bound is knowable but not from the instruction stream — a
`#define`, a caller-supplied length the project fixes elsewhere — that is a fact
for the repository's Contract Envelope to declare, not for you to assume. Say so
once and move on.

### Bound the work

A single firmware function can carry dozens of loops. Read `iters` for every
hot-path block, because that is just arithmetic — but **report** at most the top
3 loops by `block cost × iters`, then one line: `<k> further loops folded in`. The
loop detail belongs in the breakdown block, not in the conclusion table.

### Recursion is not iteration

A loop the CFG names `R1` instead of `L1` is a recursive call. Its blocks carry
`iters=?` and that stays `?`: recursion depth is `stack-depth`'s question, not a
trip count. A `cycles:` line — a backward edge that is no countable loop, or one
on blocks the entry cannot reach because the CFG is incomplete — means the same
thing: those blocks may run more than the annotation suggests, so the total is a
lower bound and the reason is worth one line.

### There is no capability check — read the annotations that are there

**Do not gate any of this on whether the build "supports" loop annotation.** Multiply
a block by the `iters` on that block; a block without one runs once. That is the
correct arithmetic either way, and it needs no question answered in advance: absent
annotations mean the function has no loops, or the build predates them, and the sum is
the same in both cases.

This is not a simplification, it is a correction. The envelope used to carry an
`annotated` capability flag derived from the installed CLI's version number. The number
it compared against never matched the release that shipped the feature, so the flag
read `false` on builds whose CFG said `loops: 1 (1 with a derived trip count)` — and
because this section used to say "false means do not use `iters`", the flag switched the
whole feature off with correct `iters=64` values sitting unread in the file. Never
reintroduce a gate of that shape; the graph in front of you is the evidence.

The roll-up is still worth reading, for triage rather than permission:

```
env=$(loci elf asm --elf "<artifact>" --functions "<fns>" --arch <loci_target>)
jq -c '.data.loops' <<<"$env"
# {"total":2,"with_trip_count":1,"unknown_trip_count":1,"recursion":0,"uncounted_cycles":0}
```

`unknown_trip_count` or `uncounted_cycles` above zero is your advance warning that some
total on this run is a lower bound.

### The recorded number changes meaning

A loop-aware `worst_ns` is **not comparable** with a loop-blind one already in the
measurement store — the same trap as diffing exec-trace's throughput time against
response time. `loci stats trend-line` cannot see the difference, so the first
loop-aware run for a function shows a step change that is the metric moving, not
the code. When the trend line's earlier points predate this and the jump is large,
add one line under the footer:

```
Trend note: earlier points for <fn> are loop-blind (loops counted once); the step is the metric, not a regression.
```

Say it once per function, then let the series continue.

## When there is no contract

`data.exists: false`, or zero enabled entries. This is a **first-class state**,
not a warning and not a degraded mode.

Report your measurement **exactly as you would otherwise** — same numbers, same
table, same verdicts against the skill's own built-in thresholds. Then, after the
report, add at most **one** line:

> No bounds are set for this repository — `/contract` sets them up.

Rules on that line: after the report, never before it and never instead of it;
**once per session**, not once per skill run; and never at session start. If you
have already said it this session, say nothing. If the user declines, drop it for
the session and do not raise it again.

---

## Supported architectures (gate)

Map the LOCI target to the loci MCP supported architectures and binary targets:

| LOCI target | CPU       |
|-------------|-----------|
| aarch64     | A53       |
| armv7e-m    | CortexM4  |
| armv6-m     | CortexM0P |
| tc399       | TC399     |

The CPU column identifies the real silicon hardware the LOCI timing and energy
predictions are traced from.

If the architecture is **not** in this table, emit and stop:

    Supported: aarch64, armv7e-m, armv6-m, tc399

(Skills that also gate on a missing compiler — preflight, stack-depth,
memory-report — state that stop inline; it is intentionally not part of this
shared arch gate so skills that never had it, e.g. post-edit, keep their
original behavior.)

---

## Cross-compilation defaults

Use these defaults **only when the user has no existing build**, or when every
existing binary failed the freshness gate and cannot be rebuilt by its own build
system. Prefer an existing binary (`.elf`, `.out`, `.o`, `.axf`) **that passes
Step 0 — Pattern B, B3** — availability alone is not a reason to measure something,
which is exactly the reading that produced an analysis of a binary older than its
own source. The `<loci_target>` values are the same vocabulary used on every
`loci elf` command.

| LOCI target | Compiler                | Flags                                | Build dir               |
|-------------|-------------------------|--------------------------------------|-------------------------|
| aarch64     | `aarch64-linux-gnu-g++` | `-g -O2 -march=armv8-a`              | `.loci-build/aarch64/`  |
| armv7e-m    | `arm-none-eabi-g++`     | `-g -O2 -mcpu=cortex-m4 -mthumb`     | `.loci-build/armv7e-m/` |
| armv6-m     | `arm-none-eabi-g++`     | `-g -O2 -mcpu=cortex-m0plus -mthumb` | `.loci-build/armv6-m/`  |
| tc399       | `tricore-elf-g++`       | `-g -O2 -mcpu=tc3xx`                 | `.loci-build/tc399/`    |

In the steps that follow, replace `<compiler>` and `<flags>` with values from the
resolved LOCI target. Always include `-g` to emit DWARF debug info (required by
asm-analyze).

---

## Rust / Cargo projects

Applies when the session context shows `Build: cargo` (the project has a
`Cargo.toml`). The front door is unchanged — the same `loci build compile` /
`loci elf` calls — but four Rust-specific rules replace their C/C++
counterparts:

1. **The artifact is one `.o` per crate, named after the crate target**, never
   `<basename>.o` — every crate has a `main.rs` / `lib.rs` / `mod.rs`, so the
   source filename says nothing about where the object goes. **Do not construct
   the path**, and do not assume the spelling: the exact name has already changed
   once between CLI releases. Take every path from the compile envelope
   (`data.output`, `data.meta_file`, and `data.output_prev` / `data.meta_prev`
   when a pre-edit snapshot exists), or from the compile-and-read-back script
   below, which is what a change-measuring skill uses.
2. **Never pass `--meta-prev` yourself on a Rust source.** The cargo route
   inherits the recorded cargo config (features, package, target) from its own
   `.prev` sidecar, and — this is the part that matters — it *withholds* the
   baseline when that sidecar records a different package or target, because such
   a pair is not comparable. Naming a sidecar by hand overrides that refusal. The
   script below handles the standalone-`.rs` case, which behaves oppositely.
3. **Function names are Rust paths.** Query `--functions` with the simple
   name (`run`) or any `::`-suffix (`main::run`, `plumbing::main::run`) —
   both match. Symbol tables, timing-CSV labels, and CFG text carry demangled
   names (`crate::module::fn`); the raw mangled form rides in each symbol
   row's `mangled` field.
4. **No cross-toolchain is required.** Objects are produced by
   `cargo rustc --emit=obj` without linking, so a Windows host can compile
   for aarch64-Linux with nothing but the rustup std
   (`rustup target add <triple>`). If the compile envelope fails with
   `error.code == "rust_target_missing"`, the message contains the exact
   `rustup target add …` command — show it to the user as
   `! rustup target add <triple>`, then stop until they have run it. The
   compiler-not-found recovery (alternate driver names, `--compiler-path`)
   does NOT apply to Rust; never point `--compiler-path` at a C compiler for
   a `.rs` source.

Rust cross-target map (the `<triple>` per LOCI target):

| LOCI target | rustc target triple           |
|-------------|-------------------------------|
| aarch64     | `aarch64-unknown-linux-gnu`   |
| armv7e-m    | `thumbv7em-none-eabihf`       |
| armv6-m     | `thumbv6m-none-eabi`          |
| tc399       | — (rustc has no TriCore backend; Rust analysis unavailable) |

Caveats to surface rather than fight:

- **Tiny functions may have no symbol of their own.** rustc ships small
  `pub`/`#[inline]` functions as MIR for cross-crate inlining — they are
  codegen'd into their callers, not into the defining crate's object. If the
  edited function is absent from `elf diff` / `elf asm` output, say exactly
  that ("`<fn>` was inlined into its callers — measuring the callers
  instead") and analyze the in-crate callers; do not report "no change".
- **Feature flags**: builds use the crate's default features. When a project
  needs specific features (e.g. gix's `--no-default-features --features
  max-pure`), put them in `.loci-build/flags.json`:
  `{"rust": {"no_default_features": true, "features": ["max-pure"]}}`.
- The first compile of a big workspace builds the whole dependency graph
  once (it stays cached under `.loci-build/cargo/`); subsequent compiles are
  incremental. If it exceeds the default 900 s budget, raise
  `LOCI_CARGO_TIMEOUT` and re-run.
- For readable Rust names asmslicer-side too, `cargo install rustfilt` is a
  nice-to-have (loci demangles at its own layer regardless).

---

## Step 0 — Pattern A: compile the source

For skills that compile the analyzed source themselves (preflight, post-edit).

Read the persisted detection results from the `<project-context>` path (the
per-session keyed file, listed as `project context:` in the session context). It
is written by session-init.sh at session start and is the single source of truth
for compiler, architecture, and build system.
**Do NOT re-run detection scripts or fall back to ELF/build-system sniffing.**

    {
      "compiler": "...",
      "build_system": "...",
      "architecture": "...",
      "loci_target": "...",
      ...
    }

If the file does not exist, stop and tell the user:

    LOCI session context not found. Please restart Claude Code so the plugin
    setup runs and detects the project environment.

Then apply the **Supported architectures (gate)** above before any analysis.

Compile the affected source(s) with `loci build compile` — do **not** reuse an
existing `.o`/`.elf` from the project's own build. LOCI needs the compiler, flags,
and version it controls so the pre/post rebuild can diff apples-to-apples:

    loci build compile --source <file> --loci-target <loci_target> \
        --context <project-context> --project-root <project_root> \
        --phase preflight

`loci build compile` resolves the compiler and flags itself from the
`<project-context>` (the persisted detection above) and `--project-root` — you do
**not** pass `--compiler`/`--flags`/`--arch`. It writes the object somewhere under
`.loci-build/<loci_target>/` — directly in it on older CLIs, in a subdirectory
mirroring the source's own path on newer ones — plus a sidecar
`<output>.meta.json`, and returns both paths in the envelope as `data.output` and
`data.meta_file`.

**Pass `--project-root` explicitly**, using `project_root` from the session context.
Left out, the CLI falls back to the shell's own directory — so a skill whose shell
sits anywhere but the project root writes a *second* `.loci-build/` tree, misses the
baseline in the real one, and leaves debris that Pattern B later ranks as a
measurement candidate.

**Take every path from the envelope. Never assemble one.** Where the object lands
is the CLI's choice, not a layout you can rely on: a Rust crate's object is named
after the *crate target*, not the source, and the C/C++ scheme is itself being
keyed on the source's own path so that two modules can each own a `util.c`. A path
built by hand from `<basename>` is a path that breaks silently the next time the
scheme moves — which is what happened, in four skills at once.

**Do not pass `--meta-prev` by hand.** It names a pre-edit sidecar, so using it
means constructing exactly the path the rule above forbids — and on a cargo crate it
overrides a deliberate refusal (rule 2 of **Rust / Cargo projects**). Inheriting the
baseline's flags is the job of the script below, which is told where the object is
rather than guessing.

**If you are measuring a change** (post-edit, or an Incremental Path), do not use
the bare call above — use **[Compile a change and read the artifact paths
back](#compile-and-read-back)** further down. It is one command, it reaches flag
parity with the pre-edit baseline, and it prints every path you need. Preflight is
the exception: it *establishes* the flags a later post-edit inherits, so the bare
call above with `--phase preflight` is correct there.

### If it fails with `compiler_not_found`

Run `loci build compile` **without** `--compiler-path` first — its own cascade
(PATH, `<project-context>`, `compile_commands.json`, globs) resolves the compiler
in the common case. Only if the envelope comes back `ok:false` with
`error.code == "compiler_not_found"` do you step in, in this order:

1. **Cheap retry — alternate driver name only.** The cascade already searched
   PATH, so do not re-scan it or guess install paths. The one thing it may have
   missed is a differently-named driver for the same toolchain: for
   `<loci_target>` try the `-gcc`/`-g++` counterpart and common versioned names
   via `command -v` (e.g. `arm-none-eabi-gcc` vs `arm-none-eabi-g++` for
   armv7e-m/armv6-m, `aarch64-linux-gnu-*` for aarch64, `tricore-elf-*` for
   tc399). If `command -v` returns an absolute path, re-run the **same**
   `loci build compile` with `--compiler-path <abs-path>` appended.
2. **Otherwise, ask the user to point us.** Do NOT hunt through vendor install
   directories yourself. Tell the user the target compiler wasn't found and ask
   them to either give the path to it (you'll re-run with `--compiler-path`) or
   install the toolchain. Keep it to what they need — the tool and that you need
   its path. When they give a path, re-run `loci build compile` with
   `--compiler-path <that-path>` once.
3. **Then stop.** If the alternate-name retry and the user-provided path both
   fail, or the user can't provide one, surface `error.message` verbatim and
   stop. Do not fabricate a path or fall back to a host compiler for a cross
   target.

Only branch on `error.code == "compiler_not_found"` (stable, from `_errors.py`).
Exit code 127 accompanies all compiler-not-found cases but is coarser; the two
other 127 shapes (`--compiler-path does not exist`, `compiler not found on PATH`
at invocation time) are terminal — surface and stop, no retry.

---

<a id="compile-and-read-back"></a>
## Compile a change and read the artifact paths back

**Who uses this:** every skill that measures a *change* — `loci-post-edit`, and the
Incremental Path of `exec-trace`, `control-flow`, `stack-depth` and
`memory-report`. It replaces the raw `<compiler> -g <flags> -c …` line those four
used to carry, and it is the only place any of them learns an artifact path.

One command. Read `plugin dir:` from the session context for `<plugin-dir>`:

```
bash "<plugin-dir>/lib/compile-and-read-back.sh" \
    --source "<source>" --loci-target <loci_target> \
    --context "<project-context>" --project-root "<project_root>" \
    --phase post-edit --turn "<turn-id>"
```

Every value is substituted, and **nothing in that command is optional syntax** — do
not paste `[…]` brackets or a `<a|b>` alternation into a shell, which reaches argparse
verbatim and fails with no envelope to explain it. Fill each placeholder from the
session context (`<loci_target>`, `<project-context>`, `<project_root>`, and
`<plugin-dir>`) and use `--phase post-edit` on any change measurement.

Three flags you may legitimately leave out entirely:

- **`--turn`** — pass it whenever you have the current turn's id; the post-edit hook's
  reminder carries it. It is the only thing that establishes a baseline belongs to
  *this* user turn rather than a previous one, so leaving it out means a delta can
  silently span two turns. Drop the whole flag when you genuinely have no id (a
  standalone `/exec-trace`, say) — the script then says so in a `NOTE` instead of
  pretending the check happened. Never invent a value.
- **`--compiler-path`** — only for the `compiler_not_found` recovery below.
- **`--reconstruct`** — only when the file the user edited is a **header**, and the
  `--source` you are passing is one of the translation units that `#include` it. See
  [Measuring a header edit](#header-edits) below. It needs `--turn`, takes no value
  of its own, and changes nothing about how you read the output: the rebuilt Before
  arrives as `PREV`/`PREV_META` like any other baseline.

It prints tab-separated `key<TAB>value` lines and nothing else:

```
OBJ        the object just compiled
META       its build record (the .meta.json sidecar)
PREV       the pre-edit baseline — an EMPTY value means there is none
PREV_META  that baseline's build record — empty whenever PREV is
NOTE       zero or more; why a baseline was withheld, or how it was established
FAILED     <code> <message>  — the compile produced no artifact
```

### Reading it back — four rules

**1. Take the paths from the output. Never assemble one, never test for a `.prev`
file yourself.** That is the entire point: where the object lives is the CLI's
choice, a Rust crate's object is named after the crate target rather than the
source, and the C/C++ scheme is being keyed on the source's own path so two modules
can each own a `util.c`. Paths built by hand broke in four skills at once.

**2. Substitute the printed values into the commands that follow.** Every fenced
block you run is a *separate* Bash call, so nothing the script set survives into the
next one — which is why it prints rather than exports. Same idiom as
`--csv-file <data.timing_csv>` elsewhere in these skills. Never branch on a variable
nothing printed.

**3. An empty `PREV` is an answer, not a gap.** It means no comparable Before
exists, and a `NOTE` says why. Do not reach around it: a `.prev` file can sit on
disk and still be another source's baseline, or one built with different flags —
using it renders a delta between unrelated artifacts as the effect of the user's
edit. Report the no-baseline (After-only) template instead.

**4. Surface a `NOTE` that explains a missing or caveated Before.** These are the
reasons the user cannot otherwise see — "built from another file", "not built with
these flags", "the turn was not verified". A silently absent Before column reads as
the tool having nothing to say. A CLI new enough states the reason itself
(`data.baseline_withheld`) and the script relays that text unchanged; it comes from
the only layer that saw the capture marker, the turn and the digests, so report it as
*the* reason rather than as one opinion among several.

**`FAILED` ends the compile path.** `error.code == "compiler_not_found"` gets the
recovery above (alternate driver name, then ask the user for a path, then stop);
anything else is surfaced verbatim and you stop. The message carries the CLI's own
remediation, or its stderr when argparse rejected something before any envelope
existed.

<a id="header-edits"></a>
### Measuring a header edit

A header emits no object, so there is nothing to compile and nothing to diff for the
file the user actually touched. What a header *does* have is text, and the
translation units that `#include` it do have objects — so the measurement is: **the
header as it was, plus a rebuild of each affected translation unit against it.**

Three commands, in this order. None of them involves you naming a path or a compiler.

**1. Name the translation units.**

```
loci build affected --source "<edited-header>" --project-root "<project_root>"
```

Read four things out of `data`, and **read all four** — the list on its own cannot be
acted on:

- `translation_units[]` — each has `source` (relative to `data.project_root`, so join
  the two rather than assuming your shell sits in the right place), `via`, and
  `confidence` (`exact` or `heuristic`).
- `coverage_complete` — was every translation unit known to exist examined by
  something? This is about *coverage*, not about the list being cut at `--limit`;
  that is `truncated`.
- `confidence` — `exact`, `mixed`, or `heuristic` for the answer as a whole.
- `coverage` — the denominator: `known_translation_units`, `examined_exact`,
  `examined_scanned`, `unexamined`.

**An empty `translation_units` is not automatically "nothing is affected".** It means
that only when `coverage_complete` is true *and* `confidence` is `exact`. Any other
combination means the search could not see the whole project, and the honest report
is "no affected translation unit was found, and here is what was not examined" — not
silence. `warnings[]` is always present and says which bound bit; carry the relevant
one into the report.

**2. Measure each named unit**, with `--reconstruct` and the turn id from the
reminder:

```
bash "<plugin-dir>/lib/compile-and-read-back.sh" \
    --source "<translation-unit>" --loci-target <loci_target> \
    --context "<project-context>" --project-root "<project_root>" \
    --phase post-edit --turn "<turn-id>" --reconstruct
```

`--source` is the **translation unit**, never the header — the header cannot be
compiled and the CLI will refuse it. `--reconstruct` is what makes `PREV` a rebuild
of the pre-edit state instead of a search for a `.prev` that was never written: this
unit was not itself edited, so nothing snapshotted its object.

From here everything is identical to an ordinary measurement — same `OBJ`/`META`/
`PREV`/`PREV_META`, same rules for reading them, same Case A / Case B.

**Bound the work and say what you bounded.** Measure at most **three** units. The
list is already ordered so the ones with exact evidence come first, so take them from
the top. When there are more, say so in one line: "measured 3 of 7 translation units
this header reaches". A header in a shared SDK can reach dozens, and silently
measuring one of them reads as a complete answer.

**A unit that fails does not end the skill.** The "surface verbatim and stop" rule
for `FAILED` is about the *unit* you were compiling, not about the header. Report
that unit as unmeasured with its message, then carry on to the next one — otherwise
one awkward file costs you every measurement you could have made. It also does not
consume one of the three: three *measured* units is the bound.

Assembly units (`.S`, `.s`) are a known case of this. They are real translation units
and a header edit genuinely reaches them, so `build affected` names them — but
`loci build compile` does not accept the extension. They sort to the **end** of the
list for that reason, and `warnings[]` says so. Report them as reached-but-not-
measurable; do not spend one of your three on one.

**3. Two `NOTE`s mean specific things here**, and they are opposites:

- *"does not read anything that was edited"* — this unit genuinely does not depend on
  what changed. Report it as **unaffected**. It is not a failure and not a gap; it is
  an answer about the code, and the commonest reason is a header whose edit was
  inside an `#ifdef` this unit does not take.
- *"could not be rebuilt"* — the pre-edit state of this unit could not be
  reconstructed, so there is **no Before**. The note carries the CLI's reason; a
  relative quoted include (`#include "../inc/x.h"`) is the usual one, because the
  preprocessor resolves it in the including file's own directory ahead of any include
  path and nothing can be put in front of that. Report the After alone and say why
  there is no Before.

A third one — *"could not be verified as having read the pre-edit copies"* — means
the Before was built but the CLI could not prove it read the captured header. Report
the numbers with that caveat attached, and **treat a reported "no change" with
suspicion**: an unverified rebuild that lost the include search is the current build,
and comparing a build against itself is exactly what produces a confident zero.

### What the script does, so you do not have to
This is background, not instructions — skip it unless something looks wrong.

It resolves the project root once (from `--project-root`, else the context's
`project_root`, else the shell's directory) and passes it explicitly, so a shell
sitting above the project root cannot compile into a second `.loci-build/` tree and
report the real baseline as absent. It asks the installed CLI whether it supports
`--inherit-prev`; when it does, one compile reaches flag parity with the baseline
and the CLI itself vouches for the pair. When it does not — the pinned version is an
exact `==` spec, so an older CLI is a normal state — the script reproduces the
missing checks with verbs that CLI does have: the sidecars must name the same
source, and `loci build diff` must report `match: true`. On a cargo crate it never
second-guesses a CLI that withheld the pair itself, because there that is an
informed refusal about the recorded package or target.

A newer CLI answers the question outright, in `data.baseline_withheld` — so when
that field is there the script prints its `reason` and skips its own diagnosis
entirely. It is a *positive* test on the field, not an inference from the
`--inherit-prev` probe: the two landed in different releases, so an envelope with
no reason means "this CLI does not say", never "the candidate was fine".

<a id="elf-diff"></a>
## Diffing the pair: what `elf diff` answers with

```
loci elf diff --elf "<PREV>" --comparing-elf "<OBJ>" --arch <loci_target>
```

The counts are in `data.summary`; the per-symbol entries are **in a file**, at
`data.diff_file`. `data` also carries `count` and `warnings`, and the two freshness
blocks when the CLI can resolve the artifacts' sources — a diff of two bare objects
outside a project has none, so their absence is not a malfunction. There is no
`data.modified` and no `data.added`; `jq -r` prints the literal `null` for both, and
`--functions null` is accepted by `elf asm` as a name that matches nothing.

The file is a JSON array, most-changed-first:

```
{"status": "modified", "symbol": "adc_read", "stt_type": "STT_FUNC",
 "similarity_ratio": 0.42, "reason": "…"}
```

`status` is `added` | `removed` | `modified` | `unchanged`, and the name is under
**`symbol`** — not `function`, not `name`.

The file lists what *changed*, and only that. The differ writes an `added`, `removed`
or `modified` row and nothing else, so `summary.unchanged` is a status the envelope can
carry rather than one you will see, and **the file's length is not a symbol count** —
do not read "3 entries" as "this object has 3 functions".

Two filters, and both are load-bearing:

- **`status`**, because `removed` symbols are in the file and the function one names is
  gone from the After. `elf asm --elf <OBJ>` cannot extract it.
- **`stt_type == "STT_FUNC"`**, because the differ diffs **variables too**. A changed
  global arrives as an ordinary entry, and `elf asm` answers `ok:true` with
  `function_count: 1`, empty assembly and `timing_csv: null` — success-shaped and
  empty. `elf cfg` fails outright on one.

One fence, which prints both answers:

```
env=$(loci elf diff --elf "<PREV>" --comparing-elf "<OBJ>" --arch <loci_target>)
jq -c '.data.summary' <<<"$env"
jq -r '.[] | select(.stt_type == "STT_FUNC")
           | select(.status == "modified" or .status == "added") | .symbol' \
    "$(jq -r '.data.diff_file' <<<"$env")"
```

The second command prints `{"added":N,"removed":N,"modified":N,"unchanged":N}`. The
third prints one changed function per line — the list `--functions` takes,
comma-separated **and quoted**, in the *next* fence you run (nothing here survives into
it). Check `.ok` first as always: a failed envelope has **no `data` key at all**, so
the third command answers `jq: error: Could not open file null: No such file or
directory` rather than telling you the diff failed.

<a id="elf-diff-empty"></a>
**An empty list does not mean the edit had no effect.** The differ hashes **masked**
instructions — immediate values are replaced before comparison — so an edit that
changes only constants (a loop bound, a buffer size, a threshold, a timeout) produces
**no entry at all**, and the envelope is byte-identical to diffing an artifact against
itself. What an empty list means is *no structural change this differ can see*.

So read `data.summary` before concluding anything, and report accordingly:

- `removed` non-zero, nothing printed → **functions were deleted.** Name them; the
  entries are in the file, they are simply not in the list above.
- every count zero → say the differ saw no change, **and say that constant-only edits
  are invisible to it**. That is an answer about *functions*, not about the artifact:
  go on to [the two questions it does not answer](#beyond-the-diff) before concluding
  that the edit changed nothing. If the user named a function, measure that function
  anyway rather than reporting nothing.
- Do not widen to every function in the object instead — for exec-trace that is one
  metered `loci timing` call per function, spent to say nothing.

When the two groups have to stay apart — extracting a Before only makes sense for a
function that already existed — label them instead:

```
env=$(loci elf diff --elf "<PREV>" --comparing-elf "<OBJ>" --arch <loci_target>)
jq -c '.data.summary' <<<"$env"
jq -r '[.[] | select(.stt_type == "STT_FUNC")
            | select(.status == "modified" or .status == "added")]
       | group_by(.status)[] | "\(.[0].status)\t\([.[].symbol] | join(","))"' \
    "$(jq -r '.data.diff_file' <<<"$env")"
```

It prints at most `added<TAB>…` then `modified<TAB>…`, in that order; a missing line
means that group is empty. It sets `env` itself because it is its own Bash call — the
rule that nothing survives between fences applies to this section too.

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
out="<project_root>/.loci-build/elf/pair-$(basename "<OBJ>")"

mm=$(loci elf memmap --elf "<PREV>" --comparing-elf "<OBJ>")
[ -n "$mm" ] || mm='{"ok":false,"error":{"message":"loci elf memmap printed nothing"}}'
jq -r 'if .ok | not then "NOTE\tfootprint not compared: \(.error.message)"
       else .data.summary_delta as $s
            | "ROM\t\($s.rom_total.base)\t\($s.rom_total.current)\t\($s.rom_total.delta)",
              "RAM\t\($s.ram_static_total.base)\t\($s.ram_static_total.current)\t\($s.ram_static_total.delta)",
              ((.data.symbol_deltas // {}) | (.rom // []) + (.ram // [])
               | .[] | "SYM\t\(.name)\t\(.status)\t\(.delta // .size // 0)")
       end' <<<"$mm"

a=$(loci elf stack --elf "<PREV>" --arch <loci_target> --out-dir "$out/before")
b=$(loci elf stack --elf "<OBJ>"  --arch <loci_target> --out-dir "$out/after")
if jq -e '.ok' >/dev/null 2>&1 <<<"$a" && jq -e '.ok' >/dev/null 2>&1 <<<"$b"; then
    jq -s -r '(.[0] // {}) as $x | (.[1] // {}) as $y
              | [($x + $y) | keys[]] | .[] as $f
              | ($x[$f].frame_size // "-") as $p | ($y[$f].frame_size // "-") as $c
              | select($p != $c) | "FRAME\t\($f)\t\($p)\t\($c)"' \
        "$(jq -r '.data.stack_analysis_file' <<<"$a")" \
        "$(jq -r '.data.stack_analysis_file' <<<"$b")"
else
    msg=$(printf '%s\n%s\n' "$a" "$b" | jq -r 'select(.ok | not) | .error.message' \
          2>/dev/null | tr '\n' ' ')
    printf 'NOTE\tframes not compared: %s\n' "${msg:-loci elf stack printed nothing}"
fi
```

**`--out-dir` on each side is load-bearing, not tidiness.** Every released CLI keys the
default output directory on the artifact's **bare stem**, so a Before and an After that
share a basename — which is exactly what the header route produces, a reconstructed
`…/turns/<key>/obj/<slot>/src/blink.o` against `.loci-build/<target>/src/blink.o` — both
write `.loci-build/elf/blink/stack-analysis.json`. The second call overwrites the first,
both envelopes name the same file, the comparison reads one side twice, and **no `FRAME`
line can ever print**. Measured on 0.1.102; the same shape as the `elf asm` out-dir trap
in [Diffing the pair](#elf-diff).

Every line is TAB-separated, and **only differences print**:

- `ROM <before> <after> <delta>` and `RAM <before> <after> <delta>`, in bytes. These
  two always print when the call answered; `0` for the delta is the real "unchanged".
- `SYM <name> <status> <bytes>` — the symbols behind that delta, when the CLI
  attributed it. `status` is `changed` | `added` | `removed`, and the last column is the
  **delta** for a changed symbol and the symbol's own **size** for one that arrived or
  went: those entries carry `size` and no `delta` at all, so a recipe printing `\(.delta)`
  puts the literal `null` in the report. `symbol_deltas` is itself sometimes `null`,
  which is why the recipe guards that too; no `SYM` line does not contradict a non-zero
  `ROM`/`RAM`.
- `FRAME <function> <before> <after>` — one per function whose own frame moved. No line
  means none moved. A `-` on either side is **not a zero frame**: it means that function
  is not in that artifact at all.
- `NOTE …` — a call that did not answer. Report it as unmeasured, never as unchanged. A
  `NOTE` carrying `not signed in` is the auth gate below, not a broken artifact.

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
  `{"ok":false,…,"code":"auth_required"}` and both halves come back as `NOTE`s. That is
  the one case where neither the quiet answer nor a delta applies: say the pair could not
  be compared and tell the user to run `! loci login`.
- **`ROM`/`RAM` here are this translation unit's, not the firmware's.** Report them as
  such, and never send them to `loci contract check` as a `rom_size` / `ram_size`
  measurement: those bounds are firmware-scale, and a 361-byte object judged against a
  512 KB budget produces a green row on a claim nobody made. When the contract does
  bound ROM/RAM, escalate to `memory-report`, which measures the linked binary.
- **Frame sizes are only as good as the installed CLI.** Before **0.1.107** every frame
  came back as the push size — measured: a 528-byte frame reported as 4 B on 0.1.102 —
  so both sides agree and "unchanged" is uninformative rather than true. Compare the
  `loci command:` version in the session context; below 0.1.107, report the frame
  question as unanswerable on this install and offer `/loci:setup`.
- **A `FRAME` line is not a stack-depth verdict.** It is one function's own frame
  (`frame_size`), not the worst-case depth through a call graph (`worst_case_depth`,
  which the recipe deliberately does not read). It says re-measurement is warranted; the
  `stack-depth` skill is what answers.
- **`SYM` attributions are only as good as both symbol tables.** Compare against a
  *stripped* artifact and every symbol on the other side reads as `added` — measured, and
  it arrives beside a ROM delta of zero, which is the tell. Attribute a delta to names
  only when the two totals actually moved.
- **Nothing checks that the two artifacts are the same architecture.** `elf memmap` takes
  no `--arch` and does not compare `e_machine`: an ARM object against an AArch64 one
  answers `ok:true` with confident, meaningless numbers. Step 1b's build-parity check is
  what stands between you and that pair; this fence assumes it passed.

<a id="elf-diff-unrequestable"></a>
**Some symbols cannot be requested through `--functions` at all.** Quote the value —
`--functions "<changed_funcs>"` — because a monomorphized Rust generic contains `<`
and `>`, which bash reads as redirections, and the command then never runs. Quoting
fixes that. What quoting does **not** fix is a comma inside a symbol
(`drop_in_place<Ring<u8, 4>>`): the CLI splits the value on commas with no way to
escape one, so that symbol arrives as two names matching nothing — **and a separate
call for it splits identically.** Report such a symbol as changed-but-unmeasurable and
name it; do not present a number for it.

Rust symbols reach you demangled only when `rustfilt` is on PATH. Without it the
mangled `_RNvMCs…` form comes through instead, which is ugly, unambiguous, and
round-trips through `--functions` without any of this trouble.

---

## Step 0 — Pattern B: analyze an existing binary

For skills that analyze an existing or freshly cross-compiled binary (exec-trace,
control-flow, stack-depth, memory-report).

The LOCI target architecture is already resolved in the session context
(`LOCI target:` line). Use it as `<loci_target>`; do not re-detect it. The
`system-reminder` block also carries:

    Target: <target>, Compiler: <compiler>, Build: <build>
    LOCI target: <loci_target>

If the user provides their own binary, asm-analyze auto-detects the architecture
from the ELF. Do **not** re-run detection scripts — use the values already in the
session context.

Selecting the binary has four parts, in this order, and **none of them is
optional**: decide what kind of artifact the question needs (B1), rank the
candidates (B2), prove the candidate is not older than its sources (B3), and name
the artifact you measured in the report (B4).

### B1 — Match the artifact to the question

| The question | Needs |
|---|---|
| Worst-case stack depth, ROM/RAM totals, timing across a call, anything that crosses a call edge | **A linked binary only** (`.elf` / `.out` / `.axf`) |
| One function's own frame, its own blocks, its own CFG | A linked binary, **or** a relocatable `.o` — labelled as such |

**A relocatable `.o` cannot answer a whole-program question, and it fails
silently.** In a `.o`, a `bl` is an unapplied relocation: the encoded target is a
branch to itself (`f7ff fffe` for `R_ARM_THM_CALL`) and the real callee lives only
in the relocation table. `objdump` *renders* the resolved name because it applies
relocations; the `loci elf` pipeline disassembles the bytes and does not, so the
call edge is simply absent. Measured on a fresh object whose `kernel_main` calls a
function with a 4096-byte stack buffer:

    on the .o        kernel_main   8 B    path ['kernel_main']                                    PASS
    relinked ELF     kernel_main   4144 B path kernel_main → build_pattern → render_frame  202%  FAIL

Both runs used a **fresh, correct** artifact. `has_unknown_callees` was `false` in
the first — the soundness flags do not catch this, so nothing warns you. Preferring
the fresh `.o` for a whole-program question is therefore *worse* than measuring a
stale ELF: a stale ELF gives a wrong answer about a provably wrong binary, while
the `.o` gives a confidently wrong answer about the right one.

So when the question needs call edges and only a `.o` is fresh, the `.o` is
**evidence that a relink is required**, not the thing to measure. Relink, then
measure the ELF.

### B2 — Collect the candidates, then filter by freshness before ranking

The project-context JSON (`project context:` in the session context) carries
`elf_files` (the project's own build) and `loci_artifacts` (artifacts LOCI itself
produced under `.loci-build/`, each with its `mtime` and `kind`, newest first).
Read both. Note the context is a **session-start snapshot** and is never refreshed,
so anything built during this session is in neither list — you know those paths
because you just created them.

**Freshness is a filter, not a tiebreak.** Gather every candidate, run B3 on them,
discard the stale ones, and only then rank what is left:

1. A binary the user named explicitly — if it survives B3, use it.
2. Any surviving **linked** binary (`.elf` / `.out` / `.axf`), from either list or
   from this session's own rebuild. Among several, take the newest `mtime`.
   **Reject one built for a different architecture.** `loci_artifacts` covers all of
   `.loci-build/`, so a relink a previous session left under
   `.loci-build/<other-target>/` can be the newest linked candidate, pass the
   freshness gate (its sources are unchanged) and then be measured with
   `--arch <loci_target>` for the wrong ISA — with no flag warning. A path under
   `.loci-build/` is scoped by the target directory it sits in; anything outside
   `.loci-build/<loci_target>/` needs the ISA confirmed before you trust it.
3. A surviving **object** — anywhere *under* `.loci-build/<loci_target>/`, which is
   not necessarily directly in it: newer CLIs key an object's path on its source's
   own directories, so `app/drivers/blink.c` can build to
   `.loci-build/<loci_target>/app/drivers/blink.o`. The ISA scoping above is
   unaffected either way — it is still inside the target dir. Take the path from
   `loci_artifacts`; never glob a level you guessed. **Only** for a single-function
   question per B1, and only with the label from B4.
4. **Nothing usable survives** — including the case where the only survivor is an
   object and the question needs call edges (B1). That is not "no build": it is
   *evidence a relink is needed*, and it is the common shape after a `make clean`
   plus a post-edit compile. Relink the surviving object(s) per B3's rebuild step,
   or cross-compile with the **Cross-compilation defaults** above, or ask the user
   which target.

Ranking by *provenance first* is what produced the reported bug, and ranking
`.loci-build` above the user's own build repeats it in a new way: a relink left in
`.loci-build/` by a **previous** session outranks the `.elf` the user's `make` just
produced, and rebuilding with `make` does not touch it — so a provenance-first
ranking will keep re-nominating the same stale artifact forever. Filter first and
that cannot happen.

`elf_files` entries carry no `mtime` (they are bare path strings); `stat` them, or
just rely on B3's `elf_mtime`, which every candidate gets anyway.

### B3 — Prove it is not older than its sources

Ask before spending a model call:

    loci build fresh --elf <candidate>

Branch on `.data.stale` — never on the message text. (The same block rides on
every `loci elf` verb as `.data.source_provenance`, so if you have already made an
`elf` call you can read it from there instead of paying for a second check.)

**Read `.data.role` first.** It is `baseline` or `measured`.

- **`baseline`** — a deliberate pre-edit artifact (a `.o.prev` captured before the
  turn's first edit). It is older than the current sources *because that is what
  makes it the "before" side*, so its age is not a defect and the rule below does
  not apply to it. **Never rebuild a baseline.** Rebuilding replaces the pre-edit
  state with the current one and destroys the comparison the report is built on.
  `.data.recommendation` says the same thing for this case.
- **`measured`** — the artifact whose numbers you are about to report. The rule
  below applies in full.

- **`true` — this candidate is out. Do not report numbers from it.**
  `.data.sources_newer` names what changed and by how much. Either move to the next
  surviving candidate from B2, or rebuild:
  1. the project's own build system when it is a cheap one-liner (`make`,
     `cmake --build <dir>`, `ninja`) — it produces a *linked* binary, which is what
     a whole-program question needs;
  2. otherwise `loci build compile --source <file> --loci-target <loci_target>
     --context <project-context>` for the translation unit, and then link it, per
     B1's rule about `.o` files. No `loci` verb links, and the defaults table gives
     compile flags only — so if you cannot reconstruct the link (no linker script,
     other objects unknown), do **not** fall back to measuring the object for a
     whole-program question. Say what is missing and stop, per the paragraph below;
     a single-function answer from the object is acceptable *only* with B4's
     scope label.

  Then **measure the artifact you just produced** — confirm it with one
  `loci build fresh` on *that path* and go straight to B4. Do **not** re-enter B2's
  candidate list: a rebuild refreshes one artifact, and re-ranking would hand you
  back a different, still-stale one. A candidate B3 rejected stays rejected for the
  rest of this run.
- **`false`** — proceed.
- **`null` — freshness is unknown, not confirmed.** Common and not an error: no
  `-g`, or the binary was built on another machine. Proceed, and say so in the
  report using `.data.reason` verbatim. For a binary with no debug info at all,
  `loci build fresh --elf <path> --source-root <project-root>` adds a coarser
  mtime comparison.

If every candidate is stale and no rebuild is possible (no build system, no
compiler, the user declines), **say that and stop**. Do not report numbers for code
that is not on disk. Reporting "here are the numbers, but they may be stale" is the
failure this gate exists to prevent — an engineer acts on the numbers and ignores
the caveat.

**If the installed CLI is too old to have the gate** — `loci build fresh` comes back
`ok:false` with a usage error (argparse `invalid choice`, exit 2) and no `loci elf`
envelope carries `source_provenance` — do not treat that as "fresh". Say once:

    This loci CLI predates the artifact-freshness check; run `/loci:setup` to
    update. Freshness of <artifact> is unverified.

then continue with the freshness state recorded as unverified, and carry that
through to B4's line. The pinned CLI (`lib/setup-steps.sh`) always has the verb;
this path exists for a stale `loci` on PATH ahead of the pin.

### B4 — Name the artifact you measured

Every Pattern B report **must** carry one provenance line, immediately before its
conclusion table or footer:

    Artifact: kernel.elf (linked 2026-07-27 13:27:42, sources current)

Variants, matching what B3 returned:

    Artifact: .loci-build/armv6-m/kernel.elf (relinked 2026-07-29 12:03:11 by this run, sources current)
    Artifact: build/app.elf (linked 2026-07-28 09:14:02, freshness unverified — no DWARF debug info)
    Artifact: .loci-build/armv6-m/blink.o (object, 2026-07-29 12:31:41, sources current)
              Single-function scope: callees are not resolved in a relocatable object.

This line is the durable part of this section. Selection rules live in prose and a
future rewording can weaken them; a line that names the file and its build time
means a reader can always see *what* was measured and decide for themselves
whether it was the right thing.
