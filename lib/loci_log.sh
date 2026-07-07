#!/bin/bash
# LOCI plugin — shared bash logger. Appends to $LOCI_STATE_DIR/loci.log, using
# Claude Code's debug-log line shape so timestamps correlate against
# ~/.claude/debug/<session>.txt:
#
#   2026-05-05T11:29:03.107Z [INFO] [loci.<source>] message
#
# File logging is on only in dev mode (LOCI_ENV in {dev,development,1,true});
# production is silent. Safe to source multiple times.

# Idempotent guard
[ -n "${_LOCI_LOG_SOURCED:-}" ] && return 0
_LOCI_LOG_SOURCED=1

# LOCI_ENV=dev turns on verbose logging (here) and the dev backend (URL
# selection lives in the loci CLI's _config.py). It does NOT choose the CLI
# install source — that is LOCI_DEV_CLI_PATH (see lib/setup-steps.sh).
loci_is_dev() {
    case "${LOCI_ENV:-}" in
        dev|development|1|true|TRUE|True) return 0 ;;
        *) return 1 ;;
    esac
}

# Numeric level for filtering.
_loci_log_level_num() {
    case "${1:-}" in
        DEBUG) echo 10 ;;
        INFO)  echo 20 ;;
        WARN)  echo 30 ;;
        ERROR) echo 40 ;;
        OFF)   echo 99 ;;
        *)     echo 20 ;;
    esac
}

# Resolve log destination ONCE at source time. Per-call resolution costs a
# stat + mkdir (~50ms on Git Bash on Windows) × ~50 calls per SessionStart.
# Same reasoning for the rotation check.
_LOCI_LOG_FILE=""
_LOCI_LOG_THRESHOLD=99
if loci_is_dev; then
    _LOCI_LOG_THRESHOLD=$(_loci_log_level_num DEBUG)
    _loci_dir="${LOCI_STATE_DIR:-${HOME}/.loci/state}"
    if mkdir -p "$_loci_dir" 2>/dev/null; then
        _LOCI_LOG_FILE="$_loci_dir/loci.log"
        # One-shot rotation: truncate to last 1MB if the file exceeds 5MB.
        if [ -f "$_LOCI_LOG_FILE" ]; then
            _loci_size=$(wc -c < "$_LOCI_LOG_FILE" 2>/dev/null | tr -d '[:space:]')
            if [ -n "$_loci_size" ] && [ "$_loci_size" -gt 5242880 ]; then
                tail -c 1048576 "$_LOCI_LOG_FILE" > "${_LOCI_LOG_FILE}.tmp" 2>/dev/null \
                    && mv -f "${_LOCI_LOG_FILE}.tmp" "$_LOCI_LOG_FILE" 2>/dev/null
            fi
            unset _loci_size
        fi
    fi
    unset _loci_dir
fi

# Public API: loci_log <LEVEL> <source-tag> <message...>
# Example: loci_log INFO session-init "jq detection: found at /usr/bin/jq"
loci_log() {
    # Disabled outside dev mode, or when path resolution failed.
    [ -z "$_LOCI_LOG_FILE" ] && return 0
    local level="${1:-INFO}"; shift || true
    local source="${1:-loci}"; shift || true
    local cur_n
    cur_n=$(_loci_log_level_num "$level")
    [ "$cur_n" -lt "$_LOCI_LOG_THRESHOLD" ] && return 0

    # Timestamp without a subprocess (saves ~40ms/call on Git Bash on Windows).
    # EPOCHREALTIME (bash 5+) and %(...)T (bash 4.2+ printf builtin) are both
    # avoided on older bash via a single date spawn.
    local ts
    if [ -n "${EPOCHREALTIME:-}" ]; then
        local _epoch_int="${EPOCHREALTIME%.*}"
        local _epoch_ms="${EPOCHREALTIME#*.}"
        TZ=UTC printf -v ts '%(%Y-%m-%dT%H:%M:%S)T' "$_epoch_int"
        ts="${ts}.${_epoch_ms:0:3}Z"
    else
        ts=$(date -u +"%Y-%m-%dT%H:%M:%S.%3NZ" 2>/dev/null)
        case "$ts" in *3N*) ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ") ;; esac
    fi

    printf '%s [%s] [loci.%s] %s\n' "$ts" "$level" "$source" "$*" \
        >> "$_LOCI_LOG_FILE" 2>/dev/null || true
}

# Time a block and log start/end. Usage:
#   loci_log_around session-init "venv check" _venv_is_py312
loci_log_around() {
    local source="$1"; shift
    local label="$1"; shift
    loci_log INFO "$source" "start: $label"
    "$@"
    local rc=$?
    loci_log INFO "$source" "end: $label (rc=$rc)"
    return $rc
}
