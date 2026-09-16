# LOCI

Coding agents write code. LOCI thinks ahead.

LOCI predicts what AI-generated code will do — time, power, memory, and system behavior — during planning and code writing, before it runs.

Without running code. No instrumentation. No code changes.

## Prerequisites

| Requirement | Version | Required for |
|-------------|---------|-------------|
| [Claude Code](https://claude.ai/code) | latest | everything |
| Python | 3.12+ | the loci CLI (local ELF/build analysis) |
| [uv](https://docs.astral.sh/uv/) | any | installs the loci CLI as a tool — **install yourself**; the plugin checks for it and, if missing, Claude gives you the install command |
| Compiled binaries | `.elf` / `.o` / `.axf` | all skills |
| Network access to the LOCI backend | — | `exec-trace`, `loci-preflight`, `loci-post-edit` |

**Cross-compiler** (one required, depending on your target):

| Target | Compiler |
|--------|----------|
| ARM Cortex-M | `arm-none-eabi-gcc` |
| ARM Cortex-A | `aarch64-linux-gnu-gcc` |
| TriCore | `tricore-elf-gcc` |
| TI ARM | `tiarmclang` or `armcl` |
| x86/x64 | `g++` or `clang++` |

Skills that work without a cross-compiler or MCP: `/loci:stack-depth`, `/loci:memory-report`, `/loci:control-flow`, `/loci:contract`

## Install

```
/plugin marketplace add auroralabs-loci/loci-claude
/plugin install loci@loci
/loci:setup
```

`/loci:setup` installs the `loci` CLI as a `uv` tool and verifies the
environment. The plugin also starts that install in the background at session
start, so it often finishes on its own; running the skill makes it deterministic
and names whatever is missing. It is idempotent — run it again any time analysis
fails with `loci: not found`.

## Quick Start

Sign in once per machine. Every analysis skill is gated on a session, including
the ones that never reach the backend:

```
! loci login          # opens a browser, so it runs in your terminal, not the agent's
loci auth status      # must report signed_in
```

Then run `/loci:init` once per checkout. It records how the project builds — target
ISA, compiler, build system, artifact — into `.loci/build.yaml`, and every
measurement after that resolves its flags from that file instead of guessing.
Both it and LOCI's own build output (`.loci/build/`) are machine-local and
gitignored, so a fresh clone needs its own `/loci:init`.

Then try these in any C/C++/Rust project with compiled binaries.

1. **Timing & energy** — ask: *"What's the execution cost of main()?"*
2. **Memory footprint** — ask: *"How much ROM/RAM does my build use?"*
3. **Stack safety** — ask: *"Is my stack safe for TaskMain?"*
4. **Control-flow safety** — ask: *"What does the call graph for process_data() look like?"*
5. **Bounds** — say: *"TaskMain must not exceed 4 kB of stack"* — LOCI drafts the bound, you apply it. Bounds are what let a report pass or fail; without one it reports the number and argues about it instead.

LOCI also runs automatically:
- **loci-preflight** fires during `/plan` - analyzes callees at the binary level before code is written.
- **loci-post-edit** fires after every edit - diffs the binary and reports what the change cost.

## Skills

Guardian — human-on-the-loop. LOCI predicts, warns, and guides; you review the verdict.

| Skill | Trigger | What it does |
|-------|---------|--------------|
| **loci:init** | User-invoked | Records how this project builds into `.loci/build.yaml` — target ISA, compiler, build system, artifact. Once per checkout; every skill that compiles or measures rests on it. Needs sign-in. |
| **loci-preflight** | Auto in `/plan` mode | Audits the plan at binary level before code is written — timing, energy, and CFG impact. |
| **loci-post-edit** | Auto after edits | Diffs pre/post compiled artifacts — timing, energy and control-flow impact of the edit. |
| **exec-trace** | User-invoked | Function-level timing and energy from real workloads and platform traces, powered by LCLM. |
| **stack-depth** | User-invoked | Worst-case stack depth via call-graph traversal, per-function frame sizes |
| **memory-report** | User-invoked | ROM/RAM section breakdown and top consumers from compiled ELF binaries. No runtime instrumentation. No code modifications. |
| **control-flow** | User-invoked | Annotated control-flow graphs optimized for LLM analysis |
| **trends** | User-invoked | Per-function measurement history and optimization progress on the current branch. |
| **contract** | User-invoked | Authors and inspects `.loci/contract.yaml` — the stack, timing, energy, memory and structural bounds every measurement is judged against. LOCI drafts, you apply. |
| **setup** | User-invoked | Installs the `loci` CLI and verifies the environment; repairs a broken or stale install. No sign-in needed. |
| **help** | User-invoked | Lists every skill and shows this checkout's recorded target, compiler and recipe path. No sign-in needed. |
| **bug-report** | User-invoked | Writes a timestamped forensic diagnostic when a skill fails or never fires. No sign-in needed. |

## Verdicts

A LOCI report closes on one of two word sets, and which one tells you how much it
is worth.

| Words | When | Means |
|---|---|---|
| `PASS` / `CAUTION` / `FAIL` | a bound covered the signal | the measurement was compared against a requirement, and this is the result |
| `flagged` / `cleared` | no bound covered it | LOCI reasoned about the number. `flagged` raises a concern and names the block or callee behind it; `cleared` raises none, and is **not** a pass |

Most reports close on `cleared`, because most repositories have no bounds set. A
bound comes from `.loci/contract.yaml` (`/loci:contract` drafts one) or from your own
request, as in *"keep this under 200 ns"*. LOCI never supplies one of its own,
which is why a large number on its own cannot fail: nothing has said what large
means for your project.

## Hooks

| Hook | Trigger | Action |
|------|---------|--------|
| `SessionStart` | startup, resume | project detection, CLI install, context injection, build-state cleanup |
| `UserPromptSubmit` | every prompt | stamps the turn id every measurement is filed under |
| `PreToolUse` | Edit, Write, Bash | keeps `.loci/contract.yaml` and `.loci/build.yaml` read-only to the agent; `.o` snapshot for delta analysis |
| `PostToolUse` | Edit, Write, Bash | asks whether the edit can change a compiled function, and reminds `loci-post-edit` if so |
| `Stop` | end of turn | flushes impact records, nudges on a pending contract draft, cleans the turn's build state |

## Cockpit

`loci cockpit` is a live terminal view of this machine's LOCI data — no browser, no sign-in.
It reads the same measurement store the skills write to, so what it shows is what the session reported.
Open it in a separate terminal: it takes over the one it runs in, so it will not share a terminal with a Claude Code session.

```
loci cockpit                      # live TUI
loci cockpit --once               # one snapshot as a JSON envelope
loci cockpit --altitude contract  # open on the contract panel
loci cockpit --catches-only       # event feed filtered to non-OK verdicts
```

| View | Shows |
|------|-------|
| **hotspots** | The functions costing the most time, energy and stack on this branch |
| **prevented** | Regressions the Guardian caught before they merged |
| **time-saved** | Analysis time LOCI spent vs. runtime measurement it replaced |
| **contract** | Every bound in `.loci/contract.yaml` and the latest measurement judged against it |

`--project` / `--branch` select what to look at; both default to the most recent.

## Powered by AI Physics

LCLM (the execution model behind AI Physics) is trained on real workloads and platform traces — six years of them — and reads the compiled binary directly. Not a GPT wrapper: no source-only reasoning, no hallucinated timing.

Connects to the LOCI backend for Binary Execution Grounding powered by LCLM — real-time execution data, no instrumentation required.
Plug LOCI into your CI/CD pipeline at any stage — code, build, test, or merge.

## Troubleshooting

### loci CLI not installed

All analysis runs through the `loci` command — a uv tool the plugin installs in
the background at session start. If skills fail with "loci not found" or nothing
happens:

1. Check it's on PATH: `command -v loci` and `loci --version`.
2. If it's missing, the background install may still be running or may have
   failed — run the `/loci:setup` skill in Claude Code to reinstall and verify
   (it's idempotent and repairs whatever's missing), or `loci doctor` once it's
   present.
3. Confirm `uv` is installed (`command -v uv`); the plugin needs it to install
   the CLI.

### Analysis needs sign-in

Every LOCI analysis skill requires a signed-in session. The local ELF skills
(`/loci:stack-depth`, `/loci:memory-report`, `/loci:control-flow`) don't reach the backend, but
the CLI still gates them behind a session. If a skill reports `auth_required`:

1. Run `! loci login` in your terminal, then retry.
2. Confirm `loci auth status` shows `signed_in`.

Skills that work signed-out: `/loci:help`, `/loci:setup`, `/loci:bug-report`, `/loci:contract`  
Skills that need sign-in: `/loci:init`, `/loci:exec-trace`, `/loci:stack-depth`, `/loci:memory-report`, `/loci:control-flow`, `/loci:trends`, `loci-preflight`, `loci-post-edit`

### LOCI says the project is not initialized

Every measurement resolves its flags from `.loci/build.yaml`, which `/loci:init`
writes. It is machine-local, so a fresh clone has none until you run it there.

1. Run `/loci:init`. It reads the project, asks at most one question — the
   target ISA when more than one is possible, otherwise just "does this look
   right?" — and names anything it needs and cannot find.
2. `/loci:help` shows the recorded target, compiler and recipe path once it succeeds.

### LOCI was not called / skills didn't trigger

**Auto-skills didn't fire:**

- `loci-preflight` only runs in `/plan` mode. Make sure you're planning new logic, not just asking a question.
- `loci-post-edit` Validation only runs after edits to C/C++/Rust source files.
- Both auto-skills require compiled binaries (`.elf`, `.o`, `.axf`) to be present. If your project hasn't been built yet, compile it first.

**On-demand skills didn't respond:**

- Type `/loci:help` to confirm LOCI is loaded and see the full skill list.
- Verify this checkout is initialized — `/loci:help` shows the recipe path, and `/loci:init` records one if it is missing.
- Check that a cross-compiler for your target is installed and on your PATH (`/loci:init` reports which one this project needs):
  - ARM Cortex-M: `arm-none-eabi-gcc`
  - ARM Cortex-A: `aarch64-linux-gnu-gcc`
  - TriCore: `tricore-elf-gcc`

**Nothing seems to work:**

Run `/loci:bug-report` to generate a full diagnostic report.

---

## Further Reading

- [LOCI Portal](PORTAL.md) — sessions, binary analysis results, Guardian verdicts, PR review, and account plans
- [agents.txt](agents.txt) — this setup path as one downloadable plain-text file a coding agent can follow
- [setup/setup.sh](setup/setup.sh) — full setup script with platform-specific install logic
- [LICENSE](LICENSE.md) — Aurora Labs Proprietary License
