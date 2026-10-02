#!/usr/bin/env bash
# LOCI plugin — SessionStart hook (runs every session via hooks/hooks.json).
# ALWAYS exits 0 — a failing hook must never block a session.
# Works on Linux, macOS, and Windows (MSYS2/Git Bash).

# The bash this runs under is decided first, while the payload is still on
# stdin: on bash 3 (stock macOS) this re-executes under a newer bash when one
# is installed, else sets `_LOCI_BASH_LEGACY=1` (AAD-7771; lib/bash-compat.sh).
. "${0%/*}/../lib/bash-compat.sh" 2>/dev/null || :

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
#
# Under GitHub Copilot CLI the payload is read too: its `source` says whether
# this is a first start or a resumed session's repeat (lib/loci_host.sh,
# AAD-7784). The host is the library's to recognise; a plugin whose lib did not
# source keeps Claude Code's path.
command -v loci_host_reads_payload >/dev/null 2>&1 || loci_host_reads_payload() { return 1; }
command -v loci_host_session_resumed >/dev/null 2>&1 || loci_host_session_resumed() { return 1; }
command -v loci_host_name >/dev/null 2>&1 || loci_host_name() { return 1; }
command -v loci_host_notice >/dev/null 2>&1 || loci_host_notice() { return 1; }
LOCI_HOOK_INPUT=""
if { loci_is_dev || loci_fail_fast || loci_host_reads_payload; } && [ ! -t 0 ]; then
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

# The `startup` matcher, enforced here for a host that runs every SessionStart
# entry regardless of it (Copilot: `source: resume` on `--continue`). A resumed
# session has its context in the transcript and its bootstrap already launched,
# so this is the whole hook for it: nothing on stdout, nothing started. Under
# Claude Code the matcher means this line is never true (lib/loci_host.sh).
if loci_host_session_resumed "$LOCI_HOOK_INPUT"; then
    loci_log INFO session-init "end: resumed session — context and bootstrap not repeated"
    exit 0
fi

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

# Cleaning the build directory is NOT done here. It is `hooks/turn-clean.sh`, which
# hooks.json registers on both `Stop` and `SessionStart`. This file is registered
# with `"matcher": "startup"` and therefore does not run on `resume`, `clear` or
# `compact` under Claude Code — a user living in `claude --continue` would never
# clean anything. Copilot runs it regardless of the matcher; the resume gate
# above is what keeps it to a first start there (AAD-7784).

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
# What the ready context says about the CLI. The healthy line, until the stale
# check below has something to add — the ready branch of the context builder
# never emitted _LOCI_STATUS_LINE, which is why a stale CLI reached nobody.
_LOCI_READY_LINE="loci command: loci (on PATH)"
if ! $_DETECTION_READY && ! have_uv; then
    _LOCI_STATUS_LINE=$(printf 'loci: NOT installed — its prerequisite `uv` is missing and the plugin does not install it. If the user requests a LOCI skill/analysis, determine the uv install command for their OS/package manager, tell them to run it (e.g. `! <install command>`), then run /loci:setup (or `bash %s/hooks/ensure-loci-cli.sh`) to install the loci CLI. Execution-aware analysis is unavailable until then.' \
        "$AUTH_PLUGIN_DIR")
    _INSTALL_FAIL_MSG=$(printf '⚠ LOCI: the loci CLI is not installed — prerequisite `uv` is missing.\nAsk Claude for the uv install command for your system, then run /loci:setup.')
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
        loci_log WARN session-init "stale loci CLI: have=${_CLI_VER} pinned=${LOCI_CLI_VERSION} path=${_CLI_PATH} shadowed=${_SHADOW:-0}"
    fi
fi

# additionalContext — injected into the session, invisible to the user.
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
# The host-shaped pieces of the two rules below (AAD-7784). Claude Code's text
# is what the two literals have always spelled, byte for byte, so the pieces
# default to it. Under GitHub Copilot CLI the edit tools are `edit`/`create`,
# the permission sentence is about Claude Code alone, and one `host:` line
# tells the model what the host is and how the tool names in LOCI's skills read
# there (`/plan`, `! <command>` and `/loci:<skill>` are the same on both).
# Decided by the library's gate, never by this file (lib/loci_host.sh).
#
# The preflight clause is host-shaped too (AAD-7787). Copilot's plan mode
# reaches its model as a `[[PLAN]]` prefix on the prompt, not as a mode it is
# told about, so the rule names the prefix. And it names what preflight runs
# on, because "its existing callees" read as "nothing" for a new function that
# calls nothing — while the function that will call it is the one the plan
# modifies and preflight measures (a live `--plan` run skipped on exactly that
# reading). Claude Code's clause is the one it has always been.
_EDIT_TOOLS='Edit/Write'
_PATH_WHY=' — Claude Code prompts the user for permission on every out-of-project access, halting automated preflight/post-edit/eval runs'
_PLAN_MODE='In /plan mode'
_PREFLIGHT_ON='on its existing callees'
_HOST_LINE=''
if _HOST_NAME=$(loci_host_name) && [ -n "$_HOST_NAME" ]; then
    _EDIT_TOOLS='edit/create'
    _PATH_WHY=''
    _PLAN_MODE='In plan mode (a [[PLAN]] prompt)'
    _PREFLIGHT_ON='on the functions it changes and the existing callees of its new code (a new function changes its caller)'
    _HOST_LINE="host: ${_HOST_NAME}. Tool names in LOCI's skills read as yours: Edit/Write is edit/create, Bash is bash or powershell, Read is view, the question tool is ask_user; /plan, /loci:<skill> and ! <command> work as written."
fi
# The reminder is the trigger, not "any edit": the hook emits it only for edits a
# recipe governs.
_AUTORUN_RULES='LOCI auto-run rules: '"$_PLAN_MODE"', when the user describes new C/C++/Rust/Go logic in a project a LOCI recipe governs, you MUST invoke loci:loci-preflight '"$_PREFLIGHT_ON"' before proposing edits. After an '"$_EDIT_TOOLS"' to a C/C++/Rust/Go source (.c,.cc,.cpp,.cxx,.c++,.rs,.go) or a header it includes, invoke loci:loci-post-edit as soon as the hook'"'"'s `You MUST invoke` reminder appears; it appears only for edits a recipe governs.'
_TOOL_POLICY='LOCI tool policy: All analysis runs through the `loci` command on PATH — call it as a bare `loci …`, never via Python. Every `loci` call prints one JSON envelope on stdout (`{"ok":true,"data":…}` or `{"ok":false,"error":…}`); read it and branch on `ok` — never `python -c` (the plugin emits Unicode like `→`, `─`, en-dash that `python -c` mangles under Windows cp1252). Path policy: NEVER write intermediate files to `/tmp/`, `/var/tmp/`, or any path outside the working directory'"$_PATH_WHY"'. Always write inside the project (e.g. `.loci/build/`) so every tool sees the same path.'
# Adopting a project is the USER'S decision, so this rule no longer auto-runs
# init — it used to, and that was the intrusive half. `loci init` writes four
# things into someone's tree (`.loci/build.yaml`, `.loci/.gitignore`, an escrow
# and a context file) and records `confirmed_by_user: false`, i.e. the files land
# and consent is asked afterwards. On a project nobody pointed LOCI at, that is
# LOCI deciding to adopt a repo because an edit happened to touch a `.c` file.
#
# Scoped to "no recipe governs": a degraded recipe still gets init's repair.
_INIT_RULE='LOCI init rule: adopting a project is the user'"'"'s decision. When a `loci` call answers `not_initialized` and no LOCI recipe governs the code, say so in one line, name `/loci:init`, and stop: do not invoke the loci:init skill or run `loci init` for it, auto-run rules included.'
# Fast-fail, and it is the ONE rule that governs every skill: a skill halts by
# instruction, so the instruction has to reach the model, and this line is the
# only channel every skill and every turn shares. It is EMPTY unless the switch
# is on, so the steady-state block pays nothing for a mode nobody but QA sets;
# the full statement lives in the shared house rules' `#fail-fast` section,
# which is what a skill reads for the detail.
_FAIL_FAST_RULE=""
if loci_fail_fast; then
    _FAIL_FAST_RULE='LOCI fast-fail: LOCI_FAIL_FAST is set. When any `loci` call fails — a non-zero exit, or an envelope with `"ok":false` — stop there. Report the command, its exit code and its output verbatim, say the analysis did not run, and wait for the user. Do not retry it, do not route around it, do not repair it, and make no further loci call this turn. This OVERRIDES every retry, fallback and recovery written in the skills and in the shared house rules, a coded error'"'"'s own recovery included.'
fi
_VOICE_RULES='LOCI voice, every report: cite the number, no emoji, never vague, a presentation tone and not a persona. Each skill'"'"'s own voice section carries the rest.'

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
_ctx_line "$_CLI_LINE"
# Once, plainly, and only where it is true: stock macOS bash with no newer bash
# installed. The hooks still run; what changes is the size they decide exactly
# (AAD-7771; lib/bash-compat.sh has the measurements).
[ -z "${_LOCI_BASH_LEGACY:-}" ] || _ctx_line "bash: ${BASH_VERSION%%(*} (stock macOS), no newer bash found — hooks run reduced: the contract guard reads 4 KB per tool call and decides larger commands coarsely (over-denies, never under). Fix: brew install bash"
_ctx_line "plugin dir: $AUTH_PLUGIN_DIR"
_ctx_line "$_HOST_LINE"
_ctx_line "$_AVAILABLE"
_ctx_line "$_AUTORUN_RULES"
_ctx_line "$_INIT_RULE"
_ctx_line "$_TOOL_POLICY"
_ctx_line "$_VOICE_RULES"

# Impact-token minting was removed with the MCP server; analysis now
# authenticates on demand via `! loci login`.

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

# A host that shows `systemMessage` to nobody (Copilot, AAD-7783) gets the text
# inside the context instead, fenced as text to display, for the model to relay
# (lib/loci_host.sh, AAD-7784) — instead, not as well: a host that one day
# renders the field would show the welcome twice. Under Claude Code the call
# answers nothing and the banner stays where Claude Code renders it.
if loci_host_notice "$SYSTEM_MSG"; then
    _ctx_line "$LOCI_HOST_NOTICE"
    SYSTEM_MSG=""
fi

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

# Claude Code renders systemMessage visibly and injects additionalContext. The
# key is omitted entirely when there is no message — an empty one renders as a
# blank banner.
loci_json_hook_output SessionStart "$CONTEXT" "$SYSTEM_MSG"

loci_log INFO session-init "end: SessionStart hook"

exit 0
