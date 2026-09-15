# Changing a recipe that already exists

Reference for `/loci:init` Step 5. Read this when the project is already initialized —
a target switch, a knob, a new source file, a migration — or when a LOCI refusal named
one of these as its recovery.

Every command here takes `--project-root "<root>"`, the root the skill established in
Step 1. Left out, the CLI resolves against the shell's own directory, which in a
session opened below the project root is a different project.

## The four variants

| variant | what it does | the rule |
|---|---|---|
| `loci init --refresh` | re-derives with the recorded answers as defaults; how a target is switched (`--target=<isa>`), and the recovery `recipe_stale`, `recipe_tampered`, `recipe_invalid` and `compiler_missing` name | `confirmed_by_user` survives only if nothing the user was shown changed; when it drops, `.data.notes` names the fields — ask again, then re-run with `--confirmed`. `--target=auto` re-detects rather than defaulting to the recorded answer. For `recipe_stale`, regenerate the compile database **before** re-running (see `compdb.md`) |
| `loci init set <key>=<value>` | records one knob: `rust.features`, `rust.no_default_features`, `rust.bin`, `rust.profile`, `go.tags`, `go.gcflags`, `go.ldflags`, `go.package`, `go.tinygo_target`, `go.cgo_enabled`, `fidelity.lto`, `fidelity.unity_build`, `build.compdb.select.prefer_output`, `artifacts.elf`, `artifacts.map`, `staleness.watch`, `env.setup` | **confirm the value with the user before invoking**: this replaces hand-editing build config, so the consent is your question, not the flag; `set` does not mark the recipe confirmed. `target` is not settable — `--refresh --target=<isa>` switches it. **A `go.*` knob needs `loci init --refresh` after it**: a Go recipe freezes its knobs into the recorded build command, so `set` alone records a knob that reaches no build |
| `loci init add-file <src>` | derives one compile-database entry for a new source from its nearest neighbour | **synthesized** databases only; on a `generated` one it refuses and names the regeneration command in `.error.regen` — which is **null** when the recipe records none, and then the message says only "regenerate it the way this project generates it", so ask rather than invent one |
| `loci init --from-existing` | forces seeding from the build records LOCI already wrote here | plain init consults them anyway; the flag makes it explicit and *requires* them. A seeded recipe records the compiler installed **now** — possibly a later toolchain than the records the seed came from name — so say which compiler it will use |

## `init_unsupported`: permanent, but say what "permanent" means

The outcome is never retried automatically and no recipe is written, so the skill
stops. But the message itself usually adds that **an unbuilt image looks exactly the
same** — a firmware repo whose cross target has not been configured or built in this
checkout reports "the architectures it builds for (host) are not ones LOCI predicts"
just as a genuine host-only project does. Pass that caveat on: "build the target image
and run `/loci:init` again" is often the whole fix, and it is the half the user acts on.

What "permanent" means precisely: LOCI will not re-arm init for this project on its
own. It does **not** mean the project can never be initialized. So do not hunt for
another target and do not retry in this session — and do not tell the user the project
is permanently unsupported when the message says an unbuilt image is indistinguishable
from one.

## `init_failed` shapes that are not just relayed, in precedence order

Step 3 relays a transient `init_failed` and stops. Some need more. **Take the first
row that matches, in this order** — the messages overlap heavily, and every discriminator
that reads the prose alone has matched the wrong row.

1. **`--accept-tier=<tier>` is offered** — a weaker demonstration is available, and
   taking it is the user's call, not yours: ask (record at the tier demonstrated / fix
   the build first) and never add the flag yourself. **Read what the message says failed
   before offering it.** A database whose entries name sources *outside this project* —
   a copied folder, a restored CI cache — also offers `--accept-tier=unvalidated`, and
   accepting that records a recipe pointing at another checkout's build; say so and
   recommend fixing it instead. Once accepted, **carry `--accept-tier` on every later
   init call in this run**, `--confirmed` included: init re-derives each time, and
   dropping it brings back the refusal you just resolved.
2. **The message says `--accept-tier` cannot *help* with it** (not "cannot lower") — the
   no-compile-database case, whose fix is regeneration (`compdb.md`), not a target
   re-detect and not a tier. It now means **no database _and_ no linked binary** — a
   project linked once initializes off its artifact instead — so the message names
   both ways out: generate a database, or link the project.
3. **`.error.recorded_target` equals `.error.target`** → the tree refuted the answer the
   recipe **already recorded**, and init was re-deriving for that same answer. A checkout
   whose board changed is the usual cause:
   `loci init --project-root "<root>" --refresh --target=auto` re-detects the ISA, and
   `loci init probe` shows what the evidence now says.
4. **Otherwise** — `.error.recorded_target` is null (a first initialization) or differs
   from `.error.target` (you or the user just switched) — **the target came from this
   run and the compile database is for a different image.** The fix is Step 2, not
   stopping: get the database for the image whose ISA is recorded (`compdb.md`), hand it
   over, re-run. This is what a *correctly*-answered target question falls into on a
   bootloader+app repo whose only database is the bootloader's, and `--target=auto` here
   just re-asks the question the user answered.

**Compare the two fields; do not test `recorded_target` for null alone.** It is set from
the *previous recipe's* target at every raise site, so on any re-initialization it is
non-null — including a deliberate `--refresh --target=<other>`, where it holds the old
ISA and `--target=auto` would loop straight back to the question the user just answered.
The sentence "not something `--accept-tier` can lower" appears in both rows 3 and 4 and
discriminates nothing. Some `init_failed` raises (a busy project, `--from-existing` with
nothing to seed, a read-only state dir) carry neither field: none of these rows matches,
and Step 3's relay-and-stop is right for them.

## Seeding from LOCI's own records

`probe`'s `.data.existing_loci_state` carries `sidecars`, a `flags_json` or a
`trace_kind` when LOCI has measured this project before. Plain `loci init` consults
those records on its own, so nothing is required of you — the project converts with no
question. The key is present even on a virgin project, so test its *contents*, never it
for null. `--from-existing` in the table above is the explicit, requiring form.

## A free-form target answer that is not a LOCI target

The target question offers a free-form option on purpose, and `--target` takes no
`argparse` choices for that reason — so a board or part number (`STM32F407`,
`nRF52840`) is a normal answer. It comes back as a caller error with **no**
`error.code`, and the message names the supported set.

Core and family aliases *do* map (`cortex-m4`, `cortex-m33`, `arm64`, `tc3xx`), so pass
the answer through as given first. An unmapped one means the question was not answered
— not that it was answered badly — so re-ask that same question once, with the
supported ISAs shown. That is still one decision, not a second one.

## `arch_mismatch` and `outside_target`: read which one it is

**`outside_target` has one cause and one fix.** The recipe's `select.prefer_output`
filter excludes every entry that could build this file — host unit-test entries beside
the firmware's is the usual shape:

```bash
loci init --project-root "<root>" set build.compdb.select.prefer_output=<pattern>
```

An empty value drops the filter. `.error.detail` lists what the filter was compared
against, which is what a pattern is written from. A re-derivation carries this knob
forward unchanged, so `--refresh` returns `ok` and the very next measurement refuses
identically — which reads as LOCI ignoring the recovery it just recommended.

**`arch_mismatch` has four causes and only one of them is that knob.** Read
`.error.message`: it names its own fix, and these are genuinely different problems.

| what the message says | the fix |
|---|---|
| no compile-database entry for this file builds for the target (a Debug or host entry beside the firmware's) | `set build.compdb.select.prefer_output=<pattern>`, as above — `.error.detail` lists the rejected entries |
| this compile asked for one target but the recipe was initialized for another | measure the recipe's target, or `--refresh --target=<isa>` to switch the checkout. This is what a **mid-session target switch** produces: the recipe and the hooks move at once, the measurement skills still send the session-start target, and the compile refuses |
| a `flags.json` `mode:"replace"` pin disagrees with the recipe | the pin outranks the recipe by design, and it is the **user's** file — a hook denies your writing it, so say what disagrees and ask them to fix or remove it. Re-initializing will not touch it |
| a cargo project's `rust.triple` disagrees with the compile target | `--refresh` (with `--target=<isa>` if the board changed). There is no compile database here, so `prefer_output` has nothing to filter |

Do **not** edit `.loci/build.yaml` by hand for any of them — a hook denies it, and
that is only the second line of defence. The recipe's integrity
record turns a hand edit into a `recipe_tampered` refusal for every file in the
project, not just the one that failed.

## Refusals with no `error.code`

`set` and `add-file` are narrower than `loci init`: they carry a code only where the
state is one init also reports (`not_initialized`, `recipe_tampered`, `init_failed`).
Everything else is a **caller error, exit 2, with no `code`** — an unknown key, a
`rust.*` key on a C project, a `rust.bin` name no package declares, a source file that
names nothing, `add-file` on a `generated` database.

Read `error.message`: it names the reason, and the details carry what you need to
recover — the settable keys, the regeneration command, the paths that were looked at,
or the `bins` a package actually declares. Fix the call. Never re-send it unchanged.
