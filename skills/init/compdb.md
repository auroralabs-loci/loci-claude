# Getting a compile database

Reference for `/loci:init` Step 2. Read this when the project is C/C++ **and**
`loci init probe` reported no compile database that is current and covers the target
you are about to record.

A compile database (`compile_commands.json`) is the record of how each translation
unit is actually compiled. LOCI wraps it rather than replacing it, so it has to come
from the project's own build system wherever a generator exists.

**Quote every path you substitute.** Probe returns absolute paths, and this machine's
projects live under directories with spaces and non-ASCII characters. An unquoted
`ninja -C <abs path> … > <abs path>/…` word-splits, fails, and leaves a junk file at
an unrelated location in the user's tree.

## Look before you generate

Probe's `.data.compdbs` only lists databases it found by walking a bounded set of
build-directory candidates, so a database in a deep or unusual layout
(`out/targets/<board>/release/cmake/ninja/build/`) is reported as absent while
sitting right there. Before running anything:

- `.data.binaries[]` names object files by full path, and a database usually sits at
  the build root a few levels above them. If one is there, hand it straight over — no
  build, no consent needed, and being `generated` it cannot record `unvalidated`. (Which
  tier it *does* reach is the tree's business: a release build with no `-g` stops at
  `compile-check`, and the report says so.)
- An initialized project records its database path in the recipe; `loci init` names it
  in the refusal when it has gone.

This is reading probe's own output, not a search. Do not walk the tree looking for
build directories.

## Consent, before anything runs

**Every command on this page changes something outside LOCI** — it configures or
reconfigures the user's build tree, writes into their build directory, or runs their
build. Say what you are about to run and why, and get a yes, before running any of it.

Open in the user's terms (`voice.md`): LOCI needs the flags their code is really
compiled with, this runs their own build, it writes build outputs and LOCI's local
setup, it does not touch their source, and the setup comes back to them to confirm.
Then the specifics of the route you are on, honestly:

- On a tree that has **never been configured**, a configure step creates a build
  directory that did not exist — on this machine's default CMake generator that can be
  hundreds of kilobytes of project files. It is not "one more file".
- `-DCMAKE_EXPORT_COMPILE_COMMANDS=ON` is **sticky**: CMake stores it in the build
  directory's cache, so it stays on for every future build of that tree.
- Synthesizing needs a **full build** to observe, which on a firmware project can take
  minutes.

If they decline, say what LOCI cannot do without it — timing, energy and structural
analysis all need the flags a translation unit is really compiled with — and stop. Do
not fall back to guessing flags.

## Check the tool exists first

Step 0 probes only `loci` and `uv`. Every generator below is a **project** tool
that may not be installed: `meson`, `pio`, `west`, `cbuild`, `bear` and `compiledb`
are all absent on some developer machines and none of them is LOCI's to install.
Confirm the one you are about to name is there before asking for consent to run it —
asking permission for a command that cannot run wastes the user's turn. If it is
missing, say so and either name the install (theirs to run) or synthesize.

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
| anything probe calls `direct`, or a monorepo whose build lives one level down | no root build system to drive: ask the user which directory builds the image and how, then treat that as the build system it is |

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
  project's own configure line: Step 1 already read `CLAUDE.md`, the README and the
  build files, and CI config counts too. Add the export flag to that line and nothing
  else. If the project documents no configure line, **ask the user for it** — a cross
  project usually needs a `-DCMAKE_TOOLCHAIN_FILE=…` you must not invent.
- **`Makefile` or `Ninja` generator** → `cmake -S "<root>" -B "<build>"
  -DCMAKE_EXPORT_COMPILE_COMMANDS=ON` re-uses the cached generator, toolchain file and
  every other cached option. The safe form, and it needs no knowledge of how they
  configured it — **but pass `-S` explicitly.** `-B` alone takes the source directory
  from the *current directory*, not the cache, so from anywhere but the project root it
  fails: either "does not appear to contain CMakeLists.txt", or — from a subdirectory
  that has its own `CMakeLists.txt` — "does not match the source … used to generate
  cache", which is rule 2's trigger. Blaming the user's tree for your cwd is the worst
  outcome on this page.
- **`Visual Studio` or `Xcode` generator** → the flag is accepted and ignored; these
  generators never write a compile database. Say so, and offer either a **separate**
  Ninja build directory built from the project's own configure line plus `-G Ninja`
  (leaving their tree untouched) or synthesizing.

### CMake, rule 2: never delete their build tree

Two failures end with CMake telling you to delete something — a `-G` that disagrees
with the cache, and a build directory whose recorded source path no longer matches
(a renamed or copied project). **Never act on that.** Report exactly what CMake said
and let the user decide: it is their tree, the stale cache may hold options nothing
else records, and a wrong guess here destroys a working configuration. Offer a
separate build directory instead — that costs them nothing and needs no deletion.

### CMake, rule 3: do not guess `-G`, and do not guess the toolchain file

Both come from the project or from the user. Guessing either turns a working tree into
a failed configure, and on a cold tree it is how the host compiler gets recorded for a
firmware project.

### Ninja: never redirect straight onto the destination

`>` truncates the destination *before* ninja runs, so a wrong `<build>` guess or a
build directory mid-regeneration leaves a **0-byte** `compile_commands.json` where the
user's good one was — and `loci init` then refuses because the database cannot be
parsed. Write beside it and move only on success:

```bash
ninja -C "<build>" -t compdb -x > "<build>/compile_commands.json.new"
mv "<build>/compile_commands.json.new" "<build>/compile_commands.json"
```

If ninja fails, delete the `.new` file rather than leaving it in their tree.

### `loci init probe --with-make`, for a make-only project

The one evidence route that is not read-only: GNU make expands `$(shell …)` at parse
time whatever `-n` says, so this **runs the project's build system** and can cost 30 s
per donor target. Only with the user's explicit go-ahead. It also needs a tree with no
build directory: the CLI reads the makefile out of the top-scoring build dir, which
becomes `build/` once built, so a warm tree answers `no makefile in build_dir`.

## Multi-image repos generate per image

A bootloader and an application are two build trees, and the one you hand over must be
the image whose target the recipe records. The wrong one is how init answers
`init_failed` with an architecture mismatch after the target question was answered
correctly.

Then hand it over, with the project root the skill established in Step 1:

```bash
loci init --project-root "<root>" --compdb="<path>" --compdb-kind=generated
```

An unresolvable path is a usage error, not a silent fallback — check the file is there
first.

## Regenerating a database that has gone stale

`probe`'s `.data.recipe_untrusted` starting `recipe_stale:` means the watched build
files changed since the recipe was derived, so the database beside them records flags
the project no longer builds with. **Run the regeneration first** — the refusal message
names it, and the recipe records it as `build.compdb.regen` — then re-run init.
Initializing over a stale database records the stale flags at full validation tier and
clears the staleness signal, which is the one outcome this whole design exists to
prevent.

**Read the recorded command before running it**, because what it is depends on the
build system. A CMake tree records a *build* command (`cmake --build build`), which
regenerates the database as a side effect of building. A ninja-only build directory
records `ninja -C build -t compdb -x > build/compile_commands.json` — the truncating
form the Ninja section below forbids, because a failure leaves a 0-byte database where
the good one was: run the `.new`-then-`mv` version instead, which produces the same
file. Either way the paths are relative to the project root, so quote them and run from
there (or make them absolute); a directory the command cannot find is the regeneration
failing, not the database being stale.

## Synthesizing, where no generator exists

For raw Keil uVision, IAR Embedded Workbench, and plain Make on Windows. The honest,
visible, one-time version of what clangd does silently on every lookup.

1. Build the project once and capture the log (with the user's go-ahead — this runs
   their build). **An already-built tree compiles nothing**: `make` answers "Nothing to
   be done" and you observe zero compile lines. Force it — `make -B` (or
   `make clean && make`, which destroys their build outputs, so ask first).
   `bear -- make` on the POSIX row has the identical hazard: a warm tree records an
   empty database.
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
   pick, the confirmation, everything. Init derives the recipe fresh each time and,
   with no recipe on disk to carry the kind forward, a re-invocation without them
   records a hand-derived database as `generated`. That is a false statement in the
   field every report renders, and it permanently breaks `loci init add-file`: the
   refusal for a `generated` database names a regeneration command, and for a
   synthesized one there is none.

A synthesized database is one of the two shapes allowed to record
`validated: unvalidated` (the artifact-only recipe below is the other). A new source
file later is `loci init add-file <src>`, never a hand-edit of the JSON.

## When the build cannot be observed: the artifact-only recipe

**The last resort, reached on a finding and never by default.** You are here only once
synthesizing's step 1 is *established* impossible — no `bear`, a `--dry-run` that
cannot parse, a build you may not run. A built-looking tree is evidence the build
runs, not that finding, so **ask first**: name the build you would run once and
observe, and what it buys — timing, energy, preflight and post-edit, which without a
database refuse `compdb_absent` for ever. A no is an answer and this recipe follows
it; never having asked is not.

**A linked binary is enough to be initialized.** With no database and the project
linked once, `loci init` records an **artifact-only recipe** — target, compiler and
`artifacts.elf`, no database, `validated: unvalidated`. Plain `loci init` (or
`--auto`) writes it on that shape; nothing extra is passed.

| | under an artifact-only recipe |
| --- | --- |
| `stack-depth`, `memory-report`, `control-flow` | **work** — they read the linked binary, needing no per-file command line |
| `exec-trace`, `loci-preflight`, `loci-post-edit` | **refuse** `compdb_absent` — timing and energy compile a source first |

Report both halves: the second is what the user meets next.
