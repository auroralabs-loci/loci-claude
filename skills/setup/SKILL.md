---
description: >
  Install LOCI's own dependencies (jq, uv, the loci CLI) and verify the
  environment — detect the C/C++/Rust compiler (checked, never installed) and,
  only if missing, write per-project detection state. Safe to re-run — the
  install steps are idempotent and repair whatever is missing.
when_to_use: >
  When the user asks to set up, install, reinstall, or repair LOCI ("set up
  loci", "install loci", "loci is not installed", "fix my loci install"), or
  when a loci command fails because the CLI is absent. With the argument
  "doctor", only run diagnostics without installing anything.
---

# LOCI Setup

Run the plugin's setup script, report what it did, and verify the result.
Only pause for genuinely manual steps (e.g. an SSH key for the private CLI
repo) — if a dependency is missing, the script installs it.

**Report for the user, not for us.** Surface only two things: what the user
must *do*, and the final verdict (setup worked / here's what's broken). Never
expose internal plumbing — do NOT mention the plugin dir, the state dir, hook
registration, project-detection internals, or how SessionStart works. If a line
isn't something the user needs to know or act on, drop it. Aim for a few lines
total; when all is well, a sentence or two is the right length.

## Step 0: Locate the plugin

Read `plugin dir: <path>` from the LOCI session context in the
`system-reminder` block emitted at session start, or use the
`CLAUDE_PLUGIN_ROOT` environment variable. Do this silently — never announce or
print the plugin dir; it's internal. If neither is available, stop and tell the
user to restart Claude Code so the plugin loads.

## Step 1: Route by argument

- `$ARGUMENTS` is `doctor` → diagnostics only: skip the install, run
  `loci doctor`, and report all checks — healthy ones in one line, any warning
  or failure with its `detail` and what to do about it. If `loci` itself is
  absent, say so and offer the full setup.
- otherwise → full setup: Install (Step 2) then Verify (Step 3). Verify runs the
  same `loci doctor`, so both routes share one check surface — the only
  difference is whether the install ran first.

## Step 2: Install

Announce it in one concise line before running — e.g. "Installing LOCI's
dependencies". Avoid conversational narration like "Running the setup script
now."

```bash
bash "<plugin-dir>/setup/setup.sh"
```

Run it from the project root. It installs jq and uv if missing and installs the
loci CLI as a uv tool (via the same self-locking installer the hooks use), then
fixes exec bits. Project detection is NOT re-run unconditionally — the
SessionStart hook owns that; setup writes per-project state only as a fallback
when none exists yet for this cwd (e.g. a plugin installed mid-session). It
prints a line-per-step report — read it, but relay only what the user needs:
whether the install succeeded and any dependency that failed. The plumbing lines
(exec bits, hooks.json validation, hook registration — including a "skipped"
registration, which is normal in plugin mode) are internal: do NOT surface them
and do NOT theorize about what they mean for auto-triggers. This step installs
LOCI's dependencies; environment/toolchain verification is Step 3 (`loci doctor`).

If the loci CLI step fails:

1. Read `~/.loci/state/loci-cli-install.log` for the cause.
2. Most common: `uv` cannot reach PyPI to fetch the `loci-tools` wheel — a
   missing/old `uv`, or a proxy blocking `pypi.org`. Report the log excerpt and
   the specific dependency that failed.
3. Network/proxy failures: report the log excerpt and suggest retrying with
   `bash <plugin-dir>/hooks/ensure-loci-cli.sh` (self-locking; waits for any
   in-flight install).

## Step 3: Verify

Nothing is installed here — this is the check half. Run `loci doctor`: it is the
single verification surface and returns one JSON envelope (`.data.checks[]`,
`.data.summary`, `.data.healthy`). Running it also proves the freshly-installed
binary actually works, so it doubles as the post-install sanity check — if the
`loci doctor` call itself errors (rather than reporting an unhealthy check),
treat that as an install problem and fall back to the CLI-failure handling in
Step 2.

Doctor checks the whole environment (Python, bundled deps, `c++filt`,
cross-compilers, credential store, sign-in state, state dir), but most of those
are our internal diagnostics. Relay only the user-facing verdict:

- If `healthy` → one sentence ("Environment checks passed"). Do NOT enumerate
  the passing checks, and never surface internal ones (state dir, credential
  store, individual Python imports).
- Otherwise → surface only the `warn`/`fail` checks that the user can act on
  (e.g. no cross-compiler for their target), each with its `detail` and the fix.
  Required-check failures block usage; optional ones are advisories.

Keep doctor's `session` check result for the sign-in offer in the next step.

## Step 4: Offer sign-in (once, optional)

Read the `session` check from the `loci doctor` output above. If signed in, say
nothing about auth. If signed out, offer sign-in as
the last setup step. Every `loci` command except the auth verbs and
`doctor` requires a session, so a signed-out install is not usable yet.
The login is a browser OAuth flow that must run in the user's own
terminal — do NOT run `loci login` yourself. Tell the user:

> One step left: sign in by typing `! loci login` (opens your browser).
> LOCI's commands need a session — if you skip this, they'll ask again
> when first run.

Declining is not a failure; setup is complete either way.

## Step 5: Report

End with a short, user-facing status — two things only: any action still
required (sign-in, a missing compiler for their target, adding an SSH key), and
the verdict. If everything is healthy, one line that setup is complete plus a
pointer to `/loci:help`. Do NOT list checks that passed, internal paths, the
state dir, or hook-registration details.

Do NOT tell the user to restart or reload Claude Code — this skill only runs
when the plugin is already loaded, so its hooks and skills are already active
in the session.
