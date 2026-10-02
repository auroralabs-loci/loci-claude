#!/usr/bin/env bash
# SubagentStart hook: under GitHub Copilot CLI, where a subagent is a session
# of its own, record that this session just started one, so that the child's
# first prompt inherits this turn's id instead of minting one (lib/loci_host.sh,
# the SUBAGENTS paragraph; AAD-7788). Under Claude Code a subagent's payloads
# already carry the parent's `prompt_id`, so there is nothing to record: the
# payload is read there only when the log is on (for its session id, as the
# thin hooks read theirs), and the adapter is one environment test.
#
# Prints nothing, ever, and NEVER exits non-zero: Copilot's hooks are
# fail-closed and a failure here would deny the spawn. The payload is
# Copilot's own shape — `sessionId`, camelCase, no `hook_event_name`
# (1.0.91) — which the adapter reads.
# The bash this runs under is decided first, while the payload is still on
# stdin: on bash 3 (stock macOS) this re-executes under a newer bash when one
# is installed, else sets `_LOCI_BASH_LEGACY=1` (AAD-7771; lib/bash-compat.sh).
. "${0%/*}/../lib/bash-compat.sh" 2>/dev/null || :

set -u

# The shared logger and the host adapter. Stubbed when missing so no call site
# below has to test for it; outside dev mode every log call is an immediate
# return, and outside Copilot the adapter is one environment test.
case "$0" in
    */*)
        . "${0%/*}/../lib/loci_log.sh" 2>/dev/null || true
        . "${0%/*}/../lib/loci_json.sh" 2>/dev/null || true
        . "${0%/*}/../lib/loci_host.sh" 2>/dev/null || true
        ;;
esac
command -v loci_log >/dev/null 2>&1 \
    || { loci_log() { :; }; loci_hook_payload_read() { return 1; }; }
command -v loci_host_subagent_start >/dev/null 2>&1 || loci_host_subagent_start() { return 1; }
command -v loci_host_reads_payload >/dev/null 2>&1 || loci_host_reads_payload() { return 1; }

# stdin is read when the log is on (the library's rule: it sets the log's
# session from it) and under the host that has something to record; under
# Claude Code in production it is left where it is.
payload=""
loci_hook_payload_read && payload="$LOCI_HOOK_PAYLOAD"
if [ -z "$payload" ] && loci_host_reads_payload; then
    payload=$(cat)
fi
loci_log INFO subagent-start "start: SubagentStart"
if loci_host_subagent_start "$payload"; then
    loci_log INFO subagent-start "end: recorded"
else
    loci_log INFO subagent-start "end: nothing to record"
fi
exit 0
