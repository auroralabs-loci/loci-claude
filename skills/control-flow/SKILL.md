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

**Bounds.** The cycles and indirect calls this skill reports are the facts the
repository's structural invariants bound, so also apply the shared **The Contract
Envelope is input only**, **One fact, one row: the entry decides the status**,
**Structural invariants: which measurement answers which signal**, and **When
there is no contract** sections. The contract is read-only to you.

Your scope is the limit here, and it decides what you may claim. A CFG is cut per
function, while every structural invariant covers the whole binary — so a hazard
you find is **evidence of a breach** and you report it as one, quoting the entry's
`text`, but a clean set of CFGs is **not** the invariant holding. Never report `0`
against a whole-binary bound from the functions you happened to analyze; say the
invariant is unmeasured at this scope and that `stack-depth` is what measures it
over the linked binary. Reporting a breach needs no such caveat: one unbounded
cycle breaches `unbounded_recursion` no matter how few functions you looked at.

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
re-detect), then pick a binary through B1-B4 — collect candidates, **discard the
ones B3 reports stale**, and cross-compile only if nothing survives. "An existing
binary is available" is not on its own a reason to measure it; that reading is what
produced an analysis of a binary older than its own source. In the steps
below, replace `<compiler>` and `<flags>` with the resolved values.

## Incremental Path — only for a known in-flight edit

**When this path applies.** Only when you are measuring an edit already in flight —
the `preflight` → `post-edit` chain, where a `.o` for *this* source was produced
earlier in *this* session and the point is the before/after diff. A standalone
`/control-flow` request in a fresh session is **not** that: it goes through
freshness-gated **Step 0 — Pattern B** instead, whose B1 rule decides whether a `.o`
can answer the question at all.

"A previous `.o` exists" is therefore a precondition, not a trigger — an orphaned
`.o` from an earlier session says nothing about the request in hand. Pattern B is
read first and it wins; this path is the exception it delegates to.

A CFG cut from a `.o` **stops at the translation-unit boundary**: every `bl` to an
outside symbol is an unapplied relocation whose encoded target is the instruction
itself, so cross-TU call edges are absent, not merely unlabelled (Pattern B, B1).
For a call-dependency or function-impact question that is a wrong answer, not a
partial one — use a linked binary. For "what changed inside this function", it is
the right tool; say "within this translation unit" in the report.

Then, with those conditions met:

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

## Artifact provenance (mandatory)

Emit the **Step 0 — Pattern B, B4** line once per run, at the end of the report
body:

```
Artifact: filter.elf (linked 2026-07-28 09:14:02, sources current)
```

Take the build time and the freshness phrase from what `loci build fresh` returned,
or from `.data.source_provenance` on the `loci elf cfg` envelope you already have
(`elf_mtime`, and `stale` → `sources current` /
`SOURCES NEWER THAN THIS BINARY` / `freshness unverified — <reason>`). When the CFG
came from a `.o`, add B4's scope note — cross-TU call edges are absent, so a
call-dependency claim from that graph is incomplete by construction.

Never omit this line, and never write "sources current" without having run the
check.

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
  are found. "A context that forbids them" is the contract when an entry
  covers the signal: its `severity` sets the icon, so a `fail` entry on
  `unresolved_indirect_calls` makes a single call site `❌`, not `⚠️`.
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
