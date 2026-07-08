# LOCI runtime contract (shared)

Canonical instructions shared by the LOCI analysis skills. A skill's `SKILL.md`
points here and names the sections it needs; read those sections, then return to
the skill body for its specifics.

This file is **not** a skill — it has no frontmatter and no `SKILL.md`, so it is
never auto-invoked or advertised as a slash command. It is a reference document
the skills read on demand.

The detection of compiler / flags / build system / architecture happens **once**
at session start (`hooks/session-init.sh` → `lib/detect-project.sh`), and the
results are persisted and injected into the session. Skills **consume** those
results — they never re-detect. Deeper build-flag discovery, when a skill needs
it, is done on demand via `loci build compile` (which runs the flag cascade).

---

## Session context placeholders

All analysis runs through the **`loci`** command — a single executable on PATH,
installed by the session bootstrap. Always invoke it as a bare `loci …`; there is
no script path or venv Python to substitute.

Read these values from the LOCI session context (the `system-reminder` block
emitted at session start) and substitute them wherever the placeholders appear:

- `LOCI target: <arch>` → use as `<loci_target>` (one of `aarch64`, `armv7e-m`, `armv6-m`, `tc399`)
- `plugin dir: <path>` → use as `<plugin-dir>` (to locate shared docs like this contract)

The compile-the-source skills (preflight, post-edit) additionally use:

- `project context: <path>` → use as `<project-context>` (the persisted detection JSON)

---

## Prerequisites: `jq` and `uv` (checked, never installed)

`jq` and `uv` are host tools the plugin **detects but does not install** — they
are prerequisites for the scripts and `loci` commands the skills run. `jq` parses
every loci envelope (below); `uv` installs the loci CLI.

**Check them up front, before running a loci command — don't wait for a failure.**
Probe with `command -v jq` / `command -v uv`. If either is absent, **you**
determine the install command for the user's OS and package manager, give it to
them as `! <command>`, and stop the current loci path until they have run it. Do
**not** install jq or uv yourself. Pick the command by platform:

- **jq** — `sudo apt-get install -y jq` (Debian/Ubuntu), `sudo dnf install -y jq`
  (Fedora/RHEL), `sudo pacman -S jq` (Arch), `brew install jq` (macOS),
  `winget install jqlang.jq` (Windows).
- **uv** — NOT in Debian/Ubuntu apt. Use `curl -LsSf https://astral.sh/uv/install.sh | sh`
  (Linux/macOS) or `pipx install uv`; `sudo pacman -S uv` (Arch),
  `brew install uv` (macOS), `winget install astral-sh.uv` (Windows).

---

## Tool boundary: `loci elf` only

All assembly, CFG, symbol, section, and ELF inspection goes through
`loci elf …`. Do **not** use `objdump`, `readelf`, `addr2line`, `nm`, or
`size` as substitutes — `loci elf` produces the LOCI-ready output (annotated
CFG, per-block timing CSV, symbol map, frame and section data) that binutils
cannot. If a `loci` command returns an error envelope (`{"ok": false}`), surface
its `error.message` and stop; do not fall back to objdump or other disassemblers.

Always pass `--arch <loci_target>` on every `loci elf` call, reading the value
verbatim from the SessionStart `LOCI target:` line. Do not guess or retry with
alternative architecture names — the pipeline expects exactly one of `aarch64`,
`armv7e-m`, `armv6-m`, `tc399`.

**Exception:** `loci elf memmap` auto-detects architecture from the ELF and
does **not** accept `--arch` (used only by memory-report). Every other `loci elf`
subcommand requires `--arch <loci_target>`.

---

## Output: the JSON envelope

Every `loci` command prints **one JSON document on stdout**:

- success → `{"ok": true, "data": {…}}`
- failure → `{"ok": false, "error": {"message": "…", "code"?: "…"}}`

Parse it with `jq` and branch on `.ok` — never on substrings of the human text.
Diagnostic/progress logs go to **stderr**; captured stdout is always the envelope.
Two error `code`s are stable and must be handled deterministically:

- `auth_required` (exit 3) — not signed in / token expired. Tell the user to run
  `! loci login`, then stop the current path cleanly (see each skill's auth gate).
- `quota_exceeded` (exit 4) — usage limit reached; surface `error.message`
  verbatim and stop the backend path.

Bulky text (assembly, CFG, diffs) is **written to files** under
`.loci-build/elf/<elf-stem>/`; the envelope's `data` carries the paths (e.g.
`data.control_flow`, `data.timing_csv`, `data.diff_file`). Read those files by path
rather than expecting the text inline. Some verbs are **size-adaptive**: `elf
symbols` returns the table inline under `data.symbols` when it is small and only
spills to `data.symbols_file` when it exceeds `--inline-threshold` — branch on
`data.payload` (`"inline"` vs `"file"`). Either way the answer is in the single
envelope you already have; never re-run a verb to re-read its own output.

---

## Supported architectures (gate)

Map the LOCI target to the loci MCP supported architectures and binary targets:

| LOCI target | CPU       |
|-------------|-----------|
| aarch64     | A53       |
| armv7e-m    | CortexM4  |
| armv6-m     | CortexM0P |
| tc399       | TC399     |

The CPU column identifies the real silicon hardware the LOCI timing and energy
predictions are traced from.

If the architecture is **not** in this table, emit and stop:

    Supported: aarch64, armv7e-m, armv6-m, tc399

(Skills that also gate on a missing compiler — preflight, stack-depth,
memory-report — state that stop inline; it is intentionally not part of this
shared arch gate so skills that never had it, e.g. post-edit, keep their
original behavior.)

---

## Cross-compilation defaults

Use these defaults **only when the user has no existing build**. Prefer an
existing binary (`.elf`, `.out`, `.o`, `.axf`) whenever one is available. The
`<loci_target>` values are the same vocabulary used on every `loci elf` command.

| LOCI target | Compiler                | Flags                                | Build dir               |
|-------------|-------------------------|--------------------------------------|-------------------------|
| aarch64     | `aarch64-linux-gnu-g++` | `-g -O2 -march=armv8-a`              | `.loci-build/aarch64/`  |
| armv7e-m    | `arm-none-eabi-g++`     | `-g -O2 -mcpu=cortex-m4 -mthumb`     | `.loci-build/armv7e-m/` |
| armv6-m     | `arm-none-eabi-g++`     | `-g -O2 -mcpu=cortex-m0plus -mthumb` | `.loci-build/armv6-m/`  |
| tc399       | `tricore-elf-g++`       | `-g -O2 -mcpu=tc3xx`                 | `.loci-build/tc399/`    |

In the steps that follow, replace `<compiler>` and `<flags>` with values from the
resolved LOCI target. Always include `-g` to emit DWARF debug info (required by
asm-analyze).

---

## Step 0 — Pattern A: compile the source

For skills that compile the analyzed source themselves (preflight, post-edit).

Read the persisted detection results from the `<project-context>` path (the
per-session keyed file, listed as `project context:` in the session context). It
is written by session-init.sh at session start and is the single source of truth
for compiler, architecture, and build system.
**Do NOT re-run detection scripts or fall back to ELF/build-system sniffing.**

    {
      "compiler": "...",
      "build_system": "...",
      "architecture": "...",
      "loci_target": "...",
      ...
    }

If the file does not exist, stop and tell the user:

    LOCI session context not found. Please restart Claude Code so the plugin
    setup runs and detects the project environment.

Then apply the **Supported architectures (gate)** above before any analysis.

Compile the affected source(s) with `loci build compile` — do **not** reuse an
existing `.o`/`.elf` from the project's own build. LOCI needs the compiler, flags,
and version it controls so the pre/post rebuild can diff apples-to-apples:

    loci build compile --source <file> --loci-target <loci_target> \
        --context <project-context> [--meta-prev <prev.meta.json>] \
        [--phase preflight|post-edit]

`loci build compile` resolves the compiler and flags itself from the
`<project-context>` (the persisted detection above) and `--project-root` — you do
**not** pass `--compiler`/`--flags`/`--arch`. It writes the object to
`.loci-build/<loci_target>/<stem>.o` plus a sidecar `<output>.meta.json`, and
returns the build metadata and those paths (`data.output`, `data.meta_file`) in
the envelope.

### If it fails with `compiler_not_found`

Run `loci build compile` **without** `--compiler-path` first — its own cascade
(PATH, `<project-context>`, `compile_commands.json`, globs) resolves the compiler
in the common case. Only if the envelope comes back `ok:false` with
`error.code == "compiler_not_found"` do you step in, in this order:

1. **Cheap retry — alternate driver name only.** The cascade already searched
   PATH, so do not re-scan it or guess install paths. The one thing it may have
   missed is a differently-named driver for the same toolchain: for
   `<loci_target>` try the `-gcc`/`-g++` counterpart and common versioned names
   via `command -v` (e.g. `arm-none-eabi-gcc` vs `arm-none-eabi-g++` for
   armv7e-m/armv6-m, `aarch64-linux-gnu-*` for aarch64, `tricore-elf-*` for
   tc399). If `command -v` returns an absolute path, re-run the **same**
   `loci build compile` with `--compiler-path <abs-path>` appended.
2. **Otherwise, ask the user to point us.** Do NOT hunt through vendor install
   directories yourself. Tell the user the target compiler wasn't found and ask
   them to either give the path to it (you'll re-run with `--compiler-path`) or
   install the toolchain. Keep it to what they need — the tool and that you need
   its path. When they give a path, re-run `loci build compile` with
   `--compiler-path <that-path>` once.
3. **Then stop.** If the alternate-name retry and the user-provided path both
   fail, or the user can't provide one, surface `error.message` verbatim and
   stop. Do not fabricate a path or fall back to a host compiler for a cross
   target.

Only branch on `error.code == "compiler_not_found"` (stable, from `_errors.py`).
Exit code 127 accompanies all compiler-not-found cases but is coarser; the two
other 127 shapes (`--compiler-path does not exist`, `compiler not found on PATH`
at invocation time) are terminal — surface and stop, no retry.

---

## Step 0 — Pattern B: analyze an existing binary

For skills that analyze an existing or freshly cross-compiled binary (exec-trace,
control-flow, stack-depth, memory-report).

The LOCI target architecture is already resolved in the session context
(`LOCI target:` line). Use it as `<loci_target>`; do not re-detect it. The
`system-reminder` block also carries:

    Target: <target>, Compiler: <compiler>, Build: <build>
    LOCI target: <loci_target>

Pick the binary to analyze in this order:

1. **User's own compilation** — if the user already compiled targeting a LOCI
   architecture, reuse their binary.
2. **Existing ELF/object files** — if the project already has `.elf`, `.out`,
   `.o`, or `.axf` files, use them directly.
3. **No existing build** — cross-compile with the **Cross-compilation defaults**
   above, or ask the user which target.

If the user provides their own binary, asm-analyze auto-detects the architecture
from the ELF. Do **not** re-run detection scripts — use the values already in the
session context.
