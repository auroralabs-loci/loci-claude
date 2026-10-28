# Getting a compile database

Reference for `/loci:init` Step 2. Read this when the project is C/C++ **and**
`loci init probe` reported no compile database that is current and covers the target
you are about to record.

A compile database (`compile_commands.json`) records how each translation unit is
actually compiled. LOCI wraps it rather than replacing it, so it comes from the
project's own build system wherever a generator exists.

**Quote every path you substitute.** Probe returns absolute paths, and projects live
under directories with spaces and non-ASCII characters. An unquoted
`ninja -C <abs path> … > <abs path>/…` word-splits, fails, and leaves a junk file in
the user's tree.

## Look before you generate

Probe's `.data.compdbs` only lists databases found in a bounded set of build-directory
candidates, so one in a deep or unusual layout is reported as absent while sitting
right there. Before running anything:

- `.data.binaries[]` names object files by full path, and a database usually sits at
  the build root a few levels above them. If one is there, hand it straight over — no
  build, no consent needed, and being `generated` it cannot record `unvalidated`.
- An initialized project records its database path in the recipe; `loci init` names it
  in the refusal when it has gone.
- Built in a container, or baked into an image? No database lands here: `loci init`
  asks `compdb_regen` → [`compdb-container.md`](compdb-container.md).

This is reading probe's own output, not a search. Do not walk the tree looking for
build directories.

## Consent, before anything runs

**Every command on this page changes something outside LOCI** — it configures or
reconfigures the user's build tree, writes into their build directory, or runs their
build. Say what you are about to run and why, and get a yes, before running any of it.

Open in the user's terms (`voice.md`): LOCI needs the flags their code is really
compiled with; this runs their own build and does not touch their source. Then the
specifics of the route you are on, honestly:

- On a tree that has **never been configured**, a configure step creates a build
  directory that did not exist — with the default CMake generator, hundreds of
  kilobytes of project files, not "one more file".
- `-DCMAKE_EXPORT_COMPILE_COMMANDS=ON` is **sticky**: CMake caches it, so it stays on
  for every future build of that tree.
- Synthesizing needs a **full build** to observe — minutes, on a firmware project.

If they decline, say what LOCI cannot do without it (timing, energy and structural
analysis all need the flags a unit is really compiled with) and stop. Do not fall back
to guessing flags.

## Check the tool exists first

Step 0 probes only `loci` and `uv`. Every generator below is a **project** tool
that may not be installed: `meson`, `pio`, `west`, `cbuild`, `bear` and `compiledb`
are absent on some machines, and none of them is LOCI's to install.
Confirm the one you are about to name is there before asking for consent to run it.
If it is missing, say so and either name the install (theirs to run) or synthesize.

## Generators, by build system

| build system | how |
|---|---|
| CMake | `cmake -S "<root>" -B "<build>" -DCMAKE_EXPORT_COMPILE_COMMANDS=ON` — read the three CMake rules below first, they are the ones that bite |
| Ninja, no CMake | `ninja -C "<build>" -t compdb -x` into a `.new` file, then move — see the redirect rule below |
| Meson | written at setup, so it is already there **if setup has run**; a never-configured tree needs `meson setup "<build>" --cross-file "<file>"` and that is a first configure like CMake's |
| PlatformIO | `pio run -t compiledb` |
| Zephyr / west | `west build -- -DCMAKE_EXPORT_COMPILE_COMMANDS=ON` (or `west config build.cmake-args -- …` to make it stick) |
| Keil csolution (CMSIS-Toolbox) | `cbuild "<name>.csolution.yml"` — CMake + Ninja underneath, database in its build dir |
| plain Make, POSIX | `bear -- make` (or `compiledb make`); if neither tool is installed, synthesize |
| plain Make on Windows · raw Keil uVision · IAR EW · TI `ccs+make` | no generator exists → synthesize, or where the build cannot be observed either, the artifact-only recipe (last section) |
| anything probe calls `direct`, or a monorepo whose build lives one level down | no root build system to drive: ask the user which directory builds the image and how, then treat it as that build system |

### CMake, rule 1: has this tree been configured at all?

```bash
grep -m1 CMAKE_GENERATOR: "<build>/CMakeCache.txt"
```

Three outcomes, and they are three different jobs:

- **No cache file** → this would be a **first configure**, not a re-configure. There is
  no cached generator to inherit, so plain `cmake -S … -B "<build>" -D…` picks *this
  machine's default* — which on Windows is Visual Studio, i.e. MSVC, i.e. the host
  compiler, silently overriding the cross toolchain the project declares. It exits 0
  and produces no `compile_commands.json`. **Do not run it.** Instead use the
  project's own configure line (Step 1 read the README, build files and CI config),
  with the export flag added and nothing else. If it documents no configure line,
  **ask the user for it** — a cross
  project usually needs a `-DCMAKE_TOOLCHAIN_FILE=…` you must not invent.
- **`Makefile` or `Ninja` generator** → `cmake -S "<root>" -B "<build>"
  -DCMAKE_EXPORT_COMPILE_COMMANDS=ON` re-uses the cached generator, toolchain file and
  every other cached option. The safe form, and it needs no knowledge of how they
  configured it — **but pass `-S` explicitly.** `-B` alone takes the source directory
  from the *current directory*, not the cache, so from anywhere but the project root it
  fails — from a subdirectory with its own `CMakeLists.txt` with the source-mismatch
  message, which is rule 2's trigger.
- **`Visual Studio` or `Xcode` generator** → the flag is accepted and ignored; these
  generators never write a compile database. Say so, and offer a **separate** Ninja
  build directory from the project's own configure line plus `-G Ninja`, or
  synthesizing.

### CMake, rule 2: never delete their build tree

Two failures end with CMake telling you to delete something — a `-G` that disagrees
with the cache, and a build directory whose recorded source path no longer matches
(a renamed or copied project). **Never act on that.** Report exactly what CMake said
and let the user decide — the stale cache may hold options nothing else records.
Offer a separate build directory instead.

### CMake, rule 3: do not guess `-G`, and do not guess the toolchain file

Both come from the project or the user; guessing either is how a working tree becomes
a failed configure and the host compiler gets recorded for a firmware project.

### Ninja: never redirect straight onto the destination

`>` truncates the destination *before* ninja runs, so a wrong `<build>` guess leaves a
**0-byte** `compile_commands.json` where the good one was, and `loci init` then
refuses it. Write beside it and move only on success:

```bash
ninja -C "<build>" -t compdb -x > "<build>/compile_commands.json.new"
mv "<build>/compile_commands.json.new" "<build>/compile_commands.json"
```

If ninja fails, delete the `.new` file rather than leaving it in their tree.

### `loci init probe --with-make`, for a make-only project

The one evidence route that is not read-only: GNU make expands `$(shell …)` at parse
time whatever `-n` says, so this **runs the project's build system** (30 s per donor
target) — only with the user's explicit go-ahead, and only on a tree with no build
directory (a warm tree answers `no makefile in build_dir`).

## Multi-image repos generate per image

A bootloader and an application are two build trees, and the one you hand over must be
the image whose target the recipe records. The wrong one is how init answers
`init_failed` with an architecture mismatch.

Then hand it over, with the project root Step 1 established:

```bash
loci init --project-root "<root>" --compdb="<path>" --compdb-kind=generated
```

An unresolvable path is a usage error, not a silent fallback: check the file is there.

## Regenerating a database that has gone stale

`probe`'s `.data.recipe_untrusted` starting `recipe_stale:` means the watched build
files changed since the recipe was derived, so the database beside them records flags
the project no longer builds with. **Run the regeneration first** — the refusal message
names it, and the recipe records it as `build.compdb.regen` — then re-run init.
Initializing over a stale database records the stale flags at full tier and clears the
staleness signal.

**Read the recorded command before running it**, because what it is depends on the
build system: a CMake tree records a *build* command (`cmake --build build`), which
regenerates the database as a side effect; a
ninja-only build directory records `ninja -C build -t compdb -x > build/compile_commands.json`,
the truncating form the Ninja section forbids: run the `.new`-then-`mv` version
instead. Paths are relative to the project root, so quote
them and run from there; a directory the command cannot find is the regeneration
failing, not the database being stale.

## Synthesizing, where no generator exists

For raw Keil uVision, IAR Embedded Workbench, and plain Make on Windows: the honest,
visible, one-time version of what clangd does silently.

1. Build the project once and capture the log (with the user's go-ahead — this runs
   their build). **An already-built tree compiles nothing**: `make` answers "Nothing to
   be done" and you observe zero compile lines. Force it — `make -B` (or
   `make clean && make`, which destroys their build outputs, so ask first); `bear --
   make` has the same hazard.

   **A dry run is not an observation.** `make -n` prints what the makefile expands to
   on this machine, not what a build ran: a generator it runs through `$(shell …)` at
   parse time (TI SysConfig) with a placeholder tool path fails silently, and every
   `@file`, `-D` and `-I` it would add is missing from every line. Read make's stderr,
   and check that the response files (`*.opt`) and generated headers in the build
   directory are named by the captured lines; if not, set the variable on the make
   command line — a tool path is not a flag — and capture again.
2. Derive one entry per translation unit from the compile lines you **observed** in
   that log. Never invent a flag, never copy a flag set from another project, and never
   widen an include path to make something resolve.
3. Write them to `.loci/build/compile_commands.json` — inside the project. **NEVER** a
   temp directory, `/tmp`, or any path outside the working directory: every
   out-of-project write costs the user a permission prompt and halts automation.
4. Hand it over as synthesized, so the recipe records the lower assurance:

   ```bash
   loci init --project-root "<root>" \
     --compdb=.loci/build/compile_commands.json --compdb-kind=synthesized
   ```

   **Carry both flags through every re-invocation of init in this run** — the target
   pick, the confirmation, everything. Init re-derives each time, so a re-invocation
   without them records a hand-derived database as `generated`: a false statement in
   every report, and it permanently breaks `loci init add-file`, whose refusal names a
   regeneration command a synthesized database does not have.

   **`.data.validation.failures` on a synthesized database means the capture is
   incomplete** — the note under it says what the entries lack. Do not put that recipe
   to the user for confirmation: report the failures and the note, fix the capture, and
   ask only once the units compile or the user says to keep it.

A synthesized database is one of the two shapes allowed to record
`validated: unvalidated` (the artifact-only recipe below is the other). A new source
file later is `loci init add-file <src>`, never a hand-edit of the JSON.

## When the build cannot be observed: the artifact-only recipe

**The last resort, reached on a finding and never by default.** You are here only once
synthesizing's step 1 is *established* impossible — no `bear`, a `--dry-run` that
cannot parse, a build you may not run. A built-looking tree is evidence the build
runs, not that finding, so **ask first**: name the build you would run once and
observe, and what it buys (timing, energy, preflight and post-edit, which refuse
`compdb_absent` without a database). A no is an answer; never having asked is not.

**A linked binary is enough to be initialized — once the user says so.** With no
database and the project linked once, `loci init` records nothing on sight: it answers
`init_needs_user` with `.error.question` `compdb`, naming the binary in
`.error.artifact_only_elf`. Their no is
`loci init --project-root "<root>" --accept-tier=unvalidated`, and only then it
writes the **artifact-only recipe** — target, compiler and `artifacts.elf`, no
database, `validated: unvalidated`. Carry the flag through `--confirmed` like any
accepted tier. `compdb-container.md`'s `--no-exec-regen-compdb` records the same
recipe. Headless: print both `loci init` lines, then stop.

| | under an artifact-only recipe |
| --- | --- |
| `stack-depth`, `memory-report`, `control-flow` | **work** — they read the linked binary, needing no per-file command line |
| `exec-trace`, `loci-preflight`, `loci-post-edit` | **refuse** `compdb_absent` — timing and energy compile a source first |

Report both halves: the second is what the user meets next.
