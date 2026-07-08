#!/usr/bin/env bash
# LOCI plugin — SessionStart hook (runs every session via hooks/hooks.json).
# ALWAYS exits 0 — a failing hook must never block a session.
# Works on Linux, macOS, and Windows (MSYS2/Git Bash).

PLUGIN_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# State lives outside the versioned plugin dir so it survives upgrades; fall
# back to the plugin dir only if ~/.loci/state isn't writable. The Python side
# reads LOCI_STATE_DIR to match this choice.
STATE_DIR="${HOME}/.loci/state"
if ! mkdir -p "$STATE_DIR" 2>/dev/null; then
    STATE_DIR="${PLUGIN_DIR}/state"
fi
export LOCI_STATE_DIR="$STATE_DIR"

# Shared bootstrap primitives + logger (defines the steps used below plus
# loci_is_dev). Sourced before the debug capture and PATH work so both see
# LOCI_ENV.
# shellcheck source=../lib/setup-steps.sh
. "${PLUGIN_DIR}/lib/setup-steps.sh" 2>/dev/null || true

# Debug capture (dev mode only): read the SessionStart payload from stdin to key
# the dump below by session_id. Only when stdin is piped, so manual invocations
# never block on stdin.
LOCI_HOOK_INPUT=""
if loci_is_dev && [ ! -t 0 ]; then
    LOCI_HOOK_INPUT=$(cat 2>/dev/null || true)
fi

# Force UTF-8: Windows consoles default to cp1252 and can't encode the Unicode
# LOCI emits. This env var is the one knob that survives every subprocess layer.
export PYTHONIOENCODING="${PYTHONIOENCODING:-utf-8}"

# Hook subprocesses don't inherit the login-shell PATH.
augment_path

loci_log INFO session-init "start: SessionStart hook (cwd=$(pwd) state_dir=$STATE_DIR)"

_plugin_version() {
    local jq_bin="$1" dir="${2:-$PLUGIN_DIR}"
    "$jq_bin" -r '.version // "0"' \
        "${dir}/.claude-plugin/plugin.json" 2>/dev/null || echo "0"
}

# Return 0 if dotted-numeric version $1 is strictly greater than $2. Pure bash
# so we don't depend on sort -V (BSD sort before macOS 10.13 lacks it).
_semver_gt() {
    local a b IFS=.
    # shellcheck disable=SC2206
    a=($1)
    # shellcheck disable=SC2206
    b=($2)
    local n=${#a[@]} m=${#b[@]} i
    [ "$m" -gt "$n" ] && n=$m
    for (( i=0; i<n; i++ )); do
        local x=${a[i]:-0} y=${b[i]:-0}
        case "$x$y" in *[!0-9]*) return 1;; esac
        if   [ "$x" -gt "$y" ]; then return 0
        elif [ "$x" -lt "$y" ]; then return 1
        fi
    done
    return 1
}

# Resolve the plugin dir for paths emitted in the session context. PLUGIN_DIR
# (from $0) is stale if Claude Code launched a previous CLAUDE_PLUGIN_ROOT or an
# in-flight upgrade deletes this version's cache dir before the first tool call.
# Scan the cache root for the highest-semver version that still has both
# .claude-plugin/plugin.json and lib/ — its paths survive the upgrade. Fall back
# to PLUGIN_DIR when the cache layout doesn't match (dev install, test sandbox).
_resolve_authoritative_plugin_dir() {
    local cache_root; cache_root="$(dirname "$PLUGIN_DIR")"
    [ -d "$cache_root" ] || { printf '%s' "$PLUGIN_DIR"; return; }
    local d ver best_dir="" best_ver=""
    for d in "$cache_root"/*/; do
        [ -d "$d" ] || continue
        ver="${d%/}"; ver="${ver##*/}"
        # Dir name must be dotted-numeric — skip "current", tarballs, hidden.
        case "$ver" in ''|*[!0-9.]*) continue;; esac
        [ -f "${d}.claude-plugin/plugin.json" ] || continue
        [ -d "${d}lib" ] || continue
        if [ -z "$best_ver" ] || _semver_gt "$ver" "$best_ver"; then
            best_ver="$ver"; best_dir="${d%/}"
        fi
    done
    if [ -n "$best_dir" ]; then printf '%s' "$best_dir"
    else printf '%s' "$PLUGIN_DIR"
    fi
}

loci_log INFO session-init "start: jq detection"
if ! JQ=$(find_jq); then
    loci_log ERROR session-init "jq not found — emitting jq-free deps-missing context"
    printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"LOCI is installed but its prerequisite tool `jq` is missing, so LOCI analysis cannot run. Determine the install command for the user'"'"'s OS and package manager, tell them to run it (e.g. `! <install command>`), then have them start a new Claude Code session."},"systemMessage":"⚠ LOCI: prerequisite `jq` is not installed. Ask Claude for the install command for your system."}'
    exit 0
fi
loci_log INFO session-init "end: jq detection (path=$JQ)"

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
Type /help for the full rundown.
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
loci_log INFO session-init "end: project detection (target=$_CTX_TARGET compiler=$_CTX_COMPILER build=$_CTX_BUILD)"

# AUTH_PLUGIN_DIR is the highest-semver version in the cache root, not
# necessarily $0's location — see _resolve_authoritative_plugin_dir.
AUTH_PLUGIN_DIR=$(_resolve_authoritative_plugin_dir)
_LOCI_VER=$(_plugin_version "$JQ" "$AUTH_PLUGIN_DIR")

# The `loci` CLI (a uv tool on PATH) ships its own asmslicer + deps, so its
# presence is the single analysis-readiness proxy.
_DETECTION_READY=false
_LOCI_CLI_VER=""
if command -v loci >/dev/null 2>&1; then
    _DETECTION_READY=true
    # Read-only version probe — reported so pin drift is visible; the reinstall
    # is the detached installer's job.
    _LOCI_CLI_VER=$(loci --version 2>/dev/null | tr -cd '0-9.')
fi

# loci health is reported here, never installed inline. Combine the installer's
# last recorded outcome (loci-cli-status.json) with the live presence check.
# Default (no status file yet, or install still running): install is in flight.
_LOCI_STATUS_LINE=$(printf 'loci: installing in background — usable shortly. Install-on-miss: if a LOCI skill or analysis is requested (or a C/C++/Rust edit needs it) while `loci` is still absent, run `bash %s/hooks/ensure-loci-cli.sh` (waits for the in-flight install or performs it, self-locking), then retry the loci call.' \
    "$AUTH_PLUGIN_DIR")
_INSTALL_FAIL_MSG=""
if ! $_DETECTION_READY && ! have_uv; then
    _LOCI_STATUS_LINE=$(printf 'loci: NOT installed — its prerequisite `uv` is missing and the plugin does not install it. If the user requests a LOCI skill/analysis, determine the uv install command for their OS/package manager, tell them to run it (e.g. `! <install command>`), then run /loci:setup (or `bash %s/hooks/ensure-loci-cli.sh`) to install the loci CLI. Execution-aware analysis is unavailable until then.' \
        "$AUTH_PLUGIN_DIR")
    _INSTALL_FAIL_MSG=$(printf '⚠ LOCI: the loci CLI is not installed — prerequisite `uv` is missing.\nAsk Claude for the uv install command for your system, then run /loci:setup.')
elif ! $_DETECTION_READY; then
    _install_status="" _install_log="${STATE_DIR}/loci-cli-install.log"
    if [ -f "${STATE_DIR}/loci-cli-status.json" ]; then
        _install_status=$("$JQ" -r '.status // ""' "${STATE_DIR}/loci-cli-status.json" 2>/dev/null)
        _s_log=$("$JQ" -r '.log // ""' "${STATE_DIR}/loci-cli-status.json" 2>/dev/null)
        [ -n "$_s_log" ] && _install_log="$_s_log"
    fi
    case "$_install_status" in
        failed)
            _LOCI_STATUS_LINE=$(printf 'loci: NOT installed — last automatic install attempt failed. Retries run automatically at session start and when a C/C++/Rust edit needs loci (edge hooks), so do NOT auto-install unprompted. But if the user requests a LOCI skill/analysis, or asks about the loci problem, invoke the loci:setup skill (or run `bash %s/hooks/ensure-loci-cli.sh` — self-locking; waits for any in-flight install), then retry the loci call — and on repeat failure read `%s` to explain the cause. Execution-aware analysis is unavailable until it succeeds.' \
                "$AUTH_PLUGIN_DIR" "$_install_log")
            _INSTALL_FAIL_MSG=$(printf '⚠ LOCI: the loci CLI is not installed yet (last automatic attempt failed).\nExecution-aware analysis (timing, energy, stack, memory) is unavailable until it installs.\nIt retries automatically at the next session start. To fix it now, run /loci:setup,\nor check %s for the cause.' \
                "$_install_log")
            ;;
        skipped)
            _LOCI_STATUS_LINE="loci: install skipped (bootstrap/test mode)"
            ;;
    esac
fi

# additionalContext — injected into the session, invisible to the user.
LOCI_VOICE='LOCI voice: When presenting LOCI analysis results, adopt Aurora Labs "Proof, Not Promises" tone — numerically specific, technically confident, peer-to-peer. Add one short remark per report (max 15 words) that acknowledges the user'\''s work grounded in actual data. LOCI is a buddy that notices good engineering and flags real concerns honestly.
Positive feedback (attribute results to the user'\''s work):
- "That refactor cut worst path by 18%. Clean work."
- "Stack usage down 12% — smart move pulling that buffer off the stack."
- "3 functions, all under 200ns. This is tight code."
- "Energy per call dropped 0.8 uWs. Battery-friendly change."
- "ROM barely moved — +24 bytes. Minimal impact."
Honest concerns (constructive, with specifics):
- "Worst path grew 340ns. Might be worth looking at that snprintf on Cortex-M4."
- "Stack at 78% budget. Still passes, but getting tight."
- "Energy up 2.1 uWs per iteration — worth batching if this runs on battery."
Neutral (when results are baseline or first measurement):
- "Callees look clean. No issues."
- "First measurement recorded — this is your baseline."
Rules: Always cite numbers. Never use emoji. Never be vague ("looks good" without data). Attribute improvements to the user. Skip the remark when results are complex or the user needs raw data only. This is a presentation tone, not a persona — do not roleplay.'

if $_DETECTION_READY; then
    CONTEXT=$(printf 'loci version: %s\nTarget: %s, Compiler: %s, Build: %s\nLOCI target: %s\nBranch: %s\nloci command: loci (on PATH, v%s)\nplugin dir: %s\nproject context: %s\nAvailable: /help, /exec-trace, /stack-depth, /memory-report, /control-flow, /bug-report\nAuto-runs: loci-preflight (in /plan), loci-post-edit (after edits)\nLOCI auto-run rules: When in /plan mode and the user describes new C/C++/Rust logic to implement, you MUST invoke the loci:loci-preflight skill on existing callees before proposing edits. After any Edit/Write/MultiEdit to C/C++/Rust source files (.c,.cc,.cpp,.cxx,.h,.hpp,.hxx,.rs), you MUST invoke the loci:loci-post-edit skill immediately. These are not optional — they are required whenever LOCI is active.\nLOCI tool policy: All analysis runs through the `loci` command on PATH — call it as a bare `loci …`, never via Python. Every `loci` call prints one JSON envelope on stdout (`{"ok":true,"data":…}` or `{"ok":false,"error":…}`); parse it with `jq` and branch on `.ok` — never `python -c` (the plugin emits Unicode like `→`, `─`, en-dash that `python -c` mangles under Windows cp1252; `jq` is faster and ships with the plugin). Path policy: NEVER write intermediate files to `/tmp/`, `/var/tmp/`, or any path outside the working directory — Claude Code prompts the user for permission on every out-of-project access, halting automated preflight/post-edit/eval runs. Always write inside the project (e.g. `.loci-build/`) so every tool sees the same path.\n%s' \
        "$_LOCI_VER" "$_CTX_TARGET" "$_CTX_COMPILER" "$_CTX_BUILD" "$_CTX_TARGET" "$_CTX_BRANCH" \
        "$_LOCI_CLI_VER" "$AUTH_PLUGIN_DIR" "$_CTX_PROJECT_CONTEXT" "$LOCI_VOICE")
else
    CONTEXT=$(printf 'loci version: %s\nTarget: %s, Compiler: %s, Build: %s\nLOCI target: %s\nBranch: %s\n%s\nplugin dir: %s\nproject context: %s\nAvailable: /help, /exec-trace, /stack-depth, /memory-report, /control-flow, /bug-report\nAuto-runs: loci-preflight (in /plan), loci-post-edit (after edits)\nLOCI auto-run rules: When in /plan mode and the user describes new C/C++/Rust logic to implement, you MUST invoke the loci:loci-preflight skill on existing callees before proposing edits. After any Edit/Write/MultiEdit to C/C++/Rust source files (.c,.cc,.cpp,.cxx,.h,.hpp,.hxx,.rs), you MUST invoke the loci:loci-post-edit skill immediately. These are not optional — they are required whenever LOCI is active.\n%s' \
        "$_LOCI_VER" "$_CTX_TARGET" "$_CTX_COMPILER" "$_CTX_BUILD" "$_CTX_TARGET" "$_CTX_BRANCH" \
        "$_LOCI_STATUS_LINE" "$AUTH_PLUGIN_DIR" "$_CTX_PROJECT_CONTEXT" "$LOCI_VOICE")
fi

# Impact-token minting was removed with the MCP server; analysis now
# authenticates on demand via `! loci login`.

# Persist the exact additionalContext we inject: Claude Code never writes
# session-start context to the transcript, so this is the only record of what
# the model saw. Dev mode only; keyed by session_id (falls back to cwd hash).
if loci_is_dev; then
    _dbg_sid=$(printf '%s' "$LOCI_HOOK_INPUT" | "$JQ" -r '.session_id // empty' 2>/dev/null)
    [ -z "$_dbg_sid" ] && _dbg_sid="$(hash_cwd)"
    _dbg_file="${STATE_DIR}/session-context-${_dbg_sid}.json"
    if "$JQ" -n --arg sid "$_dbg_sid" --arg ctx "$CONTEXT" \
            '{session_id: $sid, additional_context: $ctx}' > "$_dbg_file" 2>/dev/null; then
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

# Claude Code renders systemMessage visibly and injects additionalContext.
"$JQ" -n \
    --arg ctx "$CONTEXT" \
    --arg sys "$SYSTEM_MSG" \
    '{
        hookSpecificOutput: {
            hookEventName: "SessionStart",
            additionalContext: $ctx
        }
    }
    + if ($sys | length) > 0
      then { systemMessage: $sys }
      else {}
      end'

loci_log INFO session-init "end: SessionStart hook"

exit 0
