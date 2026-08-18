#!/usr/bin/env bash
# PostToolUse edge adapter (Claude Code → loci): work out what the edit actually
# changed, ask `loci scan` whether that can change a compiled function body, and if
# so emit the reminder to run loci-post-edit. No analysis here — only jq field
# mapping. Advisory: always exits 0, never blocks.
set -u
export PYTHONIOENCODING=utf-8
# ${HOME:-} — under `set -u` a bare $HOME exits 1 in an environment without it
# (Windows sets USERPROFILE, and hooks run non-interactive so no profile is
# sourced). Exit 1 from a PostToolUse hook reads to the model as a tool failure,
# which is the one thing this file must never do.
export PATH="${HOME:-}/.local/bin:$PATH"
command -v jq >/dev/null 2>&1 || exit 0

payload=$(cat)
fp=$(printf '%s' "$payload" | jq -r '.tool_input.file_path // ""' 2>/dev/null)

# Only C/C++/Rust sources. Kept in step with `pre-edit-hook.sh` and with the CLI's
# `_SNAPSHOT_SOURCE_EXTS` — see the longer note there for why narrowing this list is
# a correctness bug and not a scoping choice.
case "$fp" in
    *.c|*.cc|*.cpp|*.cxx|*.c++|*.rs) ;;
    *.h|*.hpp|*.hxx|*.h++|*.hh|*.inc|*.ipp|*.tcc) ;;
    *.S|*.s) ;;
    *) exit 0 ;;
esac
# Skip plan/settings files that carry a source-ish extension.
case "$fp" in
    */.claude/plans/*|*/.claude/settings*) exit 0 ;;
esac

# Install-on-miss: loci is needed now but absent — kick a background install
# (self-locking) and skip this run. Covers plugins installed mid-session.
if ! command -v loci >/dev/null 2>&1; then
    nohup bash "$(dirname "$0")/ensure-loci-cli.sh" </dev/null >/dev/null 2>&1 &
    exit 0
fi

# A failed tool call applied nothing, so there is nothing to measure.
#
# Probed against a live session rather than assumed, and the answer is in three
# parts. An Edit that fails its own VALIDATION ("String to replace not found")
# fires no hook at all — not even PreToolUse. An Edit that passes validation and
# then fails while WRITING (a read-only file: `EPERM ... rename`) fires PreToolUse
# and then `PostToolUseFailure`, whose payload carries `error` and `is_interrupt`
# and NO `tool_response`. `PostToolUse` fires in neither case.
#
# So nothing is registered on `PostToolUseFailure`, deliberately. The state a
# failed edit leaves is already right: the file on disk is unchanged, so the
# pre-edit capture IS still the turn's true Before, and first-write-wins means the
# turn's next successful edit of that file keeps it. A cleanup hook there would be
# actively wrong — it would drop a capture taken before anything else in the turn
# could touch the file, and re-taking it later is how the baseline moves.
#
# What IS here is a guard that makes the decision checkable instead of leaving it
# as an absence in `hooks.json`. Three shapes mean "not applied", and the first two
# cannot fire today — kept because the payload is undocumented and the cost of a
# shape change reaching the reminder unguarded is telling the model a file "was
# modified" when it was not, on a file whose object is the PREVIOUS content:
#
#   * a `tool_response` carrying a non-null `error`;
#   * a top-level non-null `error`, which is the shape `PostToolUseFailure` actually
#     has — and note it has no `tool_response` at all, so without this it would fall
#     through to the fragment branch and produce the MANDATORY reminder;
#   * `hook_event_name` naming a failure event.
#
# Both `error` arms test `!= null`, not `has`. A JSON producer emitting an explicit
# `"error": null` on a SUCCESSFUL edit is ordinary, and reading that as a failure
# drops the reminder and the measurement with it.
#
# The event arm is a DENY-list, and that is the whole point of it. Naming the
# success event instead — `!= "PostToolUse"` — means any future spelling of it
# silences this hook on every edit, on a field nobody here controls. That inverts
# the asymmetry the rest of this file is built on: under-triggering loses a
# measurement invisibly, over-triggering costs one wasted analysis, so an unknown
# event must fall through and remind. (An absent field is likewise not a reason to
# go quiet, which a deny-list gives for free.)
if printf '%s' "$payload" | jq -e '
        ((.tool_response? | objects | .error? != null) // false)
        or (.error? != null)
        or ((.hook_event_name? // "") | endswith("Failure"))' \
        >/dev/null 2>&1; then
    exit 0
fi

# Prefer the APPLIED edit's diff. It is the only shape that shows BOTH sides of the
# change: an Edit's new_string alone cannot reveal what it replaced, so a
# replacement text of "// memset removed" looks comment-only while being the
# deletion of a real call. The classifier therefore refuses to reject a fragment on
# its content at all — send a diff whenever one exists.
#
# The -/+ markers are KEPT for two reasons: a bare "+" — one blank line added —
# stays a non-empty line, so "no patch" is not confused with "a patch whose changed
# lines are all blank"; and the one other line shape a real patch carries,
# `\ No newline at end of file`, is excluded by the +/- filter deliberately rather
# than by luck.
#
# `objects`/`arrays` do the guarding, and this jq deliberately has NO `2>/dev/null`
# so that they must: `.structuredPatch[]?` protects only the [] suffix, so a
# tool_response that is a string or an array would die on the field access and leak
# `jq: error` to the transcript on every edit. A redirect here would hide a broken
# guard rather than prevent one. (A payload that is not JSON at all never reaches
# this line: the `fp` read above fails first and the extension filter exits.)
#
# No `[...] | join("\n")`: `jq -r` already prints one raw line each, and the array
# form is quadratic — 40k changed lines measured 3.6 s against 0.22 s streaming,
# enough to blow this hook's 5 s budget and lose the reminder entirely.
changed=$(printf '%s' "$payload" | jq -r '
    .tool_response? | objects | .structuredPatch? | arrays | .[]
    | .lines? | arrays | .[]
    | select(type == "string")
    | select(startswith("+") or startswith("-"))')

if [ -n "$changed" ]; then
    kind=diff
    code=$changed
else
    # No patch. A Write that CREATES a file reports an empty patch and carries the
    # whole content (a Write that overwrites an existing one does report a patch,
    # and takes the branch above). An Edit without a patch leaves only new_string,
    # which is a fragment — never call it a file, or the whole-file brace rule
    # reinstates the bug this exists to fix.
    kind=$(printf '%s' "$payload" \
           | jq -r 'if (.tool_input? | objects | has("content")) then "file" else "fragment" end' 2>/dev/null)
    code=$(printf '%s' "$payload" \
           | jq -r '.tool_input? | objects | (.content // .new_string // "")' 2>/dev/null)
fi

# Skip edits that cannot change a compiled function body (headers, comment-only,
# and for a whole file #include-only) — loci scan makes that call.
#
# --content-kind needs a CLI newer than the current pin, and the pin installs an
# exact `==` spec, so an older `loci` is a normal state. argparse exits 2 on the
# unknown flag. The reminder must NOT be decided by such a CLI: re-asking it
# without the flag re-applies the whole-file brace rule to a brace-less diff and
# answers "no", silently re-running the exact bug this fixes. So on a usage error
# we FAIL OPEN and remind. Under-triggering loses a measurement and says nothing;
# over-triggering costs one wasted analysis. Failing open also matches the skill's
# own frontmatter, which asks for it after any source edit.
# --path=… (not --path …) so a relative file_path beginning with a dash is not
# parsed as an option. With the fail-open above that costs classification accuracy
# rather than the reminder, but a wrong classification is still a wrong answer.
envelope=$(printf '%s' "$code" | loci scan --path="$fp" --content-kind "$kind" 2>/dev/null)
rc=$?
if [ "$rc" -eq 2 ]; then
    :                       # flag unsupported → fail open, fall through and remind
elif [ "$rc" -ne 0 ]; then
    exit 0                  # CLI broken or unavailable: the skill could not run either
else
    measurable=$(printf '%s' "$envelope" \
        | jq -r 'if (.data? | objects | has("measurable"))
                 then (.data.measurable | tostring) else "" end' 2>/dev/null)
    [ "$measurable" = "true" ] || exit 0
    # HOW to measure it, carried through to the skill. `measurable` says the edit can
    # change compiled code; `measure_via` says whether this file can be compiled at
    # all — a header cannot, and is measured through the units that #include it.
    # Passed along rather than left to the skill re-deriving it from its own list of
    # header suffixes: that list would be the FOURTH copy of the same set, and the
    # only one no test can compare against the CLI's. Empty on an older CLI, which
    # the reminder then simply omits.
    route=$(printf '%s' "$envelope" \
        | jq -r '.data.measure_via // ""' 2>/dev/null)
fi
route="${route:-}"

# The turn id rides along so the skill can pass it as `--turn`. The pre-edit hook
# already stamps the baseline with this same `prompt_id` — identical for every event
# in one user turn, distinct across turns, and still the PARENT's value inside a
# subagent — but until now nothing ever CHECKED it: the compile that reads the
# baseline was never told which turn it wanted, so a capture left by a PREVIOUS turn
# was served as this edit's Before and the delta silently spanned two turns
# (measured: +77.8% ROM reported for an edit whose true effect was 0). Reachable
# whenever the pre-edit hook did not capture for this turn — killed on its 8 s
# budget, `loci` briefly absent, or the file changed outside Claude Code.
#
# The reminder text is the channel because there is no other one: a PostToolUse hook
# cannot call the skill it asks for. Degrading is safe at every step — no `prompt_id`
# in the payload, or a model that drops it, means the skill omits `--turn` and the
# compile simply does not verify the turn, which is the behaviour before this line.
turn=$(printf '%s' "$payload" | jq -r '.prompt_id // ""' 2>/dev/null)

# Both edit hooks fire inside a subagent, and until now nothing here knew it. The
# reminder was delivered to an agent whose transcript the user never reads, so the
# measurement ran and its numbers went into the subagent's own report — which the
# parent may summarise, paraphrase, or drop entirely.
#
# The fix is a sentence, not a suppression. Skipping the reminder inside subagents
# would make a subagent's edits the one kind that is never measured, which is the
# silent skip this whole change exists to remove — and subagents are where bulk
# edits happen. So the edit is measured as it always was, and the agent is told
# that relaying the verdict is part of its job.
#
# `agent_id`/`agent_type` are present on a subagent's tool payloads and absent from
# the main agent's — established by probing a live session, because every other
# field is identical: same `session_id`, same `transcript_path`, and the same
# `prompt_id` (the PARENT turn's, which is what the shared per-turn baseline wants).
agent=$(printf '%s' "$payload" | jq -r '.agent_id // ""' 2>/dev/null)

fname=$(basename -- "$fp")
jq -n --arg f "$fname" --arg t "$turn" --arg v "$route" --arg a "$agent" '{hookSpecificOutput: {hookEventName: "PostToolUse", additionalContext:
    ("[loci] " + $f + " was modified. You MUST invoke the loci:loci-post-edit skill NOW — "
     + "do not proceed to the next edit or respond to the user first. "
     + (if $t == "" then "" else "Pass turn id " + $t + " to the skill as its --turn value. " end)
     + (if $v == "dependents" then
          "This file emits no object of its own, so measure it through the translation units that #include it — Step 0b. "
        else "" end)
     + (if $a == "" then "" else
          "You are running as a subagent: the user sees your final report, not this transcript. Include the LOCI verdict (the timing/energy delta, or why it could not be measured) in that report. "
        end)
     + "EXCEPTION: if this edit was made as part of a loci-preflight pass "
     + "(predictive measurement of a candidate function), do NOT invoke "
     + "loci-post-edit — preflight will report the analysis itself.")
}}'
exit 0
