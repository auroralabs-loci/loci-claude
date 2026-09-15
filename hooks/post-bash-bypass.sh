#!/usr/bin/env bash
# PostToolUse(Bash): `loci hook post-bash` names a source with a built object that
# changed since the turn began without a snapshot. Advisory; this hook fires on
# every Bash call, so it is silent when `loci` is absent and never exits nonzero.
set -u
export PATH="$PATH:${HOME:-}/.local/bin"
export PYTHONIOENCODING=utf-8

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
# to the CLI below. The capture happens ONLY when the log is on (the library's
# rule), so production still hands the harness's stdin to the verb untouched —
# unread and unmangled, as it always was.
loci_hook_payload_read && _pb_stdin=captured || _pb_stdin=""

loci_log INFO post-bash "start: PostToolUse(Bash)"
if ! command -v loci >/dev/null 2>&1; then
    loci_log INFO post-bash "end: skipped (loci absent)"
    exit 0
fi
# Fast-fail: the exit code this hook otherwise discards is the whole of what
# QA came for, so the mode captures it and says what failed. Still exit 0.
if loci_fail_fast; then
    loci_fail_fast_run post-bash PostToolUse loci hook post-bash
    exit 0
fi
if [ -n "$_pb_stdin" ]; then
    printf '%s' "$LOCI_HOOK_PAYLOAD" | loci hook post-bash
else
    loci hook post-bash
fi
loci_log INFO post-bash "end: loci hook post-bash rc=$?"
exit 0
