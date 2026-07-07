---
description: Analyze function execution timing and energy from compiled assembly
when_to_use: When user asks for timing/energy of a specific function from compiled assembly.
---

# LOCI Timing Analysis

**Shared runtime contract.** Before running this skill, read
`<plugin-dir>/skills/_shared/loci-runtime-contract.md` and apply its
**Session context placeholders**, **Tool boundary: `loci elf` only**, **Output:
the JSON envelope**, **Cross-compilation defaults**, and **Step 0 — Pattern B:
analyze an existing binary** sections. The sections below add only this skill's
specifics.

**Tool boundary (reminder):** `loci elf` only — never `objdump`, `readelf`,
`addr2line`, or `nm`. This skill needs the per-block timing CSV and annotated
CFG that binutils cannot produce. Always pass `--arch <loci_target>`.

For example, to extract assembly for functions `function_1` and `function_2` from `filter.elf`:
```
loci elf asm --elf filter.elf --functions function_1,function_2 --arch <loci_target>
```
The envelope's `data.timing_csv` is the consolidated per-block timing-CSV **file
path** (feed it to `loci timing` in step 3) and `data.timing_architecture` is the
arch to pass there. `data.control_flow` is the path to the annotated CFG file —
read it when generating analysis results.

## Step 0: Resolve architecture and toolchain

Follow **Step 0 — Pattern B** and **Cross-compilation defaults** in the shared
runtime contract: use `<loci_target>` from the session context (do not
re-detect), reuse an existing binary when one is available, otherwise
cross-compile for `<loci_target>` with the default compiler/flags. In the steps
below, replace `<compiler>` and `<flags>` with the resolved values.

**Authentication is on-demand.** `loci timing` (step 3) needs a signed-in LOCI
session; it checks lazily. There is no upfront probe — if step 3 returns
`error.code == "auth_required"`, handle it per §4 (tell the user to run
`! loci login`, then stop). Do not run `/mcp` — timing no longer goes through MCP.

## Incremental Path (preferred)

If a previous `.o` exists in `.loci-build/<loci_target>/`, use incremental compilation:

1. Save the existing `.o` as `.o.prev`
2. Compile only the changed source with `-c`.
   Always include `-g` to emit DWARF debug info (required by `loci elf`):
   ```
   <compiler> -g <flags> -c <source> -o .loci-build/<loci_target>/<basename>.o
   ```
3. Diff `.o.prev` vs `.o` to find changed functions:
   ```
   loci elf diff --elf .o.prev --comparing-elf .o --arch <loci_target>
   ```
4. Extract assembly for only `modified`/`added` functions:
   ```
   loci elf asm --elf .o --functions <changed_funcs> --arch <loci_target>
   ```
5. Skip to step 3 (the `loci timing` call) below.

If no `.o` exists yet, fall through to full compilation.

## Full Compilation Path

1. Cross-compile the target file for the resolved architecture:
   ```
   <compiler> <flags> -o <binary> <source>
   ```
2. Extract assembly with per-block granularity:
   ```
   loci elf asm --elf <binary> --functions <func> --blocks blocks.csv --arch <loci_target>
   ```
   The envelope's `data.timing_csv` is the consolidated per-block timing-CSV
   **file path** and `data.timing_architecture` is the architecture to predict against.
3. Call `loci timing` once with the consolidated timing CSV:
   ```
   loci timing --architecture <data.timing_architecture> --csv-file <data.timing_csv>
   ```
   It returns `data.rows` (per-block `function_name`, `execution_time_ns`,
   `std_dev_ns`, `energy_ws`); use those rows when reporting.
4. **Auth / quota gate — branch on the `loci timing` envelope `error.code`.**
   If it returns `error.code == "auth_required"`, **stop the skill
   entirely** — emit only:
   ```
   LOCI sign-in required — timing analysis skipped.
   Run `! loci login`, then re-run this skill.
   ```
   If it returns `error.code == "quota_exceeded"`, **stop the skill
   entirely** — emit only:
   ```
   LOCI usage quota reached — timing analysis skipped.

   <error.message verbatim>
   ```
   Either way, end the skill — do not continue to steps 5-8; no record/measure
   calls fire on the auth- or quota-skipped path.
5. Report execution time and standard deviation in microseconds, and energy consumption in Watt-seconds (`energy_ws`)
6. When reporting results, 
   - note that these measurements come from LOCI's LCLM trained on real HW traces — they reflect actual silicon behavior on the target board, not theoretical IPC estimates. 
   - High `std_dev_ns` indicates the assembly pattern is underrepresented in the training data; low `std_dev_ns` means strong empirical backing.
   - `loci timing` row fields are exactly: `function_name`, `std_dev_ns`, `execution_time_ns`, `energy_ws`. There is no bare `std_dev` field — reference field names literally.
   - using the annotated CFG read from the `data.control_flow` file path from step 2, select a most likely execution path to do performance analysis on with the timing data.
   - highlight the hottest blocks in source code if source code info is available in the annotated CFG.
   - Note for the model (not user-facing): exec-trace's `worst_ns` is a body-only sum and excludes callees because `loci elf`'s CFG terminates at every `bl`. Cross-skill comparison with `post-edit` worst_ns isn't apples-to-apples — post-edit is path-traced and includes callee transitions.

7. **Aggregate per-function from the LCLM block CSV + CFG.** For each function `fn` produced by step 2:
   - `worst_ns` = sum of `execution_time_ns` across every block whose `function_name` matches `<fn>_0x*`
   - `happy_ns` = sum along the longest acyclic path through the CFG starting at the entry block (back-edges contribute zero, callees not traced)
   - `energy_uws` = Σ(`energy_ws` × 1e6) across the same blocks (LCLM emits Joules; the schema field is microWatt-seconds = µJ)
   - `src` = the source file most frequently cited in the CFG block annotations for that function (project-relative path; strip absolute prefixes like `/Users/.../<project_root>/` when present, otherwise basename)
   Skip any function whose blocks all returned errors from `loci timing`.

7.5. **Look up the previous `worst_ns` and `ts` per function** from the LOCI state JSONL BEFORE the new measurement is appended. Honor `$LOCI_STATE_DIR` if set; otherwise fall back to `~/.loci/state`. Read `cwd_hash` and `branch_slug` from the project-context JSON. One per-function lookup is enough — the JSONL has one row per line:
   ```
   STATE_DIR="${LOCI_STATE_DIR:-$HOME/.loci/state}"
   PREV_LINE=$(grep -F '"fn":"<fn>"' "$STATE_DIR/loci-measurements-<cwd_hash>-<branch_slug>.jsonl" 2>/dev/null | tail -n1)
   if [ -n "$PREV_LINE" ]; then
     PREV_NS=$(printf '%s' "$PREV_LINE" | jq -r '.worst_ns // empty')
     PREV_TS=$(printf '%s' "$PREV_LINE" | jq -r '.ts // empty')
   else
     PREV_NS=""; PREV_TS=""
   fi
   ```
   Empty `PREV_NS` = no prior record (this fn baselines this run). The
   `[ -n "$PREV_LINE" ]` guard avoids feeding empty stdin to `jq`, which
   would print a parse error to stderr.

8. **Synthesise the verdict line** using the regression-based taxonomy in §Verdict semantics below. Render the line as the final line of the report body (just before the voice remark) so the user sees the same string that gets passed to `record --verdict`:
   - All-baseline (zero functions with priors): `Verdict: OK — baseline established for N functions (measurement milestone set, no prior data).`
   - K of N have priors and all `delta_pct ≤ +10%`: `Verdict: OK — K of N within ±10% vs last run (max delta <signed-pct>% on <fn>); <N-K> baselined.` Drop the `; <N-K> baselined` clause when K == N.
   - Any function with priors has `delta_pct > +10%`: `Verdict: CAUTION — <fn> regressed +<pct>% (<prev_ns>→<curr_ns> ns, last run <prev_ts>); <K-1> others stable, <N-K> baselined.` Cite the worst-regressing function. Drop the `, <N-K> baselined` clause when K == N.

   Note: the §LOCI footer skips both record commands when N == 0, so a "FLAG"
   verdict is never persisted. If you want a no-resolved-functions state to
   show up in the dashboard, that's a separate behavior change — for now,
   N == 0 runs exit silently (no footer, no record calls).

## Verdict semantics

Regression-gated, not quality-gated. Quality indicators (std_dev, partial coverage) belong in the report body — they don't drive the verdict. For each analyzed function `fn`:

```
delta_pct(fn) = (current_worst_ns - prev_worst_ns) / prev_worst_ns × 100
```

| verdict | trigger |
|---|---|
| **OK (baseline)** | No prior `worst_ns` exists for ANY analyzed function (zero functions had priors). |
| **OK** | At least one function has prior data and every prior-bearing function has `delta_pct ≤ +10%` (improvements + stable both qualify). Functions without priors are silently treated as baselines and don't contribute to the verdict — they're counted in the line as `<N-K> baselined`. |
| **CAUTION** | At least one function with prior data has `delta_pct > +10%`. Cite the worst-regressing function in the cause. |

`FLAG` is reserved (no functions resolved / total `loci timing` failure) but is not
currently shipped — the §LOCI footer skips record calls when N == 0, so
the verdict path isn't reached. Quota errors are handled by §4's
early-exit, also without recording.

Verdict line format matches `loci-post-edit`'s exactly:
```
Verdict: <OK|CAUTION> — <one-sentence cause grounded in numbers>
```

## LOCI voice remark

Before the footer, add one short LOCI voice remark (max 15 words) that
acknowledges the user's work grounded in a specific number from the
analysis. Attribute improvements to the user ("clean work", "smart move",
"tight code"). For concerns, be honest and constructive with specifics.
Skip if the analysis produced no results or the user needs raw data only.

## LOCI footer

After reporting timing results and the voice remark, append the footer
as the last thing printed — **only if N > 0**. If no functions were
processed, do NOT emit the footer.

**Record cumulative stats + verdict** (run via Bash before rendering the footer).
Pass `--verdict "<verbatim-verdict-line>"` so the verdict ride-along ships
alongside the per-function trends payload — the line is the same string
already rendered to chat (`Verdict: OK — <cause>`, `Verdict: CAUTION — <cause>`,
or `Verdict: FLAG — <cause>`), unbolded, no surrounding asterisks.
```
loci stats record --context-file "<project-context>" --skill exec-trace --functions <N> --mcp-calls <M> --co-reasoning 0 --verdict "<verbatim-verdict-line>"
```

**Record per-function measurements** (single Bash call for all functions).
Pipe one JSON object per analyzed function as JSONL via stdin. Skip any
function for which LCLM returned no rows in step 7:
```
echo '<jsonl_records>' | loci stats measure --context-file "<project-context>" --stdin --skill exec-trace
```
Where `<jsonl_records>` is one JSON object per analyzed function:
```
{"fn":"<func1>","worst_ns":<W>,"happy_ns":<H>,"energy_uws":<E>,"src":"<source_file>"}
{"fn":"<func2>","worst_ns":<W>,"happy_ns":<H>,"energy_uws":<E>,"src":"<source_file>"}
```

Both record commands MUST run only when N > 0 — when the skill exits via
the §4 quota path (or any other zero-result path), skip the footer and
the record commands entirely.

Do NOT call `loci stats summary` here. The cumulative branch-stats
line is deliberately removed from skill footers — it is available via
the `trends` skill when the user asks for it.

### Render the footer — compact by default

One line. Icon-led, no surrounding bars, middle-dot separators:

```
<icon> LOCI exec-trace · <N> fn · worst <T>
```

- `<icon>` — `✅` when the run completed with full `loci timing` data; `⚠️` when
  some blocks were skipped (partial coverage).
- `<N>` — unique functions whose assembly was sent to LOCI.
- `<T>` — worst-case execution time, human-readable unit (ns / µs / ms).

Worked examples:
```
✅ LOCI exec-trace · 2 fn · worst 1.4 µs
⚠️ LOCI exec-trace · 3 fn · worst 780 ns
```

### Expand when...

Replace the compact form with the expanded multi-line form if **any**
of the following is true:
- `loci timing` returned partial data (some rows skipped).
- The engineer asked for per-function detail, or multiple functions
  where the one-line summary would hide critical deltas.

Expanded form:
```
─── LOCI · exec-trace ──────────────────
  <N> functions · <M> loci timing calls for execution behavior
────────────────────────────────────────
```

The expanded form does **not** include the cumulative branch-stats line.

- **N** = unique functions whose assembly was sent to LOCI
- **M** = `loci timing` calls (one per timing CSV)
