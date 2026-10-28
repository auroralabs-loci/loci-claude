# LOCI

Coding agents write code. LOCI thinks ahead.

LOCI predicts what AI-generated code will do — time, power, memory, and system behavior — during planning and code writing, before it runs.

Without running code. No instrumentation. No code changes.

## Prerequisites

| Requirement | Version | Required for |
|-------------|---------|-------------|
| A coding agent: [Claude Code](https://claude.ai/code) or [GitHub Copilot CLI](https://docs.github.com/en/copilot/concepts/agents/copilot-cli/about-copilot-cli) | Claude Code: latest. Copilot CLI: 1.0.90 or newer | everything |
| Python | 3.12+ | the loci CLI (local ELF/build analysis) |
| [uv](https://docs.astral.sh/uv/) | any | installs the loci CLI as a tool — **install yourself**; the plugin checks for it and, if missing, the agent gives you the install command |
| Windows only: PowerShell 7 and [Git for Windows](https://gitforwindows.org/), with its `bash` on PATH | — | the hooks under GitHub Copilot CLI (see [Under GitHub Copilot CLI](#under-github-copilot-cli)); Claude Code finds Git Bash on its own |
| Compiled binaries | `.elf` / `.o` / `.axf` | all skills |
| Network access to the LOCI backend | — | `exec-trace`, `loci-preflight`, `loci-post-edit` |

**Cross-compiler** (one required, depending on your target):

| Target | Compiler |
|--------|----------|
| ARM Cortex-M | `arm-none-eabi-gcc` |
| ARM Cortex-A | `aarch64-linux-gnu-gcc` |
| ARM Neoverse V2 (ARMv9-A) | `aarch64-linux-gnu-gcc` or `clang`, with `-mcpu=neoverse-v2` or `-march=armv9-a` |
| TriCore | `tricore-elf-gcc` |
| TI ARM | `tiarmclang` or `armcl` |
| x86/x64 | `g++` or `clang++` |

Skills that work without a cross-compiler or the backend: `/loci:stack-depth`, `/loci:memory-report`, `/loci:control-flow`, `/loci:contract`

## Install

In **Claude Code**:

```
/plugin marketplace add auroralabs-loci/loci-claude
/plugin install loci@loci
/loci:setup
```

With **GitHub Copilot CLI**, from a terminal (it reads the same marketplace),
then `/loci:setup` inside a session:

```
copilot plugin marketplace add auroralabs-loci/loci-claude
copilot plugin install loci@loci
```

`/loci:setup` installs the `loci` CLI as a `uv` tool and verifies the
environment. The plugin also starts that install in the background at session
start, so it often finishes on its own; running the skill makes it deterministic
and names whatever is missing. It is idempotent — run it again any time analysis
fails with `loci: not found`.

## Under GitHub Copilot CLI

The same plugin serves both hosts: Copilot reads the plugin's `hooks/hooks.json`
and `skills/` as they are, so everything below about skills, hooks and verdicts
holds there too. What differs is around the edges:

- **Prerequisites on Windows.** Copilot CLI needs PowerShell 7 (its own
  requirement), and it hands every hook command to PowerShell, which resolves
  `bash` through PATH. Add `C:\Program Files\Git\bin` to PATH ahead of
  `C:\Windows\System32` (a default Git for Windows install adds only `Git\cmd`,
  and `System32\bash.exe` is the WSL launcher): `where.exe bash` must list Git's
  first. `Git\bin` holds only `bash`, `sh` and `git`, and its `bash` puts Git's
  tools on its own PATH; `Git\usr\bin` would also shadow Windows' `find`, `sort`
  and `timeout` for everything else. Claude Code locates Git Bash itself, so
  this is a Copilot-only step.
- **Sign-in.** `! loci login` works the same way: Copilot's `!` prefix runs a
  command in your shell, which is where the browser flow has to run.
- **What runs automatically.** The six hooks in the table below fire under Copilot
  as they do under Claude Code: `loci-preflight` in `/plan` mode (or a `--plan`
  run), `loci-post-edit` after an edit to C/C++/Rust/Go/Python source, the contract guard
  on every edit and command. Copilot's subagents inherit the parent turn's id
  through the `SubagentStart` hook.
- **Version.** `copilot plugin list` shows the installed version, and
  `copilot plugin update loci@loci` pulls a newer one from the marketplace. The
  marketplace serves what the public repository `auroralabs-loci/loci-claude`
  carries.
- **Removing it.** `copilot plugin uninstall loci@loci`, then
  `copilot plugin marketplace remove loci`. On Windows the uninstall has answered
  `Access is denied` (Copilot CLI 1.0.90 and 1.0.91, with other Copilot sessions
  open; a fresh profile uninstalls cleanly). If it refuses, quit the other Copilot
  sessions and retry, or `copilot plugin disable loci@loci` until you can;
  `copilot plugin marketplace remove loci --force` clears a row the uninstall
  left behind.
- **Direct installs.** `copilot plugin install auroralabs-loci/loci-claude` also
  works, but Copilot prints a deprecation warning for it ("only plugin@marketplace
  installs will be supported in a future release"), so use the two commands above.

Copilot CLI updates itself; the versions this plugin is verified against are
1.0.90 and 1.0.91 on Windows 11, which is where the floor in the prerequisites
table comes from.

## Quick Start

Sign in once per machine. Every analysis skill is gated on a session, including
the ones that never reach the backend:

```
! loci login          # at the agent's prompt: `!` runs it in your shell, which can open a browser
loci auth status      # must report signed_in
```

Then run `/loci:init` once per clone. It records how the project builds — target
ISA, compiler, build system, artifact — into `.loci/build.yaml`, and every
measurement after that resolves its flags from that file instead of guessing.
Both it and LOCI's own build output (`.loci/build/`) are machine-local and
gitignored, so a fresh clone needs its own `/loci:init`. Another checkout of a
repository you set up on this machine — a git worktree, the kind Copilot runs each
session in — reuses the recipe you confirmed, at session start. It does not when
that recipe is tied to the folder it was made in: it names that folder, builds in
a container, reaches outside it with `../`, builds outside it (a shadow build
directory every checkout would share), or uses a compile database LOCI
synthesized there. Then that checkout needs its own `/loci:init`.

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
| **loci:init** | User-invoked | Records how this project builds into `.loci/build.yaml` — target ISA, compiler, build system, artifact. Once per clone, which its worktrees reuse; every skill that compiles or measures rests on it. Needs sign-in. |
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

**Under GitHub Copilot CLI.** Copilot shows the model a skill's `description` only, so every skill's frontmatter `description` carries its own trigger clause; Claude Code also reads the richer `when_to_use` key. Skill prose names tools as Claude Code does — Edit/Write, Bash, Read, and *the question tool* for `AskUserQuestion` — and the session context's `host:` line maps them to Copilot's `edit`/`create`, `bash` or `powershell`, `view` and `ask_user`; neither host's `-p` mode has a question tool, so a headless `/loci:init` reports instead of asking. Plugin skills are addressed as `/loci:<skill>` — Copilot's built-in `/init` and `/help` are its own commands, not ours. In programmatic mode (`copilot -p`) a bare `/init` or `/setup` resolves to the plugin skill. Copilot does not substitute `$ARGUMENTS`; a skill reads the words after its command from the prompt itself, which is how `/loci:setup doctor` works on both hosts.

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
| `SessionStart` | startup, resume | CLI install, context injection, build-state cleanup, the confirmed recipe for a new checkout of a repository already set up |
| `UserPromptSubmit` | every prompt | stamps the turn id every measurement is filed under |
| `PreToolUse` | Edit, Write, Bash | keeps `.loci/contract.yaml` and `.loci/build.yaml` read-only to the agent; `.o` snapshot for delta analysis |
| `PostToolUse` | Edit, Write, Bash | asks whether the edit can change a compiled function, and reminds `loci-post-edit` if so |
| `SubagentStart` | a subagent spawned | under GitHub Copilot CLI, lets the subagent's edits inherit the parent turn's id |
| `Stop` | end of turn | flushes impact records, nudges on a pending contract draft, cleans the turn's build state |

## Cockpit

`loci cockpit` shows what LOCI catches that your coding agent might miss during planning and coding — live, in your terminal, no browser, no sign-in.
It reads the same measurement store the skills write to, so what it shows is what the session reported.
Open it in a separate terminal: it takes over the one it runs in, so it will not share a terminal with a coding agent session.

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
   failed — run the `/loci:setup` skill in your coding agent to reinstall and verify
   (it's idempotent and repairs whatever's missing), or `loci doctor` once it's
   present.
3. Confirm `uv` is installed (`command -v uv`); the plugin needs it to install
   the CLI.

### Analysis needs sign-in

Every LOCI analysis skill requires a signed-in session. The local ELF skills
(`/loci:stack-depth`, `/loci:memory-report`, `/loci:control-flow`) don't reach the backend, but
the CLI still gates them behind a session. If a skill reports `auth_required`:

1. Type `! loci login` at the agent's prompt (or `loci login` in a terminal), then retry.
2. Confirm `loci auth status` shows `signed_in`.

Skills that work signed-out: `/loci:help`, `/loci:setup`, `/loci:bug-report`, `/loci:contract`  
Skills that need sign-in: `/loci:init`, `/loci:exec-trace`, `/loci:stack-depth`, `/loci:memory-report`, `/loci:control-flow`, `/loci:trends`, `loci-preflight`, `loci-post-edit`

### LOCI says the project is not initialized

Every measurement resolves its flags from `.loci/build.yaml`, which `/loci:init`
writes. It is machine-local, so a fresh clone has none until you run it there. A
worktree of a repository you already set up reuses its recipe when the session
starts, unless that recipe is tied to its own folder (see Quick Start). A recipe
confirmed before plugin v0.3.23 is offered to other checkouts once `/loci:init`
has run again where it was made.

1. Run `/loci:init`. It reads the project, asks at most one question — the
   target ISA when more than one is possible, otherwise just "does this look
   right?" — and names anything it needs and cannot find.
2. `/loci:help` shows the recorded target, compiler and recipe path once it succeeds.

### LOCI was not called / skills didn't trigger

**Auto-skills didn't fire:**

- `loci-preflight` only runs in `/plan` mode. Make sure you're planning new logic, not just asking a question.
- `loci-post-edit` only runs after edits to C/C++/Rust/Go/Python source files, or a header they include, in a project a LOCI recipe governs.
- Both auto-skills require compiled binaries (`.elf`, `.o`, `.axf`) to be present. If your project hasn't been built yet, compile it first.

**On-demand skills didn't respond:**

- Type `/loci:help` to confirm LOCI is loaded and see the full skill list.
- Verify this checkout is initialized — `/loci:help` shows the recipe path, and `/loci:init` records one if it is missing.
- Check that a cross-compiler for your target is installed and on your PATH (`/loci:init` reports which one this project needs):
  - ARM Cortex-M: `arm-none-eabi-gcc`
  - ARM Cortex-A: `aarch64-linux-gnu-gcc`
  - ARM Neoverse V2 (ARMv9-A): `aarch64-linux-gnu-gcc` or `clang`, with `-mcpu=neoverse-v2` or `-march=armv9-a`
  - TriCore: `tricore-elf-gcc`

**Nothing seems to work:**

Run `/loci:bug-report` to generate a full diagnostic report.

---

## Further Reading

- [LOCI Portal](PORTAL.md) — sessions, binary analysis results, Guardian verdicts, PR review, and account plans
- [agents.txt](agents.txt) — this setup path as one downloadable plain-text file a coding agent can follow
- [setup/setup.sh](setup/setup.sh) — full setup script with platform-specific install logic
- [LICENSE](LICENSE.md) — Aurora Labs Proprietary License
