---
description: Create annotated CFG (Control Flow Graphs) in text format optimised for LLM analysis on compiled assembly code to provide execution insights
when_to_use: When user asks about call dependencies, function impact, or control flow analysis from compiled code.
---

# LOCI Control Flow Analysis

**Shared runtime contract.** Before running this skill, read
`<plugin-dir>/skills/_shared/loci-runtime-contract.md` and apply its
**Session context placeholders**, **Tool boundary: `loci elf` only**, **Output:
the JSON envelope**, **Cross-compilation defaults**, and **Step 0 — Pattern B:
analyze an existing binary** sections. The sections below add only this skill's
specifics.

**Tool boundary (reminder):** `loci elf` only — never `objdump`, `readelf`,
`addr2line`, or `nm`. This skill needs the annotated CFG that binutils cannot
produce. Always pass `--arch <loci_target>`.

For example, to generate annotated CFG for a function called `apply_filter` from `filter.elf`:
```
loci elf cfg --elf filter.elf --functions apply_filter --arch <loci_target>
```
The envelope's `data.control_flow` is the path to the CFG file (text optimized for
LLM analysis); read that file for the analysis steps.

## Step 0: Resolve architecture and toolchain

Follow **Step 0 — Pattern B** and **Cross-compilation defaults** in the shared
runtime contract: use `<loci_target>` from the session context (do not
re-detect), reuse an existing binary when one is available, otherwise
cross-compile for `<loci_target>` with the default compiler/flags. In the steps
below, replace `<compiler>` and `<flags>` with the resolved values.

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
4. Generate CFG's (Control Flow Graphs) for only `modified`/`added` functions:
   ```
   loci elf cfg --elf .o --functions <changed_funcs> --arch <loci_target>
   ```
   `data.control_flow` is the path to the CFG file (text optimized for LLM
   analysis); read it for step 5.
5. Report change analysis based on the generated graphs.

If no `.o` exists yet, fall through to full compilation.

## Full Compilation Path

1. Cross-compile the target file for the resolved architecture:
   ```
   <compiler> <flags> -o <binary> <source>
   ```
2. Extract annotated CFG's for analysis:
   ```
   loci elf cfg --elf <binary> --functions <func> --arch <loci_target>
   ```
   `data.control_flow` is the path to the CFG file (text optimized for LLM
   analysis); read it for step 3.
3. Report analysis for selected functions based on the generated CFG's

## LOCI voice remark

Before the footer, add one short LOCI voice remark (max 15 words) that
acknowledges the user's work grounded in a specific number from the
analysis. Attribute improvements to the user ("clean work", "smart move",
"tight code"). For concerns, be honest and constructive with specifics.
Skip if the analysis produced no results or the user needs raw data only.

## LOCI footer

After the control-flow analysis and voice remark, append the footer as
the last thing printed — **only if N > 0**. If no functions were
processed, do NOT emit the footer.

**Record cumulative stats + verdict** (run via Bash before rendering the footer).
Pass `--verdict "<verbatim-verdict-line>"` so the gate outcome ships alongside
the trends payload on the next Stop-hook flush. The line follows the same
shape used in the expanded footer (`Verdict: <CLEAN|FINDINGS|BLOCK> — <one-line summary>`),
synthesised regardless of whether the compact or expanded form is rendered to
chat — the `<one-line summary>` should match the compact footer's `<shape>`
field (e.g., `clean`, `2 indirect`, `unbounded recursion`). Pass it unbolded,
no surrounding asterisks.
```
loci stats record --context-file "<project-context>" --skill control-flow --functions <N> --mcp-calls 0 --co-reasoning 0 --verdict "<verbatim-verdict-line>"
```

Worked examples of `<verbatim-verdict-line>`:
```
Verdict: CLEAN — no findings across 3 functions
Verdict: FINDINGS — 2 indirect call sites on non-ISR paths
Verdict: BLOCK — unbounded recursion in parser_descend
```

Do NOT call `loci stats summary` here. The cumulative branch-stats
line is deliberately removed from skill footers — it is available via
the `trends` skill when the user asks for it.

### Render the footer — compact by default

One line. Icon-led, no surrounding bars, middle-dot separators:

```
<icon> LOCI control-flow · <N> fn · <shape>
```

- `<icon>` — `✅` when the analysis is clean (no unbounded cycles, no
  unresolved indirect calls in a context that forbids them); `⚠️` when
  non-critical findings surface (indirect calls on non-ISR paths,
  bounded recursion); `❌` when unbounded recursion or CFI violations
  are found.
- `<shape>` — one of: `clean` (no findings), `<K> cycles` (K
  back-edges/loops reported), `<K> indirect` (K indirect-call sites
  flagged), or a combined `<K> cycles · <L> indirect`.

Worked examples:
```
✅ LOCI control-flow · 3 fn · clean
⚠️ LOCI control-flow · 5 fn · 2 indirect
❌ LOCI control-flow · 1 fn · unbounded recursion
```

### Expand when...

Replace the compact form with the expanded multi-line form if the
verdict is `⚠️` or `❌` **and** the per-function findings need the
room (e.g. several flagged functions or a mix of cycles and indirect
calls that a one-line shape description cannot fairly summarize).

Expanded form:
```
─── LOCI · control-flow ────────────────
  <N> functions analyzed
  Verdict: <CLEAN | FINDINGS | BLOCK> — <one-line summary>
────────────────────────────────────────
```

The expanded form does **not** include the cumulative branch-stats line.

- **N** = unique functions whose CFG was extracted and analyzed
