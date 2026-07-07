---
description: >
  Quick-reference guide to LOCI — shows available skills, environment status,
  and troubleshooting for build environment and sign-in issues.
when_to_use: >
  When user asks for help with LOCI, what LOCI can do, how to use LOCI,
  available commands, or types /help. Also when user seems confused about
  LOCI setup or capabilities.
---

# LOCI Help

Show the user their environment status, available skills, and a contextual
next step. Adapt the output based on what is actually working vs missing.

## Step 0: Diagnose Environment

Read the LOCI session context from the `system-reminder` block emitted at
session start:

```
Target: <target>, Compiler: <compiler>, Build: <build>
LOCI target: <loci_target>
loci command: loci (on PATH)   ← "detection in progress" when setup not ready
plugin dir: <path>
```

To check sign-in state, run `loci auth status` (exit 0 + `data.status ==
"signed_in"` when signed in; exit 3 / `error.code == "auth_required"` when not).

Classify the environment into one of three states:

| State | How to detect | Priority |
|-------|---------------|----------|
| **Not signed in** | `loci auth status` returns `auth_required` (exit 3) | Check first |
| **No build env** | Target = `unknown` OR Compiler = `unknown` in session context | Check second |
| **Ready** | Target and Compiler are both known, and `loci auth status` is signed in | Default |

A session can be in multiple degraded states simultaneously (no build env AND
not signed in). Report all that apply.

## Step 1: Show Environment Status

Based on Step 0, render the appropriate status block.

### When fully ready

If signed in, call `loci usage` to get the user's plan and current quota. Read
`data.plan` and `data.daily` (`{used, limit, remaining}`); `data.eligible` is
`false` when the quota is exhausted. (`loci usage` returns the real limit for
the user's plan — no need to hardcode tier limits.)

```
## Environment
  Target:    <loci_target> (<mapped CPU name>)
  Compiler:  <compiler>
  Build:     <build_system>
  Auth:      signed in
  Quota:     <data.daily.used> / <data.daily.limit> daily tokens (<data.plan>)
```

If `data.eligible` is `false` (quota exhausted), show instead:
```
  Quota:     <data.daily.used> / <data.daily.limit> daily tokens — LIMIT REACHED (<data.plan>)
```

Map LOCI target to CPU name:

| LOCI target | CPU |
|---|---|
| aarch64 | A53 |
| cortexm / armv7e-m | Cortex-M4 |
| armv6-m | Cortex-M0+ |
| tricore / tc399 | TC399 |

### When build environment is missing

```
## Environment — setup needed

LOCI didn't detect a build environment in this directory.

To get started:
1. `cd` into a C/C++/Rust project with source files
2. Ensure a cross-compiler is installed:
   - ARM Cortex-M: `arm-none-eabi-gcc`
   - ARM Cortex-A: `aarch64-linux-gnu-gcc`
   - TriCore: `tricore-elf-gcc`
3. Restart Claude Code so LOCI can auto-detect the project

Or point LOCI at an existing binary directly:
  "What's the execution cost of main() in path/to/firmware.elf?"
```

### When not signed in

```
## Environment — sign-in needed

LOCI's timing and energy analysis requires a signed-in session.

→ Run `! loci login` in your terminal, then re-run /help.

Skills that work signed-out: /stack-depth, /memory-report, /control-flow
Skills that need sign-in:    /exec-trace, loci-preflight, loci-post-edit
```

## Step 2: Show Available Skills

Always show the full skill list regardless of environment state — users
should know what's possible even if their setup isn't complete yet.

```
## On-demand skills

  /exec-trace      Timing & energy from real silicon traces
                   "What's the execution cost of main()?"

  /stack-depth     Worst-case stack depth & budget check
                   "Is my stack safe for TaskMain with 2048 bytes?"

  /memory-report   ROM/RAM breakdown from ELF/map files
                   "How much ROM/RAM does my build use?"

  /control-flow    Annotated control-flow graphs
                   "Show me the call graph for process_data()"

## Auto-running (no command needed)

  loci-preflight   Runs in /plan — checks call graph, timing, energy, execution fit
                   Escalates to /stack-depth or /memory-report when needed
                   Verdict: GOOD / ADJUST PLAN / STOP

  loci-post-edit   Runs after edits — diffs binary, reports timing/energy % delta
                   Verdict: OK / CAUTION / FLAG (proposes fix on FLAG)
```

## Step 3: Contextual Next Step

Based on the environment state from Step 0, suggest a single next action:

- **Ready + ELF files exist in project**: "You have compiled binaries — try asking about timing for a specific function, or run `/memory-report` for a full ROM/RAM breakdown."
- **Ready + no ELF files**: "Compile your project first, then ask about timing or stack depth for a specific function."
- **No build env**: "Navigate to your C/C++/Rust project directory and restart Claude Code, or point me at a `.elf`, `.o`, or `.axf` file directly."
- **Not signed in**: "Run `! loci login` to unlock timing and energy analysis."

If multiple issues exist, prioritize sign-in first (it's the quicker fix),
then build environment setup.

## Stats Footer

After rendering all help output, run via Bash:
```
loci stats global-summary
```

If `data.report` is non-empty, append it as the last line — no heading, just
the stats line. If empty (first-time user), show nothing.

Do NOT record stats for this skill — help is informational only.

