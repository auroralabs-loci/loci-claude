#!/usr/bin/env bash
# Reclaim what LOCI's own `.loci-build` accumulates. Registered on TWO events,
# and the event decides how much it does:
#
#   Stop         once per assistant turn — retire old turn trees, protecting the
#                turn that just ended. Cheap, and it runs constantly.
#   SessionStart the same, plus `--reclaim-objects`: stem-keyed objects from
#                before the layout moved, which no compile writes any more and
#                no lookup finds, but which the project detector still ADVERTISES
#                to skills as measurement candidates. That stage is the only one
#                that deletes something a compile produced, so it belongs to the
#                once-per-session caller and not to the per-turn one.
#
# `loci build snapshot --turn` writes one tree per user turn — the pre-edit copy
# of every file the turn touched, plus any baseline objects rebuilt from them —
# and until phase 03 nothing removed any of it.
#
# Both events, not just SessionStart, because `session-init.sh` is registered with
# `"matcher": "startup"` and therefore does not run on `resume`, `clear` or
# `compact`. A user living in `claude --continue` would never have reached the
# reclamation stage at all.
#
# Three hard rules, the first two shared with `draft-pending-nudge.sh`:
#   * NEVER exit 2. On Stop that blocks the stop and continues the conversation,
#     which for a hook that runs every turn is an infinite loop. Always exit 0.
#   * Print nothing. Plain stdout goes to the debug log on Stop, and on
#     SessionStart anything printed has to be a hook-output document — a reap has
#     nothing to tell the user either way.
#   * Stay cheap on the common turn. It gates on the directory existing before it
#     spawns anything.
set -u

payload=$(cat)

command -v jq >/dev/null 2>&1 || exit 0

event=$(printf '%s' "$payload" | jq -r '.hook_event_name // ""' 2>/dev/null)

# The project root, resolved **the way the writer resolves it** — and since phase
# 10 that is by construction rather than by agreement: `pre-edit-hook.sh` now passes
# this same payload `cwd` to `build snapshot` as `--project-root`, so the two read
# one field instead of each guessing.
#
# An earlier version of this hook walked that up to the git top level instead,
# which is a different directory whenever a session runs in a subdirectory of a
# repo (`repo/firmware` in a monorepo): the snapshot would write
# `repo/firmware/.loci-build` while the reap swept `repo/.loci-build`, the gate
# below would find nothing, and retention would never run for that project —
# silently, for ever. Keep the two spellings identical. The fallbacks below run in
# the writer's order too — payload `cwd`, then `CLAUDE_PROJECT_DIR`, then this
# process's own directory; the writer spells that last rung by omitting the flag
# and letting the CLI take `Path.cwd()`, which is the same directory.
root=$(printf '%s' "$payload" | jq -r '.cwd // ""' 2>/dev/null)
[ -n "$root" ] || root="${CLAUDE_PROJECT_DIR:-}"
[ -n "$root" ] || root="$PWD"

# Cheap gate. Most turns in most projects never create one of these.
[ -d "$root/.loci-build" ] || exit 0

# ${HOME:-} — a bare $HOME under `set -u` aborts where HOME is unset. Appended,
# not prepended: a `loci` already on PATH is a deliberate one (an editable dev
# checkout, a venv) and must win over the pip-installed copy.
export PATH="$PATH:${HOME:-}/.local/bin"
export PYTHONIOENCODING=utf-8
command -v loci >/dev/null 2>&1 || exit 0

# `prompt_id` is on every hook payload, identical for every event within one user
# turn, and it is the same token `pre-edit-hook.sh` passed to `build snapshot
# --turn` — so it names the tree this turn's captures went into.
#
# It is passed so that tree is KEPT. A Stop hook fires when the main agent stops,
# which is not the same as "nothing is running": a background task can still be
# reading the Before this turn captured. Retention would keep it anyway at the
# default count, so this is the guarantee that survives someone tightening the
# count later.
turn=$(printf '%s' "$payload" | jq -r '.prompt_id // ""' 2>/dev/null)

# `--flag=value`, not `--flag value`. `prompt_id` is undocumented — it was
# established by probing real sessions — and argparse reads a value beginning
# with `-` as the next OPTION, answering "expected one argument" and exiting 2.
# That is indistinguishable here from "this CLI has no `build reap`", so the
# whole reap would silently stop running for every project. The joined form has
# no such reading.
args=( "--project-root=$root" )
if [ "$event" = "SessionStart" ]; then
    args+=( --reclaim-objects )
elif [ -n "$turn" ]; then
    args+=( "--turn=$turn" )
fi

# NOT the `rc == 2 → retry with fewer flags` pattern the edge hooks use, and
# deliberately. There, exit 2 means "this CLI has the verb but not the flag" and
# dropping the flag restores the older behaviour. Here exit 2 means the CLI has
# no `build reap` AT ALL — the pin installs an exact `==` and no released CLI has
# this — so there is nothing to retry with, and the no-op is exactly the previous
# behaviour. Retrying without `--turn` would be worse than doing nothing: it
# would drop the one guarantee the flag buys.
#
# Bounded where `timeout` exists. SessionStart sits on the path to the user's
# first prompt and Stop has a 10 s budget of its own, so a `.loci-build` on a
# stalled network mount must cost seconds, not the whole budget.
if command -v timeout >/dev/null 2>&1; then
    timeout 10 loci build reap "${args[@]}" >/dev/null 2>&1 || true
else
    loci build reap "${args[@]}" >/dev/null 2>&1 || true
fi

exit 0
