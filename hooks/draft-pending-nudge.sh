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

payload=$(cat)

# jq gates everything: it is needed to read the count and to emit safely-escaped
# JSON. Without it, stay silent rather than risk a malformed payload.
command -v jq >/dev/null 2>&1 || exit 0

root="${CLAUDE_PROJECT_DIR:-}"
if [ -z "$root" ]; then
    cwd=$(printf '%s' "$payload" | jq -r '.cwd // ""' 2>/dev/null)
    root=$(cd "${cwd:-$PWD}" 2>/dev/null && git rev-parse --show-toplevel 2>/dev/null)
fi
[ -n "$root" ] || root="$PWD"

# Cheap gate first: the draft is absent on almost every turn, and this hook runs
# on all of them. No file, no `loci` spawn.
[ -f "$root/.loci-build/contract.draft.yaml" ] || exit 0

# A hook's PATH does not always carry the pip user-scripts dir where `loci`
# installs. Appended, not prepended: a `loci` already on PATH is a deliberate one
# (an editable dev checkout, a venv) and must win over the pip-installed copy.
# ${HOME:-} — a bare $HOME under `set -u` (line 15) aborts this hook where HOME is
# unset: Windows sets USERPROFILE, and hooks run non-interactive so no profile is
# sourced. Same defect the edge hooks had.
export PATH="$PATH:${HOME:-}/.local/bin"
export PYTHONIOENCODING=utf-8
command -v loci >/dev/null 2>&1 || exit 0
out=$(cd "$root" && loci contract draft show 2>/dev/null) || exit 0
[ -n "$out" ] || exit 0

ok=$(printf '%s' "$out" | jq -r '.ok // false' 2>/dev/null) || exit 0
[ "$ok" = "true" ] || exit 0

pending=$(printf '%s' "$out" | jq -r '.data.pending // 0')
stale=$(printf '%s' "$out" | jq -r '.data.stale // false')
[ "$pending" -gt 0 ] 2>/dev/null || exit 0

summary=$(printf '%s' "$out" | jq -r '
    def noun($n): if $n == 1 then "bound" else "bounds" end;
    (.data.ops // []) as $ops
    | [{k:"add",     v:"added"},
       {k:"edit",    v:"changed"},
       {k:"disable", v:"retired"},
       {k:"enable",  v:"restored"}]
    | map(. as $e
          | ($ops | map(select(.op == $e.k)) | length) as $n
          | select($n > 0) | {n: $n, v: $e.v})
    | . as $parts
    | if ($parts | map(.n) | add // 0) != ($ops | length) or ($ops | length) == 0
      then ""
      else $parts | to_entries
           | map(if .key == 0
                 then "\(.value.n) \(noun(.value.n)) \(.value.v)"
                 else "\(.value.n) \(.value.v)" end)
           | join(", ")
      end' 2>/dev/null) || summary=""

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

jq -n --arg m "$msg" '{systemMessage: $m}'
exit 0
