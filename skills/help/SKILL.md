---
description: >
  Quick-reference guide to LOCI — shows available skills, environment status,
  and troubleshooting for initialization and sign-in issues.
when_to_use: >
  When user asks for help with LOCI, what LOCI can do, how to use LOCI,
  available commands, or types /loci:help. Also when user seems confused about
  LOCI setup or capabilities. Not for recording how the project builds —
  that is /loci:init, which this skill points at.
---

# LOCI Help

Show the user their environment status, available skills, and a contextual
next step. Adapt the output based on what is actually working vs missing.

## Fast path

1. SessionStart carries whichever of the recipe's facts are known, and a `LOCI:`
   line **only when something is off** — a healthy session has none. Do **not**
   re-derive any of them, and do not `find`, Glob or `ls` the project for ELF
   files.
2. **One Bash** covering auth, quota, and stats:
   `loci auth status; loci usage; loci stats global-summary`
   (skip `usage` / `global-summary` only if `auth status` is `auth_required`).
   Each prints one JSON envelope; let it print and read the fields off it — no
   `jq`, and no `2>&1`, which puts stderr text in front of the JSON.
3. Render the status block, the **whole** Step 2 skill list (never a subset —
   it is what the user is being shown LOCI can do) and one next step. Stop.

## Step 0: Diagnose Environment

Read the LOCI session context from the `system-reminder` block emitted at
session start:

```
Compiler: <compiler>, Build: <build>      ← recipe facts; absent when none were read
LOCI target: <loci_target>                ← recipe facts; absent when none were read
recipe: <path to the project's .loci/build.yaml>
artifact: <the linked binary that recipe records>
loci command: loci (on PATH)
plugin dir: <path>
project context: <path>
LOCI: <one sentence about this project's state>   ← only when something is off
```

**Every one of those lines is printed only when it is a fact, and they are
printed independently.** A wiped state directory shows `recipe:` and no target;
a recipe that cannot be found from here shows the target and no `recipe:`. So
**no line is the test for "is this project initialized"** — the shared contract
says so in as many words, and the only reliable answer is what a `loci` call
returns. Report what is there; never fill in a line that is not.

The **`LOCI:` line is the one to relay, and a healthy session has none.** It is
emitted only when something needs saying, it names the state in words, and it
carries its own remedy — which is more specific than anything this skill can
infer. Match it case-sensitively: a lowercase `loci: …` line is the CLI
installer's status, not the project's.

To check sign-in state, run `loci auth status` (exit 0 + `data.status ==
"signed_in"` when signed in; exit 3 / `error.code == "auth_required"` when not).

Classify, in this order — and report every one that applies, since a session can
be several at once:

| State | How to tell | |
|-------|-------------|---|
| **Not signed in** | `loci auth status` returns `auth_required` (exit 3) | Check first |
| **Inactive** | a `LOCI: inactive (…)` line | Relay its sentence and **its** remedy verbatim. It covers `no_project`, `multi_project`, `detection: failed`, `init: needs_user` and `init: unsupported`, and their remedies differ — a directory holding several projects is told to start a session inside one of them, *not* to initialize this one |
| **Not initialized** | a `LOCI:` line saying the project is not initialized, or a `loci` call answering `not_initialized` | The block below |
| **Something else is off** | **any other** `LOCI:` line | Relay it, and do not substitute the not-initialized block. One says a recipe was recorded but cannot be found from here; the others say one exists whose state is degraded, unrecognised, or whose last init failed. Each carries its own recovery and none of them is the plain first-init flow |
| **Ready** | **no `LOCI:` line at all**, and a `LOCI target:` line | The block below |
| **No LOCI context at all** | none of the lines above is present | The block below. This is ordinary, not broken: session-init runs on `startup` only, so `--continue`, `--resume`, `/clear` and a compact all resume without it, and a host missing `uv` gets a short block that says so and nothing else |

**Read the rows in that order and stop at the first match, and this table is
total** — the last row is the fallback, so there is no state you may classify by
resemblance. Four of the six key on a `LOCI:` line, which is why the order
matters: a table read any other way sends the commonest states to the wrong
block, and that is how a wiped state directory came to be reported as a healthy
one.

**`init: unsupported` is recorded and permanent** — nothing retries it and only
`/loci:init` changes it, so never present it as something an install would fix.

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
  Recipe:    <path from the recipe: line>
  Auth:      signed in
  Quota:     <data.daily.used> / <data.daily.limit> daily tokens (<data.plan>)
```

**One row per line the context actually carries — omit the rest.** Each comes
from the line of the same name and from nowhere else: do not re-derive a target,
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

Only when the `LOCI:` line says so. A degraded or failed state is a different
sentence with a different remedy — relay that one instead of this block.

```
## Environment — not initialized

No build recipe records how this project builds, so LOCI does not yet know its
target ISA, its compiler or its flags — and every measurement rests on those.

→ Run `/loci:init`. It reads the project, asks at most one question (which
  target ISA), and records `.loci/build.yaml` — machine-local, gitignored, one
  per checkout. Anything it needs and cannot find, it names.

Or point LOCI at an existing binary directly:
  "What's the execution cost of main() in path/to/firmware.elf?"
```

Do not list toolchains to install — `/loci:init` reports what is missing on this
machine, and anything named here is a guess about a project you have not read.

### When there is no LOCI context

```
## Environment — not established in this session

The LOCI session block is not in this conversation, so nothing here says what
this project is. That is expected after `--continue`, `--resume`, `/clear` or a
compact: the block is injected once, at startup.

→ Start a fresh session in the project directory to see it, or name a binary and
  I'll measure it without one: "stack depth of main in build/app.elf".
```

**Unless a block IS present and says a host tool is missing** — then that is the
finding, and the block above is the wrong answer. Relay the sentence instead.

If the context instead says `uv` is missing, relay that: nothing else in LOCI
runs until it is there. LOCI checks for it and **never** installs it — it is a
host tool — so give the user the install line and say `/loci:setup` verifies the rest
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

  /loci:trends         Per-function measurement history on this branch
                       "How are my functions doing?"

  /loci:bug-report     Forensic diagnostic when LOCI itself misbehaves
                       "LOCI didn't run" · works signed out

  /loci:setup          Install or repair the loci CLI and check the environment

## Auto-running (no command needed)

  loci-preflight       Runs in /plan — checks call graph, timing, energy, execution fit
                       Escalates to /loci:stack-depth or /loci:memory-report when needed

  loci-post-edit       Runs after edits — diffs binary, reports timing/energy % delta
                       Proposes a fix on FAIL or a flag

## Live view

  loci cockpit         A terminal view of this machine's LOCI data: the costliest
                       functions, what LOCI caught, and the contract state
                       Open it in a separate terminal — it takes over the one it
                       runs in, so it cannot share this session's terminal

## What the verdict words mean

  A report closes on one word, composed from two columns on every row.

  STATUS — computed against a bound you set:
    PASS      every bound held
    CAUTION   tight against a bound, or the figure is only a lower bound
    FAIL      a bound is breached
    —         nothing computed: no bound covers this row

  AGENT ASSESSMENT — what LOCI made of the row:
    Needs attention   worth raising, with the block or callee named
    Looks good        nothing raised
    As reported       nothing here to judge the row on

  The verdict is the worst row, the two composed: Needs attention on an
  unbounded row reads CAUTION, Looks good on one reads PASS, and a row that
  reached neither is counted beside it ("3 of 14 judged"). Most repositories
  set no bounds, so most rows show a —. A bound comes from .loci/contract.yaml
  (/loci:contract drafts one) or from your own request, as in "keep this under
  200 ns". LOCI never supplies one, which is why a large number on its own
  cannot fail.
```

## Step 3: Contextual Next Step

Based on the environment state from Step 0, suggest a single next action:

- **Ready + an `artifact:` line**: "You have a compiled binary — try asking about timing for a specific function, or run `/loci:memory-report` for a full ROM/RAM breakdown."
- **Ready + no `artifact:` line**: name a binary instead — "point me at a `.elf`, `.o` or `.axf` and I'll measure it." The line is absent for three different reasons (the recipe records no ELF — which is every cargo project — the one it records is not on disk, or the context asserted none), so do **not** say "build your project first": on a Rust project that is permanently wrong, and on the others it may be.
- **Not initialized**: "Run `/loci:init` to record how this project builds, or point me at a `.elf`, `.o`, or `.axf` file directly."
- **Inactive, or any other `LOCI:` line**: repeat that line's own remedy. It is written for the state this project is actually in; a guess made here is not.
- **Not signed in**: "Run `! loci login` to enable timing and energy analysis."

If several apply, prioritize sign-in first (it's the quicker fix), then whatever
the `LOCI:` line asks for.

## Stats Footer

If you already ran `loci stats global-summary` in the Fast path Bash, do **not**
run it again. If `data.report` is non-empty, append it as the last line — no
heading. If empty (first-time user), show nothing.

Do NOT record stats for this skill — help is informational only.

