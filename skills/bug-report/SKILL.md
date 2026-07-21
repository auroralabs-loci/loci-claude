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
fast-failing probes `command -v loci`, `loci auth status`, and `loci doctor`.

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
   missing, read `<plugin-dir>/.claude-plugin/plugin.json` and extract
   `.version` with `jq -r '.version'`. Fall back to "unknown".
4. **OS info** — `uname -a`
5. **OS short name** — `uname -s | tr '[:upper:]' '[:lower:]'` (for filename)
6. **Project context** — Read `<project-context>` (the per-session keyed file
   listed as `project context:` in this session). Record the full JSON. If
   missing, record "MISSING".
7. **CLI health** — run `loci doctor` and record `data.report` (covers Python
   3.12, asmslicer, analysis deps, c++filt, cross-compilers, credential store,
   and the state dir). If `loci` is unavailable, record "loci not on PATH".
8. **Git info** — `git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown"`
   and `git log --oneline -3 2>/dev/null || echo "no git history"`
9. **Hooks config** — Read `<plugin-dir>/hooks/hooks.json`. If missing,
   record "MISSING".
10. **CLI auth** — run `loci auth status` and record signed-in / signed-out.
    (The plugin no longer registers an MCP server; all backend calls go through
    the `loci` CLI and authenticate on demand via `! loci login`.)

## Step 2: Run 10-point diagnostics checklist

For each check, record status (PASS / FAIL) and a detail string.

| # | Check | How to test | PASS when |
|---|-------|-------------|-----------|
| 1 | loci CLI available & signed in | `command -v loci` resolves AND `loci auth status` exits 0 (`data.status == "signed_in"`) | loci on PATH and signed in |
| 2 | Session context exists | `<project-context>` (keyed file) exists and contains `project_root` | File exists with key |
| 3 | Compiler detected | `compiler` field in `<project-context>` is not `unknown` or empty | Has a value |
| 4 | Architecture detected | `architecture` field in `<project-context>` is not `unknown` or empty | Has a value |
| 5 | LOCI target supported | `loci_target` in `<project-context>` is one of: `aarch64`, `armv7e-m`, `armv6-m`, `tc399` | Value in set |
| 6 | loci CLI healthy | `loci doctor` exits 0 and `data.healthy` is true (covers Python 3.12, asmslicer, analysis deps, c++filt, cross-compilers, credential store, state dir) | Exit 0 / healthy |
| 7 | Build artifacts exist | Glob for `.loci-build/**/*.o` or any `.elf`/`.o`/`.axf` in project root | At least one found |
| 8 | session-init executable | `test -x <plugin-dir>/hooks/session-init.sh` | Exit code 0 |
| 9 | hooks.json valid | `<plugin-dir>/hooks/hooks.json` parses with `jq .` | Valid JSON |
| 10 | Quota not exceeded | If check 1 passed (signed in), run `loci usage` and read `data.eligible` / `data.daily` (`{used, limit}`). | `data.eligible` is true |

If `loci` is not on PATH, checks 6 and 10 automatically FAIL (the analysis
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

Record `data.report` from each, or "stats unavailable — loci not working".

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
   - `loci-post-edit`: Was the edited file a C/C++/Rust source
     (.c, .cc, .cpp, .cxx, .h, .hpp, .hxx, .rs)? Was an Edit/Write/MultiEdit
     tool used?
   - `loci-preflight`: Was Claude in `/plan` mode when the user described
     new logic?

3. **Skill visibility** — is the skill listed in the `Available:` line of the
   session-reminder? Currently expected:
   `/help, /exec-trace, /stack-depth, /memory-report, /control-flow, /bug-report`.
   If not, session-init may not have registered it.

4. **Deferred tools** — check if `loci:loci-post-edit`, `loci:loci-preflight`,
   `loci:trends`, etc. appear in the system-reminder available skills list.
   If absent, the plugin may not be loaded.

5. **Competing behavior** — did Claude answer directly instead of invoking the
   skill? Did another skill or tool pre-empt? Note what Claude did instead.

### B. Results Not Evaluated or Not Valid

If a skill ran but produced no results, wrong results, or results that weren't
used, investigate:

1. **Compilation** — did the compilation step succeed? Look for compiler errors,
   missing headers, wrong flags. Check if the compiler from `<project-context>`
   is actually installed: `which <compiler>`.

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

5. **Delta comparison** — for post-edit: did `.o.prev` exist before the
   recompile? Did `loci elf diff` return 0 changed functions (meaning the binary
   didn't actually change)?

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

If all 10 checks pass, the issue is likely:
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
| Architecture | <architecture or "unknown"> |
| LOCI target | <loci_target or "unknown"> |
| Auth status | <signed in / not signed in> |
| loci CLI | <path from `command -v loci`, or "unavailable"> |
| LOCI_STATE_DIR | <resolved path> |

## Diagnostics Checklist

| # | Check | Status | Detail |
|---|-------|--------|--------|
| 1 | loci CLI available & signed in | <PASS/FAIL> | <detail> |
| 2 | Session context exists | <PASS/FAIL> | <detail> |
| 3 | Compiler detected | <PASS/FAIL> | <detail> |
| 4 | Architecture detected | <PASS/FAIL> | <detail> |
| 5 | LOCI target supported | <PASS/FAIL> | <detail> |
| 6 | loci CLI healthy | <PASS/FAIL> | <detail> |
| 7 | Build artifacts exist | <PASS/FAIL> | <detail> |
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
