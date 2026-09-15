#!/usr/bin/env bash
# Stop hook: "must call measure after prepare" made visible.
#
# All the state-reading logic — which manifest belongs to this turn, prepared/
# failed/pending — lives in `loci analyse status`, not here (needs no jq: see
# .local/docs/open-questions.md "Hooks without jq"). This script only makes the
# call fail-open when `loci` itself is missing, then invokes it directly — the
# harness's stdin passes straight through, unread and unmangled, so the CLI
# gets `prompt_id` off it. In dev mode it is read once for the log's session id
# and re-fed byte-for-byte; nothing else ever touches it.
#
# Same two hard rules as `draft-pending-nudge.sh`:
#   * `systemMessage` is the only field the USER sees on Stop.
#   * NEVER exit 2 — that blocks the stop and continues the conversation, an
#     infinite loop for a hook that runs every turn. `--hook-json` always
#     exits 0 by its own contract; the explicit `exit 0` at the end is the
#     second line of defence against a stale or mismatched `loci` that does not
#     honour it.
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
loci_hook_payload_read && _ms_stdin=captured || _ms_stdin=""

loci_log INFO manifest-nudge "start: Stop manifest check"
if ! command -v loci >/dev/null 2>&1; then
    # Silent fail-open here would be exactly the degradation the open question
    # complains about — say so once instead of leaving it to be inferred.
    loci_log WARN manifest-nudge "end: skipped (loci absent)"
    printf '%s\n' '{"systemMessage":"LOCI: turn-end manifest check skipped — `loci` not found on PATH."}'
    exit 0
fi

# Fast-fail: the exit code this hook otherwise discards is the whole of what
# QA came for, so the mode captures it and says what failed. Still exit 0.
if loci_fail_fast; then
    loci_fail_fast_run manifest-nudge Stop loci analyse status --turn --hook-json
    exit 0
fi
if [ -n "$_ms_stdin" ]; then
    printf '%s' "$LOCI_HOOK_PAYLOAD" | loci analyse status --turn --hook-json
else
    loci analyse status --turn --hook-json
fi
loci_log INFO manifest-nudge "end: loci analyse status rc=$?"
exit 0
