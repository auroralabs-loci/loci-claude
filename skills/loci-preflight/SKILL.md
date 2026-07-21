---
name: loci-preflight
description: >
  Execution-aware preflight analysis (control-flow, timing/energy) on the
  functions an edit touches and the callees of any new code, using compiled
  artifacts, to catch problems while the design is still cheap to change.
when_to_use: >
  MANDATORY in /plan mode when user describes new logic or a modification.
  Triggers: "implement", "add", "write a function", "new feature", "how
  should I", "modify", "refactor", "guard". Do NOT invoke for review/explain
  requests or direct edits outside plan mode.
---

# loci-preflight

This skill is a **thinking tool, not a write-gate**. Run it during planning —
while you are still deciding what to write — so the execution fit is visible
before any code changes. The output shapes how you write, not just whether.

**Preflight requires compiled artifacts.** It does not fall back to source-level
reasoning. If the project cannot be compiled or the architecture is not
supported, the skill stops and tells the user why.

## Tool boundary and shared contract

**Shared runtime contract.** Before running this skill, read
`<plugin-dir>/skills/_shared/loci-runtime-contract.md` and apply its
**Tool boundary: `loci elf` only**, **Output: the JSON envelope**, **Supported
architectures (gate)**, and **Step 0 — Pattern A: compile the source** sections.
The sections below add only this skill's specifics.

**Tool boundary (reminder):** `loci elf` only — never `objdump`, `readelf`,
`addr2line`, or `nm`. This skill needs the annotated CFG and per-block CSV
`loci timing` expects. Always pass `--arch <loci_target>`, read verbatim from the
SessionStart `LOCI target:` line.

## When to run

Run preflight as part of forming your plan, immediately after you understand
what function(s) you need to write and before you issue any Edit/Write call:

1. User describes the task
2. You read the relevant files to understand the call site and surrounding code
3. **← run preflight here, while thinking**
4. Adjust the plan based on findings
5. Write the code

**Plan mode:** Always emit the full preflight report (Execution, CFG Analysis,
Execution fit, footer) in the **response text** — never inside the plan body.
The plan body should contain only the adjusted implementation steps that
incorporate preflight findings. The user must see the complete structured
report in the response, not a summary buried in the plan context.

## Step 0: Check session context

**Authentication is on-demand.** The timing step (Step 2) needs a signed-in LOCI
session; `loci timing` checks lazily. There is no upfront probe and no `/mcp` —
if a `loci timing` call returns `error.code == "auth_required"`, skip
timing/energy, note "(timing/energy unavailable — run `! loci login`)", and
continue with CFG-only analysis (Step 2's quota/auth handling covers this).

Follow **Step 0 — Pattern A** and **Supported architectures (gate)** in the
shared runtime contract: read the persisted detection results from the
`<project-context>` path — the single source of truth for compiler,
architecture, and build system. **Do NOT re-run detection scripts.** If that
file does not exist, stop and tell the user:

> LOCI session context not found. Please restart Claude Code so the plugin
> setup runs and detects the project environment.

Preflight emits its own STOPPED block when the gate fails. If `<loci_target>` is
**not** a supported architecture (`aarch64`, `armv7e-m`, `armv6-m`, `tc399`),
emit and stop:

```
## Preflight: STOPPED
Architecture not supported.
Supported: aarch64, armv7e-m, armv6-m, tc399
```

If no compiler was detected in the session context, emit and stop:

```
## Preflight: STOPPED
No compiler detected in session context.
Action: resolve the build environment, then re-run preflight.
```

## Step 1: Compile the affected source(s) via `loci build compile`

Always compile the source file(s) whose callees the new code will invoke
through `loci build compile`. Do **not** reuse an existing `.o` or `.elf`
from the project's own build — LOCI needs the compiler, flags, and version it
controls so that the post-edit rebuild can diff apples-to-apples.

Read `plugin dir:` and `project context:` from the SessionStart context. For
each source:

```
loci build compile \
    --source <path/to/src.cpp> \
    --loci-target <loci_target> \
    --context "<project-context>" \
    --phase preflight
```

`loci build compile` resolves flags through a typed cascade — each step is
recorded in the `.meta.json` sidecar under `flag_source_v2.attempts`:

1. User override (`.loci-build/flags.json`, `LOCI_EXTRA_CFLAGS`)
2. `compile_commands.json` (exact)
3. `make --dry-run` against the project's own makefile (exact)
4. Sibling `.obj`/`.o` DWARF in the build directory (high)
5. Same-stem `.obj`/`.o` DWARF near the source (high)
6. Linked ELF DWARF (medium; prefers CU whose `DW_AT_name` matches source)
7. TI `.projectspec` XML — `-I`/`-D` only, CPU stripped (medium, partial)
8. Makefile regex scan — augmenter only (low, partial)
9. Hardcoded defaults — last resort with a warning

It guarantees `-g` and `-c`, and writes `.loci-build/<loci_target>/<basename>.o`
plus `.loci-build/<loci_target>/<basename>.o.meta.json`. The compiler /
flags / version / discovery tier are recorded in the sidecar; post-edit
calls `loci build diff` to verify parity. If you need a field from the envelope,
capture it in a shell variable (`out=$(loci build compile …); jq … <<<"$out"`) —
do **not** redirect it to a `.loci-build/*.json` file; the `.meta.json` sidecar is
the durable record, so a captured copy of stdout is pure litter. **Do not print
the build block to the user** — the sidecar is the source of truth, and the block
is intentionally suppressed to keep the skill output focused on the analysis.

**Validate the .o** — a standalone `-c` compile can exit 0 yet produce an
empty object file when the source is wrapped in `#if` / `#ifdef` guards whose
defines (`-D`) were not on the command line. After `loci build compile`
succeeds, run:

```
loci elf symbols --elf .loci-build/<loci_target>/<basename>.o --arch <loci_target>
```

Read everything from **this one envelope** — never re-run it to "peek". `data.count`
is the symbol count (the validation gate); `data.payload` tells you where the table
is — inline under `data.symbols` (the usual case for a small object file) or at
`data.symbols_file` (a large ELF above `--inline-threshold`), which you `jq`/grep
rather than re-invoking. If `data.count` is 0 or the call returns an error mentioning
"no code" or "preprocessor", the target function was compiled out. In that case ask
the user for the `-D` flags the project build system uses, re-run
`loci build compile`, and re-validate.

**Secondary path: existing binary**

Use a full binary (.elf, .out) for *analysis* only if the callees span multiple
compilation units and linking is needed. You MUST still run
`loci build compile` for the relevant source file — the `.o` +
`.meta.json` pair is what the pre-edit hook snapshots, and what post-edit
compares against. Skipping it breaks the entire pre/post chain.

**`compiler_not_found`: retry, then ask the user**

If the envelope is `ok:false` with `error.code == "compiler_not_found"`, do NOT
stop yet — follow the recovery in the runtime contract ("If it fails with
`compiler_not_found`"): try the alternate driver name via `command -v` and, if
that misses, ask the user for the compiler path (do not hunt vendor dirs
yourself), then re-run `loci build compile` with `--compiler-path` once.

**Hard stop: `loci build compile` fails**

If `loci build compile` returns any other error envelope (`ok:false`), or the
`compiler_not_found` retry above still fails, emit its `error.message` verbatim
and stop. Do NOT paraphrase, do NOT proceed to analysis. The message already
carries the source, flag-source trace, and remediation options.

```
## Preflight: STOPPED
loci build compile failed for <source>.
<error.message from the command, verbatim>
```

## Step 2: Call graph and timing/energy analysis

Read `plugin dir:` and `project context:` from the LOCI session context
(system-reminder at session start). All analysis runs through the bare `loci`
command on PATH — no script path or venv Python.

The goal is to analyze the functions the edit will affect — for new code, the
callees it will invoke; for a modification, the function itself (plus any new
callees) — before writing anything.

### Extract assembly

Extract CFGs for the callees the new function will invoke:

```
loci elf asm --elf <.o or binary> --functions <callee_1,callee_2...> --arch <loci_target>
```

The envelope's `data.control_flow` is the path to the annotated CFG file
(text optimized for LLM analysis); read that file when analyzing the CFG.

`data.timing_csv` (the consolidated per-block timing-CSV **file path**) and
`data.timing_architecture` are what the `loci timing` call below consumes.

**Parse the envelope with `jq`, not `python -c`.** The envelope is small — `loci
elf asm` already spilled the annotated CFG and per-block timing CSVs to files
under `.loci-build/elf/`, and the envelope only carries their *paths*. Capture it
in a shell variable and read it inline with a here-string; do **not** redirect it
to a file (that leaves a redundant copy of stdout in `.loci-build/`). If you ever
write a file yourself, keep it inside the working directory — NEVER `/tmp/`,
`/var/tmp/`, or any out-of-project path (Claude Code prompts for permission and
halts automation). Then:

```
env=$(loci elf asm --elf <…> --functions <…> --arch <loci_target>)
jq -r '.data.control_flow'        <<<"$env"   # path to annotated CFG file
jq -r '.data.timing_architecture' <<<"$env"   # arch string for loci timing
jq -r '.data.timing_csv'          <<<"$env"   # path to consolidated timing CSV
```


### Timing and energy via `loci timing`

Immediately after extraction, get hardware-accurate timing and energy for the
callees:

Call `loci timing` once with the consolidated timing CSV:
```
loci timing --architecture <data.timing_architecture> --csv-file <data.timing_csv>
```

It returns `data.rows` (one row per block); use those rows to compute
per-callee metrics.

Compute per-callee:
- **Worst path** = `execution_time_ns` + `std_dev_ns`
- **Energy** = `energy_ws` (report in uWs; convert from Ws by multiplying by 1e6)

`loci timing` row fields are exactly: `function_name`, `std_dev_ns`,
`execution_time_ns`, `energy_ws`. Reference those field names literally
when reading rows — there is no bare `std_dev` field.

Sum worst-case timings and energy across the hot-path call chain — but
**not** by adding the bare `execution_time_ns` of every hot-path
block. Hot-path blocks that end in `bl` / `blx` are *call sites*: the
`loci timing` cost for that single block reflects only the branch-only /
single-instruction call-site cost, NOT the cost of the callee's body.
You MUST expand every such block first (see next sub-step) before summing.

If the cumulative expanded chain exceeds a known deadline or energy
budget, flag it now — before any code is written.

### Expand `bl` / `blx` call-site rows

For every block on the hot path whose disassembly ends in `bl` / `blx`
(or whose CFG terminator is annotated `(external-call ...)`,
`→ <callee_symbol>`, or `(unresolved reloc)`):

1. **Identify the callee.** Read the symbol from the CFG annotation
   and/or the `bl` instruction's target. Strip any `_0x<hex>` block
   suffix — you want the function name (e.g. `ClockP_start`,
   `xTimerCreateStatic`).

2. **In-binary callee** — rows whose `function_name` starts with
   `<callee>_` are present in the same `loci timing` rows. Walk the
   callee's hot path through its CFG, then compute:

   ```
   callee_worst_ns  = Σ over callee hot-path blocks of (execution_time_ns + std_dev_ns)
   callee_energy_ws = Σ over callee hot-path blocks of  energy_ws
   ```

   Replace the call-site cost with `bl_cost + callee_worst_ns` (and
   energy with `bl_energy + callee_energy_ws`). If the callee itself
   contains a `bl` to another in-binary symbol, recurse one more
   level. Stop at recursion depth 2 to bound work; if a deeper chain
   is on the hot path, surface it as a CFG note rather than recursing
   indefinitely.

3. **External callee** — `function_name` prefix `<callee>_` is NOT in
   the rows (the callee's `.o` was not in `--functions` /
   `--elf`, e.g. FreeRTOS / vendor library symbols). Keep
   `bl_cost` as a **lower bound** for this site. Do NOT silently
   accept it as the call-site cost. You MUST:

   - Add a CFG-Analysis line: `⚠️ external callee body unmeasured —
     <callee> figure is a lower bound`.
   - Append `(≥ <total> ns — external callees unmeasured)` to the
     Latency row's Note in the conclusion table.
   - Where reasonable, suggest re-extracting with the callee's
     `.o` added so the next pass measures the body.

The hot-path total is the sum over all hot-path blocks where every
`bl`-terminated block's cost has been replaced by its expanded form
per the rules above. Treating a bare `bl` row as the full call-site
cost (instead of expanding an in-binary callee's hot-path cost, or
marking an external callee as an explicit lower bound when its body
is unavailable) silently understates timing for any function whose
hot path traverses an in-binary callee, and silently understates
external-callee cost without flagging it as a lower bound.

If modifying an existing function and a `.o.prev` exists, also extract timing
and energy for the baseline (pre-edit) function. Compute delta:
```
diff_pct = ((post_value - pre_value) / pre_value) * 100
```

If a `loci timing` call returns `error.code == "auth_required"`, skip
timing/energy, note "(timing/energy unavailable — run `! loci login`)", and
continue with CFG-only analysis.

If a `loci timing` call returns `error.code == "quota_exceeded"`,
**stop the skill entirely** — do not continue with CFG analysis or
escalation triggers. Instead, output the quota message with reset time
and upgrade CTA:
```
LOCI usage quota reached — preflight analysis skipped.

<error.message verbatim — includes usage/limit, reset countdown, and upgrade link>
```
The message already contains reset time and upgrade CTA, e.g.:
"Daily token limit reached (31,000 / 30,000 tokens). Resets in 4h 23m.
Upgrade to Premium at auroralabs.com for 300,000 tokens/day."
Show it verbatim. Then end the skill.

If the `loci timing` call returns any other error (not quota, not auth), treat it
as timing-unavailable for the affected callees: skip timing, flag each affected
callee with `⚠️ RISK: timing data unavailable for <callee>` in CFG Analysis,
and continue with CFG-only analysis.

### Analyze the CFG output

Check the CFG text (from the `data.control_flow` file) for structural hazards:
- **Missing declarations**: are callees present in the binary with the expected
  signatures? If a callee is absent, flag a missing forward declaration or
  linkage issue.
- **Indirect calls**: any `bl` to a register in a callee's CFG — flag as a
  potential CFI hazard.
- **Recursion/cycles**: back edges in the CFG with no visible exit condition —
  flag unbounded recursion.
- **Latency**: use the `loci timing` results above; flag any callee whose worst
  path violates a timing budget, or where the cumulative hot-path chain
  exceeds a known deadline.
- **Energy**: use the `loci timing` energy results above; flag any callee or hot-path
  chain whose energy cost is notably high relative to the use case (e.g.,
  battery-powered device, ISR context, tight power budget).

### Reason over results

After analyzing the CFG and receiving LOCI results, reason through the
following before proceeding to output. This is a mandatory thinking step —
do not skip it when results look clean. Increment **R** (reasoning cycle
counter) by 1 now.

**Interpretation questions:**
- What is this function's role in the system — is it on a hot path, ISR,
  periodic task, or called once? This determines whether any timing delta
  is critical, advisory, or irrelevant.
- If `.o.prev` exists: is `|delta| < std_dev_ns`? If yes — change is within measurement
  noise, treat as stable. If `|delta| > std_dev_ns` — change is real; flag it.
  If no `.o.prev`: this is the first measurement — record these numbers as the
  baseline and note no prior exists for comparison.
- Does `std_dev_ns` indicate a stable path or high hardware variance — and why
  (cache sensitivity, branch misprediction, pipeline stalls visible in CFG)?
- Is a timing budget known from the session context? If yes, compare hot-path
  worst against it and flag if exceeded. If no budget is known, report the
  number and skip the fit assessment.
- What does the CFG structure explain about the timing — which blocks
  dominate, are there expensive paths the new code will always hit?
- Has every hot-path `bl` / `blx` site been expanded per the
  "Expand `bl` / `blx` call-site rows" step? If a callee's body rows
  are present in the `loci timing` rows but its bare `bl` cost is still
  what's flowing into the Latency total, the number is the entry-block
  understatement — re-aggregate before continuing. If a callee is
  external (no `<callee>_*` rows), is the lower-bound annotation in
  the Latency Note?
- Is the hot-path energy distribution balanced across callees, or does one
  callee dominate? If dominated, that callee is the leverage point — plan
  to cache its result, call it less frequently, or substitute a lighter alternative.
- Do any CFG findings (indirect calls, recursion, missing declarations) change
  the design — does the plan need a guard, a different callee, or a linkage fix?
- **Synthesize per-row Status**: when multiple sub-findings roll up to the
  same Gate (e.g. several CFG hazards under Safety, both worst-case latency
  and dominance under Performance), the row's Status is the worst of the
  contributors and the Note lists them comma-separated, worst-first.
- **Verdict cause comes from sub-findings, not Gate names**: the
  ADJUST PLAN / STOP one-sentence cause lifts the lead item from the
  driving row's Note (e.g. "STOP — unbounded recursion blocks plan", not
  "STOP — Safety row is ❌"). Gate names are for the table; the verdict
  speaks in concrete findings.


**Escalation triggers (run skill inline, then reason over its results):**

*Escalate to `stack-depth`* when — increment R by 1 at trigger:
- Execution context is ISR, HWI, or interrupt callback, AND call chain
  depth > 3 levels visible in CFG, OR
- Recursion already flagged in CFG analysis above, OR
- Plan adds a new RTOS task (xTaskCreate, Task_construct, osThreadNew) that
  needs stack sizing, OR
- Plan introduces large local variables on stack (buffers, arrays, C++ objects
  with non-trivial constructors), OR
- Plan adds a known-deep callee (printf, snprintf, crypto, TLS functions).

After stack-depth returns, reason over its results — increment R by 1:
- Does worst-case stack depth fit the task's or ISR's configured stack budget?
- Are there large frames that could move to static or heap allocation?
- Does any frame in the chain add cost the plan can eliminate?
- Could the call chain be flattened to reduce depth?
→ adjust plan based on conclusion before proceeding.

*Escalate to `memory-report`* when — increment R by 1 at trigger:
- The plan introduces significant new static allocations (large buffers,
  global arrays, static structs) visible from reading the source, OR
- `.o.prev` exists and the plan grows or restructures existing data sections.

After memory-report returns, reason over its results — increment R by 1:
- Does the new allocation fit within available ROM/RAM headroom?
  (answerable only if map file was provided — memory_regions shows usage %;
  without map file, report section size delta only)
- Which region is under most pressure after the change?
- Does the plan need to reduce static footprint before proceeding?
→ adjust plan based on conclusion before proceeding.

### Re-query loop

After reasoning, check whether a better candidate exists before committing to
the plan. If any of the following is true, go back to **Extract assembly** with
the alternative callees and repeat through **Reason over results**:

- Reasoning identified a lighter or safer alternative callee worth evaluating
- A flagged callee (timing violation, CFI hazard, recursion) has a named alternative
  visible in the source files already read
- Hot-path energy is dominated by one callee that may have a lighter variant
- The plan for the new function changed (different call sequence, new callees
  introduced) and those callees have not yet been measured by LOCI — re-query
  with the new callee set before finalizing the plan

Increment **R** by 1 and **M** by the number of new `loci timing` calls for each re-query cycle.

**Cycle limit: 3 re-query iterations maximum.** If the limit is reached without
a stable plan, emit the best candidate found and note the cycle limit was hit.

**Convergence condition — exit the loop when:**
- The plan is stable (no new callees to evaluate and no unresolved flags), OR
- All remaining flags are ✗ BLOCK (require user decision, not further querying), OR
- The cycle limit is reached.

## Output format

Emit the preflight report in the **response text**, before describing what
you will write. In `/plan` mode, the report goes in the response — NOT
inside the plan body.

The output has three blocks in order: (1) conclusion table, (2) voice
remark, (3) LOCI footer. No free-form prose sections, no multi-paragraph
reasoning write-ups, no per-callee enumerations. The reasoning happens
in Step "Reason over results" above — it's mandatory and increments `R`
— but the OUTPUT of the reasoning lands as Status + Note in table rows.

The build block from `loci build compile` is intentionally
NOT shown to the user. Compiler/flag provenance lives in the `.meta.json`
sidecar; `loci build diff` surfaces its own `LOCI · build mismatch`
block on its own when parity actually breaks, and that is the only case
the user needs to see it.

### Conclusion table — structure

Header:

```
## Preflight: <FunctionName>
```

Followed by the conclusion table. Icon vocabulary: ✅ PASS · ⚠️ WARNING ·
❌ FAIL.

**Row-inclusion rules:**
- Include a row only if the gate actually executed this run.
- Include a row only if there is something to report (skip "Recursion ✅
  none" noise rows).
- Every ⚠️ / ❌ row MUST cite a reason in the Note column — no icon
  without a cause. The Note is the one-line synthesis of the "Reason
  over results" pass for that gate.
- Skipped gates are omitted (no fourth "N/A" icon).

**Row catalogue** (order when present):

1. **Safety** — fires when CFG analysis surfaces a structural hazard
   (missing declaration, indirect call, recursion / cycle). Status:
   ❌ for unbounded recursion or a BLOCK-level missing declaration;
   ⚠️ for benign-but-noteworthy hazards (function-pointer dispatch,
   bounded recursion, weak-symbol miss); otherwise the row is omitted.
   Note names the specific hazard(s).
2. **Performance** — fires when `loci timing` returned. Captures **response
   time** (worst-case latency including callees), hot-path dominance (one callee >60% of budget),
   and noise margin (only when `.o.prev` exists: did the delta exceed
   `std_dev_ns`?). Status: ✅ within budget and within noise; ⚠️ near
   budget OR delta exceeds std-dev; ❌ over budget. Note format:
   `worst <X> µs (vs. <budget> when known); dominant: <callee> (<pct>%)`.
3. **Energy** — fires when `loci timing` returned energy. Threshold follows the
   target context: ISR / battery-powered tighter than once-per-boot.
   Note format: `<X> µWs`.
4. **Stack** — only when stack-depth was invoked this run. Note:
   `stack: <N> B (<usage>%) — <verdict>` (verbatim from stack-depth).
5. **Memory** — only when memory-report was invoked this run. Note:
   `memory: ROM <X>% / RAM <Y>% — <verdict>`.

Build success and symbol-resolution are NOT table rows. The
`LOCI · build` block at the top already reports compiler/flags/target.
If compile or symbol-extract fails, the skill STOPs before reaching
the conclusion table — no state in which a "Build ✅" row carries new
information.

### Conditional per-callee breakdown (between table and verdict)

Per-callee timing is usually hidden to keep clean runs compact, but it
appears automatically when the engineer needs it. Render a "Hot-path
breakdown" block between the table and the verdict line WHEN any of
these triggers match:

- The **Performance** row's status is ⚠️ or ❌, OR
- The **Performance** Note names a dominant callee (>60% of hot-path worst)

Show top-5 callees along the hot path, sorted by
`worst_ns_summed_across_callee_hot_path` desc. The per-callee
`worst_ns` here is the **summed** body cost, NOT the entry-block
worst — same expansion as the Step 2 sub-step. External callees
appear with `≥ <bl_cost>` and a `(body unmeasured)` tag:

```
Hot-path breakdown (top-5 by worst):
  <in_binary_callee_1>   <summed_worst_ns> (<pct>%)   <summed_energy_uWs>
  <in_binary_callee_2>   ...
  <external_callee>      ≥ <bl_cost_ns> (<pct>%)      ≥ <bl_energy_uWs>   (body unmeasured)
  ...
```

Omit this block when neither trigger matches (clean runs stay short).
When fewer than 5 callees contributed to the hot path, show what's
there — don't pad.

**Table footer** (always): bolded single-line verdict.
`Execution fit: **GOOD** — proceed with plan` ·
`**ADJUST PLAN** — <one-sentence change>` ·
`**STOP** — <one-sentence reason>`

### Template

```
## Preflight: <FunctionName>

| Gate                     | Status | Note                              |
|--------------------------|:------:|-----------------------------------|
| <row 1 when applicable>  |   ?   | <cited reason>                     |
| ...                      |   ?   | ...                                |

<Hot-path breakdown block — only if Performance ⚠️/❌ or its Note names a dominant callee>

Execution fit: **<GOOD|ADJUST PLAN|STOP>** — <one sentence>
```

### Example (typical clean run, ~10 lines)

```
## Preflight: process_message

| Gate         | Status | Note                              |
|--------------|:------:|-----------------------------------|
| Safety       |   ⚠️   | dispatch via function pointer — benign |
| Performance  |   ✅   | hot-path worst 1.8 µs              |
| Energy       |   ✅   | 0.05 µWs                           |

Execution fit: **GOOD** — proceed with plan
```

For modifying an existing function with `.o.prev` available, the
**Performance** row's Note carries the noise-margin sub-finding
(`|delta| vs std_dev_ns`). The Before/After comparison lives inside
that Note, not as a separate Delta block.

## Re-reasoning triggers (table-driven)

Before emitting the final conclusion table, inspect what the first-pass
analysis produced. If any of the row patterns below matches, loop back
— re-query `loci timing`, escalate, or re-read source — BEFORE emitting. Each
looped-back pass increments `R` (co-reasoning); each extra `loci timing` call
increments `M`. The table the user sees is the post-loop version, not
the first-pass draft.

| Row pattern | Trigger |
|---|---|
| **Performance** Note shows dominance > 80% | Re-query `loci timing` on the dominant callee's per-block timings (not just the entry block). One extra `loci timing` call. Often reveals a specific block as the leverage point, which the hot-path-summary hid. |
| **Safety** ❌ with missing-decl sub-finding | Before STOP: re-read the source to check for alternate callees that share the name (macro redefinition, weak symbol, LTO-inlined). Don't STOP on the first miss; verify. |
| **Safety** with indirect-call sub-finding AND function is on an ISR path | Escalate to stack-depth even if usual triggers don't match — indirect dispatch can hide call-graph depth from static analysis. |
| **Safety** with recursion sub-finding | Escalate to stack-depth (already the existing rule, restated here for table-completeness). |
| **Performance** Note shows `|delta|` within `std_dev` | Downgrade any ⚠️ on Performance/Energy to ✅ automatically — the measured regression is within measurement noise. Verdict stays GOOD even if raw numbers suggested ADJUST. |

Per-callee timing detail appears in the conditional "Hot-path breakdown"
block above, but only when the Performance row is ⚠️/❌ or its Note names
a dominant callee — clean runs skip it to stay short. If the engineer
needs per-block breakdown beyond top-5 callees, re-extract via
`loci elf asm` directly.

## Adjusting the plan based on findings

The value of running preflight during thinking is that findings change the
plan, not just add comments:

- A missing forward declaration → add it as a step before the function edit
- An unbounded loop in a callee → plan to add a termination guard or budget
- A callee timing violation → plan to cache the result, call asynchronously,
  or choose a lighter alternative before committing to the design
- An energy concern → plan to batch calls, use a lighter alternative, or move
  work off the hot path

Write the adjusted plan, then write the code. Do not write the code and then
note risks afterward — that defeats the purpose.

## LOCI voice remark

Before the footer, add one short LOCI voice remark (max 15 words) that
acknowledges the user's work grounded in a specific number from the
analysis. Attribute improvements to the user ("clean work", "smart move",
"tight code"). For concerns, be honest and constructive with specifics.
Skip if the analysis produced no results or the user needs raw data only.

## LOCI footer

After emitting the preflight report (or all-clear shorthand), append the
footer as the last thing printed — **only if N > 0** (at least one
function was sent to LOCI). If no functions were processed (`loci timing`
unavailable or no functions to measure), do NOT emit the footer.

**Record cumulative stats** (run via Bash before rendering the footer).
Pass `--verdict "<verbatim-verdict-line>"` so the verdict ride-along
ships alongside the per-function trends payload — the line is the same
string already rendered to chat (`Execution fit: GOOD — proceed with plan`,
`Execution fit: ADJUST PLAN — <reason>`, or `Execution fit: STOP — <reason>`),
unbolded, no surrounding asterisks.

Also pass `--gates '<gates-json>'` — a compact JSON object capturing
the per-row Status from the conclusion table just rendered. Map the
icons: `✅→pass · ⚠️→warn · ❌→fail`. Only include gates that fired
this run (omitted gates were not part of the table). Allowed gate
names: `Safety` · `Performance` · `Energy` · `Stack` · `Memory`.
Example for the clean-run preflight example:
`{"Safety":"warn","Performance":"pass","Energy":"pass"}`.
```
loci stats record --context-file "<project-context>" --skill preflight --functions <N> --mcp-calls <M> --co-reasoning <R> --verdict "<verbatim-verdict-line>" --gates '<gates-json>'
```

**Record per-function measurements** (single Bash call for all functions).
Pipe all measurements as JSONL via stdin. Skip functions where `loci timing`
was unavailable.
```
echo '<jsonl_records>' | loci stats measure --context-file "<project-context>" --stdin --skill preflight
```
Where each line is one function. Tag every row with `"metric":"response_time"` —
preflight measures **response time** (worst-case latency including callees: the
longest acyclic path + bl-expanded callee), the same metric post-edit records, so
`loci stats` treats the two as one comparable series (and keeps exec-trace's
throughput time separate):
```
{"fn":"<func1>","worst_ns":<execution_time_ns>,"energy_uws":<E>,"metric":"response_time"}
{"fn":"<func2>","worst_ns":<execution_time_ns>,"energy_uws":<E>,"metric":"response_time"}
```

The `worst_ns` field name is the storage-schema key consumed by
`loci stats` (preserved for compat with prior on-disk measurements);
pass `execution_time_ns` into it.

### Render the footer — compact by default

One line. Icon-led, no surrounding bars, middle-dot separators, spaces
around any `→` arrow:

```
<icon> LOCI preflight · <N> functions · fit <GOOD|ADJUST|STOP>
```

- `<icon>` — mirrors the body's Execution-fit verdict: `✅` for GOOD,
  `⚠️` for ADJUST, `❌` for STOP.

Worked example (clean run):
```
✅ LOCI preflight · 2 functions · fit GOOD
```

### Clean-escalation suffix

When preflight escalated into `stack-depth` or `memory-report` AND the
escalated skill returned clean, append a space-separated `+<skill>`
marker to the primary scalar so the compact line still surfaces that
the deeper check ran:

```
✅ LOCI preflight · 2 functions · fit GOOD  +stack-depth
✅ LOCI preflight · 5 functions · fit GOOD  +stack-depth +memory-report
```

A non-clean escalated result already flips a Stack/Memory row in the
preflight conclusion table to ⚠️/❌ and the verdict to ADJUST/STOP, so
`+<skill>` only ever appears next to a green icon. The conclusion
table itself carries the bad news — the footer stays compact regardless
of verdict, and the cumulative branch-stats line is not included.

Counter definitions (used by `loci stats record` above):

- **N** = unique functions whose assembly was sent to LOCI (callees of
  new code, or modified functions themselves)
- **M** = `loci timing` calls (one per timing CSV)
- **R** = co-reasoning: 1 for the initial LOCI result pass, +1 for each
  re-query loop iteration, +2 for each escalated skill (1 at trigger,
  1 when reasoning over results)
