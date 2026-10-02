---
name: trends
description: >
  Per-function measurement history on the current branch: timing, energy,
  stack, and memory trends over time from LOCI analysis. Use when the user says
  "show trends", "optimization progress", "what changed on this branch" (in
  LOCI data), "how are my functions doing", or asks about performance trajectory.
when_to_use: >
  Also /loci:trends, or whether an optimization sprint is working.
---

# LOCI Trends

This skill calls the bare `loci` command (on PATH via the session bootstrap)
and resolves `<project-context>` with `loci project` — see Step 0. Every
`loci` call prints one JSON envelope (`{ok,data}`); let it print and read `ok`.

## Step 0: Resolve the project

Run `loci project`, passing `--project-root` if the user named one, and read the
file its `context_file` names — that is `<project-context>`. Extract `git_branch`
for the report header. No file there means this project has no recorded history:
say so and stop — offering `/loci:init <project_root>` when the answer was
`not_initialized`. `not_resolved`: ask which project.

## Step 1: Retrieve trend summary

Run via Bash:
```
loci trends --context-file "<project-context>"
```

If `data.count` is 0 (no measurements), respond with:

> Nothing recorded on this branch yet.

Nothing more. Do not suggest running other skills or explain how to generate
measurements.

## Step 2: Render the report

If step 1 returned measurements (`data.count > 0`), render `data.report` (the
rendered trend table) with a heading that includes the branch name from step 0,
followed by a one-line summary derived from the trend data:

```
## LOCI Trends: <branch_name>

**<N> functions tracked · <M> measurements · <K> improvements · <J> regressions · <B> baselines**

<data.report from step 1>
```

Computing the summary line:
- **N** = rows in the trend table (same as the "Branch summary: N functions tracked" line that `loci trends` already prints in `data.report` — safe to copy verbatim).
- **M** = total measurements (sum of the Edits column, also present in the CLI's "Branch summary" line).
- **K** = count of rows with `Direction = improved`.
- **J** = count of rows with `Direction = regressed`.
- **B** = count of rows with `Direction = baseline`.

The table shows only columns that have data — timing from post-edit
auto-runs, stack from /loci:stack-depth invocations, memory from /loci:memory-report
invocations. No empty columns, no missing-data notices.

## Step 3: Single-function drill-down (optional)

If the user asks about a specific function, run:
```
loci trends --context-file "<project-context>" --function <func_name>
```

Render `data.report` (the chronological output) under a heading:
```
### <func_name>

<data.report from above>
```

## LOCI voice remark

One line, at the **end** of the report rather than before a footer:
[The voice remark](../_shared/voice.md#voice-remark). This skill's own slant is
that the remark reinforces the value of measuring — why tracking matters, and a
nudge to keep going.

With improvements or regressions, ground it in a specific number:
- "3 functions faster since branch start. The data is paying off."
- "process_data down 25% from peak — you caught that early."
- "All stable. That's the baseline locked in for the next change."

When there are only baselines (single measurements), highlight what comes
next:
- "Baseline captured — LOCI flags the next edit that moves it."
- "Baseline locked. Your next edit shows the delta."
- "1 function tracked. LOCI will show the impact of your next edit."

No footer separator lines after the remark.

Do NOT record stats for this skill — trends is a read-only view.
