#!/usr/bin/env bash
# LOCI plugin — SessionStart hook (runs every session via hooks/hooks.json).
# ALWAYS exits 0 — a failing hook must never block a session.
# Works on Linux, macOS, and Windows (MSYS2/Git Bash).

PLUGIN_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# State lives outside the versioned plugin dir so it survives upgrades; fall
# back to the plugin dir only if ~/.loci/state can't be created. The ladder —
# $LOCI_STATE_DIR, then ~/.loci/state, then <plugin>/state, with the fallback
# taken on a FAILED CREATE and not on a missing directory — is spelled the same
# way in every bash entry point here, because two spellings is two answers to
# "where is my state" and the CLI writes the very file these hooks read by name.
#
# Where it agrees with `stats._resolve_state_dir`, stated precisely rather than
# claimed wholesale: rung 1 ($LOCI_STATE_DIR) and the create-not-exists rule are
# identical. Rung 2 is NOT, on Windows: bash reads $HOME, Python's `Path.home()`
# reads %USERPROFILE% and ignores $HOME, so a machine where those differ has the
# two halves reading different directories. That predates this change and is
# parked (T08/P41) — converging it moves eight home-derived paths including the
# credential store. Rung 3 differs too: <plugin> here is the checkout, in the
# CLI it is the installed package. Pin $LOCI_STATE_DIR to settle both.
STATE_DIR="${LOCI_STATE_DIR:-${HOME:-}/.loci/state}"
if ! mkdir -p "$STATE_DIR" 2>/dev/null; then
    STATE_DIR="${PLUGIN_DIR}/state"
fi
export LOCI_STATE_DIR="$STATE_DIR"

# Shared bootstrap primitives + logger (defines the steps used below plus
# loci_is_dev). Sourced before the debug capture and PATH work so both see
# LOCI_ENV.
# shellcheck source=../lib/setup-steps.sh
. "${PLUGIN_DIR}/lib/setup-steps.sh" 2>/dev/null || true

# Payload capture: read the SessionStart payload from stdin for the session id —
# it names every log line below, and it keys the dev-mode dump. Only when stdin
# is piped, so manual invocations never block on stdin. Fast-fail reads it too:
# an unattributable ERROR line is the state todo 021 exists to end, and nothing
# further in this hook reads stdin.
LOCI_HOOK_INPUT=""
if { loci_is_dev || loci_fail_fast; } && [ ! -t 0 ]; then
    LOCI_HOOK_INPUT=$(cat 2>/dev/null || true)
fi

# Name the session on every log line below, so two sessions running at once can
# be told apart by a reader of the log. The payload just read is the only place
# the id appears, and stdin is consumed by now.
loci_log_session_from_payload "$LOCI_HOOK_INPUT"

# Force UTF-8: Windows consoles default to cp1252 and can't encode the Unicode
# LOCI emits. This env var is the one knob that survives every subprocess layer.
export PYTHONIOENCODING="${PYTHONIOENCODING:-utf-8}"

# Hook subprocesses don't inherit the login-shell PATH.
augment_path

loci_log INFO session-init "start: SessionStart hook (cwd=$(pwd) state_dir=$STATE_DIR)"
# WARN, not INFO: fast-fail alone logs from WARN, and a session running in this
# mode is exactly the session whose log a reader needs to see it in.
loci_fail_fast && loci_log WARN session-init "fail-fast: on (LOCI_FAIL_FAST=${LOCI_FAIL_FAST})"

# The plugin's own version, out of its manifest. `$(<file)` is a subshell and no
# process — this used to be a `jq`, which is a host tool the plugin does not
# bundle, so a machine without one got no session context at all rather than a
# version it could not read.
_plugin_version() {
    local dir="${1:-$PLUGIN_DIR}" manifest ver=""
    manifest="${dir}/.claude-plugin/plugin.json"
    if [ -f "$manifest" ]; then
        loci_json_load "$(<"$manifest")"
        ver=$(loci_json_get version)
    fi
    printf '%s' "${ver:-0}"
}

mkdir -p "$STATE_DIR" 2>/dev/null || exit 0

_welcome_text() {
    # Marker lives at ~/.loci/.welcome-shown, outside the versioned plugin dir,
    # so the one-time welcome isn't re-shown on every version bump. Fall back to
    # PLUGIN_DIR when ~/.loci isn't writable.
    local marker_dir="${HOME}/.loci"
    [ -d "$marker_dir" ] || marker_dir="$PLUGIN_DIR"
    local marker="${marker_dir}/.welcome-shown"
    [ -f "$marker" ] && return 0

    cat <<'WELCOME'
LOCI is ready.

Try:
  "What's the execution cost of main()?"   → timing & energy
  "How much ROM/RAM does my build use?"    → memory report
  "Is my stack safe for TaskMain?"         → stack depth

Auto-runs during /plan and after edits — no setup needed.
Sign in once with `! loci login` when timing/energy is first requested.
Type /loci:help for the full rundown.
WELCOME

    touch "$marker" 2>/dev/null
}

loci_log INFO session-init "start: exec-bit fixup"
fix_exec_bits >&2      # logs go to stderr (not parsed as hook output)
loci_log INFO session-init "end: exec-bit fixup"

# Install runs detached in ensure-loci-cli.sh, never inline: SessionStart must
# stay fast (a cold `uv tool install` takes tens of seconds). It self-locks and
# early-exits when loci is present, so this is cheap every session. </dev/null
# so it never holds this hook's stdin open; nohup so it survives the hook.
nohup bash "${PLUGIN_DIR}/hooks/ensure-loci-cli.sh" </dev/null >/dev/null 2>&1 &

loci_log INFO session-init "start: project detection"
detect_and_write_context
loci_log INFO session-init "end: project detection (state=$_CTX_STATE status=$_CTX_STATUS init_status=${_CTX_INIT_STATUS:-none} recipe=${_CTX_RECIPE:-none} target=$_CTX_TARGET)"

# Cleaning the build directory is NOT done here. It is `hooks/turn-clean.sh`, which
# hooks.json registers on both `Stop` and `SessionStart`. This file is registered
# with `"matcher": "startup"` and therefore does not run on `resume`, `clear` or
# `compact` — a user living in `claude --continue` would never clean anything.

# AUTH_PLUGIN_DIR is the highest-semver version in the cache root, not
# necessarily $0's location — see _resolve_authoritative_plugin_dir.
AUTH_PLUGIN_DIR=$(_resolve_authoritative_plugin_dir)
_LOCI_VER=$(_plugin_version "$AUTH_PLUGIN_DIR")

# The `loci` CLI (a uv tool on PATH) ships its own asmslicer + deps, so its
# presence is the single analysis-readiness proxy.
_DETECTION_READY=false
if command -v loci >/dev/null 2>&1; then
    _DETECTION_READY=true
fi

_VERSION_LINE="loci version: ${_LOCI_VER} — LOCI's only user-facing version; when the user asks for LOCI's version, report exactly this number. Internal component versions (e.g. the loci CLI binary's) are not LOCI's version — do not report or compare them."

# loci health is reported here, never installed inline. Combine the installer's
# last recorded outcome (loci-cli-status.json) with the live presence check.
# Default (no status file yet, or install still running): install is in flight.
_LOCI_STATUS_LINE=$(printf 'loci: installing in background — usable shortly. Install-on-miss: if a LOCI skill or analysis is requested (or a C/C++/Rust/Go edit needs it) while `loci` is still absent, run `bash %s/hooks/ensure-loci-cli.sh` (waits for the in-flight install or performs it, self-locking), then retry the loci call.' \
    "$AUTH_PLUGIN_DIR")
_INSTALL_FAIL_MSG=""
# Non-empty ONLY when something is wrong with the CLI. The armed and initialized
# blocks always report CLI health; the inactive ones report it only when it is
# bad, so a docs repo does not pay for "loci command: loci (on PATH)" — but a
# broken or shadowed CLI stays visible in a directory LOCI will not analyze,
# which is often exactly where the user asks about it.
_CLI_ADVISORY=""
# What the ready context says about the CLI. The healthy line, until the stale
# check below has something to add — the ready branch of the context builder
# never emitted _LOCI_STATUS_LINE, which is why a stale CLI reached nobody.
_LOCI_READY_LINE="loci command: loci (on PATH)"
if ! $_DETECTION_READY && ! have_uv; then
    _LOCI_STATUS_LINE=$(printf 'loci: NOT installed — its prerequisite `uv` is missing and the plugin does not install it. If the user requests a LOCI skill/analysis, determine the uv install command for their OS/package manager, tell them to run it (e.g. `! <install command>`), then run /loci:setup (or `bash %s/hooks/ensure-loci-cli.sh`) to install the loci CLI. Execution-aware analysis is unavailable until then.' \
        "$AUTH_PLUGIN_DIR")
    _INSTALL_FAIL_MSG=$(printf '⚠ LOCI: the loci CLI is not installed — prerequisite `uv` is missing.\nAsk Claude for the uv install command for your system, then run /loci:setup.')
    _CLI_ADVISORY="$_LOCI_STATUS_LINE"
elif ! $_DETECTION_READY; then
    _install_status="" _install_log="${STATE_DIR}/loci-cli-install.log"
    if [ -f "${STATE_DIR}/loci-cli-status.json" ]; then
        loci_json_load "$(<"${STATE_DIR}/loci-cli-status.json")"
        _install_status=$(loci_json_get status)
        _s_log=$(loci_json_get log)
        [ -n "$_s_log" ] && _install_log="$_s_log"
    fi
    case "$_install_status" in
        failed)
            _LOCI_STATUS_LINE=$(printf 'loci: NOT installed — last automatic install attempt failed. Retries run automatically at session start and when a C/C++/Rust/Go edit needs loci (edge hooks), so do NOT auto-install unprompted. But if the user requests a LOCI skill/analysis, or asks about the loci problem, invoke the loci:setup skill (or run `bash %s/hooks/ensure-loci-cli.sh` — self-locking; waits for any in-flight install), then retry the loci call — and on repeat failure read `%s` to explain the cause. Execution-aware analysis is unavailable until it succeeds.' \
                "$AUTH_PLUGIN_DIR" "$_install_log")
            _INSTALL_FAIL_MSG=$(printf '⚠ LOCI: the loci CLI is not installed yet (last automatic attempt failed).\nExecution-aware analysis (timing, energy, stack, memory) is unavailable until it installs.\nIt retries automatically at the next session start. To fix it now, run /loci:setup,\nor check %s for the cause.' \
                "$_install_log")
            ;;
        skipped)
            _LOCI_STATUS_LINE="loci: install skipped (bootstrap/test mode)"
            ;;
    esac
    _CLI_ADVISORY="$_LOCI_STATUS_LINE"
else
    # Present but behind the pin. Every branch above tests ABSENCE, so a CLI that
    # exists and never upgrades used to report nothing at all — the failure was
    # invisible across releases. Resolve the spec the same way the installer does
    # (it also adopts a newer pin from the authoritative plugin dir); dev
    # checkouts float by design and leave _loci_cli_pinned empty.
    _loci_resolve_install_spec
    _CLI_VER=$(loci_cli_version) || _CLI_VER=""
    # ⚠ Do NOT put `$_CLI_VER` on the `loci command:` line. That format —
    # `loci command: loci (on PATH, v<cli>)` — was RETIRED on purpose: with two
    # numbers in the block the model answered "what version is loci" with both and
    # editorialized about a mismatch that is by design. `test_version_announcement`
    # pins its absence, and T13 re-added it for a day before that test said no.
    #
    # The four rules that gate on a CLI version read the stale-CLI advisory below
    # instead, whose whole subject is the two numbers. That works only because the
    # advisory's ABSENCE means something, and it does not mean it in every branch:
    # `_loci_resolve_install_spec` leaves `_loci_cli_pinned` empty for a
    # `LOCI_DEV_CLI_PATH` checkout (dev installs float by design), and `_CLI_VER`
    # is empty when `loci --version` prints nothing parseable. In both, the
    # advisory cannot fire however old the CLI is — so a model reading "no
    # advisory" as "at or above the pin" would take the newer branch of a rule on
    # a CLI that does not implement it. **That state is now said out loud**, in a
    # marker that carries no number: the contract's `#cli-version-gate` treats it
    # as unknown and takes the safe branch.
    #
    # A LINE OF ITS OWN, not a parenthetical on `loci command:`. Every phrasing
    # that fits inside those parentheses begins with "version", and
    # `test_version_announcement` bans the substring `on PATH, v` outright — so
    # the marker would have tripped the very ban it is written to respect.
    # Short on purpose: this line is paid on EVERY session start for as long as
    # the install stays unpinned — a dev checkout is that state permanently — so
    # unlike the stale-CLI advisory it is not transient and it lives inside the
    # 2 048 B budget. The first draft restated what `#cli-version-gate` row 3
    # already says and put the initialized block 97 B over.
    #
    # "could not be determined", not "is not the pinned one": the hook has not
    # established the second. `_CLI_VER` is also empty for a correctly pinned
    # install whose `--version` merely failed to print.
    if [ -z "$_loci_cli_pinned" ] || [ -z "$_CLI_VER" ]; then
        _LOCI_READY_LINE=$(printf 'loci command: loci (on PATH)\nloci CLI version: could not be determined (see #cli-version-gate).')
    fi
    if [ -n "$_loci_cli_pinned" ] && [ -n "$_CLI_VER" ] \
       && _semver_gt "$LOCI_CLI_VERSION" "$_CLI_VER"; then
        _CLI_PATH=$(command -v loci 2>/dev/null)
        _UV_BIN=$(loci_uv_bin_dir)
        _SHADOW=""
        # The installer writes into the uv shim dir; if the `loci` that answers
        # lives elsewhere, reinstalling will never change the version.
        case "$_CLI_PATH" in
            "${_UV_BIN}/"*) ;;
            *) [ -x "${_UV_BIN}/loci" ] && _SHADOW=1 ;;
        esac
        _LOCI_STATUS_LINE=$(printf 'loci: CLI is %s but this plugin pins %s — the automatic upgrade is not taking effect%s. A retry runs in the background this session; if the user hits stale behaviour or asks about the version, invoke the loci:setup skill and read `%s` for the cause.' \
            "$_CLI_VER" "$LOCI_CLI_VERSION" \
            "${_SHADOW:+ (the \`loci\` on PATH is ${_CLI_PATH}, but the installer writes ${_UV_BIN}/loci — a shadowing binary)}" \
            "${STATE_DIR}/loci-cli-install.log")
        if [ -n "$_SHADOW" ]; then
            _INSTALL_FAIL_MSG=$(printf '⚠ LOCI: the loci CLI on your PATH is %s, but LOCI pins %s.\n%s shadows the installed %s/loci, so upgrades never take effect.\nRemove or rename the shadowing binary, then start a new session.' \
                "$_CLI_VER" "$LOCI_CLI_VERSION" "$_CLI_PATH" "$_UV_BIN")
        else
            _INSTALL_FAIL_MSG=$(printf '⚠ LOCI: the loci CLI is %s but this version pins %s — the upgrade is not taking effect.\nA retry is running in the background. If it persists, run /loci:setup or check %s.' \
                "$_CLI_VER" "$LOCI_CLI_VERSION" "${STATE_DIR}/loci-cli-install.log")
        fi
        _LOCI_READY_LINE=$(printf 'loci command: loci (on PATH)\n%s' "$_LOCI_STATUS_LINE")
        _CLI_ADVISORY="$_LOCI_STATUS_LINE"
        loci_log WARN session-init "stale loci CLI: have=${_CLI_VER} pinned=${LOCI_CLI_VERSION} path=${_CLI_PATH} shadowed=${_SHADOW:-0}"
    fi
fi

# additionalContext — injected into the session, invisible to the user.
#
# Four blocks, one per branch of report §6.3; `detect_and_write_context` has
# already decided which (`$_CTX_STATE`). The pieces are shared so each rule is
# written once:
#
#   initialized           a recipe governs this project and `loci init` recorded
#                         it — the compact block: target, compiler, build system,
#                         artifact, auto-run rules. No scan ran.
#   initialized_degraded  the same, minus half the facts, plus one line saying
#                         which half is missing and what heals it. Still armed.
#   armed                 no recipe, but the cheap gate found a declared build:
#                         armed, plus the `not_initialized` → auto-init rule and
#                         one /loci:init suggestion.
#   inactive_*            not a project, or the CLI recorded a status that means
#                         "do not arm". The disarm sentence is EXPLICIT there,
#                         because loci-post-edit's own description says MANDATORY
#                         and that text is in the model's context regardless of
#                         what this block says.
#
# The 1 385-byte LOCI_VOICE block is gone from here, but not all of it. Every
# skill that renders a measurement carries its own "## LOCI voice remark"
# section — verified for both mandatory auto-run skills (loci-preflight,
# loci-post-edit) and for exec-trace, control-flow, stack-depth, memory-report
# and trends; bug-report opts out on purpose, and `contract` phrases its own
# rule inline. `tests/unit/test_project_detection_gate.py` pins that list, so
# the drop cannot outlive its justification.
#
# What those sections do NOT carry is the four hard rules — cite numbers, no
# emoji, never vague, not a persona — which after the first cut existed nowhere
# in the plugin at all. They are 205 bytes; the twelve worked examples that made
# up the rest of the block are what the per-skill sections replace.
# `Auto-runs: loci-preflight (in /plan), loci-post-edit (after edits)` used to
# sit between these two. It was a 62-byte restatement of the line below it, in a
# block with a byte budget, and nothing reads the label.
_AVAILABLE='Available: /loci:help, /loci:init, /loci:exec-trace, /loci:stack-depth, /loci:memory-report, /loci:control-flow, /loci:contract, /loci:bug-report'
_AUTORUN_RULES='LOCI auto-run rules: When in /plan mode and the user describes new C/C++/Rust/Go logic to implement, you MUST invoke the loci:loci-preflight skill on existing callees before proposing edits. After any Edit/Write to a C/C++/Rust/Go source (.c,.cc,.cpp,.cxx,.c++,.rs,.go) or any header it includes, you MUST invoke the loci:loci-post-edit skill immediately. These are not optional — they are required whenever LOCI is active.'
# AAD-7607. The auto-run rules are WRONG under an artifact-only recipe, so this
# REPLACES them rather than following them. Both halves of that matter. Printing
# both would put "you MUST invoke loci-post-edit" and "do not" in one block, and
# the `_DISARM` note below is the record of why that loses: a session-start line
# does not win an argument it starts with the MANDATORY wording it is arguing
# against. And it must still be SAID, because the two skills' auto-runs have two
# different levers — post-edit's is the PostToolUse reminder, which
# `post-edit-hook.sh` now suppresses at source, while preflight's is a /plan-mode
# prompt with no hook behind it at all. This line is the only lever preflight has.
#
# It also carries the rebuild refusal. That is not belt-and-braces: the session
# that produced this ticket had post-edit stop on `compdb_absent` exactly as
# written, and then offer "rebuild the project so basic_ble.out picks up the
# change, and re-run stack-depth" — which the user did, destroying the only
# pre-edit binary the project had and taking a 160 B → 1,032 B stack regression
# with it. Under this recipe the linked artifact IS the baseline.
_AUTORUN_ARTIFACT_ONLY='LOCI scope: this project'"'"'s recipe is artifact-only — the linked binary, no compile database, because the project has none. stack-depth, memory-report and control-flow read that binary and work normally. Timing and energy compile a source first, so every `loci analyse prepare` here answers `compdb_absent`, and the loci-preflight and loci-post-edit auto-run rules do NOT apply in this session: never invoke either automatically, whatever the "MANDATORY"/"You MUST invoke" wording in their own descriptions says. Scope, not a fault — do NOT run /loci:init (it wrote this recipe because no database exists, and would find none again), and do NOT rebuild or relink the recorded artifact to reach a measurement: under this recipe that binary is the only pre-edit state there is. Asked for timing or energy, say a compile database enables it and that `loci init --compdb=<path>` records one, the user'"'"'s to generate between turns.'
_TOOL_POLICY='LOCI tool policy: All analysis runs through the `loci` command on PATH — call it as a bare `loci …`, never via Python. Every `loci` call prints one JSON envelope on stdout (`{"ok":true,"data":…}` or `{"ok":false,"error":…}`); read it and branch on `ok` — never `python -c` (the plugin emits Unicode like `→`, `─`, en-dash that `python -c` mangles under Windows cp1252). Path policy: NEVER write intermediate files to `/tmp/`, `/var/tmp/`, or any path outside the working directory — Claude Code prompts the user for permission on every out-of-project access, halting automated preflight/post-edit/eval runs. Always write inside the project (e.g. `.loci/build/`) so every tool sees the same path.'
# Adopting a project is the USER'S decision, so this rule no longer auto-runs
# init — it used to, and that was the intrusive half. `loci init` writes four
# things into someone's tree (`.loci/build.yaml`, `.loci/.gitignore`, an escrow
# and a context file) and records `confirmed_by_user: false`, i.e. the files land
# and consent is asked afterwards. On a project nobody pointed LOCI at, that is
# LOCI deciding to adopt a repo because an edit happened to touch a `.c` file.
#
# This line is emitted ONLY in the armed-but-uninitialized branch (the case
# statement below skips it for `failed` and `unknown`, which both mean a recipe
# IS on disk). So the split is structural, not a judgement the model has to
# make: no recipe → stop and let the user choose; recipe present → the repair
# recoveries are unchanged, because that project was already adopted.
_INIT_RULE='LOCI init rule: this project has NO recipe, and initializing it is the user'"'"'s decision, not yours. When a `loci` call fails with `error.code` `not_initialized`, say so in one line, name `/loci:init` as what enables measurement here, and STOP. Do NOT invoke the loci:init skill, and do not run `loci init` yourself — it writes files into this project and adopting a repo is not a side effect of an edit. This applies however the call was reached, auto-run rules included. If the user asks for LOCI analysis here, the same one line is the answer. Once they run /loci:init themselves, everything arms normally.'
# Fast-fail, and it is the ONE rule that governs every skill: a skill halts by
# instruction, so the instruction has to reach the model, and this line is the
# only channel every skill and every turn shares. It is EMPTY unless the switch
# is on, so the steady-state block pays nothing for a mode nobody but QA sets;
# the full statement lives in the shared runtime contract's `#fail-fast` section,
# which is what a skill reads for the detail.
_FAIL_FAST_RULE=""
if loci_fail_fast; then
    _FAIL_FAST_RULE='LOCI fast-fail: LOCI_FAIL_FAST is set. When any `loci` call fails — a non-zero exit, or an envelope with `"ok":false` — stop there. Report the command, its exit code and its output verbatim, say the analysis did not run, and wait for the user. Do not retry it, do not route around it, do not repair it, and make no further loci call this turn. This OVERRIDES every retry, fallback and recovery written in the skills and in the shared runtime contract, a coded error'"'"'s own recovery included.'
fi
_VOICE_RULES='LOCI voice, every report: cite the number, no emoji, never vague, a presentation tone and not a persona. Each skill'"'"'s own voice section carries the rest.'
# The disarm sentence, and it has to beat two things that outrank a session-start
# line: `loci-post-edit`'s own description says MANDATORY, and the PostToolUse
# hook prints "You MUST invoke the loci:loci-post-edit skill NOW" after every
# edit, later in the context and file-specific (that hook is T09's to gate). So
# this names the competitors instead of contradicting them in the abstract, and
# it bounds the recovery: the pre-T08 text ended by telling the model to SUGGEST
# starting a session elsewhere, and dropping that clause left an unbounded "run
# /loci:init" as the only written way out.
_DISARM='The loci-preflight and loci-post-edit auto-run rules do NOT apply in this session: do not invoke any LOCI skill automatically. This OVERRIDES the "MANDATORY"/"You MUST invoke" wording in those skills'"'"' own descriptions and in any post-edit reminder you see later in this session — in this directory they do not apply, and an edit to a C/C++/Rust/Go file here is not a reason to run one. /loci:help and /loci:bug-report remain available. If the user explicitly asks for LOCI analysis, say why it is inactive here and suggest either starting a session in the project directory or running /loci:init if this directory really is the project.'

# Built line by line rather than by one big printf: the blocks differ by which
# lines are PRESENT, and a format string with conditional `%s`es is how a
# missing value becomes a blank line — or an off-by-one that shifts every
# remaining line up by one argument.
CONTEXT=""
_ctx_line() {
    [ -n "$1" ] || return 0
    CONTEXT="${CONTEXT:+$CONTEXT
}$1"
}

if $_DETECTION_READY; then _CLI_LINE="$_LOCI_READY_LINE"
else                       _CLI_LINE="$_LOCI_STATUS_LINE"
fi

_ctx_line "$_VERSION_LINE"
_ctx_line "$_FAIL_FAST_RULE"
# What `detect_and_write_context` has to say about a `loci` call that failed
# under fast-fail. Empty in every other session.
_ctx_line "${_LOCI_FF_SESSION_NOTE:-}"

case "$_CTX_STATE" in
initialized|initialized_degraded)
    # Only facts that exist. A degraded context (wiped state directory) has none
    # of them, and printing `Target: unknown` is how a session acquires a
    # fabricated target — the failure this change exists to end.
    if [ "$_CTX_COMPILER" != "unknown" ] || [ "$_CTX_BUILD" != "unknown" ]; then
        _ctx_line "Compiler: ${_CTX_COMPILER}, Build: ${_CTX_BUILD}"
    fi
    case "$_CTX_TARGET" in
        unknown|null|"") ;;
        # The one spelling of the target in the whole block. Every skill reads
        # it from this exact line ("LOCI target:"), and it used to be printed
        # twice — once here and once in a `Target:` display line.
        *) _ctx_line "LOCI target: ${_CTX_TARGET}" ;;
    esac
    # Both come from the recipe's mirror and both are `-f`-checked there, so a
    # recipe recorded on another machine and an artifact since deleted are
    # simply not asserted — the degraded-recipe block used to name both as fact
    # in the same breath as saying the recipe could not be found.
    _ctx_line "${_CTX_RECIPE:+recipe: $_CTX_RECIPE}"
    _ctx_line "${_CTX_ARTIFACT:+artifact: $_CTX_ARTIFACT}"
    _ctx_line "Branch: $_CTX_BRANCH"
    _ctx_line "$_CLI_LINE"
    _ctx_line "plugin dir: $AUTH_PLUGIN_DIR"
    _ctx_line "project context: $_CTX_PROJECT_CONTEXT"
    case "$_CTX_DEGRADED" in
        state)
            _ctx_line 'LOCI: this project has a recipe but no recorded state on this machine (a wiped state directory, or a checkout init has not seen). The first analysis rebuilds it — `loci init --auto` is a no-op on an initialized project — so invoke the loci:init skill once if a call reports missing context or answers `not_initialized`.' ;;
        recipe)
            _ctx_line 'LOCI: the recorded state says this project is initialized, but no `.loci/build.yaml` was found walking up from this directory. If a `loci` call answers `not_initialized`, invoke the loci:init skill once to re-establish the recipe.' ;;
    esac
    _ctx_line "$_AVAILABLE"
    # One or the other, never both — see the note above the artifact-only rule.
    if [ -n "$_CTX_ARTIFACT_ONLY" ]; then
        _ctx_line "$_AUTORUN_ARTIFACT_ONLY"
    else
        _ctx_line "$_AUTORUN_RULES"
    fi
    _ctx_line "$_TOOL_POLICY"
    _ctx_line "$_VOICE_RULES"
    ;;
armed)
    # No target line. An uninitialized project has no recorded target, and since
    # T14 nothing guesses one: the scan that used to derive a target from PATH
    # is gone, so there is no hint to leave for a hook either.
    _ctx_line "Branch: $_CTX_BRANCH"
    _ctx_line "$_CLI_LINE"
    _ctx_line "plugin dir: $AUTH_PLUGIN_DIR"
    _ctx_line "project context: $_CTX_PROJECT_CONTEXT"
    if [ "$_CTX_DEGRADED" = unknown ]; then
        _ctx_line 'LOCI: this project has a build recipe, and its recorded initialization status is one this version of LOCI does not recognise — most likely written by a newer CLI. Analysis still runs, and no target is asserted here. Whatever coded error the first `loci` call answers with, invoke the loci:init skill ONCE with that code and let it route the recovery; do not retry after a second failure.'
    elif [ "$_CTX_DEGRADED" = failed ]; then
        # A recipe IS on disk; the last `loci init` failed against it (stale,
        # invalid, tampered, or a compiler that has gone). §6.2 makes that
        # transient, so the session is armed — but the coded error the next
        # compile answers with will not be `not_initialized`, and telling the
        # model there is no recipe would send it down the wrong recovery.
        _ctx_line 'LOCI: this project has a build recipe, but the last initialization of it FAILED and no target is recorded. Analysis still runs. Whatever coded error the first `loci` call answers with — `recipe_stale`, `recipe_invalid`, `recipe_tampered`, `compiler_missing`, `not_initialized` — invoke the loci:init skill ONCE with that code and let it route the recovery; do not retry after a second failure.'
    else
        # This branch is a project nobody has pointed LOCI at. It used to say
        # "the first call initializes the project", which was true of no route,
        # and then to name the init skill as the recovery — which auto-adopted
        # the repo. `_INIT_RULE` just below carries the rule; this line only has
        # to stop asserting that measurement is available here.
        _ctx_line 'LOCI: this project is not initialized — no `.loci/build.yaml` records its target ISA, compiler or flags, so no target is asserted and nothing can be measured yet. Whether LOCI runs on this project is the user'"'"'s choice: `/loci:init` records how it builds, and until they run it a `loci` call answers `not_initialized`.'
    fi
    _ctx_line "$_AVAILABLE"
    _ctx_line "$_AUTORUN_RULES"
    case "$_CTX_DEGRADED" in failed|unknown) ;; *) _ctx_line "$_INIT_RULE" ;; esac
    _ctx_line "$_TOOL_POLICY"
    _ctx_line "$_VOICE_RULES"
    ;;
*)
    # Inactive. No analysis target, no mandatory auto-run rules. A parent dir
    # holding many repos, a docs tree, or a project the CLI has already found it
    # cannot support must not get a fabricated "Target: <host arch>" context.
    _ctx_line "Branch: $_CTX_BRANCH"
    _ctx_line "plugin dir: $AUTH_PLUGIN_DIR"
    _ctx_line "project context: $_CTX_PROJECT_CONTEXT"
    case "$_CTX_STATE" in
        inactive_status)
            case "$_CTX_STATUS" in
                unsupported)
                    _ctx_line 'LOCI: inactive (init: unsupported) — `loci init` found no target ISA LOCI supports in this project. That is recorded and permanent: only /loci:init changes it, and nothing should retry initialization on its own.' ;;
                *)
                    _ctx_line 'LOCI: inactive (init: needs_user) — initialization stopped on a question nobody has answered (most likely a headless run). Do not arm anything; if the user asks for LOCI analysis, invoke the loci:init skill, which asks it.' ;;
            esac ;;
        inactive_failed)
            _ctx_line 'LOCI: inactive (detection: failed) — project detection could not run in this directory, so LOCI cannot say what this project is. This is not a claim that it is not a project. /loci:init records how it builds and activates LOCI for it; /loci:bug-report collects what went wrong.' ;;
        inactive_multi)
            _ctx_line "$(printf 'LOCI: inactive (detection: multi_project) — this directory contains %s independent projects (each with its own repo or build files) and is not itself a project. To analyze one of them, start a session in that project'"'"'s directory.' \
                "${_CTX_SUBPROJECT_COUNT:-multiple}")" ;;
        *)
            _ctx_line 'LOCI: inactive (detection: no_project) — no build file (Makefile, CMakeLists.txt, Cargo.toml, meson.build, a vendor project file, …) declares a build in this directory, so nothing here is a LOCI analysis target. If this IS a project, /loci:init records how it builds and activates LOCI for it.' ;;
    esac
    _ctx_line "$_DISARM"
    _ctx_line "$_CLI_ADVISORY"
    ;;
esac

# Impact-token minting was removed with the MCP server; analysis now
# authenticates on demand via `! loci login`.

# Persist the exact additionalContext we inject: Claude Code never writes
# session-start context to the transcript, so this is the only record of what
# the model saw. Dev mode only; keyed by session_id (falls back to cwd hash).
if loci_is_dev; then
    loci_json_load "$LOCI_HOOK_INPUT"
    _dbg_sid=$(loci_json_get session_id)
    [ -z "$_dbg_sid" ] && _dbg_sid="$(hash_cwd)"
    _dbg_file="${STATE_DIR}/session-context-${_dbg_sid}.json"
    if loci_json_object session_id "$_dbg_sid" additional_context "$CONTEXT" \
            > "$_dbg_file" 2>/dev/null; then
        loci_log DEBUG session-init "wrote session-context dump -> $_dbg_file"
    else
        loci_log WARN session-init "session-context dump failed ($_dbg_file)"
    fi
    unset _dbg_sid _dbg_file
fi

WELCOME=$(_welcome_text)

# systemMessage = one-time welcome plus, on install failure, a banner. The
# banner is NOT gated by the welcome marker, so it recurs until install succeeds.
SYSTEM_MSG="$WELCOME"
if [ -n "$_INSTALL_FAIL_MSG" ]; then
    if [ -n "$SYSTEM_MSG" ]; then
        SYSTEM_MSG="${SYSTEM_MSG}

${_INSTALL_FAIL_MSG}"
    else
        SYSTEM_MSG="$_INSTALL_FAIL_MSG"
    fi
fi

# Claude Code renders systemMessage visibly and injects additionalContext. The
# key is omitted entirely when there is no message — an empty one renders as a
# blank banner.
loci_json_hook_output SessionStart "$CONTEXT" "$SYSTEM_MSG"

loci_log INFO session-init "end: SessionStart hook"

exit 0
