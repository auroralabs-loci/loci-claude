#!/usr/bin/env bash
# PreToolUse edge adapter (Claude Code → loci): extract {file_path, code} from
# the hook payload and hand them to `loci build snapshot` (freeze the source's
# .o for post-edit diffing) and `loci scan` (static call-graph pre-scan). No
# analysis here — only jq field mapping. Advisory: always exits 0, never blocks.
set -u
export PYTHONIOENCODING=utf-8
export PATH="$HOME/.local/bin:$PATH"
command -v jq >/dev/null 2>&1 || exit 0

payload=$(cat)
fp=$(printf '%s' "$payload" | jq -r '.tool_input.file_path // ""')

# Only C/C++/Rust sources — skips plan files, markdown, configs, etc.
case "$fp" in
    *.c|*.cc|*.cpp|*.cxx|*.h|*.hpp|*.hxx|*.rs) ;;
    *) exit 0 ;;
esac

# Install-on-miss: loci is needed now but absent — kick a background install
# (self-locking) and skip this run. Covers plugins installed mid-session.
if ! command -v loci >/dev/null 2>&1; then
    nohup bash "$(dirname "$0")/ensure-loci-cli.sh" </dev/null >/dev/null 2>&1 &
    exit 0
fi

# The incoming edit content (not yet written to disk), per write-family tool.
code=$(printf '%s' "$payload" | jq -r '
    .tool_name as $tn | .tool_input as $ti |
    if   $tn == "Write"     then ($ti.content // "")
    elif $tn == "Edit"      then ($ti.new_string // "")
    elif $tn == "MultiEdit" then ([$ti.edits[]?.new_string // ""] | join("\n"))
    else "" end')

# Freeze the current .o → .o.prev (no-op unless a LOCI-built .o + meta exist).
loci build snapshot --source "$fp" >/dev/null 2>&1 || true

# Static pre-scan of the incoming code; surface the report if non-empty.
report=$(printf '%s' "$code" | loci scan --path "$fp" 2>/dev/null | jq -r '.data.report // ""')
[ -n "$report" ] && jq -n --arg c "$report" \
    '{hookSpecificOutput: {hookEventName: "PreToolUse", additionalContext: $c}}'
exit 0
