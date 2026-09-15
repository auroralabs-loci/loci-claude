#!/usr/bin/env bash
# LOCI plugin — shared bash logger. Appends to $LOCI_STATE_DIR/loci.log, using
# Claude Code's debug-log line shape so timestamps correlate against
# ~/.claude/debug/<session>.txt:
#
#   2026-05-05T11:29:03.107Z [INFO] [loci.<source>] [session=<id>] message
#
# The session group appears once a caller has handed the id over
# (`loci_log_session`); a line without it was written before any hook payload
# had been read, and no reader can attribute it to a session.
#
# File logging is on in dev mode (LOCI_ENV in {dev,development,1,true}), and in
# fast-fail mode (LOCI_FAIL_FAST) at WARN — QA runs production artifacts, so the
# switch that makes a `loci` failure loud has to be able to record it in a build
# where LOCI_ENV says nothing. The two gates are independent and neither implies
# the other. Otherwise production is silent. Safe to source multiple times.

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

# LOCI_FAIL_FAST=1 turns every recovery around a failing `loci` call into a halt.
# Read here because this is where the log destination is resolved, and read
# INDEPENDENTLY of LOCI_ENV: a switch that needed a dev build would not test what
# ships. `lib/loci_failfast.sh` carries the rest of the mode.
loci_fail_fast() {
    case "${LOCI_FAIL_FAST:-}" in
        1|true|TRUE|True|yes|on) return 0 ;;
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
if loci_is_dev || loci_fail_fast; then
    # Fast-fail alone logs from WARN: what it exists to record is the failure and
    # the notice that carries it, not the start/end line of every hook in a
    # production session.
    if loci_is_dev; then
        _LOCI_LOG_THRESHOLD=$(_loci_log_level_num DEBUG)
    else
        _LOCI_LOG_THRESHOLD=$(_loci_log_level_num WARN)
    fi
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

# Which session every later line belongs to. A hook is handed it on stdin as
# `.session_id`, and stdin reads once — so the caller passes the value it has
# already parsed and this library never touches the stream.
_LOCI_LOG_SESSION=""

# Public API: loci_log_session <session-id>
#
# Charset-checked, because the value goes into a log line: an id spelling a
# newline or a `]` would otherwise forge a line of its own, and the payload is
# not ours to trust.
loci_log_session() {
    [ -z "$_LOCI_LOG_FILE" ] && return 0
    case "${1:-}" in
        ""|*[!A-Za-z0-9._-]*) return 0 ;;
    esac
    _LOCI_LOG_SESSION="$1"
}

# Public API: loci_log_session_from_payload <raw-hook-payload>
#
# Forkless, and it stays that way: every hook that calls this has already read
# the payload, several run ahead of every tool call in the session, and one of
# them (`contract-guard.sh`) is fail-open on a 5 s budget, so a spawn here would
# be paid on paths that must not pay one. `session_id` is the FIRST key the
# harness writes, so the scan below never walks far — bash's `${v#*pat}` is
# quadratic in the offset of the match, which is why `lib/loci_json.sh` bounds
# what it parses and this one does not have to.
loci_log_session_from_payload() {
    [ -z "$_LOCI_LOG_FILE" ] && return 0
    case "${1:-}" in
        *'"session_id"'*) ;;
        *) return 0 ;;
    esac
    local sid="${1#*\"session_id\"}"
    sid="${sid#*:}"
    sid="${sid#"${sid%%[!" "]*}"}"
    case "$sid" in
        '"'*) sid="${sid#\"}"; sid="${sid%%\"*}" ;;
        *) return 0 ;;
    esac
    loci_log_session "$sid"
}

# Public API: loci_hook_payload_read
#
# For the thin hooks that hand their stdin straight to `loci`: the session id is
# on that stdin and a stream reads once, so capture it here and let the caller
# re-feed the CLI with `printf '%s' "$LOCI_HOOK_PAYLOAD" | loci ...`.
#
# Returns non-zero when the payload was NOT captured — logging off (so nothing
# would read it), or no `cat` to read it with (Git for Windows installs
# `Git\usr\bin` only optionally). The caller must then leave stdin alone and
# pass it through, rather than send the CLI an empty one.
loci_hook_payload_read() {
    LOCI_HOOK_PAYLOAD=""
    [ -z "$_LOCI_LOG_FILE" ] && return 1
    command -v cat >/dev/null 2>&1 || return 1
    LOCI_HOOK_PAYLOAD=$(cat)
    loci_log_session_from_payload "$LOCI_HOOK_PAYLOAD"
}

# Public API: loci_log <LEVEL> <source-tag> <message...>
# Example: loci_log INFO session-init "start: project detection"
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

    printf '%s [%s] [loci.%s]%s %s\n' "$ts" "$level" "$source" \
        "${_LOCI_LOG_SESSION:+ [session=$_LOCI_LOG_SESSION]}" "$*" \
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
