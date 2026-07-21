# LOCI

AI Writes Code. LOCI Gates It.

LOCI's quality gate agent models regressions, power, latency, and bugs from the binary. From plan to merge. 

Without running code. No instrumentation. No code changes.

## Prerequisites

| Requirement | Version | Required for |
|-------------|---------|-------------|
| [Claude Code](https://claude.ai/code) | latest | everything |
| Python | 3.12+ | the loci CLI (local ELF/build analysis) |
| [uv](https://docs.astral.sh/uv/) | any | installs the loci CLI as a tool — **install yourself**; the plugin checks for it and, if missing, Claude gives you the install command |
| jq | any | session hooks — **install yourself**; the plugin checks for it and, if missing, Claude gives you the install command |
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

Skills that work without a cross-compiler or MCP: `stack-depth`, `memory-report`, `control-flow`

## Install

```
/plugin marketplace add auroralabs-loci/loci-claude
/plugin install loci@loci
```

## Quick Start

After installing, try these in any C/C++/Rust project with compiled binaries.

AI Writes Code. LOCI Gates It.
1. **Timing & energy** — ask: *"What's the execution cost of main()?"*
2. **Memory budget** — ask: *"How much ROM/RAM does my build use?"*
3. **Stack safety** — ask: *"Is my stack safe for TaskMain?"*
4. **Control-flow safety** — ask: *"What does the call graph for process_data() look like?"*

LOCI also runs automatically:
- **loci-preflight** fires during `/plan` - analyzes callees at the binary level before code is written.
- **loci-post-edit** fires after every edit - diffs the binary and returns a regression verdict.

## Skills

Gate — Human Decides. Define what matters. LOCI enforces it.

| Skill | Trigger | What it does |
|-------|---------|--------------|
| **loci-preflight** | Auto in `/plan` mode | Audits the plan at binary level before code is written — timing, energy, and CFG impact. |
| **loci-post-edit** | Auto after edits | Diffs pre/post compiled artifacts — regression verdict on  timing, energy, and control-flow. |
| **exec-trace** | User-invoked | Function-level timing and energy from real-time hardware traces, powered by LCLM. |
| **stack-depth** | User-invoked | Worst-case stack depth via call-graph traversal, per-function frame sizes |
| **memory-report** | User-invoked | ROM/RAM section breakdown and top consumers from compiled ELF binaries. No runtime instrumentation. No code modifications. |
| **control-flow** | User-invoked | Annotated control-flow graphs optimized for LLM analysis |
| **trends** | User-invoked | Per-function measurement history and optimization progress on the current branch. |

## Hooks

| Hook | Trigger | Action |
|------|---------|--------|
| `SessionStart` | startup | project detection, venv setup, context injection |
| `PreToolUse` | Edit, Write, MultiEdit | call-graph safety check, `.o` snapshot for delta analysis |

## Powered by LCLM

LCLM (Large Code Language Model — trained on billions of ASM blocks and real hardware traces from IoT, networking, and safety-critical systems) — the only execution-aware model for code. Not a GPT wrapper.

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
(`stack-depth`, `memory-report`, `control-flow`) don't reach the backend, but
the CLI still gates them behind a session. If a skill reports `auth_required`:

1. Run `! loci login` in your terminal, then retry.
2. Confirm `loci auth status` shows `signed_in`.

Skills that work signed-out: `/help`, `/loci:setup`, `/bug-report`  
Skills that need sign-in: `exec-trace`, `stack-depth`, `memory-report`, `control-flow`, `trends`, `loci-preflight`, `loci-post-edit`

### LOCI was not called / skills didn't trigger

**Auto-skills didn't fire:**

- `loci-preflight` only runs in `/plan` mode. Make sure you're planning new logic, not just asking a question.
- `loci-post-edit` Validation only runs after edits to C/C++/Rust source files.
- Both auto-skills require compiled binaries (`.elf`, `.o`, `.axf`) to be present. If your project hasn't been built yet, compile it first.

**On-demand skills didn't respond:**

- Type `/help` to confirm LOCI is loaded and see the full skill list.
- Verify the build environment was detected at session start — restart Claude Code from inside your project directory if needed.
- Check that a cross-compiler is installed and on your PATH:
  - ARM Cortex-M: `arm-none-eabi-gcc`
  - ARM Cortex-A: `aarch64-linux-gnu-gcc`
  - TriCore: `tricore-elf-gcc`

**Nothing seems to work:**

Run `/bug-report` to generate a full diagnostic report.

---

## Further Reading

- [LOCI Portal](PORTAL.md) — sessions, binary analysis results, quality gate verdicts, PR review, and account plans
- [setup/setup.sh](setup/setup.sh) — full setup script with platform-specific install logic
- [LICENSE](LICENSE) — Aurora Labs Proprietary License
