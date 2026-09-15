#!/usr/bin/env bash
# Stop hook: while a contract draft is pending, tell the USER every turn.
#
# A drafted bound nobody applied is a requirement nobody set, and the skill can
# only mention it in the turn that drafted it — after that the agent's memory of
# it is one compaction away from gone. This reads the draft file instead, so the
# nudge repeats until `contract accept` consumes it or `draft clear` deletes it.
#
# Two hard rules for a Stop hook:
#   * `systemMessage` is the only field the USER sees. Plain stdout goes to the
#     debug log on this event, so printing the reminder is the same as not
#     printing it.
#   * NEVER exit 2. On Stop that blocks the stop and continues the conversation —
#     a pending draft would become an infinite loop. This hook always exits 0.
set -u

# The shared logger and the forkless JSON reader. Stubbed when the logger is
# missing so no call site below has to test for it; outside dev mode every call
# is an immediate return.
case "$0" in
    */*)
        . "${0%/*}/../lib/loci_log.sh" 2>/dev/null || true
        . "${0%/*}/../lib/loci_json.sh" 2>/dev/null || true
        . "${0%/*}/../lib/loci_failfast.sh" 2>/dev/null || true
        ;;
esac
command -v loci_log >/dev/null 2>&1 \
    || { loci_log() { :; }; loci_log_session_from_payload() { :; }; }
command -v loci_fail_fast >/dev/null 2>&1 || loci_fail_fast() { return 1; }

payload=$(cat)

# The session id comes off the payload just read — stdin is consumed by now and
# nothing may read it again.
loci_log_session_from_payload "$payload"
loci_log INFO draft-nudge "start: Stop draft check"
_dn_state="no draft in reach"
trap 'loci_log INFO draft-nudge "end: $_dn_state (hook rc=$?)"' EXIT

# The reader gates everything: it reads the count and it writes the escaped
# `systemMessage`. Without it, stay silent rather than risk a malformed payload.
command -v loci_json_load >/dev/null 2>&1 \
    || { _dn_state="skipped (lib/loci_json.sh did not source)"; exit 0; }

# Where to START looking. Not where the draft is — the walk below decides that,
# and it is what makes this ladder's exact rungs stop mattering.
#
# `git rev-parse` is gone from here. It was reached only when
# `$CLAUDE_PROJECT_DIR` was unset, and when it FAILED (an ordinary directory that
# is not a repo) it left `root` empty and the fallback took this process's own
# `$PWD` — discarding the payload's `cwd`, which is the one thing that actually
# names the session. The walk reaches the git top level anyway, without the spawn.
root="${CLAUDE_PROJECT_DIR:-}"
if [ -z "$root" ]; then
    loci_json_load "$payload"
    root=$(loci_json_get cwd)
fi
[ -n "$root" ] || root="$PWD"

# Cheap gate first: the draft is absent on almost every turn, and this hook runs
# on all of them. No file, no `loci` spawn.
#
# ONE path since T14. `contract` writes and reads `.loci/build/contract.draft.yaml`,
# and migrates a pre-move draft up to it on first contact — a draft a pre-move
# CLI left under `.loci-build/` is not pending until a verb has copied it, and
# nudging over it first would tell the user about a draft `draft show` cannot
# yet see. The gate is a gate and nothing below it names a path: which file's
# CONTENT is reported stays the CLI's answer.
#
# ⚠ AND THE DIRECTORY MATTERS AS MUCH AS THE FILENAME — see the gate below.
# ⚠ THE GATE IS A GATE. It answers "could there be a draft for this session"
# and nothing else; WHICH draft, and whether `loci contract accept` can apply it,
# is the CLI's answer and is asked below by running `draft show` from the
# SESSION's own directory — the same directory, and therefore the same resolution,
# that `accept` will use when the user runs it.
#
# Two earlier shapes were both wrong for the same reason: they made this hook hold
# an opinion about the root.
#
#   * Testing only `$CLAUDE_PROJECT_DIR` missed a monorepo entirely. That is where
#     the session is LAUNCHED; every `contract` verb resolves to the git top level,
#     so a draft written from `repo/firmware` lands at `repo/` and was never
#     reported, on any turn, ever.
#   * Climbing to the first directory that HOLDS a draft, or to the nearest `.git`
#     and stopping there, replaced that with a worse failure: in a non-git tree the
#     CLI does not climb, so the hook nagged about a draft `accept` answers
#     "nothing to accept" for — every Stop, for ever, naming the only command it
#     offers. And `[ -e .git ]` is not the same question as "git will accept this
#     as a repository": an empty `.git` directory, a truncated `.git` file, a
#     worktree whose gitdir has been deleted — git refuses all three and falls
#     back to the cwd, while the hook did not, in both directions.
#
# So the walk below is deliberately OVER-permissive: it looks for a draft at every
# level from the session up to the nearest `.git` INCLUSIVE, and any hit opens the
# gate. Being too permissive costs one `loci` spawn on a turn that then says
# nothing; being too strict costs a bound the user is never told about. It spawns
# nothing itself — parameter expansion and one file test per level — so the common
# turn, which has no draft anywhere, is unchanged.
#
# No `$HOME` ceiling: it would be one more opinion, and a reviewer showed the
# comparison was inert on Windows anyway (`C:/Users/User` never equals
# `/c/Users/User`). A stray draft in a home directory opens the gate and the CLI
# then says `pending: 0`, which is the right answer arrived at by the right party.
_dg_seen=""
_dg_d="${root//\\//}"
_dg_i=0
while [ "$_dg_i" -lt 64 ]; do
    if [ -f "$_dg_d/.loci/build/contract.draft.yaml" ]; then
        _dg_seen=1
        break
    fi
    # Tested AFTER the candidate, so a draft at the top level itself still opens
    # the gate. `[ -e ]`, not `[ -d ]`: a worktree and a submodule have a `.git`
    # FILE.
    [ -e "$_dg_d/.git" ] && break
    _dg_up="${_dg_d%/*}"
    [ "$_dg_up" != "$_dg_d" ] && [ -n "$_dg_up" ] || break
    _dg_d="$_dg_up"
    _dg_i=$((_dg_i + 1))
done
[ -n "$_dg_seen" ] || exit 0

# A hook's PATH does not always carry the pip user-scripts dir where `loci`
# installs. Appended, not prepended: a `loci` already on PATH is a deliberate one
# (an editable dev checkout, a venv) and must win over the pip-installed copy.
# ${HOME:-} — a bare $HOME under `set -u` (line 15) aborts this hook where HOME is
# unset: Windows sets USERPROFILE, and hooks run non-interactive so no profile is
# sourced. Same defect the edge hooks had.
export PATH="$PATH:${HOME:-}/.local/bin"
export PYTHONIOENCODING=utf-8
command -v loci >/dev/null 2>&1 || { _dn_state="skipped (loci absent)"; exit 0; }
_dn_state="draft candidate seen; the CLI said nothing usable"
# The silent `exit 0` below costs the user every later mention of a draft they
# have to apply, and discards the reason with it. Fast-fail reports the reason.
if loci_fail_fast; then
    out=$(cd "$root" && loci contract draft show 2>&1)
    _dn_rc=$?
    if [ "$_dn_rc" -ne 0 ]; then
        _dn_state="halted (draft show rc=$_dn_rc)"
        loci_fail_fast_emit draft-nudge Stop "loci contract draft show" "$_dn_rc" "$out"
        exit 0
    fi
else
    out=$(cd "$root" && loci contract draft show 2>/dev/null) || exit 0
fi
[ -n "$out" ] || exit 0

loci_json_load "$out"
[ "$(loci_json_get ok)" = "true" ] || exit 0

pending=$(loci_json_get pending)
stale=$(loci_json_get stale)
_dn_state="pending=${pending:-0} stale=$stale"
[ "${pending:-0}" -gt 0 ] 2>/dev/null || exit 0

# "2 bounds added, 1 retired" out of the draft's `ops`, counted rather than
# walked: each op names its verb once, so the tally is one pass per verb over the
# envelope and needs no array parsing.
#
# ⚠ THE TOTAL IS CHECKED, and that is what makes the sentence safe to print. A
# draft carrying a verb this version does not know would otherwise be summarised
# as fewer changes than it holds — "1 bound added" over a draft that also
# disables one. When the four recognised verbs do not account for every op, the
# summary is dropped and the count below speaks instead.
_dn_total=$(loci_json_count op)
_dn_sum=0
summary=""
for _dn_pair in add:added edit:changed disable:retired enable:restored; do
    _dn_n=$(loci_json_count op "${_dn_pair%%:*}")
    [ "$_dn_n" -gt 0 ] || continue
    _dn_sum=$((_dn_sum + _dn_n))
    if [ -z "$summary" ]; then
        _dn_noun="bounds"
        [ "$_dn_n" = "1" ] && _dn_noun="bound"
        summary="$_dn_n $_dn_noun ${_dn_pair#*:}"
    else
        summary="$summary, $_dn_n ${_dn_pair#*:}"
    fi
done
[ "$_dn_total" -gt 0 ] && [ "$_dn_sum" = "$_dn_total" ] || summary=""

if [ -z "$summary" ]; then
    changes="changes"
    [ "$pending" = "1" ] && changes="change"
    summary="$pending $changes"
fi

if [ "$stale" = "true" ]; then
    msg="LOCI: contract draft — $summary — but .loci/contract.yaml changed since, so it can no longer be applied. Ask the agent to re-draft it."
else
    msg="LOCI: contract draft not applied — $summary. Nothing is in force until you run:  ! loci contract accept"
fi

printf '{"systemMessage":"%s"}\n' "$(loci_json_escape "$msg")"
_dn_state="nudged (pending=$pending stale=$stale)"
exit 0
