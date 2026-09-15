#!/usr/bin/env bash
# Stop hook: flush this turn's impact records so `/loci:trends` stays honest
# across sessions.
#
# It was an inline command string in hooks/hooks.json until it needed a log
# line: Claude Code records a Stop hook's exit status and nothing about what it
# did, so a hook with no trace of its own is one QA cannot see fire, time out or
# fail. The PATH here is PREPENDED, unlike every other hook, because that is
# what the inline string did.
#
# NEVER exit 2 — on Stop that blocks the stop and continues the conversation,
# an infinite loop for a hook that runs every turn.
set -u
export PYTHONIOENCODING=utf-8
export PATH="${HOME:-}/.local/bin:$PATH"

# The shared logger. Stubbed when the library is missing; the stub's non-zero
# `loci_hook_payload_read` means "stdin was not captured", which is the same
# answer production gives.
case "$0" in
    */*)
        . "${0%/*}/../lib/loci_log.sh" 2>/dev/null || true
        . "${0%/*}/../lib/loci_failfast.sh" 2>/dev/null || true
        ;;
esac
command -v loci_log >/dev/null 2>&1 \
    || { loci_log() { :; }; loci_hook_payload_read() { return 1; }; }
command -v loci_fail_fast >/dev/null 2>&1 || loci_fail_fast() { return 1; }

# stdin carries `.session_id` and reads once, so it is captured here and re-fed
# to the verb below. The capture happens ONLY when the log is on (the library's
# rule), so production still hands the harness's stdin over untouched.
loci_hook_payload_read && _sf_stdin=captured || _sf_stdin=""

loci_log INFO stats-flush "start: Stop impact flush"
if ! command -v loci >/dev/null 2>&1; then
    loci_log INFO stats-flush "end: skipped (loci absent)"
    exit 0
fi
# Fast-fail: the exit code this hook otherwise discards is the whole of what
# QA came for, so the mode captures it and says what failed. Still exit 0.
if loci_fail_fast; then
    loci_fail_fast_run stats-flush Stop loci stats flush-impacts
    exit 0
fi
if [ -n "$_sf_stdin" ]; then
    printf '%s' "$LOCI_HOOK_PAYLOAD" | loci stats flush-impacts
else
    loci stats flush-impacts
fi
loci_log INFO stats-flush "end: loci stats flush-impacts rc=$?"
exit 0
