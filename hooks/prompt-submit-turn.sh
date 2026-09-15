#!/usr/bin/env bash
# UserPromptSubmit hook: stamp this turn's id where a skill can find it even
# when `prompt_id` never reaches model context — a subagent (no
# UserPromptSubmit of its own) or a degraded host reads the file instead.
#
# All the payload parsing lives in `loci hook prompt-submit` (Python `json`,
# no jq/sed shell parsing — see .local/docs/open-questions.md "Hooks without
# jq"). This script only fails open when `loci` is missing, then pipes stdin
# through unread and unmangled. In dev mode it is read once for the log's
# session id and re-fed byte-for-byte; nothing else ever touches it.
#
# NEVER exit 2 — on UserPromptSubmit a nonzero/blocking exit interferes with
# the user's prompt. `loci hook prompt-submit` never exits 2 by its own
# contract; the explicit `exit 0` at the end is the second line of defence
# against a stale or mismatched `loci` that does not honour it.
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
loci_hook_payload_read && _ps_stdin=captured || _ps_stdin=""

loci_log INFO prompt-submit "start: UserPromptSubmit"
if ! command -v loci >/dev/null 2>&1; then
    loci_log WARN prompt-submit "end: skipped (loci absent)"
    printf '%s\n' '{"systemMessage":"LOCI: turn id not stamped — `loci` not found on PATH."}'
    exit 0
fi

# Fast-fail: the exit code this hook otherwise discards is the whole of what
# QA came for, so the mode captures it and says what failed. Still exit 0.
if loci_fail_fast; then
    loci_fail_fast_run prompt-submit UserPromptSubmit loci hook prompt-submit
    exit 0
fi
if [ -n "$_ps_stdin" ]; then
    printf '%s' "$LOCI_HOOK_PAYLOAD" | loci hook prompt-submit
else
    loci hook prompt-submit
fi
loci_log INFO prompt-submit "end: loci hook prompt-submit rc=$?"
exit 0
