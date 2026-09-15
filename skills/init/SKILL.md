---
name: init
description: >
  Record how this project builds — one machine-local recipe
  (`.loci/build.yaml`) naming the target ISA, the compiler, the build system and
  the artifact — so every LOCI measurement compiles the way the project itself
  does.
when_to_use: >
  "initialize LOCI", "set up this project for LOCI", "/loci:init", "switch the
  LOCI target", "LOCI says this project is not initialized". Also whenever a LOCI
  analysis failed with `error.code` `recipe_stale`, `recipe_tampered`,
  `recipe_invalid`, `compiler_missing`, `arch_mismatch`, `outside_target`, or
  `not_initialized` **on a project that already has a recipe**: this skill routes
  each to its recovery, which is sometimes a knob rather than a re-init. **Not**
  `not_initialized` with no recipe — adopting a project is the user's decision,
  so a skill hitting that names `/loci:init` and stops.
  Not for installing the CLI (that is `/loci:setup`), and not for bounds (that is
  `/loci:contract`, which authors requirements — this one records build facts).
---

# LOCI Init

`.loci/build.yaml` records how this checkout builds — **one target ISA**, the
compiler, the build system, the compile database, the artifact. Every LOCI
measurement resolves its flags from it instead of guessing, which is what makes them
reproducible. It is **machine-local**: `loci init` gitignores it via a
`.loci/.gitignore` it writes.

**The CLI never prompts. You do.** It refuses with a code at a genuine decision point;
you turn that into a question. **One decision about the recipe: the target ISA** —
everything else follows from it, the answer is recorded, and an initialized project is
not asked it again. Two things are not that decision and do not spend it: **permission**
before running anything that changes their build tree, and their **explicit acceptance
of a weaker validation tier** where one is offered (`recovery.md`). Both are their call
about their own tree, and each fires only in the branch that needs it.

- **Never invent the answer.** `--confirmed` goes on the command line only after a
  real person said yes to what you showed them. Every report renders
  `confirmed_by_user` until it is true, so a fabricated one silences that warning
  instead of earning it.
- **One ISA, chosen once.** A multi-image repo (bootloader + app, TrustZone,
  dual-core) initializes for the image the user cares about; switching is
  `--refresh --target=<isa>`, not a second recipe.

One JSON envelope on stdout per call, refusals included: let it print, branch on
`ok`, never on substrings. Bare `loci …`, never via Python. **NEVER** write to
`/tmp` or anywhere outside the project.

## Step 0 — the CLI has to exist

Probe `loci` and `uv` with `command -v`. Either missing, not signed in, or a
`loci` answering `loci init` with **empty stdout and `invalid choice: 'init'`** (a stale
CLI, not a failed init) → **[`bootstrap.md`](bootstrap.md)** — the host-tool policy, the
self-locking installer and the rule to **wait for it** rather than start a second,
`<plugin-dir>`, and the sign-in line.

Two of those states **run `/loci:setup` first** and re-probe before Step 1: a `loci`
that is **missing**, and one **behind this plugin's `LOCI_CLI_VERSION` pin**
(`<plugin-dir>/lib/setup-steps.sh`). `bootstrap.md` has both routes and the floor
rule that spares a CLI at or ahead of the pin.

Empty stdout with any *other* argparse complaint is your malformed command: fix the
call, do not run the installer, or you loop.

## Step 1 — the project root, then evidence

**Establish the project root first and pass it to every call.** Both `loci init` and
`probe` default to the shell's own directory, so a session opened in `fw/src`
initializes — or misdiagnoses — a subtree. Use the git toplevel
(`git rev-parse --show-toplevel`; it exits non-zero outside a repo, and in a submodule
or monorepo can sit above the tree that actually builds — the root build files are the
check), else the highest directory holding them. Then `--project-root "<root>"` on every
`loci init` below, `probe` and the reference files' commands included.

**If a coded refusal sent you here, start with its recovery, not this flow** —
**[`recovery.md`](recovery.md)** routes all of them, and for `arch_mismatch` /
`outside_target` the fix is usually a knob. Re-deriving a recipe that was not the problem
loses the user's confirmation and leaves the refusal where it was.

Read the project's account of itself: `CLAUDE.md`, `AGENTS.md`, `README*`, CI config and
the root build files (`CMakeLists.txt`, `meson.build`, `Makefile`, `Cargo.toml`,
`platformio.ini`, `west.yml`, `*.csolution.yml`/`*.uvprojx`/`*.ewp`) — a cold CMake
tree's configure line comes from there, not from you. Then `probe`, whose `.data` is
everything `loci init` decides from.

- `.data.initialized == true` → a recipe is here; show `.data.recipe` and do not
  re-initialize a project that only asked you to look. Its fields route you first.
  **`escrow` not `"ok"`** → **always go to Step 3**, whatever else the recipe says.
  Nothing outside the repo vouches for the file, so its `validated` and
  `confirmed_by_user` are claims, not facts: a hand-written or copied-in recipe reads
  exactly like a healthy one, and a forged `confirmed_by_user: true` reaches every
  measurement and silences the warning that sent the user here. Plain `loci init` is the
  repair — it re-vouches only for what this tree still demonstrates, and never
  re-blesses consent. (`.data.recipe.target` absent from `.data.candidates` is a second
  reason to re-derive, not the only one.) Otherwise `confirmed_by_user: false` →
  **Step 4**, else **Step 5**.
- `.data.recipe_untrusted` present → a recipe is here that **cannot be vouched for**;
  the value names which of `recipe_stale`, `recipe_tampered`, `recipe_invalid`,
  `compiler_missing` it is. `recipe_stale` on a C/C++ build system means the build
  files moved: **regenerate the compile database first** (Step 2) — initializing over
  a stale one records the stale flags at full tier and clears the staleness signal for
  good. On cargo there is nothing to regenerate; re-derive. The others re-derive
  cleanly.
- `.data.candidates` empty with `.data.unsupported_candidates` filled → **skip Step 2**
  and go straight to **Step 3**. There is no target to acquire a database *for*, so
  reconfiguring their tree first mutates it to learn nothing — and do not call this
  permanent yourself: only a compile database or a cargo manifest makes "no supported
  ISA" permanent, while a lone committed object makes it *transient*. Step 3 tells them
  apart.

**Then say what you recognised** — one line out of `.data`, before any build-system
talk. **[`voice.md`](voice.md)** has it, and governs every line this run prints: the
words to lead with, the consent opener, the progress checklist, Step 4's order.

**Add no evidence of your own.** `probe` plus the build files is the budget: no
vendor-directory hunting, no compiler-fallback ladder, no trial compiles for flags, no
walking build trees for ELFs. If probe found nothing, the answer is a coded outcome in
Step 3, not a deeper search. (`probe --with-make` **runs the project's build** — see
`compdb.md` first.)

## Step 2 — the compile database (C/C++ build systems only)

**Skip this step when `.data.build_system` is `cargo` or `go`** — none of
these is handed a compile database, so none of `compdb.md` applies, whatever
`.data.languages` says (a tree with both a manifest and C sources is one of these;
`.data.notes` says which build is recorded and which language is excluded). Cargo init
runs a real build: say so first (`voice.md`), and that it may leave an untracked
`Cargo.lock`.

**Go and TinyGo also build for real**, and what you say afterwards differs — the
board, and Go's inlining. Read [go.md](go.md), which replaces `compdb.md` for them.

Otherwise the gate is not "does one exist" but **current, and covering the target you
are about to record**:

- Step 1 reported `recipe_stale` → regenerate, whatever `.data.compdbs` says.
- `.data.compdbs[]` carries `targets` per database. If none builds for the ISA you will
  record — the bootloader's in a bootloader+app repo — you lack the one you need.
- `.data.compdbs` empty → none probe could *find*, which is not none existing; a deep
  build layout is the usual reason, and `compdb.md` opens with how to tell.
- Otherwise you have one → **Step 3**.

The ISA is not settled until Step 3's question is answered, so on a multi-image repo
**come back here after the pick** if the database you have does not cover it.

To get one, read **[`compdb.md`](compdb.md)**: where to look first, the matrix, the
three CMake rules (a never-configured tree is where this goes wrong), and how to
synthesize. **Every route in it changes something outside LOCI**, so get a yes first —
it says what to tell them, and `voice.md` how to open it. LOCI will not run configure
for you, and neither should you.

Hand it over with `--compdb="<path>" --compdb-kind=generated|synthesized`, and **carry
every answer flag through every later init call in this run** — the target pick and the
confirmation included. That means `--compdb`/`--compdb-kind`, and `--accept-tier` once a
tier has been accepted: init re-derives from scratch each time, so a `--confirmed` that
drops a flag meets the refusal you already resolved.

## Step 3 — run init, ask at most one question

Run `loci init --project-root "<root>" [--compdb="<path>" --compdb-kind=<kind>] [--target=<isa>]`.

`.ok == true` → Step 4. Otherwise branch on `.error.code`:

| code | what it is | what you do |
|---|---|---|
| `init_needs_user` | several supported ISAs, and a recipe records exactly one | the one question, below |
| `init_unsupported` | not an ISA LOCI predicts for; never retried automatically | relay `.error.message` **plus `.error.detail`** (where the paths are), then **stop**. [`recovery.md`](recovery.md) has the caveat to pass on |
| `init_failed` + `.error.transient` | a temporary state of the tree — unbuilt checkout, broken build, database gone or for another image | **read [`recovery.md`](recovery.md)'s `init_failed` shapes first, top-down in the order given** — the first is a dead end that repeats every session. Otherwise relay `.error.message` (it names the fix) and stop: no recipe was written, init re-arms next session start, and you must **not loop** |
| `auth_required` | not signed in | `bootstrap.md`'s sign-in line |
| no `code` at all | a caller error, exit 2 | read `error.message`, fix the call, never re-send unchanged |

**The one question** — exactly one `AskUserQuestion`, header `Target`:

- Options come from `.error.candidates[]`, ordered by evidence class with ties
  alphabetical — the order is not a recommendation; do not present it as one.
- There are at most four supported ISAs, so the list always fits `AskUserQuestion`'s
  four options with no folding. Name any the user expects and does not see, from
  `.error.supported`.
- `evidence` is one `"; "`-joined string per candidate. Split it and describe each with
  the fact that **names the image** — the ELF or build directory — not necessarily the
  first: where one database builds both images the leading facts are near identical.
  Paths project-relative; the raw string runs to hundreds of characters.

Then re-invoke with the pick and — answering this question *is* the confirmation —
`--confirmed`, carrying Step 2's flags and the project root. A free-form answer is
expected, so a board or part number is normal; **[`recovery.md`](recovery.md)** has what
comes back and why re-asking once is still one decision.

Single candidate and no refusal? Init has already written the recipe with
`confirmed_by_user: false`. Do not skip the confirmation and do not add a second question
for it — Step 4's confirm *is* your one question.

### Headless runs: never ask

With no user to answer — a pipeline, CI, print mode, a hook — **do not attempt
`AskUserQuestion`.** On `init_needs_user`, print the candidates with their evidence and
the exact line the pipeline's author needs (`loci init --target=<isa>`), then stop; the
outcome is recorded and the next interactive session asks. Do **not** pick a target
yourself to keep the run moving: an unattended guess measures the wrong image for as long
as the recipe lives.

The same holds for **Step 4's confirmation**, which fires on every single-candidate
project: with nobody to ask, **never pass `--confirmed`** — report the recipe as
unconfirmed, hand over the `loci init --confirmed` line, and let the next interactive
session earn it. `AskUserQuestion` does not exist in these runs, so there is no version of
this where you asked. A tier acceptance is the user's too: never take one here.

## Step 4 — show it, get the confirmation

Reached from Step 3, or from Step 1 with an unconfirmed recipe — in that case run
`loci init --project-root "<root>"` first for the envelope this step reads: **`probe`
carries none of these fields.**

**Order what you print** — [`voice.md`](voice.md): one readiness line in the user's
terms first, saying **checked against your real build** only where `.data.validated`
is true; then every note, warning and `!` line below it; then the setup this step
lists. A caveat goes **under** that line, never folded into it.

`.data.report` is the rendered summary; relay it (trimmed) rather than rebuilding one
from `.data.recipe_summary`. What survives: **target**, **compiler**, **build system +
compile-database kind**, the **artifact** where the report has one (a cargo recipe has
none, and `compiler: unknown` is right there), `.data.validated`,
`.data.confirmed_by_user`, and — the part a trim always drops — **every line of
`.data.notes`, every line of `.data.warnings`, and every `!` line in the report**. Those
say a recipe it could not vouch for was replaced, that nobody confirmed this one, or
that the database belongs to another checkout. **Check all three: they are independent
channels**, and the fact that matters is routinely in one of them alone — a database
from another checkout reports `notes: []` with the reason only in an `!` line.
(`.data.warnings` is emitted on the already-initialized envelope, so on a first write it
is absent rather than empty.) Where `.data.confirmed_by_user` and `recipe_summary`'s
copy disagree the top-level field is authoritative — and so it is over the **report**,
which renders the document: after init re-establishes a missing integrity record the
report can read `confirmed by user: True` one line above a `.data.confirmed_by_user` of
`false`. Relay the report, then correct it from the field.

If `.data.confirmed_by_user` is false, ask once with `AskUserQuestion` (header
`Recipe`; options **Looks right** / **Wrong target** / **Something else is wrong**),
the summary in the question so they decide on what they see:

- **Looks right** → `loci init --project-root "<root>" --confirmed`, plus Step 2's
  flags and any accepted `--accept-tier`: it re-derives, so a dropped flag brings back
  the refusal you resolved. The command the CLI's own note names.
- **Wrong target** → take the alternatives from **`loci init probe`**'s
  `.data.candidates[]`. This envelope is not reliable for them: the already-initialized
  path omits the key entirely. Run
  `loci init --project-root "<root>" --refresh --target=<one>` (carrying the same
  answer flags), come back here, and pass **no `--confirmed`**: they said the setup was
  wrong, which is not consent for the next one. No alternative offered →
  `--target=auto` re-detects.
- **Something else is wrong** → fix that first (usually the database, Step 2).

**Either of this step's commands can refuse.** `--confirmed` and `--refresh --target=`
both re-derive, so they meet the same states Step 3 does: take the refusal back to Step
3's table rather than re-asking, and never read a refusal here as the user's answer
being wrong.

The recipe governs the hooks immediately — init writes the same keyed project-context
file session start does — but the measurement skills read the target from the
session-start line, so after a mid-session **switch** the next `/loci:exec-trace` does not
silently use the old target: it **refuses** with `arch_mismatch` until the session
restarts. Say both when you switch one. Close in one short block on what a first init
unlocks: **one invitation**, naming this recipe's artifact and target, with
`/loci:exec-trace`, `/loci:stack-depth` and an edit as the words they can type — not a
command list, and no function you have not seen. Never print the recipe YAML unless
asked, nor the plumbing (integrity record, context file, plugin dir).

On a first init only, one more line: `loci cockpit` — this machine's measurements,
live, in a *separate* terminal. You never run it.

## Step 5 — an existing recipe, and knob-fixable refusals

**[`recovery.md`](recovery.md)** has the four variants (`--refresh`, `set`, `add-file`,
`--from-existing`) with their consent rules, the `init_failed` shapes in precedence
order, and the routing easiest to get wrong: `outside_target` is always
`loci init set build.compdb.select.prefer_output`, but **`arch_mismatch` has four causes
and only one is that knob** — read what the message names before acting.
