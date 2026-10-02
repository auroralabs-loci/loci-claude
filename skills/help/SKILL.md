---
name: help
description: >
  Quick-reference guide to LOCI — shows available skills, environment status,
  and troubleshooting for initialization and sign-in issues. Use when the user
  asks for help with LOCI, what LOCI can do, how to use it, which LOCI commands
  exist, or seems confused about LOCI setup or capabilities.
when_to_use: >
  Also when the user types /loci:help. Not for recording how the project builds —
  that is /loci:init, which this skill points at.
---

# LOCI Help

Show the user their environment status, available skills, and a contextual
next step. Adapt the output based on what is actually working vs missing.

## Fast path

1. The project's facts come from `loci project` (Step 0). Do **not** re-derive any
   of them, and do not `find`, Glob or `ls` the project for ELF files.
2. **One Bash** covering the project, auth, quota, and stats:
   `loci project; loci auth status; loci usage; loci stats global-summary`
   (skip `usage` / `global-summary` only if `auth status` is `auth_required`).
   Each prints one JSON envelope; let it print and read the fields off it — no
   `jq`, and no `2>&1`, which puts stderr text in front of the JSON.
3. Render the status block, the **whole** Step 2 skill list (never a subset —
   it is what the user is being shown LOCI can do) and one next step. Stop.

## Step 0: Diagnose Environment

The session context names no project. It carries the CLI's health — `loci command:`,
and a lowercase `loci: …` line when the install is not right — and `plugin dir:`.
Ask which project the user is in, and whether they are signed in:

    loci project [--project-root <path the user named>]
    loci auth status

`loci auth status` exits 0 with `data.status == "signed_in"` when signed in, and 3
with `error.code == "auth_required"` when not.

**`expires_at` is not a sign-in deadline — never report it as one.** It is the
*access* token's expiry, and the CLI spends the refresh token to renew that one
silently on the next command that needs it (`_session.ensure_session`). The date
a user would have to act on is the refresh token's, which the CLI does not see,
so relaying "signed in until <date>" tells them to plan for a sign-in that will
not happen. Say "signed in", and quote a timestamp only if the user asked what
the access token's lifetime is.

Classify in this order and report every row that applies:

| State | How to tell | |
|-------|-------------|---|
| **Not signed in** | `loci auth status` answers `auth_required` | Check first |
| **CLI unhealthy** | a lowercase `loci: …` line in the session context | Relay it verbatim; it names its remedy |
| **No project named** | `loci project` answers `not_resolved` | The block below |
| **No supported target** | `loci project` answers `not_initialized` with `init_status: unsupported` | The block below |
| **Not initialized** | `loci project` answers `not_initialized` otherwise | The block below |
| **Ready** | `loci project` answers `ok` | The block below |

## Step 1: Show Environment Status

Based on Step 0, render the appropriate status block.

### When ready

If signed in, call `loci usage` to get the user's plan and current quota. Read
`data.plan` and `data.daily` (`{used, limit, remaining}`); `data.eligible` is
`false` when the quota is exhausted. (`loci usage` returns the real limit for
the user's plan — no need to hardcode tier limits.)

```
## Environment
  Target:    <loci_target> (<mapped CPU name>)
  Compiler:  <compiler>
  Build:     <build_system>
  Recipe:    <recipe>
  Auth:      signed in
  Quota:     <data.daily.used> / <data.daily.limit> daily tokens (<data.plan>)
```

**One row per field `loci project` returned — omit a null one.** Each comes from
that field and from nowhere else: do not re-derive a target,
do not read a compiler off PATH, and do not report a validation tier here (that
belongs on a measurement's own provenance line, where the numbers it qualifies
are).

**One exception, and it is the reason people run `/loci:help`:** when the `loci`
line carries an advisory — the CLI being behind the version this plugin pins, or
still installing — relay **that sentence** under `Auth:` as its own row and name
`/loci:setup`. Relay it, do not restate it: the CLI's own version number belongs
in `/loci:bug-report` and nowhere else, so a user who typed `/loci:help` because something
behaved oddly learns that something is wrong and what to run, without being handed
two version numbers to compare.

If `data.eligible` is `false` (quota exhausted), show instead:
```
  Quota:     <data.daily.used> / <data.daily.limit> daily tokens — LIMIT REACHED (<data.plan>)
```

Map LOCI target to CPU name:

| LOCI target | CPU |
|---|---|
| aarch64 | A53 |
| armv7e-m | Cortex-M4 |
| armv6-m | Cortex-M0+ |
| tc399 | TC399 |

### When the project is not initialized

When `loci project` answers `not_initialized`.

```
## Environment — not initialized

No build recipe records how this project builds, so LOCI does not yet know its
target ISA, its compiler or its flags — and every measurement rests on those.

→ Run `/loci:init <project_root>`. It reads the project, asks at most one question (which
  target ISA), and records `.loci/build.yaml` — machine-local, gitignored, one
  per checkout. Anything it needs and cannot find, it names. A toolchain that
  lives in a Docker container is detected; the image is all it may ask for.

Or point LOCI at an existing binary directly:
  "What's the execution cost of main() in path/to/firmware.elf?"
```

Do not list toolchains to install — `/loci:init` reports what is missing on this
machine, and anything named here is a guess about a project you have not read.

### When no project is named

```
## Environment — no project named

LOCI works on the project you name; nothing is assumed from where this session
started.

→ Name one — "check main in ~/Projects/fw" — or point me at a binary:
  "stack depth of main in build/app.elf".
```

If the `loci:` line says `uv` is missing, relay that: nothing else in LOCI runs
until it is there. LOCI checks for it and **never** installs it — it is a host
tool — so give the user the install line and say `/loci:setup` verifies the rest
once they have it.

### When the project has no supported target

```
## Environment — no supported target

`loci init` found no target ISA LOCI supports here (aarch64, armv7e-m, armv6-m,
tc399), and that result is recorded and permanent.

→ If LOCI should be measuring a different image in this repo (a bootloader, a
  second core), run `/loci:init` and name its target explicitly.
```

### When not signed in

```
## Environment — sign-in needed

Every LOCI analysis skill requires a signed-in session.

→ Run `! loci login` in your terminal, then re-run /loci:help.

Skills that work signed-out: /loci:help, /loci:setup, /loci:bug-report, /loci:contract
Skills that need sign-in:    /loci:init, /loci:exec-trace, /loci:stack-depth,
                             /loci:memory-report, /loci:control-flow, /loci:trends,
                             loci-preflight, loci-post-edit
```

## Step 2: Show Available Skills

Always show the full skill list regardless of environment state — users
should know what's possible even if their setup isn't complete yet.

```
## On-demand skills

  /loci:init           Record how this project builds — target ISA, compiler,
                       build system, artifact — into `.loci/build.yaml`
                       "Set this project up for LOCI"

  /loci:exec-trace     Timing & energy from real workloads & platform traces
                       "What's the execution cost of main()?"

  /loci:stack-depth    Worst-case stack depth & budget check
                       "Is my stack safe for TaskMain with 2048 bytes?"

  /loci:memory-report  ROM/RAM breakdown from ELF/map files
                       "How much ROM/RAM does my build use?"

  /loci:control-flow   Annotated control-flow graphs
                       "Show me the call graph for process_data()"

  /loci:contract       Author and inspect this repo's bounds in `.loci/contract.yaml`
                       "TaskMain must not exceed 4 kB of stack"

  /loci:trends         What changed per function on this branch
                       "How are my functions doing?"

  /loci:bug-report     Forensic diagnostic when LOCI itself misbehaves
                       "LOCI didn't run" · works signed out

  /loci:setup          Install or repair the loci CLI and check the environment

## Auto-running (no command needed)

  loci-preflight       Runs in /plan — checks call graph, timing, energy, execution fit
                       Escalates to /loci:stack-depth or /loci:memory-report when needed

  loci-post-edit       Runs after edits — diffs binary, reports timing/energy % delta
                       Proposes a fix on CAUTION or FLAG

## Live view

  loci cockpit         What LOCI catches that your coding agent might miss during
                       planning and coding: the costliest functions, every catch,
                       and the contract state. Open it in a separate terminal

## What the verdict words mean

  A report closes on one word, composed from two columns on every row.

  STATUS — computed against a bound you set:
    PASS      every bound held
    CAUTION   tight against a bound, or a regression a bound still allows
    FAIL      a bound is breached
    —         nothing computed: no bound covers this row

  AGENT ASSESSMENT — what LOCI made of the row:
    Needs attention   worth raising, with the block or callee named
    Looks good        nothing raised
    As reported       nothing here to judge the row on

  The verdict is the worst row, the two composed: Needs attention on an
  unbounded row reads CAUTION, Looks good on one reads PASS, and a row that
  reached neither is counted beside it ("3 of 14 judged"). Most repositories
  set no bounds — there the word you see is LOCI's reading of the row, and the
  caption under the table says so. A bound comes from .loci/contract.yaml
  (/loci:contract drafts one) or from your own request, as in "keep this under
  200 ns". LOCI never supplies one, which is why a large number on its own
  cannot fail.

  The two checkpoints headline that word as the next move:
    preflight   GOOD write the plan · ADJUST PLAN change it first · STOP replace it
    post-edit   OK nothing to do · CAUTION a fix is proposed · FLAG not shippable as is
```

## Step 3: Contextual Next Step

Based on the environment state from Step 0, suggest a single next action:

- **Ready, `artifact` set**: "You have a compiled binary — try asking about timing for a specific function, or run `/loci:memory-report` for a full ROM/RAM breakdown."
- **Ready, `artifact` null**: name a binary instead — "point me at a `.elf`, `.o` or `.axf` and I'll show you what LOCI catches in it." It is null when the recipe records no ELF or the one it records is not on disk. On a cargo project it is null until the project's own build has linked a program (`cargo build --release`; `/loci:init --refresh` then records it), so there "build your project first" is right; on the others it may not be, so do **not** assert it.
- **Not initialized**: "Run `/loci:init` to record how this project builds, or point me at a `.elf`, `.o`, or `.axf` file directly."
- **No project named**: ask which one, or name a binary.
- **Not signed in**: "Run `! loci login` to enable timing and energy analysis."

If several apply, prioritize sign-in first (it's the quicker fix), then the
project's state.

## Stats Footer

If you already ran `loci stats global-summary` in the Fast path Bash, do **not**
run it again. If `data.report` is non-empty, append it as the last line — no
heading. If empty (first-time user), show nothing.

Do NOT record stats for this skill — help is informational only.

