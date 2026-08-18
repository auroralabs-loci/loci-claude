#!/usr/bin/env bash
# PreToolUse edge adapter (Claude Code → loci): extract {file_path, code} from
# the hook payload and hand them to `loci build snapshot` (freeze the source's
# .o for post-edit diffing) and `loci scan` (static call-graph pre-scan). No
# analysis here — only jq field mapping. Advisory: always exits 0, never blocks.
set -u
export PYTHONIOENCODING=utf-8
# ${HOME:-} — a bare $HOME under `set -u` exits 1 where HOME is unset, and a
# non-zero exit from an edge hook reads to the model as a tool failure.
export PATH="${HOME:-}/.local/bin:$PATH"
command -v jq >/dev/null 2>&1 || exit 0

payload=$(cat)
fp=$(printf '%s' "$payload" | jq -r '.tool_input.file_path // ""')

# Only C/C++/Rust sources — skips plan files, markdown, configs, etc.
#
# This list must not be NARROWER than the CLI's `_SNAPSHOT_SOURCE_EXTS`, and that is
# a correctness rule rather than tidiness. The CLI now captures a pre-edit copy of
# every extension it lists, and rebuilds a header edit's baseline out of those
# copies. A file this hook filters out is one that never gets captured — and the
# reconstruction then reads it at its CURRENT, edited content while every other
# check passes, producing an object that mixes pre-edit and post-edit sources and is
# reported as a clean Before. Widening costs one no-op `loci` call per edit of a
# file that turns out to have no object; narrowing costs a silent wrong number.
case "$fp" in
    *.c|*.cc|*.cpp|*.cxx|*.c++|*.rs) ;;
    *.h|*.hpp|*.hxx|*.h++|*.hh|*.inc|*.ipp|*.tcc) ;;
    # `.S` is preprocessed (so it reads headers) and `.s` is not, but a
    # case-insensitive filesystem cannot tell the two names apart — so both, and the
    # CLI decides.
    *.S|*.s) ;;
    *) exit 0 ;;
esac
# Skip plan/settings files that carry a source-ish extension. Kept in step with
# `post-edit-hook.sh`, which has had this since phase 04 — the two hooks are a pair
# and a file one of them acts on while the other ignores it is a state neither was
# designed for. The concrete cost of the gap was small (a snapshot and a pre-scan
# over a file with no object), but the shape is the one 06d found five copies of:
# two filters that are meant to agree and are separately maintained.
case "$fp" in
    */.claude/plans/*|*/.claude/settings*) exit 0 ;;
esac

# Install-on-miss: loci is needed now but absent — kick a background install
# (self-locking) and skip this run. Covers plugins installed mid-session.
if ! command -v loci >/dev/null 2>&1; then
    nohup bash "$(dirname "$0")/ensure-loci-cli.sh" </dev/null >/dev/null 2>&1 &
    exit 0
fi

# The incoming edit content (not yet written to disk), per write-family tool, and
# WHAT that content is. The two are read together because they are one fact: a
# Write's `content` is a whole file, an Edit's `new_string` is the replacement text
# alone, and `loci scan` applies different rules to each. Until now this hook sent
# both without saying which, so the CLI applied its default (`file`) to every Edit
# — and the call-graph checks are absence tests, so "no early-return base case"
# was asserted about a function on the evidence of the few lines that replaced part
# of it. The finding still comes back; it now says what it could see.
#
# `fragment` is the default for anything else because it is the kind with no
# whole-file rules: a wrong `file` claims more than it knows, a wrong `fragment`
# only declines to conclude.
kind=$(printf '%s' "$payload" | jq -r '
    if (.tool_input? | objects | has("content")) then "file" else "fragment" end' \
    2>/dev/null)
kind="${kind:-fragment}"
code=$(printf '%s' "$payload" | jq -r '
    .tool_name as $tn | .tool_input as $ti |
    if   $tn == "Write" then ($ti.content // "")
    elif $tn == "Edit"  then ($ti.new_string // "")
    else "" end')

# Where the project starts, as the payload states it — not as this process's CWD
# happens to be, and not as the git top level would say.
#
# `build snapshot` resolves its root from `--project-root` or `Path.cwd()`, and
# passing neither is how the capture and every reader of it came to be guessing
# separately: `hooks/turn-reap.sh` had to reproduce the writer's guess to sweep the
# right tree, and `build compile --baseline` refuses when the two disagree (it fails
# closed, but it fails). A session running in a subdirectory of a repo is all it
# takes. Stating it once, here, is what lets both of those stop guessing.
#
# The payload's NATIVE spelling, passed through unchanged: a `/c/...` conversion
# reaches Python on Windows as a rooted path on the CURRENT DRIVE, i.e. `C:\c\...`.
# Same rule, same reason, as `turn-reap.sh`.
root=$(printf '%s' "$payload" | jq -r '.cwd // ""' 2>/dev/null)
[ -n "$root" ] || root="${CLAUDE_PROJECT_DIR:-}"

# Freeze the current .o → .o.prev (no-op unless a LOCI-built .o + meta exist).
#
# --turn makes the capture first-write-wins for the turn. Without it the baseline
# is destroyed by the turn's second edit: this hook runs on every edit and the
# post-edit skill recompiles the .o in between, so edit 2 froze edit 1's output and
# called it "pre-edit". `prompt_id` is on every hook payload, identical for every
# event within one user turn, distinct across turns, and — verified — still the
# PARENT turn's id for edits made inside a subagent, which is exactly what a
# per-turn baseline wants.
#
# Degrade, don't drop: --turn needs a CLI newer than the pin, and the pin installs
# an exact `==` spec, so an older `loci` is a normal state and rejects the flag with
# a usage error. Retry without it — that restores the previous overwrite-every-time
# behaviour, which is worse but is what those installs already had. Never skip the
# snapshot entirely, which would leave no baseline at all.
# Branch on the exit STATUS, not on failure generally: argparse answers an unknown
# flag with exit 2, and that is the only case where dropping --turn is the right
# move. Retrying on any non-zero would escalate an unrelated failure to the
# overwrite-every-time path — i.e. to the defect — silently and for the rest of the
# turn, since everything here is redirected to /dev/null. (The post-edit hook makes
# the same distinction; this follows it.)
turn=$(printf '%s' "$payload" | jq -r '.prompt_id // ""' 2>/dev/null)

# Which target's object to freeze. Optional, and only ever an improvement: without
# it `_canonical_object` considers every target directory and ranks them, which
# keeps a turn's baseline STABLE but cannot know which target this session builds —
# so on a project built for two, the baseline can land beside the object the next
# compile does not write and the post-edit report degrades to absolute-only.
#
# It comes out of the keyed context file `session-init.sh` writes, named the way
# THAT writer names it: `hash_cwd` over the project root, which is a device:inode
# key, so the payload's native `C:\...` spelling and the writer's Git Bash `/c/...`
# one resolve to the same file without either side comparing strings. Sourcing the
# writer's own library rather than reimplementing the rule — a second copy is a
# state directory two components disagree about, and this one would be silent.
#
# It replaced a scan of every `project-context-*.json`, which was wrong twice over:
# the `IFS=$(printf '\t')` prefix on the `read` re-forked a command substitution on
# EVERY iteration (measured 6.4 s at 500 context files, against this hook's 8 s
# budget), and passing the whole glob to one `jq` hit the Windows 32 KB argv limit at
# about 200 files and silently returned nothing. Both scaled with a directory that
# only ever grows. One file, named directly, has neither problem.
#
# Only a target detection actually resolved is sent — `unknown`/`null` mean it did
# not. The CLI is what validates the value: `snapshot --loci-target` takes it as a
# HINT and drops an unrecognised one with a reason, rather than exiting 2 and taking
# `--turn` down with it on the shared retry below. That is deliberate and it is why
# there is no target list here: `lib/detect-project.sh` and the CLI's `LOCI_TARGETS`
# are separately maintained and coincide today, and a third spelling of the set in
# this file is how 06d's defect happened.
target=""
if [ -n "$root" ]; then
    PLUGIN_DIR="$(cd "$(dirname "$0")/.." && pwd)"
    STATE_DIR="${LOCI_STATE_DIR:-${HOME:-}/.loci/state}"
    [ -d "$STATE_DIR" ] || STATE_DIR="${PLUGIN_DIR}/state"
    export LOCI_STATE_DIR="$STATE_DIR"
    # Sourcing only DEFINES — no installs, no writes, and the shared logger is inert
    # outside dev mode. A missing or broken library leaves `hash_cwd` undefined and
    # the target simply unsent, which is the behaviour every install has today.
    . "${PLUGIN_DIR}/lib/setup-steps.sh" 2>/dev/null || true
    if declare -F hash_cwd >/dev/null 2>&1; then
        _key=$(hash_cwd "$root" 2>/dev/null) || _key=""
        _ctx="${STATE_DIR}/project-context-${_key}.json"
        if [ -n "$_key" ] && [ -f "$_ctx" ]; then
            target=$(jq -r '.loci_target // ""' "$_ctx" 2>/dev/null)
        fi
    fi
    case "$target" in unknown|null|"") target="" ;; esac
fi

# `--flag=value`, joined, for everything whose content this hook does not control.
# `prompt_id` is undocumented and a value beginning with `-` is read by argparse as
# the next OPTION — it then answers "expected one argument" and exits 2, which is
# indistinguishable here from "this CLI does not know the flag", so a malformed id
# would silently cost the retry as well. `--source` keeps the separate form it has
# always had; `file_path` is a path the tool produced, not an opaque token.
snap=( --source "$fp" )
# `--project-root` is NOT in the group that gets dropped below: it predates all of
# this work (verified present on 0.1.102, older than the pinned 0.1.119), so no CLI
# this hook can meet rejects it.
[ -n "$root" ] && snap+=( "--project-root=$root" )
new=()
[ -n "$turn" ]   && new+=( "--turn=$turn" )
[ -n "$target" ] && new+=( "--loci-target=$target" )

if [ ${#new[@]} -gt 0 ]; then
    loci build snapshot "${snap[@]}" "${new[@]}" >/dev/null 2>&1
    rc=$?
    if [ "$rc" -eq 2 ]; then
        # ALL of the unreleased flags come off together, not just `--turn`. They
        # ship in the same release, so no CLI has one and not the other; dropping
        # them one at a time would cost an extra `loci` spawn per edit on every
        # install running today's pinned CLI, which is all of them.
        loci build snapshot "${snap[@]}" >/dev/null 2>&1 || true
    fi
else
    loci build snapshot "${snap[@]}" >/dev/null 2>&1 || true
fi

# Static pre-scan of the incoming code; surface the report if non-empty.
#
# --path=… (not --path …) so a file_path beginning with a dash is not parsed as an
# option, matching `post-edit-hook.sh`. --content-kind needs a CLI newer than the
# pin, so on a usage error ask again without it: unlike post-edit, where the answer
# decides whether a measurement happens at all, the only thing riding on this is how
# the report is worded — and the pinned CLI is the one production runs, so an
# unconditional flag would take the pre-scan away from every install today.
envelope=$(printf '%s' "$code" | loci scan --path="$fp" --content-kind "$kind" 2>/dev/null)
rc=$?
if [ "$rc" -eq 2 ]; then
    envelope=$(printf '%s' "$code" | loci scan --path="$fp" 2>/dev/null)
fi
report=$(printf '%s' "$envelope" | jq -r '.data.report // ""' 2>/dev/null)
[ -n "$report" ] && jq -n --arg c "$report" \
    '{hookSpecificOutput: {hookEventName: "PreToolUse", additionalContext: $c}}'
exit 0
