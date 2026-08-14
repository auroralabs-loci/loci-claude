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

All skills read this one; the Pattern B skills depend on it:

- `project context: <path>` → use as `<project-context>` (the persisted detection
  JSON). The compile-the-source skills (preflight, post-edit) pass it to
  `loci build compile`; the Pattern B skills read `elf_files` and `loci_artifacts`
  out of it when choosing a binary.

The Pattern B skills read two lists out of that same JSON when picking a binary
(see **Step 0 — Pattern B**):

- `elf_files` — linked binaries and objects found in the project's own build.
- `loci_artifacts` — artifacts **LOCI** produced under `.loci-build/`, each as
  `{path, mtime, kind}` with `kind` one of `linked` / `object`, newest first. Kept
  separate from `elf_files` on purpose: these are the ones whose provenance LOCI
  knows exactly, and the two are ranked differently.

### Reporting versions to the user

The context carries two versions, and they are released separately:

    loci version: 0.1.105                     ← the plugin
    loci command: loci (on PATH, v0.1.104)    ← the CLI

Report the plugin version as *the* LOCI version, and never show the two side by
side. Surface the CLI version only in `/bug-report`. If the CLI is genuinely too
old, say so as an action (run `/loci:setup`), not as a number.

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

## The Contract Envelope is input only

`.loci/contract.yaml` holds the bounds this repository requires — stack, timing,
energy, memory, and structural invariants. Read it with `loci contract show` and
judge your findings against the **enabled** entries, quoting an entry's `text`
when you report a verdict so the user hears their own words back.

**You never change it.** Not with Edit/Write, and not with the CLI verbs that
write it (`accept`, `init`, bare `edit`/`disable`/`enable`) — a hook denies all
of them. Only `/loci:contract` drafts changes, and only the **user** applies one.

A breach is **reported, never resolved by moving the bound.** If a measurement
exceeds a budget, say so with the numbers; do not propose loosening the entry, do
not suggest disabling it, and do not mention that either is possible. The entry
is the requirement — your job is to report against it, not to negotiate it.

Fields you will read: `text` (the intent, verbatim), `kind`
(`budget`/`regression`/`invariant`), `function` (absent ⇒ whole binary), `signal`,
`bound` (`max`/`min`/`max_delta`, with `unit`), `severity` (absent ⇒ `warn`), and
`enabled` (`false` ⇒ skip it entirely). An entry with no `signal`/`bound` is a
sentence — judge it yourself and say that you did. An entry whose `signal` you do
not recognise is the same case; never substitute a signal you do know.

## One fact, one row: the entry decides the status

Where an enabled entry covers a fact you would also have classified on your own —
a recursion or indirect-call hazard under `Safety`, a depth you would have judged
against your own 50%/80% thresholds — **the entry decides that row's status**, and
the row quotes its `text`. Your measurement is the evidence in the Note, not a
second verdict on it. Take the icon from `severity`: `fail` ⇒ ❌, `warn` (and
absent) ⇒ ⚠️, `info` ⇒ report it and let it gate nothing.

Your own thresholds apply only where **no** enabled entry covers that signal. That
is the **When there is no contract** rule below, applied per signal rather than per
run: a contract holding one stack budget does not silence your built-in judgement
of everything else.

Never emit both your own row and a contract row for one fact. Two verdicts on one
fact is how a ⚠️ of your own and a ❌ from the user's stated requirement end up in
the same table with nothing saying which of them gates the run.

**A soundness caveat is not a verdict** and is never displaced by an entry: "this
depth is a lower bound because a callee is missing" qualifies what the number
means. Keep those caveats whatever the contract says — including on a row whose
status an entry just decided.

## Structural invariants: which measurement answers which signal

The four structural signals are the ones nothing was mapping to a measurement, so
`loci contract check` filed them as "no measurement supplied" and the report
dropped them as routine. They come from one `loci elf stack` run over a **linked**
binary, and each one's `curr` is a **count** — the signal has no unit:

| Signal | Read from | `curr` is |
|---|---|---|
| `unbounded_recursion` | a recursion warning whose cycle has no visible exit condition — `--max-recursion-depth` bounded it by fiat, not by the code | cycles that could not be bounded |
| `recursion_cycles` | `has_recursion`, and the recursion `warnings` | distinct cycles, bounded ones included |
| `unresolved_indirect_calls` | `has_indirect_calls`, and the indirect-call `warnings` | call sites with no statically resolved target |
| `unknown_callees` | `has_unknown_callees`, and the unknown-callee `warnings` | distinct symbols missing from the binary |

Three rules make the mapping usable:

- **Report the zero.** A clean run measures `0` and must say so against the entry.
  A bound nothing measured is filed as unjudged, and unjudged is invisible — which
  is exactly how these four went two-thirds of the starter contract unnoticed.
- **They are whole-binary, always.** The contract rejects a `function` on a
  structural signal (`scope_unexpected`), so there is no per-function structural
  bound to judge. A hazard you found in one function is evidence *for the
  whole-binary entry*; name the function in the Note, not in the scope.
- **A `.o` cannot answer them.** In a relocatable object the call edges are
  unapplied relocations, so `has_unknown_callees` reads `false` for a binary whose
  callees were simply never linked (Pattern B, B1). From a `.o`, the invariant is
  **unmeasured** — say that. Never report `0` for it.

## When there is no contract

`data.exists: false`, or zero enabled entries. This is a **first-class state**,
not a warning and not a degraded mode.

Report your measurement **exactly as you would otherwise** — same numbers, same
table, same verdicts against the skill's own built-in thresholds. Then, after the
report, add at most **one** line:

> No bounds are set for this repository — `/contract` sets them up.

Rules on that line: after the report, never before it and never instead of it;
**once per session**, not once per skill run; and never at session start. If you
have already said it this session, say nothing. If the user declines, drop it for
the session and do not raise it again.

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

Use these defaults **only when the user has no existing build**, or when every
existing binary failed the freshness gate and cannot be rebuilt by its own build
system. Prefer an existing binary (`.elf`, `.out`, `.o`, `.axf`) **that passes
Step 0 — Pattern B, B3** — availability alone is not a reason to measure something,
which is exactly the reading that produced an analysis of a binary older than its
own source. The `<loci_target>` values are the same vocabulary used on every
`loci elf` command.

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

## Rust / Cargo projects

Applies when the session context shows `Build: cargo` (the project has a
`Cargo.toml`). The front door is unchanged — the same `loci build compile` /
`loci elf` calls — but four Rust-specific rules replace their C/C++
counterparts:

1. **The artifact is one `.o` per crate, named after the crate target** —
   `.loci-build/<loci_target>/<crate_target>.o` (e.g. `gitoxide.o`,
   `sensor_hub.o`), NEVER `<basename>.o` (every crate has a `main.rs` /
   `lib.rs` / `mod.rs`). Do not construct artifact paths from the source
   filename. Take every path from the compile envelope: `data.output`,
   `data.meta_file`, and — when a pre-edit snapshot exists — `data.output_prev`
   and `data.meta_prev`. Those four fields are everything `build diff` and
   `elf diff` need.
2. **Omit `--meta-prev`.** The Rust path auto-inherits the recorded cargo
   config (features, package, target) from `<output>.meta.json.prev` when it
   exists; the envelope's `flag_source_v2.kind` reads `"inherited"` on that
   path. There is no flag cascade to re-run and no flag drift to worry about.
3. **Function names are Rust paths.** Query `--functions` with the simple
   name (`run`) or any `::`-suffix (`main::run`, `plumbing::main::run`) —
   both match. Symbol tables, timing-CSV labels, and CFG text carry demangled
   names (`crate::module::fn`); the raw mangled form rides in each symbol
   row's `mangled` field.
4. **No cross-toolchain is required.** Objects are produced by
   `cargo rustc --emit=obj` without linking, so a Windows host can compile
   for aarch64-Linux with nothing but the rustup std
   (`rustup target add <triple>`). If the compile envelope fails with
   `error.code == "rust_target_missing"`, the message contains the exact
   `rustup target add …` command — show it to the user as
   `! rustup target add <triple>`, then stop until they have run it. The
   compiler-not-found recovery (alternate driver names, `--compiler-path`)
   does NOT apply to Rust; never point `--compiler-path` at a C compiler for
   a `.rs` source.

Rust cross-target map (the `<triple>` per LOCI target):

| LOCI target | rustc target triple           |
|-------------|-------------------------------|
| aarch64     | `aarch64-unknown-linux-gnu`   |
| armv7e-m    | `thumbv7em-none-eabihf`       |
| armv6-m     | `thumbv6m-none-eabi`          |
| tc399       | — (rustc has no TriCore backend; Rust analysis unavailable) |

Caveats to surface rather than fight:

- **Tiny functions may have no symbol of their own.** rustc ships small
  `pub`/`#[inline]` functions as MIR for cross-crate inlining — they are
  codegen'd into their callers, not into the defining crate's object. If the
  edited function is absent from `elf diff` / `elf asm` output, say exactly
  that ("`<fn>` was inlined into its callers — measuring the callers
  instead") and analyze the in-crate callers; do not report "no change".
- **Feature flags**: builds use the crate's default features. When a project
  needs specific features (e.g. gix's `--no-default-features --features
  max-pure`), put them in `.loci-build/flags.json`:
  `{"rust": {"no_default_features": true, "features": ["max-pure"]}}`.
- The first compile of a big workspace builds the whole dependency graph
  once (it stays cached under `.loci-build/cargo/`); subsequent compiles are
  incremental. If it exceeds the default 900 s budget, raise
  `LOCI_CARGO_TIMEOUT` and re-run.
- For readable Rust names asmslicer-side too, `cargo install rustfilt` is a
  nice-to-have (loci demangles at its own layer regardless).

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

If the user provides their own binary, asm-analyze auto-detects the architecture
from the ELF. Do **not** re-run detection scripts — use the values already in the
session context.

Selecting the binary has four parts, in this order, and **none of them is
optional**: decide what kind of artifact the question needs (B1), rank the
candidates (B2), prove the candidate is not older than its sources (B3), and name
the artifact you measured in the report (B4).

### B1 — Match the artifact to the question

| The question | Needs |
|---|---|
| Worst-case stack depth, ROM/RAM totals, timing across a call, anything that crosses a call edge | **A linked binary only** (`.elf` / `.out` / `.axf`) |
| One function's own frame, its own blocks, its own CFG | A linked binary, **or** a relocatable `.o` — labelled as such |

**A relocatable `.o` cannot answer a whole-program question, and it fails
silently.** In a `.o`, a `bl` is an unapplied relocation: the encoded target is a
branch to itself (`f7ff fffe` for `R_ARM_THM_CALL`) and the real callee lives only
in the relocation table. `objdump` *renders* the resolved name because it applies
relocations; the `loci elf` pipeline disassembles the bytes and does not, so the
call edge is simply absent. Measured on a fresh object whose `kernel_main` calls a
function with a 4096-byte stack buffer:

    on the .o        kernel_main   8 B    path ['kernel_main']                                    PASS
    relinked ELF     kernel_main   4144 B path kernel_main → build_pattern → render_frame  202%  FAIL

Both runs used a **fresh, correct** artifact. `has_unknown_callees` was `false` in
the first — the soundness flags do not catch this, so nothing warns you. Preferring
the fresh `.o` for a whole-program question is therefore *worse* than measuring a
stale ELF: a stale ELF gives a wrong answer about a provably wrong binary, while
the `.o` gives a confidently wrong answer about the right one.

So when the question needs call edges and only a `.o` is fresh, the `.o` is
**evidence that a relink is required**, not the thing to measure. Relink, then
measure the ELF.

### B2 — Collect the candidates, then filter by freshness before ranking

The project-context JSON (`project context:` in the session context) carries
`elf_files` (the project's own build) and `loci_artifacts` (artifacts LOCI itself
produced under `.loci-build/`, each with its `mtime` and `kind`, newest first).
Read both. Note the context is a **session-start snapshot** and is never refreshed,
so anything built during this session is in neither list — you know those paths
because you just created them.

**Freshness is a filter, not a tiebreak.** Gather every candidate, run B3 on them,
discard the stale ones, and only then rank what is left:

1. A binary the user named explicitly — if it survives B3, use it.
2. Any surviving **linked** binary (`.elf` / `.out` / `.axf`), from either list or
   from this session's own rebuild. Among several, take the newest `mtime`.
   **Reject one built for a different architecture.** `loci_artifacts` covers all of
   `.loci-build/`, so a relink a previous session left under
   `.loci-build/<other-target>/` can be the newest linked candidate, pass the
   freshness gate (its sources are unchanged) and then be measured with
   `--arch <loci_target>` for the wrong ISA — with no flag warning. A path under
   `.loci-build/` is scoped by the target directory it sits in; anything outside
   `.loci-build/<loci_target>/` needs the ISA confirmed before you trust it.
3. A surviving **object** (`.loci-build/<loci_target>/*.o`) — **only** for a
   single-function question per B1, and only with the label from B4.
4. **Nothing usable survives** — including the case where the only survivor is an
   object and the question needs call edges (B1). That is not "no build": it is
   *evidence a relink is needed*, and it is the common shape after a `make clean`
   plus a post-edit compile. Relink the surviving object(s) per B3's rebuild step,
   or cross-compile with the **Cross-compilation defaults** above, or ask the user
   which target.

Ranking by *provenance first* is what produced the reported bug, and ranking
`.loci-build` above the user's own build repeats it in a new way: a relink left in
`.loci-build/` by a **previous** session outranks the `.elf` the user's `make` just
produced, and rebuilding with `make` does not touch it — so a provenance-first
ranking will keep re-nominating the same stale artifact forever. Filter first and
that cannot happen.

`elf_files` entries carry no `mtime` (they are bare path strings); `stat` them, or
just rely on B3's `elf_mtime`, which every candidate gets anyway.

### B3 — Prove it is not older than its sources

Ask before spending a model call:

    loci build fresh --elf <candidate>

Branch on `.data.stale` — never on the message text. (The same block rides on
every `loci elf` verb as `.data.source_provenance`, so if you have already made an
`elf` call you can read it from there instead of paying for a second check.)

- **`true` — this candidate is out. Do not report numbers from it.**
  `.data.sources_newer` names what changed and by how much. Either move to the next
  surviving candidate from B2, or rebuild:
  1. the project's own build system when it is a cheap one-liner (`make`,
     `cmake --build <dir>`, `ninja`) — it produces a *linked* binary, which is what
     a whole-program question needs;
  2. otherwise `loci build compile --source <file> --loci-target <loci_target>
     --context <project-context>` for the translation unit, and then link it, per
     B1's rule about `.o` files. No `loci` verb links, and the defaults table gives
     compile flags only — so if you cannot reconstruct the link (no linker script,
     other objects unknown), do **not** fall back to measuring the object for a
     whole-program question. Say what is missing and stop, per the paragraph below;
     a single-function answer from the object is acceptable *only* with B4's
     scope label.

  Then **measure the artifact you just produced** — confirm it with one
  `loci build fresh` on *that path* and go straight to B4. Do **not** re-enter B2's
  candidate list: a rebuild refreshes one artifact, and re-ranking would hand you
  back a different, still-stale one. A candidate B3 rejected stays rejected for the
  rest of this run.
- **`false`** — proceed.
- **`null` — freshness is unknown, not confirmed.** Common and not an error: no
  `-g`, or the binary was built on another machine. Proceed, and say so in the
  report using `.data.reason` verbatim. For a binary with no debug info at all,
  `loci build fresh --elf <path> --source-root <project-root>` adds a coarser
  mtime comparison.

If every candidate is stale and no rebuild is possible (no build system, no
compiler, the user declines), **say that and stop**. Do not report numbers for code
that is not on disk. Reporting "here are the numbers, but they may be stale" is the
failure this gate exists to prevent — an engineer acts on the numbers and ignores
the caveat.

**If the installed CLI is too old to have the gate** — `loci build fresh` comes back
`ok:false` with a usage error (argparse `invalid choice`, exit 2) and no `loci elf`
envelope carries `source_provenance` — do not treat that as "fresh". Say once:

    This loci CLI predates the artifact-freshness check; run `/loci:setup` to
    update. Freshness of <artifact> is unverified.

then continue with the freshness state recorded as unverified, and carry that
through to B4's line. The pinned CLI (`lib/setup-steps.sh`) always has the verb;
this path exists for a stale `loci` on PATH ahead of the pin.

### B4 — Name the artifact you measured

Every Pattern B report **must** carry one provenance line, immediately before its
conclusion table or footer:

    Artifact: kernel.elf (linked 2026-07-27 13:27:42, sources current)

Variants, matching what B3 returned:

    Artifact: .loci-build/armv6-m/kernel.elf (relinked 2026-07-29 12:03:11 by this run, sources current)
    Artifact: build/app.elf (linked 2026-07-28 09:14:02, freshness unverified — no DWARF debug info)
    Artifact: .loci-build/armv6-m/blink.o (object, 2026-07-29 12:31:41, sources current)
              Single-function scope: callees are not resolved in a relocatable object.

This line is the durable part of this section. Selection rules live in prose and a
future rewording can weaken them; a line that names the file and its build time
means a reader can always see *what* was measured and decide for themselves
whether it was the right thing.
