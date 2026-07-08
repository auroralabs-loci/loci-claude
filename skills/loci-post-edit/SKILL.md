---
name: loci-post-edit
description: >
  Compare pre-edit and post-edit compiled artifacts to report execution
  timing % diff, energy consumption, and control-flow analysis.
when_to_use: >
  MANDATORY after any Edit/Write/MultiEdit to C/C++/Rust source
  (.c,.cc,.cpp,.cxx,.h,.hpp,.hxx,.rs). Invoke IMMEDIATELY — do not skip,
  batch, or wait. Also: "analyze the change", "measure the edit", "timing diff".
---

# loci-post-edit

This skill merges execution-trace (timing/energy) and control-flow (CFG)
analysis into a single post-edit report. It compares pre-edit and post-edit
compiled artifacts to show exactly how the change affects hardware execution.

## Tool boundary and shared contract

**Shared runtime contract.** Before running this skill, read
`<plugin-dir>/skills/_shared/loci-runtime-contract.md` and apply its
**Tool boundary: `loci elf` only**, **Output: the JSON envelope**, **Supported
architectures (gate)**, and **Step 0 — Pattern A: compile the source** sections.
The sections below add only this skill's specifics.

**Tool boundary (reminder):** `loci elf` only — never `objdump`, `readelf`,
`addr2line`, or `nm`. This skill needs the per-block CSV, timing CSV, and
annotated CFG `loci timing` expects. Always pass `--arch <loci_target>`, read
verbatim from the SessionStart `LOCI target:` line.

**Authentication is on-demand.** The timing step (Step 4) needs a signed-in LOCI
session; `loci timing` checks lazily. There is no upfront probe and no `/mcp` —
if a `loci timing` call returns `error.code == "auth_required"`, handle it per
Step 4's graceful-degradation rules (skip timing, tell the user to run
`! loci login`).

## Step 0: Check session context

Follow **Step 0 — Pattern A** and **Supported architectures (gate)** in the
shared runtime contract: read the persisted detection results from the
`<project-context>` path — the single source of truth for compiler,
architecture, and build system. **Do NOT re-run detection or fall back to
ELF/build-system sniffing.** Stop with the session-context-not-found message if
the file is missing, and stop (`Supported: aarch64, armv7e-m, armv6-m, tc399`)
if `<loci_target>` is not a supported architecture.

## Step 1: Compile post-edit using preflight's flags

Compile the edited source with the exact compiler + flags preflight used. The
pre-edit hook captured preflight's metadata at
`.loci-build/<loci_target>/<basename>.o.meta.json.prev`. Pass it via
`--meta-prev` so the post-edit build inherits those flags rather than
re-detecting them:

```
loci build compile \
    --source <path/to/src.cpp> \
    --loci-target <loci_target> \
    --context "<project-context>" \
    --meta-prev .loci-build/<loci_target>/<basename>.o.meta.json.prev \
    --phase post-edit
```

The command writes only the `.o` and its `.meta.json` sidecar to disk and returns
the metadata in the envelope. If you need a field from the envelope, capture it in
a shell variable (`out=$(loci build compile …); jq … <<<"$out"`) — do **not**
redirect it to a `.loci-build/*.json` file; the `.meta.json` sidecar is the
durable record, so a captured copy of stdout is pure litter. **Do not print the
build block to the user** — the sidecar is the source of truth, and Step 1b's
`loci build diff` already surfaces a `LOCI · build mismatch` block on its own when
parity fails, which is the only case the user needs to see.

If `.o.meta.json.prev` does not exist, preflight did not run before this
edit. Omit `--meta-prev`; `loci build compile` will re-detect flags and record
them. Report absolute timing only in Step 5 — no % diff is available without
a preflight baseline.

If the envelope is `ok:false` with `error.code == "compiler_not_found"`, follow
the recovery in the runtime contract: try the alternate driver name via
`command -v` and, if that misses, ask the user for the compiler path (do not hunt
vendor dirs yourself), then re-run this same `loci build compile` with
`--compiler-path` once. Any other error, or a still-failing retry: surface
`error.message` verbatim and stop.

**Validate the .o** — a standalone `-c` compile can exit 0 yet produce an
empty object file when the source is wrapped in `#if` / `#ifdef` guards
whose defines (`-D`) were not on the command line. After compiling, run:
```
loci elf symbols --elf .loci-build/<loci_target>/<basename>.o --arch <loci_target>
```
Everything you need is in **this one envelope** — do not re-run the command to
"inspect" it. `data.count` is the symbol count (the validation gate). `data.payload`
is `"inline"` or `"file"`: when `"inline"` (an object file is small, so this is the
usual case), the table is right there in `data.symbols` — read names from it
directly; when `"file"` (a large linked ELF above `--inline-threshold`), the table
is at `data.symbols_file` — `jq`/grep that path, don't re-invoke. If `data.count`
is 0 or the call errors with "no code" / "preprocessor", the target function was
compiled out: ask the user for the `-D` flags the project build system uses, re-run
`loci build compile`, and re-validate. Do not fall back to a project-built `.elf`
with unknown flags.

## Step 1b: Verify build parity between preflight and post-edit

```
loci build diff --verbose \
    --prev .loci-build/<loci_target>/<basename>.o.meta.json.prev \
    --curr .loci-build/<loci_target>/<basename>.o.meta.json
```

`loci build diff` is **informational only — never a stop condition**.
Divergence is a *finding*, not an error: the envelope is `ok:true` with
`data.match` carrying the outcome (exit stays 0 either way). Always proceed
to Step 2 and run the full timing/CFG analysis regardless.

Branch on `data.match`:
- **`true`** — compiler, version, flags, and target match → the timing diff is
  apples-to-apples; proceed normally.
- **`false`** — `data.mismatches` lists the deltas and `data.report` (from
  `--verbose`) is the rendered `LOCI · build mismatch` block.
  **Emit `data.report` verbatim** in the post-edit report, tag the final verdict
  as `LOW CONFIDENCE — build environment changed between preflight and
  post-edit`, and **continue with full timing analysis**. The % diffs may
  reflect the toolchain delta rather than the code change — note that,
  but still report the numbers. Do not stop, skip steps, or omit the
  per-function table on a build mismatch.

  A `flag_source` kind regression (e.g. preflight used `gmake-dry-run`
  but post-edit fell through to `defaults`) shows up as a dedicated line
  in the mismatch block: `flag_source   kind 'X' → 'Y' — discovery
  regressed between preflight and post-edit; baseline unreliable`.
  Treat that as a stronger signal than a flag-list-only delta, but the
  same rule applies: surface it, do not stop.

Skip this step entirely only if preflight did not run (no
`.o.meta.json.prev`).

## Step 2: `loci elf diff` — find modified/added functions

### Case A: `.o.prev` exists (preflight ran before the edit)

The pair of artifacts to compare lives in `.loci-build/<loci_target>/`:
- pre-edit:  `<basename>.o.prev`   (captured by the pre-edit hook)
- post-edit: `<basename>.o`        (just compiled in Step 1)

```
loci elf diff \
    --elf .loci-build/<loci_target>/<basename>.o.prev \
    --comparing-elf .loci-build/<loci_target>/<basename>.o \
    --arch <loci_target>
```

This returns lists of `modified` and `added` functions. Only these functions
need analysis — skip unchanged code entirely.

### Case B: no `.o.prev` (preflight did not run)

Do **NOT** invoke `loci elf diff` — it requires both artifacts and will error
on a missing `--elf`. Skip directly to Step 3 and extract assembly from
the post-edit `.o` only; treat every function in the output as "added" for
reporting purposes. Note in the final report:
`(no preflight baseline — first-edit measurement; % diff not available)`.

## Step 3: extract assembly (pre + post)

For **modified** functions, extract assembly from both artifacts:

```
loci elf asm --elf .loci-build/<loci_target>/<basename>.o.prev --functions <func1>,<func2> --arch <loci_target>
loci elf asm --elf .loci-build/<loci_target>/<basename>.o      --functions <func1>,<func2> --arch <loci_target>
```

For **added** functions, extract from post-edit only:

```
loci elf asm --elf .loci-build/<loci_target>/<basename>.o --functions <new_func> --arch <loci_target>
```

The envelope's `data.timing_csv` (the consolidated per-block timing-CSV **file
path**) and `data.timing_architecture` are what the `loci timing` call consumes.
`data.control_flow` is the path to the annotated CFG file (text optimized for
LLM analysis); read that file when analyzing the CFG.

**Parse the envelope with `jq`, not `python -c`.** `jq` ships with the plugin,
handles UTF-8 cleanly on every platform, and never trips on the Windows console
codepage. The envelope is small — `loci elf asm` already spilled its bulky
outputs (the annotated CFG and per-block timing CSVs) to files under
`.loci-build/elf/`, and the envelope only carries their *paths*. So capture the
envelope in a shell variable and read it inline with a here-string — do **not**
redirect it to a file (that just litters `.loci-build/` with a redundant copy of
stdout):

```
env=$(loci elf asm --elf <…> --functions <…> --arch <loci_target>)

jq -r '.data.control_flow'        <<<"$env"   # path to annotated CFG file
jq -r '.data.timing_architecture' <<<"$env"   # timing arch string
jq -r '.data.timing_csv'          <<<"$env"   # path to consolidated timing CSV
```

The paths the envelope returns (`data.control_flow`, `data.timing_csv`) point to
real files the CLI already wrote under `.loci-build/` — read those by path. If
you ever need to write a file yourself, keep it inside the working directory:
NEVER `/tmp/`, `/var/tmp/`, or any out-of-project path — Claude Code prompts the
user for permission on every out-of-project access, halting automation.

## Step 4: `loci timing` — compute % diff

Call `loci timing` once with the consolidated timing CSV:
```
loci timing --architecture <data.timing_architecture> --csv-file <data.timing_csv>
```

It returns `data.rows` (one row per block); use those rows to compute metrics.

Do this for both pre-edit and post-edit assembly of modified functions, and
for post-edit only of added functions.

From the `loci timing` rows and also using the annotated CFG's from step 3, compute:
- **Timing** = `execution_time_ns`
- **Energy** = `energy_ws` (report in uWs)

`loci timing` row fields are exactly: `function_name`, `std_dev_ns`,
`execution_time_ns`, `energy_ws`. Reference those field names literally
when reading rows. Use `execution_time_ns` and `energy_ws`; the
`std_dev_ns` field is not surfaced in this skill.

### Expand `bl` / `blx` call-site rows (pre AND post)

Hot-path blocks that end in `bl` / `blx` are *call sites*; `loci timing`
returns only the branch-only / single-instruction cost for that block
(e.g. ~32 ns on Cortex-M0+), NOT the callee body. You MUST expand every
such site on both the pre-edit and post-edit hot paths before computing
the Worst path / Happy path / Energy values that go into the table —
otherwise both sides are entry-block-only and a callee-internal
regression (e.g. a 200 → 240 ns body change at a `bl` site) silently
shows up as 0 ns delta because both sides counted only the `bl`
instruction.

For each hot-path block ending in `bl` / `blx`:

1. **In-binary callee** (rows whose `function_name` starts with
   `<callee>_` are present in the same `loci timing` rows): replace the
   call-site cost with `bl_cost + Σ over the callee's hot-path blocks
   of (execution_time_ns + std_dev_ns)`, and energy similarly.
   Recurse one more level if the callee itself contains an in-binary
   `bl`. Stop at depth 2.
2. **External callee** (no `<callee>_*` rows in the response — e.g.
   FreeRTOS / vendor library): keep `bl_cost` as a **lower bound**.
   Append `(≥ … ns — external callees unmeasured)` to the Note of
   every affected summary row — Worst path, Happy path, and/or Energy
   whenever that row's hot path includes the external callee — and add
   a CFG-Analysis line naming the external callee.

The Worst / Happy / Energy values that go into the Worst path / Happy
path / Energy rows are the expanded sums. If any included hot-path
block is an external callee kept at `bl_cost`, the corresponding row
remains a lower bound and must be annotated accordingly. The Hot
blocks breakdown between table and verdict still uses *per-block* CSV
rows (the user wants to see the heaviest blocks) — but a
`bl`-terminated block in that breakdown should be labelled
`bl <callee>` so the reader knows its measured cost is just the
branch, with the callee's body counted separately in the Worst path
total when available, or left unmeasured for external callees.

For modified functions, compute % diff:
```
diff_pct = ((post_value - pre_value) / pre_value) * 100
```

The diff is meaningful only after expansion — if either side is
entry-block-only, the % diff is between two understated baselines and
the noise-margin downgrade rule will silently mask real regressions.

### Graceful degradation

- **Not signed in** (`loci timing` → `error.code == "auth_required"`) — report
  CFG analysis only, note "(timing unavailable — run `! loci login`)", and tell
  the user to run `! loci login`, then re-run the skill once signed in.
- **Timing backend unreachable** (any other non-quota error) — report CFG
  analysis only, note "(timing unavailable — `loci timing` error)".
- **Quota exceeded** (`loci timing` → `error.code == "quota_exceeded"`) — **stop
  the skill entirely** — do not emit the post-edit report template. Instead,
  output the quota message with reset time and upgrade CTA:
  ```
  LOCI usage quota reached — post-edit analysis skipped.

  <error.message verbatim — includes usage/limit, reset countdown, and upgrade link>
  ```
  The message already contains reset time and upgrade CTA, e.g.:
  "Daily token limit reached (31,000 / 30,000 tokens). Resets in 4h 23m.
  Upgrade to Premium at auroralabs.com for 300,000 tokens/day."
  Show it verbatim. Then end the skill.
- **No pre-edit artifact** — report absolute timing only, no % diff

## Step 5: Internal reasoning pass (mandatory)

Before emitting any output, think through each of these questions.
Increment `R` (co-reasoning counter) by 1 for this pass.

1. **Timing impact** — is the diff expected given the code change? Flag
   regressions >10% on `execution_time_ns` as a Performance sub-finding.
   Note when the change is timing-neutral or improves performance.
2. **Hotspot check** — does any new/changed block sit among the top 3
   hottest blocks? If yes, record as a Performance sub-finding
   (`new hot-path block <addr> (top-N)`).
3. **Energy budget** — is the energy delta acceptable for the target
   context? Battery-powered / ISR / hot-path: tighten. Once-per-boot:
   looser.
4. **Synthesize per-row Status** — when multiple sub-findings roll up
   to the same Gate (e.g. timing regression + new hot-path block both
   under Performance), the row's Status is the worst of the
   contributors and the Note lists them comma-separated, worst-first.
5. **Verdict cause comes from sub-findings, not Gate names** — the
   OK / CAUTION / FLAG one-sentence cause lifts the lead item from the
   driving row's Note (e.g. "FLAG — timing +147% past budget", not
   "FLAG — Performance row is ❌"). Gate names are for the table;
   the verdict speaks in concrete findings.
6. **Verdict** — OK / CAUTION / FLAG with one-sentence cause. The
   cause goes in the table footer verdict line.

## Step 6: Emit report

The output has three blocks in order: (1) conclusion table, (2) voice
remark, then the LOCI footer. No free-form prose sections, no
multi-paragraph Reasoning write-ups, no per-callee enumerations.

The build block from `loci build compile` is intentionally
NOT shown to the user. The only build-related thing that ever surfaces in
the report is the `LOCI · build mismatch` block, and only when Step 1b's
`loci build diff` actually finds a parity break — that block prints
itself, emit it verbatim when it appears.

Icon vocabulary: ✅ PASS · ⚠️ WARNING · ❌ FAIL.

**Row-inclusion rules:**
- Include a row only if the gate actually produced a value this run.
- Every ⚠️ / ❌ row MUST cite a reason in the Note column — the Note is
  the one-sentence synthesis of the Step 5 reasoning for that gate.
- Skipped gates are omitted (no fourth "N/A" icon).

### Row catalogue — with baseline (`.o.prev` present and non-empty)

Order when present. Before/After columns carry the metric value
(timing or energy); sub-findings ride in the Note.

1. **Safety** — fires only when a CFG-structural hazard is incidentally
   observed in the diff (recursion introduced, indirect call added,
   missing declaration). Status: ❌ for unbounded recursion or BLOCK-
   level missing declaration; ⚠️ for benign-but-noteworthy hazards.
   Rare in post-edit — the row is omitted when nothing was observed.
2. **Performance** — fires when `loci timing` returned. Captures
   `execution_time_ns` diff and hot-path position (new block in top-3).
   Status: ✅ if `|diff%| ≤ 10%` or improvement AND no new hot-path
   block; ⚠️ if `|diff%| > 10%` with absolute within budget OR a new
   hot-path block landed in top-3; ❌ if a known budget is exceeded.
   Before/After = `execution_time_ns`. Note format:
   `<pre>→<post> ns (±X%) [, new hot-path block <addr> (top-N)]`.
3. **Energy** — fires when `loci timing` returned energy. Status logic same as
   Performance with target-context thresholds (ISR/battery tighter
   than once-per-boot). Before/After = energy values. Note cites
   `±X%` and absolute when small.
4. **Stack** — only when stack-depth was invoked as an escalation.
   Note is the one-line summary handed back by stack-depth:
   `stack: <N> B (<usage>%) — <verdict>`. No Before/After.
5. **Memory** — only when memory-report was invoked as an escalation.
   Note: `memory: ROM <X>% / RAM <Y>% — <verdict>`. No Before/After.

Build-parity issues are NOT a table row. `loci build diff`'s own
`LOCI · build mismatch` block (emitted on non-zero exit) already
handles that case visibly and loudly.

### Row catalogue — no baseline (first-edit measurement or empty `.o.prev`)

Drop the Before column; single-column After for the Performance and
Energy rows (no `±%` in the Note since there is no baseline to diff
against — record the absolute values as the new baseline). Safety,
Stack, and Memory rows fire on the same triggers as the with-baseline
case.

### Template (with baseline)

```
## Post-Edit: <FunctionName>

| Gate               | Before    | After     | Status | Note                        |
|--------------------|-----------|-----------|:------:|-----------------------------|
| <row 1 applicable> | <val>     | <val>     |   ?   | <cited reason>               |
| ...                | ...       | ...       |   ?   | ...                          |

Verdict: **<OK|CAUTION|FLAG>** — <one sentence cause>
```

### Template (no baseline)

```
## Post-Edit: <FunctionName> (NEW)

| Gate               | After     | Status | Note                        |
|--------------------|-----------|:------:|-----------------------------|
| <row 1 applicable> | <val>     |   ?   | <cited reason>               |
| ...                | ...       |   ?   | ...                          |

Verdict: **<OK|CAUTION|FLAG>** — <one sentence cause>
(no pre-edit artifact — first measurement on this branch)
```

### Example (with baseline, typical ~6 lines)

```
## Post-Edit: process_message

| Gate         | Before   | After    | Status | Note                              |
|--------------|----------|----------|:------:|-----------------------------------|
| Performance  | 1404 ns  | 3474 ns  |   ⚠️   | +147%, new hot-path block bb_0x1ea (top-1) |
| Energy       | 0.20 µWs | 0.49 µWs |   ⚠️   | +148%, absolute <1 µWs             |

Verdict: **CAUTION (acceptable)** — explicable, once-per-event handler
```

### Action on CAUTION or FLAG

When the table footer is `CAUTION` or `FLAG`, don't stop at reporting.
The skill must:

1. Propose a concrete fix in one sentence, named by the ⚠️ or ❌ row.
   (Example: "`bb_0x1ea` is a wide-integer arithmetic step — consider
   narrowing the type to a 32-bit integer where the value range allows,
   saves ~500 ns.")
2. Ask the user whether to apply the rewrite. Do not silently proceed.

## Re-reasoning triggers (table-driven)

Before emitting the final conclusion table, inspect what the first-pass
reasoning produced. If any pattern below matches, loop back BEFORE
emitting. Each extra `loci timing` call increments `M`; each looped-back synthesis
increments `R`. The table the user sees is the post-loop version.

| Row pattern | Trigger |
|---|---|
| **Performance** ⚠️ with both timing-regression AND new-hot-path-block sub-findings | The new block IS the regression. Don't just report — propose a concrete optimization (cache, lighter callee, inline, different data type) naming the specific block in the Note. Follow the "Action on CAUTION or FLAG" flow. |
| **Performance** AND **Energy** ⚠️ both regress | Real regression in two metrics, not isolated to one. Confidence in ⚠️ is high; proceed to propose root cause. |
| **Stack** Note shows usage > 80% of task budget | Re-run stack-depth with larger `--max-recursion-depth` to confirm; surface the top frame contributor by name in the Note before emitting. |
| **Memory** Note shows region > 90% | Re-run memory-report with `--top-n 20` to identify the specific symbols pushing the region toward its limit before emitting. |

## LOCI voice remark

Before the footer, add one short LOCI voice remark (max 15 words) that
acknowledges the user's work grounded in a specific number from the
analysis. Attribute improvements to the user ("clean work", "smart move",
"tight code"). For concerns, be honest and constructive with specifics.
Skip if the analysis produced no results or the user needs raw data only.

## LOCI footer

After emitting all per-function reports and the voice remark, append the
footer as the last thing printed — **only if N > 0**. If no functions
were processed, do NOT emit the footer.

**Record cumulative stats** (run via Bash before rendering the footer).
Pass `--verdict "<verbatim-verdict-line>"` so the verdict ride-along
ships alongside the per-function trends payload — the line is the same
string already rendered to chat (`Verdict: OK — <cause>`, `Verdict: CAUTION — <cause>`,
or `Verdict: FLAG — <cause>`), unbolded, no surrounding asterisks.

Also pass `--gates '<gates-json>'` — a compact JSON object capturing
the per-row Status from the conclusion table just rendered. Map the
icons: `✅→pass · ⚠️→warn · ❌→fail`. Only include gates that fired
this run (omitted gates were not part of the table). Allowed gate
names: `Safety` · `Performance` · `Energy` · `Stack` · `Memory`.
Example for the worked example above:
`{"Performance":"warn","Energy":"warn"}`.
```
loci stats record --context-file "<project-context>" --skill post-edit --functions <N> --mcp-calls <M> --co-reasoning <R> --verdict "<verbatim-verdict-line>" --gates '<gates-json>'
```

**Record per-function measurements** (single Bash call for all functions).
Pipe all measurements as JSONL via stdin. Skip functions where `loci timing`
was unavailable.
```
echo '<jsonl_records>' | loci stats measure --context-file "<project-context>" --stdin --skill post-edit
```
Where `<jsonl_records>` is one JSON object per line for each modified/added
function with post-edit timing values:
```
{"fn":"<func1>","worst_ns":<execution_time_ns>,"energy_uws":<E>,"src":"<source_file>"}
{"fn":"<func2>","worst_ns":<execution_time_ns>,"energy_uws":<E>,"src":"<source_file>"}
```

The `worst_ns` field name is the storage-schema key consumed by
`loci stats` (preserved for compat with prior on-disk measurements);
pass `execution_time_ns` into it. The `happy_ns` field is no longer
written.

**Read trend lines** (single Bash call for all functions; capture output):
```
loci stats trend-line --context-file "<project-context>" --function <func1>,<func2>,...
```

### Render the footer — compact by default

One line. Icon-led, no surrounding bars, middle-dot separators, spaces
around the `→` arrow. The `trend-line` output is the primary scalar —
parse it into `<fn> · <pre> → <post> ns (<±pct>, <N> edits)`:

```
<icon> LOCI post-edit · <fn> · <pre> → <post> ns (<±pct>, <N> edits)
```

- `<icon>` — mirrors the body's conclusion-table verdict: `✅` for OK,
  `⚠️` for CAUTION, `❌` for FLAG.
- `<fn>` — when `N = 1`, the single edited function. When `N > 1`, the
  compact form is replaced by the expanded form (see below).

Worked example (clean run, N=1):
```
✅ LOCI post-edit · Connection_ConnEventHandler · 1815 → 1498 ns (-17%, 2 edits)
```

### Clean-escalation suffix

When post-edit escalated into `stack-depth` or `memory-report` AND the
escalated skill returned clean, append a space-separated `+<skill>`
marker to the primary scalar:

```
✅ LOCI post-edit · Connection_ConnEventHandler · 1815 → 1498 ns (-17%, 2 edits)  +stack-depth
```

A non-clean escalated result already flips a Stack/Memory row in the
post-edit conclusion table to ⚠️/❌, which flips the post-edit verdict
to CAUTION/FLAG and triggers expansion via the verdict rule below. So
`+<skill>` only ever appears next to a green icon.

### Expand when...

Replace the compact form with the expanded multi-line form if **any**
of the following is true:
- Verdict is `⚠️ CAUTION` or `❌ FLAG`.
- Build-parity mismatch — a `LOCI · build mismatch` block was emitted
  earlier in the report (toolchain changed between preflight and
  post-edit; % diffs are low-confidence).
- `N > 1` functions were modified/added in this run — the compact line
  cannot carry per-function trends honestly; render the expanded form
  with one `↳ trend:` line per function.

Expanded form:
```
─── LOCI · post-edit ───────────────────
  <N> functions · <M> loci timing calls · <R> co-reasoning
  Verdict: <OK | CAUTION | FLAG> — <one-line summary>
    ↳ trend: <trend-line-output>       ← one line per function
────────────────────────────────────────
```

The expanded form does **not** include the cumulative branch-stats line.

- **N** = unique functions (modified + added) whose assembly was sent to LOCI
- **M** = `loci timing` calls (one per timing CSV)
  (typically 2 for modified functions: pre + post; 1 for added functions)
- **R** = co-reasoning (one per function that has a Reasoning section)
