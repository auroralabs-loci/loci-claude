#!/usr/bin/env bash
# LOCI plugin — fast-fail mode, the QA switch. `LOCI_FAIL_FAST=1` turns every
# recovery around a failing `loci` call into a halt: the command, its exit code
# and its output are reported verbatim, and nothing retries, routes around or
# repairs the failure. Off, every one of those recoveries behaves exactly as it
# always has — the switch is invisible until it is set.
#
# WHY IT EXISTS: the recoveries hide LOCI's own bugs. A session whose pre-scan,
# snapshot or turn stamp failed reads as one that worked, and QA never learns the
# CLI errored underneath. `LOCI_ENV` is not the gate (that is
# `lib/loci_log.sh`'s `loci_fail_fast`, read independently): QA tests production
# artifacts, so a switch that needed a dev build would not test what ships.
#
# ⚠ A HOOK DOES NOT HALT BY ITS EXIT CODE, and must not: a non-zero exit from an
# edge hook reads to the model as a tool failure, and `contract-guard.sh` is
# fail-open on a 5 s budget. So a hook says it on the channel it already owns —
# `additionalContext`, or `systemMessage` on Stop — logs it at ERROR, and still
# exits 0. The halt is the model's to perform, and the notice is what instructs
# it. The one rule that covers every skill is prose, in the shared runtime
# contract's `#fail-fast` section, plus one line of session context that
# `hooks/session-init.sh` injects only while the switch is on.

[ -n "${_LOCI_FAILFAST_SOURCED:-}" ] && return 0
_LOCI_FAILFAST_SOURCED=1

# `loci_fail_fast` (the predicate) is in the logger, beside `loci_is_dev`, so
# that the log destination can be resolved at source time. Both libraries here
# guard against a second source, so a caller that has already taken them pays
# nothing.
case "${BASH_SOURCE[0]:-}" in
    */*)
        . "${BASH_SOURCE[0]%/*}/loci_log.sh" 2>/dev/null || true
        . "${BASH_SOURCE[0]%/*}/loci_json.sh" 2>/dev/null || true
        ;;
esac

# How much of a failing command's output reaches the log line. The notice itself
# carries all of it; a log LINE is one line, so this one is truncated and the
# newlines are folded rather than left to forge lines of their own.
_LOCI_FF_LOG_MAX=400

# Public API: loci_fail_fast_notice <source-tag> <command> <exit-code> <output>
#
# The text a hook puts on its own channel, and the ERROR line that makes it
# attributable to a session in loci.log.
loci_fail_fast_notice() {
    local src="$1" cmd="$2" rc="$3" out="$4" flat
    flat="${out//$'\n'/ | }"
    [ "${#flat}" -gt "$_LOCI_FF_LOG_MAX" ] && flat="${flat:0:$_LOCI_FF_LOG_MAX}…"
    loci_log ERROR "$src" "fail-fast: \`$cmd\` exit $rc: ${flat:-no output}"
    printf 'LOCI fast-fail is on (LOCI_FAIL_FAST). `%s` failed with exit %s. Stop this turn now: report that command, its exit code and the output below to the user verbatim, and say the LOCI analysis did not run. Do not retry it, do not route around it, do not repair it, and do not make another loci call this turn. Output:\n%s' \
        "$cmd" "$rc" "${out:-(no output)}"
}

# Public API: loci_fail_fast_emit <source-tag> <hook-event> <command> <exit-code> <output>
#
# The notice, on the channel the hook already owns. `Stop` gets `systemMessage`,
# the only field a user sees there and the only one that event carries; every
# other event gets `additionalContext`, which is what the model reads.
loci_fail_fast_emit() {
    local src="$1" event="$2" notice
    notice=$(loci_fail_fast_notice "$src" "$3" "$4" "$5")
    case "$event" in
        Stop) printf '{"systemMessage":"%s"}\n' "$(loci_json_escape "$notice")" ;;
        *) loci_json_hook_output "$event" "$notice" ;;
    esac
}

# Public API: loci_fail_fast_run <source-tag> <hook-event> <verb…>
#
# For the thin hooks that hand their stdin to a `loci` verb and print what it
# prints. They ignore its exit code, which is the whole of what fast-fail wants
# to see, so here the code and the streams are captured.
#
# stderr goes to a FILE under the state directory rather than into stdout: a
# traceback folded into stdout corrupts the JSON document the harness parses, and
# a traceback is exactly the failure QA is looking for. Where that file cannot be
# written, the verb runs with its stderr untouched and only the exit code is
# reported — a degraded notice beats a corrupted hook output.
loci_fail_fast_run() {
    local src="$1" event="$2"; shift 2
    local errf out rc err detail
    errf="${LOCI_STATE_DIR:-${HOME:-}/.loci/state}/failfast-$$.err"
    : > "$errf" 2>/dev/null || errf=""
    if [ -n "$errf" ]; then
        if [ -n "${LOCI_HOOK_PAYLOAD:-}" ]; then
            out=$(printf '%s' "$LOCI_HOOK_PAYLOAD" | "$@" 2>"$errf")
        else
            out=$("$@" 2>"$errf")
        fi
        rc=$?
        err=$(cat "$errf" 2>/dev/null)
        rm -f "$errf" 2>/dev/null
        [ "$rc" -eq 0 ] && [ -n "$out" ] && printf '%s\n' "$out"
    else
        if [ -n "${LOCI_HOOK_PAYLOAD:-}" ]; then
            printf '%s' "$LOCI_HOOK_PAYLOAD" | "$@"
        else
            "$@"
        fi
        rc=$?
        out="" err="stderr was not captured: the state directory is not writable."
    fi
    if [ "$rc" -eq 0 ]; then
        loci_log INFO "$src" "end: $* rc=0"
        return 0
    fi
    detail="${err:+stderr: $err}"
    [ -n "$out" ] && detail="${detail:+$detail$'\n'}stdout: $out"
    loci_fail_fast_emit "$src" "$event" "$*" "$rc" "$detail"
    return 0
}

# Public API: loci_fail_fast_absent <source-tag> <hook-event> <command>
#
# `loci` is not on PATH where the hook needed it. Said instead of repaired: the
# edge hooks answer this by starting a background install, and "attempt no fix"
# is the mode's whole point.
loci_fail_fast_absent() {
    loci_fail_fast_emit "$1" "$2" "$3" 127 \
        "loci is not on PATH. No background install was started: fast-fail is on."
}
