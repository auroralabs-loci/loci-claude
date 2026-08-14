#!/usr/bin/env bash
# PreToolUse guard: .loci/contract.yaml is read-only to the agent — it states the
# bounds the agent's own work is judged against. The agent drafts
# (`loci contract draft …`), the USER applies (`loci contract accept`). ADR-0016/17.
#
# The one LOCI hook that BLOCKS — do not merge it into the advisory pre-edit hook,
# and do not soften the deny into a warning. Two routes: an Edit/Write whose
# file_path is the contract, and a Bash contract-writing verb.
set -u

payload=$(cat)

# Runs on every Bash call in every repo, so nothing may fork before this. Both
# routes need the literal `contract`.
case "$payload" in
    *contract*) ;;
    *) exit 0 ;;
esac

deny() {
    # Fixed literal, so no escaping and no jq needed.
    printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"%s"}}\n' "$1"
    exit 0
}

REASON_FILE="The Contract Envelope (.loci/contract.yaml) is read-only to you. It states the bounds your work is judged against, so only the user changes it. Draft the change instead: echo '<entry json>' | loci contract draft add   then hand the user exactly this line to run: ! loci contract accept"
REASON_VERB="That verb writes the Contract Envelope, which is the user's to apply. Draft with 'loci contract draft add|edit|disable|enable', then hand the user this line to run: ! loci contract accept"

# Hook PATH is often minimal. ${HOME:-} — under `set -u` an unset HOME kills the
# hook, and PreToolUse is fail-open, so the write would proceed.
PATH="$PATH:/usr/local/bin:/opt/homebrew/bin:${HOME:-}/.local/bin"

fp=""; cmd=""; cwd=""
if command -v jq >/dev/null 2>&1; then
    fp=$(printf '%s' "$payload" | jq -r '.tool_input.file_path // ""')
    cmd=$(printf '%s' "$payload" | jq -r '.tool_input.command // ""')
    cwd=$(printf '%s' "$payload" | jq -r '.cwd // ""')
else
    # No jq: route 1 must still decide on the field, not on the payload text —
    # matching the text denies edits for their own content.
    fp=$(printf '%s' "$payload" \
        | sed -n 's/.*"file_path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
    case "$payload" in *'"command"'*) cmd="$payload" ;; esac
fi

# ── route 1: a direct write to the file ───────────────────────────────────────
if [ -n "$fp" ]; then
    # Forks git, so it stays out of route 2 — the hot path.
    root="${CLAUDE_PROJECT_DIR:-}"
    if [ -z "$root" ]; then
        root=$(cd "${cwd:-$PWD}" 2>/dev/null && git rev-parse --show-toplevel 2>/dev/null)
    fi
    [ -n "$root" ] || root="${cwd:-$PWD}"

    target="$root/.loci/contract.yaml"
    # -m: a Write creates the file, so it need not exist to be resolved.
    if command -v realpath >/dev/null 2>&1; then
        resolved=$(realpath -m "$fp" 2>/dev/null || printf '%s' "$fp")
        want=$(realpath -m "$target" 2>/dev/null || printf '%s' "$target")
        [ "$resolved" = "$want" ] && deny "$REASON_FILE"
    fi
    case "$fp" in
        */.loci/contract.yaml|.loci/contract.yaml|./.loci/contract.yaml) deny "$REASON_FILE" ;;
    esac
fi

# ── route 2: a Bash command running a contract-writing verb ──────────────────
#
# Shell writes (`cat >`, `sed -i`, `cp`, `git restore`) are deliberately NOT
# matched — shape-matching produced every false positive this guard ever had and
# caught nothing; the commit diff is the backstop. Reads stay allowed: the agent
# must know the bounds, and the skill runs `git diff -- <path>` itself.
if [ -n "$cmd" ]; then
    norm=" $(printf '%s' "$cmd" | tr '\n\t' '  ' | sed 's/  */ /g') "

    # The leading space keeps `contract draft edit` allowed while `contract edit`
    # is denied — one token apart.
    case "$norm" in
        *" contract accept"*|*" contract init"*) deny "$REASON_VERB" ;;
        *" contract edit"*|*" contract disable"*|*" contract enable"*) deny "$REASON_VERB" ;;
    esac
fi

exit 0
