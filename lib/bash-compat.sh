#!/usr/bin/env bash
# LOCI plugin — the bash a hook runs under, decided before it reads anything.
#
# `hooks.json` starts every hook as `bash -c 'exec bash <script>'` — the outer
# `bash -c` is what survives PowerShell, which reads the line under GitHub
# Copilot CLI on Windows (AAD-7780); the inner `bash` is the one this file runs
# under — and on a Mac with no Homebrew that `bash` is /bin/bash 3.2.57 (2007).
# The hooks and `lib/loci_json.sh` are written for bash 4+, and two things
# about 3.2 matter (AAD-7771, measured on the mac-mini and on a 3.2.57 built
# from source):
#
#   * `${v//pat/rep}` costs about 2 ms PER MATCH over a 16 KB string and grows
#     with the SQUARE of the length — 31 ms per match at 64 KB, 19 s for a
#     16 KB string of matches. bash 4+ does the same in 85 ms. The contract
#     guard's 5 s budget over a 64 KB field is 150 matches away from a kill,
#     and a killed PreToolUse hook fails OPEN.
#   * a quoted replacement keeps its quote characters (see the note at the top
#     of `lib/loci_json.sh`).
#
# So a hook that finds itself under bash 3 first looks for a newer bash in the
# places macOS package managers install one and re-executes itself there. The
# payload is still on stdin at this point — every hook sources this file BEFORE
# its `payload=$(cat)` — so nothing is lost across the `exec`. When no newer
# bash exists the hook carries on under 3.2 with `_LOCI_BASH_LEGACY=1` set, and
# the readers that have a size-dependent cost lower their caps to what 3.2
# decides inside the budget (4 KB — see `LOCI_JSON_MAX` in `lib/loci_json.sh`
# and `_R2_MAX_TOKENISE` in `hooks/contract-guard.sh`), erring toward deny.
# `session-init.sh` says so once.
#
# Nothing here forks on bash 4+: one arithmetic test and it is over. On bash 3
# each candidate costs one fork for its version probe, and only when the file
# exists — a probe, not a trust in the path: a `bash` that is not bash 4+ is
# skipped, not exec'd.
#
# `$0` must name the script: the hooks are started as `exec bash /abs/hook.sh`,
# and a sourcing context where `$0` is not a readable file (an interactive
# shell, a `bash -c`) is left alone. The `exec` happens at top level, where
# `$@` is the SCRIPT's argument list; a function's `$@` would be its own.

[ -n "${_LOCI_BASH_COMPAT_SOURCED:-}" ] && return 0
_LOCI_BASH_COMPAT_SOURCED=1

_LOCI_BASH_CANDIDATES="/opt/homebrew/bin/bash /usr/local/bin/bash /opt/local/bin/bash"
_LOCI_BASH_LEGACY=""
_LOCI_BASH_NEWER=""

# $1 = the running bash's major version; $2… = candidate paths. Sets
# `_LOCI_BASH_NEWER` to the first candidate that is bash 4+ and returns 0;
# returns 1 when the running bash is already 4+ or no candidate qualifies.
_loci_bash_newer() {
    local _major="$1" _b _v
    shift
    _LOCI_BASH_NEWER=""
    [ "$_major" -lt 4 ] 2>/dev/null || return 1
    for _b in "$@"; do
        [ -x "$_b" ] || continue
        _v=$("$_b" -c 'printf %s "${BASH_VERSINFO[0]}"' 2>/dev/null) || continue
        [ "$_v" -ge 4 ] 2>/dev/null || continue
        _LOCI_BASH_NEWER="$_b"
        return 0
    done
    return 1
}

if [ "${BASH_VERSINFO[0]:-0}" -lt 4 ]; then
    # Once. The probe makes a second round impossible in production, but an
    # `exec` that could recur is a hang with no error, so it is barred twice.
    # shellcheck disable=SC2086
    if [ -z "${_LOCI_BASH_REEXECED:-}" ] && [ -n "${0:-}" ] && [ -r "$0" ] \
        && _loci_bash_newer "${BASH_VERSINFO[0]:-0}" $_LOCI_BASH_CANDIDATES; then
        export _LOCI_BASH_REEXECED=1
        exec "$_LOCI_BASH_NEWER" "$0" "$@"
    fi
    _LOCI_BASH_LEGACY=1
fi
