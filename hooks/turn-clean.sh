#!/usr/bin/env bash
# `loci build clean` for LOCI's own build directory, on two events:
#   Stop          every assistant turn: retire old turn trees (keeping the one
#                 that just ended), orphaned scratch, dumps of binaries that are gone.
#   SessionStart  the same plus `--deep`: objects whose source left the project,
#                 pre-upgrade objects, and stale files under ~/.loci/state.
# SessionStart alone would miss anyone living in `claude --continue`, because
# `session-init.sh` is registered with `"matcher": "startup"`.
#
# Rules: NEVER exit 2 (on Stop that blocks the stop and loops for ever), print
# nothing, and gate on the build directory before spawning anything.
# The bash this runs under is decided first, while the payload is still on
# stdin: on bash 3 (stock macOS) this re-executes under a newer bash when one
# is installed, else sets `_LOCI_BASH_LEGACY=1` (AAD-7771; lib/bash-compat.sh).
. "${0%/*}/../lib/bash-compat.sh" 2>/dev/null || :

set -u

# The shared logger and the forkless JSON reader. Stubbed when the logger is
# missing so no call site below has to test for it; outside dev mode every call
# is an immediate return.
case "$0" in
    */*)
        . "${0%/*}/../lib/loci_log.sh" 2>/dev/null || true
        . "${0%/*}/../lib/loci_json.sh" 2>/dev/null || true
        . "${0%/*}/../lib/loci_failfast.sh" 2>/dev/null || true
        . "${0%/*}/../lib/loci_host.sh" 2>/dev/null || true
        ;;
esac
command -v loci_log >/dev/null 2>&1 \
    || { loci_log() { :; }; loci_log_session_from_payload() { :; }; }
command -v loci_fail_fast >/dev/null 2>&1 || loci_fail_fast() { return 1; }
command -v loci_host_adapt >/dev/null 2>&1 || loci_host_adapt() { return 1; }

payload=$(cat)
# Copilot sends no `prompt_id`: the host adapter injects the turn id it recorded
# at UserPromptSubmit, so the `--turn=` below names this turn's tree as it does
# under Claude Code; on SessionStart it sweeps its old session files instead
# (lib/loci_host.sh, AAD-7781). Outside Copilot the line touches nothing.
loci_host_adapt "$payload" && payload="$LOCI_HOST_PAYLOAD"

# The session id comes off the payload just read — stdin is consumed by now and
# nothing may read it again.
loci_log_session_from_payload "$payload"
_tc_state="skipped"
trap 'loci_log INFO turn-clean "end: $_tc_state (hook rc=$?)"' EXIT

command -v loci_json_load >/dev/null 2>&1 \
    || { _tc_state="skipped (lib/loci_json.sh did not source)"; exit 0; }

# Three fields, forklessly. All three are near the front of the payload — this
# event carries no tool input at all — so the bounded prefix `loci_json_load`
# parses holds every one of them.
loci_json_load "$payload"
event=$(loci_json_get hook_event_name)
loci_log INFO turn-clean "start: $event"

# The writer's spelling of the project root: `pre-edit-hook.sh` passes this same
# payload `cwd` to `build snapshot --project-root`. Walking up to the git top
# level instead once swept a different directory than the snapshot wrote.
root=$(loci_json_get cwd)
[ -n "$root" ] || root="${CLAUDE_PROJECT_DIR:-}"
[ -n "$root" ] || root="$PWD"

# The one build root there is (T14): a project whose only LOCI directory is a
# pre-move `.loci-build/` has nothing this CLI would clean.
# Only a spawn gate, so looser than the CLI's walk, which decides; same stops.
_tc_home=$(cd "${HOME:-/nonexistent}" 2>/dev/null && pwd -P) || _tc_home=""
_tc_d=$(cd "$root" 2>/dev/null && pwd -P) || _tc_d=""
# $HOME by identity as well as by spelling. Under Git Bash a `cd` into the
# payload's native `C:\…\Temp\…` answers `pwd -P` through the mount table
# (`/tmp/…`) while `$HOME` keeps its `/c/…` form, and a case variant is one more
# spelling of the same directory; the string compare missed both and adopted
# `~/.loci/build` as a project. `-ef` is the builtin device:inode test, the rule
# `_loci_find_recipe` stops at $HOME by, and spawns nothing.
#
# With the same refusal: a volume that reports inode 0 for every directory
# (FAT/exFAT, some SMB and virtual mounts; why `_inode_key` rejects `:0`) would
# match every directory on it to $HOME and stop the walk at its first level for
# every project there, silently. So `-ef` is trusted only where it tells $HOME
# apart from its parent and from `~/.loci`, decided once, with no process.
_tc_home_up="${_tc_home%/*}"
[ -n "$_tc_home_up" ] || _tc_home_up="/"
_tc_home_ef=""
if [ -n "$_tc_home" ] && ! [ "$_tc_home" -ef "$_tc_home_up" ] \
   && ! [ "$_tc_home" -ef "$_tc_home/.loci" ]; then
    _tc_home_ef=1
fi
_tc_seen=""
_tc_i=0
while [ -n "$_tc_d" ] && [ "$_tc_i" -lt 64 ]; do
    if [ -n "$_tc_home" ] && { [ "$_tc_d" = "$_tc_home" ] \
       || { [ -n "$_tc_home_ef" ] && [ "$_tc_d" -ef "$_tc_home" ]; }; }; then
        break
    fi
    if [ -d "$_tc_d/.loci/build" ]; then _tc_seen=1; break; fi
    [ -e "$_tc_d/.git" ] && break
    _tc_up="${_tc_d%/*}"
    [ -n "$_tc_up" ] || _tc_up="/"
    [ "$_tc_up" = "$_tc_d" ] && break
    _tc_d="$_tc_up"
    _tc_i=$((_tc_i + 1))
done
[ -n "$_tc_seen" ] || { _tc_state="skipped (no .loci/build at or above $root)"; exit 0; }

# Appended, not prepended: a `loci` already on PATH is a deliberate one.
export PATH="$PATH:${HOME:-}/.local/bin"
export PYTHONIOENCODING=utf-8
command -v loci >/dev/null 2>&1 || { _tc_state="skipped (loci absent)"; exit 0; }

# `prompt_id` names the tree this turn's captures went into; passing it keeps that
# tree whatever the retention count says, for a background task still reading it.
turn=$(loci_json_get prompt_id)

# `--flag=value`: `prompt_id` is undocumented and a value starting with `-` makes
# argparse exit 2, which is indistinguishable here from "no such verb".
# No `--project-root`: the CLI resolves from its cwd with the capture's walk, and
# no path converted here reaches Python (the `/c/...` trap above).
cd "$root" 2>/dev/null || { _tc_state="skipped (cannot enter $root)"; exit 0; }
args=()
if [ "$event" = "SessionStart" ]; then
    args+=( --deep )
elif [ -n "$turn" ]; then
    args+=( "--turn=$turn" )
fi

# The turn's intent note dies with the turn. `loci hook prompt-submit` wrote the
# user's prompt text there so that a post-edit firing late in a long turn can
# still see what the turn was for; once the turn is over nothing may read it
# again, and it holds prompt text — credentials people paste, customer names,
# unreleased detail — so it is deleted rather than left to age out.
#
# Deleted HERE and not by the CLI's `build clean`, because it does not live under
# `--project-root`: it is in the state directory, deliberately outside the repo
# so prompt text can never reach a commit.
#
# This is the honest expiry and not the only one. A Stop hook does not fire when
# the process is killed or crashes, so `loci hook prompt-submit` also sweeps
# notes older than its retention when it writes a new one. Without that backstop
# the notes that survive are exactly the ones from sessions that ended badly,
# which is the set nobody goes looking for.
if [ "$event" != "SessionStart" ] && [ -n "$turn" ]; then
    _intent_dir="${LOCI_STATE_DIR:-${HOME:-}/.loci/state}"
    # No glob, no `rm -r`: one exact path built from a token that is already
    # JSON-extracted and non-empty. A `turn` containing a slash would name a
    # file in another directory, so anything but the flat basename is refused.
    case "$turn" in
        */*|.|..) ;;
        *) rm -f "${_intent_dir}/turn-intent-${turn}.txt" 2>/dev/null ;;
    esac
fi

# No retry on exit 2: here it means the CLI has no `build clean` at all, and a
# retry without `--turn` would drop the one guarantee the flag buys.
#
# Fast-fail keeps the output it otherwise discards, and reports a non-zero exit
# instead of recording it in a log nobody reads in production. `timeout` returns
# 124, which is a LOCI failure like any other and is reported as one.
#
# Stock macOS has no `timeout` (and no `gtimeout` without coreutils), so the
# `else` arms run the verb unbounded there; what bounds them is the 15 s this
# hook has in `hooks.json`, and a kill at that line is silent (AAD-7771).
if loci_fail_fast; then
    if command -v timeout >/dev/null 2>&1; then
        _tc_out=$(timeout 10 loci build clean ${args[@]+"${args[@]}"} 2>&1)
    else
        _tc_out=$(loci build clean ${args[@]+"${args[@]}"} 2>&1)
    fi
    _tc_rc=$?
    _tc_state="build clean rc=$_tc_rc"
    [ "$_tc_rc" -eq 0 ] \
        || loci_fail_fast_emit turn-clean Stop "loci build clean" "$_tc_rc" "$_tc_out"
    exit 0
fi
if command -v timeout >/dev/null 2>&1; then
    timeout 10 loci build clean ${args[@]+"${args[@]}"} >/dev/null 2>&1
else
    loci build clean ${args[@]+"${args[@]}"} >/dev/null 2>&1
fi
_tc_state="build clean rc=$?"
exit 0
