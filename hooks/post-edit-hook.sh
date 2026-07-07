#!/usr/bin/env bash
# PostToolUse edge adapter (Claude Code → loci): extract {file_path, code} from
# the hook payload, ask `loci scan` whether the edit can change a compiled
# function body, and if so emit the reminder to run loci-post-edit. No analysis
# here — only jq field mapping. Advisory: always exits 0, never blocks.
set -u
export PYTHONIOENCODING=utf-8
export PATH="$HOME/.local/bin:$PATH"
command -v jq >/dev/null 2>&1 || exit 0

payload=$(cat)
fp=$(printf '%s' "$payload" | jq -r '.tool_input.file_path // ""')

# Only C/C++/Rust sources.
case "$fp" in
    *.c|*.cc|*.cpp|*.cxx|*.h|*.hpp|*.hxx|*.rs) ;;
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

code=$(printf '%s' "$payload" | jq -r '
    .tool_name as $tn | .tool_input as $ti |
    if   $tn == "Write"     then ($ti.content // "")
    elif $tn == "Edit"      then ($ti.new_string // "")
    elif $tn == "MultiEdit" then ([$ti.edits[]?.new_string // ""] | join("\n"))
    else "" end')

# Skip edits that cannot change a compiled function body (headers, #include-only,
# comment-only, no-brace) — loci scan makes that call.
measurable=$(printf '%s' "$code" | loci scan --path "$fp" 2>/dev/null | jq -r '.data.measurable // false')
[ "$measurable" = "true" ] || exit 0

fname=$(basename "$fp")
jq -n --arg f "$fname" '{hookSpecificOutput: {hookEventName: "PostToolUse", additionalContext:
    ("[loci] " + $f + " was modified. You MUST invoke the loci:loci-post-edit skill NOW — "
     + "do not proceed to the next edit or respond to the user first. "
     + "EXCEPTION: if this edit was made as part of a loci-preflight pass "
     + "(predictive measurement of a candidate function), do NOT invoke "
     + "loci-post-edit — preflight will report the analysis itself.")
}}'
exit 0
