---
name: bug-report
description: >
  Forensic diagnostic report for LOCI — collects environment state, runs health
  checks, and writes a timestamped report when analysis fails or doesn't trigger.
when_to_use: >
  When user says "bug report", "LOCI isn't working", "exec-trace didn't run",
  "skill didn't trigger", "MCP not connecting", "results are wrong",
  "results missing", "generate diagnostic", "something is broken",
  "debug LOCI", or any LOCI failure the user wants investigated.
argument-hint: "[description of what failed]"
---

# LOCI Bug Report

Generate a forensic diagnostic report when LOCI analysis fails, a skill does
not invoke, or results are missing or invalid. The report is written to a
timestamped `.md` file that can be shared or loaded into a future Claude Code
session to diagnose and fix the issue.

This skill must work even when LOCI is completely broken. Do NOT run analysis
skills or heavy `loci` verbs (timing / elf) for collection — they may be the
thing that's broken. Use only: Read, Bash, Glob, Grep, plus the lightweight,
fast-failing probes `command -v loci`, `loci auth status`, `loci doctor`,
`loci init probe` (a read-only dump of what init decides from) and
`loci build fresh` (a local mtime/DWARF check — no backend call, no asmslicer).
The last two run **signed out**, which is why check 7 and step 1's recipe snapshot
can rely on them; `loci init` itself needs a session, and this skill never runs
it.

Read these values from the LOCI session context (system-reminder block at
session start) and substitute them wherever the placeholders appear below:
- `plugin dir: <path>` → use as `<plugin-dir>`
- `project context: <path>` → use as `<project-context>`
- `loci version: <semver>` → use as `<plugin-version>`

The analysis front door is the bare `loci` command on PATH — a uv tool that
ships its own analysis stack (asmslicer + deps). There is no plugin-side venv:
`command -v loci` and `loci doctor` are the readiness probes in the checklist
below.

If `plugin dir:` is not in the session context, fall back to the
`CLAUDE_PLUGIN_ROOT` environment variable. If neither is available, stop and
tell the user: "Cannot locate LOCI plugin directory. Ensure the plugin is
installed and restart Claude Code."

## Persistent layout

State files live outside the versioned plugin cache so they survive plugin
upgrades. The analysis stack itself lives in the `loci` CLI (a uv tool), not
in a plugin-side venv.

| Path | Purpose | Fallback |
|------|---------|----------|
| `$LOCI_STATE_DIR` (typically `~/.loci/state`) | project-context, measurements, stats | `<plugin-dir>/state` |
| `~/.loci/impact-token.json` | per-user telemetry token | — |
| `loci` CLI (a uv tool on PATH) | analysis stack — asmslicer + deps | — |

The plugin exports `LOCI_STATE_DIR` at session start; read it with
`${LOCI_STATE_DIR:-$HOME/.loci/state}` so the fallback path is used when this
skill runs outside a hook context.

## Step 0: Capture user description

The skill accepts an optional argument string describing the problem.
Store it as `<user-description>`.

If no argument was provided, ask the user in one sentence:
"What did you expect LOCI to do, and what happened instead?"

## Step 1: Collect environment snapshot

Run these in parallel where possible via Bash and Read:

1. **Claude Code version** — `claude --version 2>/dev/null || echo "unknown"`
2. **Claude model** — read from your own system prompt (e.g. `claude-opus-4-7`,
   `claude-sonnet-4-6`). Record the exact model ID.
3. **Plugin version** — prefer `<plugin-version>` from session context. If
   missing, Read `<plugin-dir>/.claude-plugin/plugin.json` and take its
   `version` key. Fall back to "unknown".
4. **loci CLI version** — `loci --version 2>/dev/null || echo "unknown"`. This
   is the ONE place the CLI's own number is surfaced, and the shared contract
   says so: everywhere else the plugin version is *the* LOCI version. It belongs
   here because half the reports that reach us are a CLI a release behind, and
   the session context deliberately does not carry the number.
5. **OS info** — `uname -a`
6. **OS short name** — `uname -s | tr '[:upper:]' '[:lower:]'` (for filename)
7. **Project context** — Read `<project-context>` (the per-session keyed file
   listed as `project context:` in this session). Record the full JSON. If
   missing, record "MISSING". Read two groups of fields apart, because they have
   different writers and a report that confuses them files the wrong finding:
   `init_status`, `init_recipe`, `validated`, `confirmed_by_user`, `loci_target`,
   `compiler`, `compiler_path`, `build_system` and `artifact` all
   come from **`loci init`, out of the recipe** — the session writes only
   `detection_status` and `subproject_roots` (the gate's answer; the scan that used
   to fill the rest is gone). A `null` in the recipe group means *no recipe stands
   behind this context* — which is true of a project that was never initialized
   **and** of one whose last init FAILED against a recipe still on disk.
   `init_status` is what separates them; record it verbatim — and note it has a
   second writer of its own: the session writes `uninitialized` for an armed
   project `loci init` has never seen, and in the inactive branches the key is
   **absent** rather than null. "Absent", "uninitialized", "failed" and "ok" are
   four different findings.
8. **CLI health** — run `loci doctor` and record `data.report` (covers Python
   3.12, asmslicer, analysis deps, c++filt, the Rust demangler, cross-compilers,
   credential store, and the state dir). If `loci` is unavailable, record "loci not on PATH".
9. **Git info** — `git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown"`
   and `git log --oneline -3 2>/dev/null || echo "no git history"`
10. **Hooks config** — Read `<plugin-dir>/hooks/hooks.json`. If missing,
   record "MISSING".
11. **CLI auth** — run `loci auth status` and record signed-in / signed-out.
    (The plugin no longer registers an MCP server; all backend calls go through
    the `loci` CLI and authenticate on demand via `! loci login`.)
12. **Turn baselines** — the per-turn trees `loci build snapshot --turn` writes,
    which are what a post-edit "Before" is read from, under the one build root the
    CLI reads: `.loci/build/`. (A `.loci-build/` beside it is a pre-move CLI's
    leftover; nothing reads it, and a tree under it is not a baseline.) Substitute
    the `project_root` you read in step 6 for `<project-root>` before running this;
    the fence sets it itself so nothing depends on an exported variable:

    ```bash
    ROOT='<project-root>'
    . '<plugin-dir>/lib/loci_json.sh'
    for t in "$ROOT"/.loci/build/turns/*/; do
      [ -d "$t" ] || continue
      loci_json_load "$(cat "$t/turn.json" 2>/dev/null)"
      printf '%s\t%s\t%s\t%s\t%s\n' \
        "$(date -r "$t" '+%Y-%m-%dT%H:%M:%S' 2>/dev/null \
           || date -r "$(stat -f %m "$t" 2>/dev/null)" '+%Y-%m-%dT%H:%M:%S' 2>/dev/null \
           || echo '?')" \
        "$(loci_json_get turn || echo '?')" \
        "$(find "$t/orig" -type f 2>/dev/null | wc -l)" \
        "$(find "$t/obj" -name '*.o' 2>/dev/null | wc -l)" \
        "$t"
    done | sort | tail -10
    ```

    `lib/loci_json.sh` is the plugin's own forkless JSON reader, the one the hooks
    use. It is sourced here for the same reason they use it: `jq` is a host tool
    the plugin does not ship, and a diagnostic that reports `?` for every turn id
    on a machine without one is a diagnostic that hides the state it was run to
    find.

    One `sort` over every tree, then `tail -10`: the newest ten. The mtime is
    read GNU-first then BSD, like every other mtime read in this plugin —
    `date -r FILE` is a file mtime on GNU and an *epoch* on BSD/macOS, where it
    errors on a path. A row that fell back to `?` would sort on the turn digest,
    which the paragraph below says is uncorrelated with time. Each row is
    `mtime, turn id, captured sources, reconstructed objects, path`. The CLI
    stamps each turn once and never copies a tree, so two rows with the same
    turn id would mean something wrote one by hand. Record all of it, or "none"
    when there are no rows. Sorting is on the
    **timestamp** because the directory name is a one-way digest of the turn id —
    lexical order is uncorrelated with time, and `turn.json` is the only place the
    id itself survives. Also record whether `uncaptured.jsonl` exists in the tree
    with the newest mtime and its last line: that file is where an edit the CLI
    could not capture records why.

    **Reading the two counts.** They answer *"there was no Before"*, but only in
    one direction each:

    - **`orig/` empty** — the pre-edit hook never captured for that turn. That is
      a real finding: it means no `--turn` reached `build snapshot` (an older CLI,
      the hook killed on its 8 s budget, or `loci` briefly absent).
    - **A turn id the report mentions that matches no row** — the compile was
      asked to verify a turn that was never stamped.
    - **`obj/` empty is NORMAL and is not a finding on its own.** That directory
      is written only when a *header* edit is measured through the translation
      units that include it. An ordinary `.c` edit is measured from the `.o.prev`
      beside the object and puts nothing there, and `--baseline` refuses Rust
      outright, so a cargo project always shows `0`. Only treat it as a
      reconstruction failure when the report is about a header edit.

13. **The build recipe and its escrow** — every measurement resolves its flags
    from the recipe, so a report about wrong numbers that does not say which
    recipe governed the run is missing its first fact. `probe` writes nothing and
    runs signed out:

    ```bash
    loci init probe --project-root '<project-root>'
    ```

    Substitute the `project_root` from step 6, as item 11 does. If step 6 recorded
    "MISSING", drop the flag entirely — probe defaults to the current directory,
    which is a better answer than a placeholder.

    Record from `.data`: `initialized`, and when true the `recipe` block's `path`,
    `target`, `validated` (the tier the flags were demonstrated at),
    `confirmed_by_user` and **`escrow`** — the out-of-repo integrity record at
    `<LOCI_STATE_DIR>/recipe-escrow-<cwd_hash>.json`. `ok` means the recipe's bytes
    are the ones `loci init` wrote; `missing` means nothing vouches for them and
    `confirmed_by_user` can still read `true` beside it; `unchecked` means this
    load did not verify it, which is not the same as either. A recipe the CLI
    would **refuse** never reaches that block at all — it comes back under
    `data.recipe_untrusted`, whose string carries the code. Record that string
    verbatim and do **not** read it as tampering: `recipe_tampered` is only one of
    the codes that land there, alongside `recipe_stale`, `recipe_invalid`,
    `compiler_missing` and an `unreadable:` prefix for a recipe that would not
    parse at all — a cross-compiler someone uninstalled produces the same key. For the same reason `initialized` reads `false` for all four, so it is
    not evidence that no recipe exists; the `recipe_untrusted` string is.

    On a CLI predating the recipe (`invalid choice: 'init'`), say so and fall back
    to `init_status` / `init_recipe` / `validated` / `confirmed_by_user` from the
    project context: the same values, as of the last successful init.

14. **Rust toolchain** (only when the project context shows
    `build_system: "cargo"`) — record `cargo --version`, `rustc --version`,
    and `rustup target list --installed 2>/dev/null | head -10` (each falling
    back to "not found"). Rust compile failures usually trace to a missing
    rustup std for the LOCI target.

15. **Go toolchain** (only when the project context shows `build_system: "go"`
    or `"tinygo"`) — record `go version`, falling back to "not found".

    **The two Go values this step needs are in the recipe FILE, not in any
    envelope.** `loci init probe`'s `recipe` block carries `path`, `target`,
    `validated`, `confirmed_by_user` and `escrow` — it has no `go:` block. Read
    them from the file that `path` names:

    ```
    sed -n '/^go:/,/^[a-z]/p' "<data.recipe.path>"
    ```

    Substitute the literal path the probe envelope printed under
    `data.recipe.path`. No path means no recipe, and there is nothing to read.

    (`data.recipe` is an object here and a plain path string in an `init`
    envelope — read the one the verb you ran actually returns.)

    For a TinyGo project also record `tinygo version` and, when the block gave a
    board, `tinygo info -target=<board> 2>&1 | head -5`. **Do not run
    `tinygo info` with an empty `-target=`** — it errors, and an empty board is
    itself the finding. Two Go-specific failures are worth naming because neither
    looks like a toolchain problem in the report:

    - **A version skew between `go` and `tinygo`.** TinyGo pins a supported Go
      range and refuses outside it with `requires go version 1.25 through 1.27,
      got go1.24` — a build failure whose cause is the *other* tool's version.
    - **A missing symbol is usually inlining, not a broken build.** Go inlines
      small functions into their callers by default, so "the function I edited
      is not in the binary" is expected rather than a defect. `go.gcflags` from
      the block above says which: empty means inlining is on, `-l` means the user
      has already traded it away.

## Step 2: Run 10-point diagnostics checklist

For each check, record status (PASS / FAIL) and a detail string.

| # | Check | How to test | PASS when |
|---|-------|-------------|-----------|
| 1 | loci CLI available & signed in | `command -v loci` resolves AND `loci auth status` exits 0 (`data.status == "signed_in"`) | loci on PATH and signed in |
| 2 | Session context exists | `<project-context>` (keyed file) exists and contains `project_root` | File exists with key |
| 3 | Compiler recorded | `compiler` field in `<project-context>` is not `unknown`, `null` or empty (recipe-only since T14: the session no longer scans PATH) | Has a value |
| 4 | LOCI target supported | `loci_target` in `<project-context>` is one of: `aarch64`, `armv7e-m`, `armv6-m`, `tc399` | Value in set |
| 5 | loci CLI healthy | `loci doctor` exits 0 and `data.healthy` is true (covers Python 3.12, asmslicer, analysis deps, c++filt, the Rust demangler, cross-compilers, credential store, state dir) | Exit 0 / healthy |
| 6 | Build artifacts exist | Read `artifact` from `<project-context>` (the recipe's own, written by `loci init`); fall back to a glob for `**/*.o` under the one build root, `.loci/build/` — **skipping `turns/`, `cargo/`, `dumps/` (and the pre-rename `elf/`) and every `.loci-stage-*/` directory at any depth** — or any `.elf`/`.o`/`.axf` in the project root. A `.loci-build/` beside it is a pre-move CLI's leftover and is not searched | At least one found |
| 7 | Analysed artifact is not stale | For each candidate from check 6 (cap at 5, newest first) run `loci build fresh --elf <path>` and read `.data.role` **before** `.data.stale` + `.data.sources_newer` | No candidate reports `stale: true`, **unless** its `role` is exactly `"baseline"` |
| 8 | session-init executable | `test -x <plugin-dir>/hooks/session-init.sh` | Exit code 0 |
| 9 | hooks.json valid | Read `<plugin-dir>/hooks/hooks.json` — it parses as JSON and its `hooks` key holds the event arrays | Valid JSON |
| 10 | Quota not exceeded | If check 1 passed (signed in), run `loci usage` and read `data.eligible` / `data.daily` (`{used, limit}`). | `data.eligible` is true |

Check 7 is here because **"the results are wrong" is most often "the results
describe a different binary"** — that is the defect behind the report this check
was added for. Record, per candidate, the artifact path, its `elf_mtime`, `role`,
`stale`, and the first entry of `sources_newer` (path + `newer_by_s`), so a
"results are wrong" report arrives with the artifact/source delta already computed
instead of needing a round trip. A `stale: null` is **not** a FAIL — record it with
its `reason` (usually no `-g`, or built on another machine).

**`role` decides what `stale: true` means, so read it first.** A baseline is older
than its sources *by construction* — that is what makes it the "before" side — so
`role: "baseline"` with `stale: true` is the healthy state and never a FAIL. Record
it as `baseline (expected)`. Two kinds of artifact answer `baseline`: a `.prev`
written by `loci build snapshot`, and anything under either root's `turns/`, which
is where `loci build compile --baseline` reconstructs a header edit's Before. The
envelope's own `recommendation` says the same thing in a sentence — and note its
advice for a stale baseline is *not* "rebuild", because rebuilding one destroys the
very state it is the baseline of.

**A MISSING `role` is a FAIL, not an exemption.** The field postdates the CLI
version the plugin currently pins, so on an un-upgraded install `.data.role` is
absent on every artifact. Phrasing the criterion as "no candidate whose `role` is
`measured` reports `stale: true`" would therefore be satisfied *vacuously* on
exactly the installs most likely to have the problem — a demonstrably stale object
recorded as PASS. Only the literal string `"baseline"` exempts a candidate; absent,
null, or anything else is treated as `measured` and a `stale: true` FAILs.

That is also why check 6's fallback glob skips `turns/`: everything under it is a
Before, and a Before is never the answer to "which artifact is being measured".

**And why it skips `.loci-stage-*/`.** `loci build compile` writes its object into
a private `.loci-stage-<x>/` beside the destination and renames it out, so nothing
partial is ever visible at the object's real path. A compile that is *killed*
leaves that directory behind, holding a file called `<stem>.o` that is the newest
object under the build root — so an unpruned glob reports it as the artifact being
measured, and `loci build clean` deletes it out from under the next check. Prune it
at every depth: it sits beside its destination, not at a fixed level.
The same prune list `loci build clean` walks by, so the fallback agrees with the
CLI about what is an artifact — a glob that reaches wider than the CLI's own view
reports a different project than every other check does. `cargo/` is LOCI's
private `CARGO_TARGET_DIR` (hundreds of build-script objects, and the crate's real
object is published above it) and `dumps/` holds text dumps, not artifacts.

**Checks 3, 4 and 6 move together, and the pattern is the finding.** Since
T14 the session writes nothing about a project's build — no scan runs — so
`compiler`, `loci_target` and `artifact` are all `loci init`'s
and stand or fall with the recipe. Three shapes, each a different finding:

- **all three present, `init_status: ok`** — a recipe governs; each check judges
  its own value.
- **all three absent, `init_status` absent or `uninitialized`** — no recipe yet.
  Checks 3, 4 and 6 fail together and that is ONE finding — the project is not
  initialized — said once, citing step 1's snapshot; the first analysis will run
  `/loci:init`.
- **all three absent, `init_status` is `unsupported` or `needs_user`** — init
  stopped before recording anything, and the finding is `init_status`.
- **`compiler` is `null` beside a `.loci/build.yaml` on disk** — `loci init` wrote
  the file with its no-recipe branch and no scan reran, so `init_status` is
  `failed`. Check 3 fails while a recipe exists; the finding is the failed init,
  and `null` (init wrote it) is a different signal from `"unknown"` (the scan did)
  and from absent (nobody did).

If `loci` is not on PATH, checks 5, 7 and 10 automatically FAIL (the analysis
stack lives inside the CLI; `loci doctor` reports the specific missing piece).
If check 1 failed (not signed in), check 10 automatically FAILs with
"not signed in — cannot check quota".

Check 10 is the only check that reaches the backend. Skip it if check 1
failed; record "skipped: not signed in" in the detail column.

## Step 3: Collect stats

Run via Bash (skip if `loci` is unavailable):
```
loci stats summary --context-file "<project-context>"
loci stats global-summary
```

Record `data.report` from each, or the reason it did not run: both verbs need a
session, so a signed-out run records "stats unavailable — not signed in", which is
a different finding from "stats unavailable — loci not working".

## Step 4: Reasoning — common failure forensics

This is the most important section. Analyze the session context and
diagnostics to determine what went wrong. Write this as free-form reasoning
(not templated) so it captures the actual session state.

### A. Skill Not Invoked

If the user's issue is that a LOCI skill should have triggered but didn't,
investigate:

1. **Prompt match** — compare the user's original prompt against the
   `when_to_use` triggers for each relevant skill. List the trigger keywords
   from the SKILL.md and note which matched or didn't.

2. **Auto-run conditions** — for auto-triggered skills:
   - `loci-post-edit`: Was the edited file a C/C++/Rust/Go source
     (.c, .cc, .cpp, .cxx, .c++, .rs, .go, .h, .hpp, .hxx, .h++, .hh, .inc, .ipp, .tcc, .inl, .tpp, .def)? Was an Edit/Write
     tool used?
   - `loci-preflight`: Was Claude in `/plan` mode when the user described
     new logic?

3. **Skill visibility** — is the skill listed in the `Available:` line of the
   session-reminder? Currently expected:
   `/loci:help, /loci:init, /loci:exec-trace, /loci:stack-depth, /loci:memory-report, /loci:control-flow,
   /loci:contract, /loci:bug-report`. If not, session-init may not have registered it.

4. **Deferred tools** — check if `loci:loci-post-edit`, `loci:loci-preflight`,
   `loci:trends`, etc. appear in the system-reminder available skills list.
   If absent, the plugin may not be loaded.

5. **Competing behavior** — did Claude answer directly instead of invoking the
   skill? Did another skill or tool pre-empt? Note what Claude did instead.

### B. Results Not Evaluated or Not Valid

If a skill ran but produced no results, wrong results, or results that weren't
used, investigate:

0. **Which binary was measured, and was it current?** Check this first — it is the
   cheapest explanation for "the numbers are wrong" and the one that has actually
   happened. Take check 7's output: if the artifact the skill analysed reports
   `stale: true`, the numbers describe a program that is no longer on disk, and
   nothing downstream needs investigating. Two shapes to separate:
   - **stale linked ELF** — the project's own build predates the edit. The report
     is correct about the wrong binary.
   - **fresh `.o`, whole-program question** — a relocatable object's call edges are
     unapplied relocations, so a worst-case depth or a cross-call timing measured
     from a `.o` collapses to the single function, with `has_unknown_callees: false`
     and no warning. Correct artifact, wrong scope. Check the report's `Artifact:`
     provenance line for which of the two it was; if that line is
     missing altogether, record *that* as the finding.
   - **right artifact, wrong flags** — the numbers rest on the recipe, so a run
     whose `Recipe:` line reads `unvalidated`, or that carried a `flag_source_v2`
     warning about a `flags.json` `mode: "replace"` pin used *instead of* the
     recipe, measured a build the project does not make. Step 1's snapshot shows
     it.

1. **Compilation** — did the compilation step succeed? Look for compiler errors,
   missing headers, wrong flags. Do **not** go hunting for the compiler: a recipe
   naming one this machine does not have answers `compiler_missing`, whose one
   recovery is `/loci:init --refresh`, and the envelope says so. Record the
   `error.code` the compile returned; it is the diagnosis.

2. **`loci elf` output** — did `loci elf asm` or `loci elf cfg` return an
   `{"ok":true,"data":…}` envelope? Common failures: function name not found in
   binary, architecture mismatch between ELF and LOCI target, empty output, or
   an `{"ok":false,"error":…}` envelope (read `error.message`). Re-run with
   `LOCI_DEBUG=1` (the CLI forwards any captured stdout to stderr in debug mode)
   to see leaked third-party text. On Windows, also confirm the caller did not
   merge streams with `2>&1 > file` — stderr diagnostics before the JSON would
   produce the same symptom.

3. **`loci timing` response** — did `loci timing` return timing/energy data?
   Common failures: backend timeout, `auth_required` (token expired
   mid-session — re-run `! loci login`), `quota_exceeded`, server error, empty
   `data.rows`. Timing goes through the loci backend's REST endpoint via the `loci` CLI (the backend URL is configured inside the CLI).

4. **Result parsing** — were `data.timing_csv` / `data.timing_architecture` (from
   `loci elf asm`) or `execution_time_ns` (from `loci timing`) present? If `loci`
   returned data but Claude didn't use it, note the gap.

5. **Delta comparison** — for post-edit: did the compile report a baseline, i.e.
   was `data.output_prev` present (equivalently, does `prepare`'s `provenance[]`
   line carry a `before`)? Ask it that way round, not "does a `.o.prev`
   file exist": a `.prev` can sit on disk and still be **correctly withheld** —
   built from a different source, or with different flags, or captured for another
   turn — and any accompanying `NOTE` says which, as does the compile envelope's own
   `data.baseline_withheld` (`{code, reason}`) on a CLI new enough to report one.
   Record the `code` when you have it: it is the CLI's verdict on the candidate it
   actually examined, and it is what separates a deliberate refusal from a capture
   that never happened (`not_captured`). Finding the file and concluding a
   baseline existed turns a working refusal into a filed defect. Then: did
   `loci elf diff` return 0 changed functions? That is **not** the same as "the
   binary didn't change" — the differ hashes masked instructions, so a constant-only
   edit produces an empty diff, and `data.summary.removed` can be non-zero while the
   changed-function list is empty. Read `data.summary`, and see
   [Diffing the pair](../_shared/loci-runtime-contract.md#elf-diff) before recording
   an empty diff as either a defect or a clean run.

6. **Output suppression** — did Claude generate analysis but fail to present
   it? (Context window pressure, interrupted response, tool call error.)

### C. loci CLI installed and healthy?

The analysis stack lives in the `loci` CLI (a uv tool on PATH), installed by
session-init.sh at SessionStart. If the user just upgraded the plugin or
installed fresh and analysis broke, check:

- Does `command -v loci` resolve? If not, session-init's `_ensure_loci_cli`
  install may have failed (offline, uv missing) — re-run the session or
  `uv tool install --force loci`.
- Does `loci doctor` report `data.healthy: true`? A `fail` on the `asmslicer`
  or `python` probe means the CLI's own environment is broken — reinstall it
  with `uv tool install --force loci_cli`. Warnings (c++filt, cross-compilers,
  signed-out) are non-fatal.

### D. Root cause

Based on the diagnostics and reasoning above, state the root cause. Use the
dependency chain to find the most upstream failure:

```
hooks → loci CLI install → sign-in → project-context → loci timing → compilation → analysis
```

If all 11 checks pass, the issue is likely:
- Skill trigger wording mismatch (Claude didn't recognize the intent)
- Transient `loci timing` backend timeout
- A bug in the skill logic itself

## Step 5: Write report file

Determine the output filename:
```
report-<YYYY-MM-DD>-<os-short>.md
```

Write the file to the current working directory using this structure:

```markdown
# LOCI Diagnostic Report

Generated: <YYYY-MM-DD HH:MM:SS UTC>

## Versions

| Component | Version |
|-----------|---------|
| Claude Code | <claude --version output> |
| Claude model | <model ID, e.g. claude-opus-4-7> |
| LOCI plugin | <plugin version from plugin.json> |
| loci CLI | <loci --version output, or "unknown"> |
| OS | <uname -a output> |

## User Description

<user-description>

## Environment

| Field | Value |
|-------|-------|
| Project root | <project_root or cwd> |
| Git branch | <branch> |
| Compiler | <compiler or "unknown"> |
| Build system | <build_system or "unknown"> |
| LOCI target | <loci_target or "unknown"> |
| Auth status | <signed in / not signed in> |
| loci CLI | <path from `command -v loci`, or "unavailable"> |
| LOCI_STATE_DIR | <resolved path> |
| init_status | <from `<project-context>`, or "absent"> |
| Recipe | <`recipe.path`, or "none — `initialized: false`", or the `recipe_untrusted` string verbatim> |
| Recipe validated | <tier + `confirmed_by_user`, or "n/a"> |
| Recipe escrow | <`ok` / `missing` / "n/a — no recipe"> |

## Diagnostics Checklist

| # | Check | Status | Detail |
|---|-------|--------|--------|
| 1 | loci CLI available & signed in | <PASS/FAIL> | <detail> |
| 2 | Session context exists | <PASS/FAIL> | <detail> |
| 3 | Compiler recorded | <PASS/FAIL> | <detail> |
| 4 | LOCI target supported | <PASS/FAIL> | <detail> |
| 5 | loci CLI healthy | <PASS/FAIL> | <detail> |
| 6 | Build artifacts exist | <PASS/FAIL> | <detail> |
| 7 | Analysed artifact is not stale | <PASS/FAIL> | <detail, e.g. "kernel.elf stale — blink.c 225s newer" or "3 candidates current" or "unverified: no DWARF"> |
| 8 | session-init executable | <PASS/FAIL> | <detail> |
| 9 | hooks.json valid | <PASS/FAIL> | <detail> |
| 10 | Quota not exceeded | <PASS/FAIL> | <detail, e.g. "18,000 / 30,000 daily tokens (free)" or "LIMIT REACHED — 35,000 / 30,000"> |

**Result: <N>/10 checks passed.**

## Reasoning

### What the user was trying to do
<describe the intent and expected behavior>

### What should have happened
<which skill should have triggered, with trigger conditions from when_to_use>

### What actually happened
<what Claude did instead — answered directly, wrong skill, error, silence>

### Why it failed
<root cause reasoning chain, referencing specific checklist failures>

### Skill trigger analysis
<for each relevant skill, did the trigger conditions match?>

## Diagnosis

**Root cause:** <one-sentence root cause>

**Contributing factors:** <any additional FAIL checks>

**Suggested fix:**
<numbered actionable steps to resolve>

## Stats

### Branch stats
<loci stats summary `data.report`, or "no stats recorded">

### Global stats
<loci stats global-summary `data.report`, or "no stats recorded">

## Raw Data

<details>
<summary>project context (`<project-context>` keyed file)</summary>

```json
<sanitized contents or "MISSING">
```
</details>

<details>
<summary>loci init probe</summary>

```json
<`loci init probe` .data — `initialized`, the `recipe` block and
`recipe_untrusted` ONLY; or "loci init probe unavailable (CLI predates the
recipe)". Do not paste the whole envelope: its `sources` and `compilers[].path`
are absolute paths across the user's machine, and this file is written to be
shared.>
```
</details>

<details>
<summary>loci doctor</summary>

```
<`loci doctor` data.report, or "loci not on PATH">
```
</details>

<details>
<summary>hooks.json</summary>

```json
<sanitized contents or "MISSING">
```
</details>

<details>
<summary>loci auth status</summary>

```
<`loci auth status` output — signed-in / signed-out>
```
</details>

<details>
<summary>Recent git log</summary>

<git log --oneline -3 output>
</details>
```

### Redaction

Before embedding any file contents in the Raw Data section above, sanitize
them in-memory:

1. **Secrets** — replace values matching common secret patterns (API keys,
   tokens, passwords, `Bearer ...`, `Authorization: ...`, private key blocks,
   the `token` field inside `impact-token.json`) with `[REDACTED]`.
2. **Home paths** — replace the user's home directory prefix
   (`/Users/<name>/`, `/home/<name>/`, `C:\Users\<name>\`) with `~/`.

Apply substitutions BEFORE writing the report. Do NOT write unsanitized
contents and edit afterward.

## Step 6: Present summary to user

After writing the report file, display a concise summary:

```
## LOCI Diagnostic Summary

<N>/10 checks passed.

**Root cause:** <one-sentence diagnosis>

**Suggested fix:**
<numbered steps>

Share this file when reporting issues, or open it in a new Claude Code
session for further investigation.

─── LOCI · bug-report ─────────────────
  Report: <absolute-path-to-report-file>
────────────────────────────────────────
```

The report file path MUST appear in the footer as the last visible output.
Use the absolute path so the user can copy-paste it directly.

Do NOT record stats for this skill (diagnostic/informational only).
Do NOT emit a LOCI voice remark (inappropriate for failure context).
