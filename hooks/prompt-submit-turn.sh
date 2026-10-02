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
# The bash this runs under is decided first, while the payload is still on
# stdin: on bash 3 (stock macOS) this re-executes under a newer bash when one
# is installed, else sets `_LOCI_BASH_LEGACY=1` (AAD-7771; lib/bash-compat.sh).
. "${0%/*}/../lib/bash-compat.sh" 2>/dev/null || :

set -u

export PATH="$PATH:${HOME:-}/.local/bin"
export PYTHONIOENCODING=utf-8

# The shared logger. Stubbed when the library is missing; the stub's non-zero
# `loci_hook_payload_read` means "stdin was not captured", which is the same
# answer production gives.
case "$0" in
    */*)
        . "${0%/*}/../lib/loci_log.sh" 2>/dev/null || true
        . "${0%/*}/../lib/loci_json.sh" 2>/dev/null || true
        . "${0%/*}/../lib/loci_failfast.sh" 2>/dev/null || true
        . "${0%/*}/../lib/loci_host.sh" 2>/dev/null || true
        ;;
esac
command -v loci_log >/dev/null 2>&1 \
    || { loci_log() { :; }; loci_hook_payload_read() { return 1; }; }
command -v loci_fail_fast >/dev/null 2>&1 || loci_fail_fast() { return 1; }
command -v loci_host_payload_adapt >/dev/null 2>&1 || loci_host_payload_adapt() { return 1; }
# The stub takes CONSTANT text only: without `lib/loci_json.sh` there is no
# escaper, and the one message that reaches it below has nothing to escape.
command -v loci_json_system_message >/dev/null 2>&1 \
    || loci_json_system_message() { printf '{"systemMessage":"%s"}\n' "$2"; }
command -v loci_host_carry_done >/dev/null 2>&1 || loci_host_carry_done() { return 1; }

# stdin carries `.session_id` and reads once, so it is captured here and re-fed
# to the CLI below. The capture happens ONLY when the log is on (the library's
# rule), so production still hands the harness's stdin to the verb untouched —
# unread and unmangled, as it always was.
#
# The one other capture: under Copilot, which sends no `prompt_id`, the host
# adapter mints this turn's id, records it for the session and injects it, so
# `loci hook prompt-submit` stamps and echoes it as it does under Claude Code
# (lib/loci_host.sh, AAD-7781). Outside Copilot the call touches nothing.
loci_hook_payload_read && _ps_stdin=captured || _ps_stdin=""
loci_host_payload_adapt && _ps_stdin=captured
# What the previous turn's Stop hooks had for the user, under the host whose
# Stop reply reaches nobody: the adapter took the session's records along with
# the new turn id, and the verb appends them below its `[loci] turn=` line
# (lib/loci_host.sh, AAD-7783). Empty — and left unset — everywhere else; an
# inherited value is not the plugin's and is dropped.
if [ -n "${LOCI_HOST_CARRY:-}" ]; then export LOCI_HOOK_CARRY="$LOCI_HOST_CARRY"
else unset LOCI_HOOK_CARRY; fi

loci_log INFO prompt-submit "start: UserPromptSubmit"
_ps_absent='LOCI: turn id not stamped — `loci` not found on PATH.'
if ! command -v loci >/dev/null 2>&1; then
    loci_log WARN prompt-submit "end: skipped (loci absent)"
    if [ -n "${LOCI_HOST_CARRY:-}" ]; then
        # No verb to carry it: the notice and the carry in one document, then
        # the records go — delivered is delivered.
        loci_json_hook_output UserPromptSubmit "$_ps_absent"$'\n'"$LOCI_HOST_CARRY" "$_ps_absent"
        loci_host_carry_done
    else
        loci_json_system_message UserPromptSubmit "$_ps_absent"
    fi
    exit 0
fi

# With a carry in hand the verb's stdout is captured: a verb that printed
# nothing (no `prompt_id` it could read, a swallowed error) would otherwise
# take the carry down with it, so the hook then says the carry itself; and the
# records are removed only once a document has gone out. Under fast-fail the
# notice carries the text (lib/loci_failfast.sh). Copilot only — nothing of
# this runs where the adapter set no carry.
if [ -n "${LOCI_HOST_CARRY:-}" ]; then
    if loci_fail_fast; then
        _ps_out=$(loci_fail_fast_run prompt-submit UserPromptSubmit loci hook prompt-submit)
    else
        _ps_out=$(printf '%s' "$LOCI_HOOK_PAYLOAD" | loci hook prompt-submit)
        loci_log INFO prompt-submit "loci hook prompt-submit rc=$? (captured)"
    fi
    [ -n "$_ps_out" ] || _ps_out=$(loci_json_hook_output UserPromptSubmit "$LOCI_HOST_CARRY")
    printf '%s\n' "$_ps_out"
    loci_host_carry_done
    loci_log INFO prompt-submit "end: carry delivered"
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
