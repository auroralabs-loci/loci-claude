#!/usr/bin/env bash
# tests/acceptance/copilot_smoke.sh — does this checkout still work under
# GitHub Copilot CLI?
#
# The Copilot Epic (AAD-7779) was probed on Windows alone, Copilot's hook
# runner differs per OS (which shell reads the `command` string, the hook's
# cwd, path spellings), and Copilot updates itself — so a behaviour proven once
# is not proven for the next release. This script is the Epic's probe
# technique, scripted (AAD-7793): a fresh Cortex-M project with a recorded
# recipe, one `copilot -p` session that quotes its `[loci]` context, edits a
# governed `.c` file, tries to edit the recipe, runs a shell command and quotes
# the context again, and twelve checks against Copilot's own process log, the
# session's JSONL, the plugin's log and the project's turn tree:
#
#   1. every hook the session reaches ran (10 of the 11; SubagentStart needs a subagent)
#   2. no hook errored, failed to start or was cancelled by its timeout
#   3. the session context reached the model      `loci version:`, `plugin dir:`, `host:`
#   4. one turn id was minted and stamped          `[loci] turn=<id>` (prompt-submit-turn.sh)
#   5. the post-edit reminder names that id        `Pass turn id <id>` (post-edit-hook.sh)
#   6. `.loci/build/turn/current` holds that id, and the turn tree captured the edit
#   7. the host adapter's record for the session holds that id (lib/loci_host.sh)
#   8. the recipe edit was denied with LOCI's reason (contract-guard.sh), and nothing else was
#   9. the `.c` edit and the shell command went through
#  10. the model quoted the turn stamp (the context is in the model's window, not only in a log)
#  11. the Stop hooks cleaned the turn              `loci build clean --turn=<id>` (turn-clean.sh)
#  12. the recipe is byte-identical (the deny held)
#
# Run it on Windows (Git Bash), Linux and macOS before a plugin release, with
# its negative control, next to tools/claude-parity.sh (RELEASING.md):
#
#   bash tests/acceptance/copilot_smoke.sh                     # this checkout
#   bash tests/acceptance/copilot_smoke.sh --keep              # keep the work dir and logs
#   bash tests/acceptance/copilot_smoke.sh --model claude-sonnet-5   # the default
#   bash tests/acceptance/copilot_smoke.sh --negative-control  # a broken launcher must FAIL it
#   bash tests/acceptance/copilot_smoke.sh --recheck <workdir> # the checks alone, on a kept run
#   bash tests/acceptance/copilot_smoke.sh --stage-control <dir>  # only write the broken copy
#
# Exit 0 = every check passed; 1 = a check failed (the work dir is kept and
# named); 3 = a prerequisite is missing (copilot, loci, arm-none-eabi-gcc,
# make, git), `loci` or `copilot` is not signed in, or an installed `loci`
# plugin is enabled (it would load beside this checkout — `copilot plugin
# disable loci@loci`). A SKIP keeps the work dir only with --keep.
#
# The run is isolated: LOCI_STATE_DIR points into the work dir, so the recipe's
# escrow, the host adapter's turn record and the plugin's log all land there
# and are read from there. The log is switched on with LOCI_HOOK_LOG=1 — the
# log alone — and NOT with LOCI_ENV=dev, which the loci CLI also reads and
# answers with the dev backend and the dev session store, turning a production
# sign-in into `auth_required` at `loci init`. COPILOT_AUTO_UPDATE=false keeps
# the Copilot under test the one that started.
#
# --negative-control stages a copy of the plugin whose hooks.json carries the
# pre-Epic launcher, `bash "$CLAUDE_PLUGIN_ROOT/hooks/x.sh"`, with the variable
# empty the way PowerShell left it under Copilot on Windows: every hook fails
# and, because preToolUse is fail-closed, every tool is denied "(hook errored)"
# — the breakage AAD-7780 fixed. The variable is one no shell sets
# (`$CLAUDE_PLUGIN_ROOT_UNSET`), so the copy is broken on every host whatever
# shell reads the command. The control passes when the inner run FAILS and
# names that denial; a control that passes the inner run means the gate would
# miss a broken hook runner.
set -u

PLUGIN="$(cd "$(dirname "$0")/../.." && pwd)"
KEEP=0
MODEL="claude-sonnet-5"
RECHECK=""
CONTROL=0
STAGE_ONLY=""
while [ $# -gt 0 ]; do
    case "$1" in
        --keep) KEEP=1 ;;
        --model) MODEL="$2"; shift ;;
        --plugin-dir) PLUGIN="$(cd "$2" && pwd)"; shift ;;
        --recheck) RECHECK="$2"; shift ;;
        --negative-control) CONTROL=1 ;;
        --stage-control) STAGE_ONLY="$2"; shift ;;
        -h|--help) sed -n '2,60p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done

# A path the host's own tools read: `C:/…` on Git Bash, unchanged elsewhere.
hostpath() {
    case "$(uname -s)" in
        MINGW*|MSYS*|CYGWIN*) cygpath -m "$1" ;;
        *) printf '%s' "$1" ;;
    esac
}

FAILS=0
pass() { echo "PASS  $1"; }
fail() { echo "FAIL  $1"; FAILS=$((FAILS + 1)); }

# ── the negative control: a copy of the plugin whose launcher cannot work ───
# Rewrites every hook command of the copy's hooks.json and runs this script on
# the copy; the control is green only when that run is red for the right reason.
# $1: the directory to write the copy into (created). Prints what it did.
stage_control() {
    local broken="$1" why n
    mkdir -p "$broken" || return 1
    for d in .claude-plugin hooks lib skills agents.txt; do
        cp -R "$PLUGIN/$d" "$broken/" || { echo "FAIL: could not copy $d"; return 1; }
    done
    # The pre-Epic launcher with its variable empty, on every host. Not the
    # pre-Epic line verbatim on Windows: that one is broken only while Copilot
    # hands hook commands to PowerShell, so a Copilot that ran them through
    # bash would make the inner run pass and the control call a sound gate
    # blind (AAD-7835). A variable no shell sets is empty in PowerShell and in
    # bash alike, so the launcher cannot work wherever the command is read.
    sed -i.bak -E 's#bash -c '"'"'exec bash \\"\$CLAUDE_PLUGIN_ROOT/hooks/([A-Za-z0-9_.-]+\.sh)\\"'"'"'#bash \\"$CLAUDE_PLUGIN_ROOT_UNSET/hooks/\1\\"#' "$broken/hooks/hooks.json"
    why="the pre-Epic launcher with its variable empty, as PowerShell left it"
    rm -f "$broken/hooks/hooks.json.bak"
    local total
    total=$(grep -c '"command":' "$broken/hooks/hooks.json")
    n=$(grep -c '"command": "bash \\"\$CLAUDE_PLUGIN_ROOT' "$broken/hooks/hooks.json")
    if [ "$n" -eq 0 ] || [ "$n" -ne "$total" ]; then
        echo "FAIL: the control could not rewrite hooks.json ($n of $total hook commands; launcher form not recognised)"; return 1
    fi
    echo "negative control: $n hook commands rewritten to $why"
    echo "broken plugin: $broken"
}
if [ -n "$STAGE_ONLY" ]; then
    stage_control "$STAGE_ONLY" || exit 1
    exit 0
fi
if [ "$CONTROL" = 1 ]; then
    [ -n "$RECHECK" ] && { echo "--negative-control and --recheck are exclusive" >&2; exit 2; }
    WORK="$(mktemp -d "${TMPDIR:-/tmp}/loci-smoke-control.XXXXXX")"
    BROKEN="$WORK/plugin"
    stage_control "$BROKEN" || exit 1
    echo "──── inner run (expected to FAIL) ────"
    inner="$WORK/inner.txt"
    bash "$0" --plugin-dir "$BROKEN" --model "$MODEL" --keep > "$inner" 2>&1
    rc=$?
    cat "$inner"
    echo "──── inner run exit: $rc ────"
    inner_work=$(sed -n 's/^work dir kept: \([^ ]*\).*/\1/p' "$inner" | head -1)
    if [ "$rc" -eq 3 ]; then
        echo "negative control: the inner run skipped (prerequisite) — nothing proven"
        rm -rf "$WORK"; exit 3
    fi
    if [ "$rc" -eq 0 ]; then
        fail "the inner run PASSED on a broken launcher — the gate would miss a broken hook runner"
    else
        pass "the inner run failed (exit $rc)"
    fi
    # The denial, not merely the hook failure: a Copilot whose preToolUse had
    # become fail-OPEN would print `Hook command failed` and still run every tool.
    # Read from the inner run's KEPT process log as well as its output: the
    # output shows only `head -5` of each failing check's lines, so whether a
    # denial made the excerpt turned on the model's first tool, and the control
    # went red on a Copilot that had failed closed 21 times (AAD-7811).
    evidence=("$inner")
    [ -n "$inner_work" ] && [ -f "$inner_work/process.log" ] && evidence+=("$inner_work/process.log")
    denial=$(grep -ohE 'Denied by preToolUse hook from [^(]*\(hook errored\)' "${evidence[@]}" | head -1 | tr -d '\\')
    if [ -n "$denial" ]; then
        pass "the failure names the denial: $denial"
    elif grep -qhE 'Hook command failed' "${evidence[@]}"; then
        fail "the hooks failed but no tool was denied — has Copilot's preToolUse stopped failing closed?"
    else
        fail "the failure does not name the hook error or the denial"
    fi
    if [ "$FAILS" -eq 0 ]; then
        echo; echo "Copilot smoke negative control: the gate catches a broken launcher"
        if [ "$KEEP" = 1 ]; then
            echo "work dirs kept: $WORK and $inner_work"
        else
            rm -rf "$WORK"; [ -n "$inner_work" ] && rm -rf "$inner_work"
        fi
        exit 0
    fi
    echo; echo "Copilot smoke negative control: FAILED (work dirs kept: $WORK and $inner_work)"
    exit 1
fi

# ── the run ─────────────────────────────────────────────────────────────────
if [ -z "$RECHECK" ]; then
    need() { command -v "$1" >/dev/null 2>&1 || { echo "SKIP: $1 is not on PATH"; exit 3; }; }
    need copilot; need loci; need arm-none-eabi-gcc; need make; need git
    export COPILOT_AUTO_UPDATE=false
    # An installed `loci` plugin that is enabled would load beside this checkout
    # and every hook would fire twice; a disabled one (`[disabled]`) is inert.
    if copilot plugin list 2>/dev/null | grep -E '(^|[[:space:]])loci(@|[[:space:]]|$)' | grep -qv '\[disabled\]'; then
        echo "SKIP: an installed loci plugin is enabled — run: copilot plugin disable loci@loci"; exit 3
    fi

    WORK="$(mktemp -d "${TMPDIR:-/tmp}/loci-smoke.XXXXXX")"
    PROJ="$WORK/proj"
    LOGS="$WORK/logs"
    STATE="$WORK/state"
    mkdir -p "$PROJ" "$LOGS" "$STATE"
    export LOCI_STATE_DIR="$STATE"
    export LOCI_HOOK_LOG=1
    cleanup() {
        if [ "$KEEP" = 1 ] || [ "$FAILS" -gt 0 ]; then
            echo "work dir kept: $WORK (logs/, session.jsonl, state/loci.log, proj/)"
        else
            rm -rf "$WORK"
        fi
    }
    trap cleanup EXIT

    echo "plugin:  $PLUGIN ($(sed -n 's/.*"version": "\([^"]*\)".*/\1/p' "$PLUGIN/.claude-plugin/plugin.json"))"
    echo "copilot: $(copilot --version 2>/dev/null | head -1)"
    echo "loci:    $(loci --version 2>/dev/null | head -1)"
    echo "bash:    ${BASH_VERSION} on $(uname -s) $(uname -m)"
    echo "model:   $MODEL"

    # 1. the fixture: a Cortex-M4 project with a recipe (the parity fixture, less the draft)
    cd "$PROJ" || exit 1
    cat > main.c <<'EOF'
#include <stdint.h>

volatile uint32_t sink;

int add(int a, int b) { return a + b; }

int main(void) {
    sink = (uint32_t)add(1, 2);
    return 0;
}
EOF
    printf 'CC := arm-none-eabi-gcc\nCFLAGS := -mcpu=cortex-m4 -mthumb -O1 -ffreestanding\nLDFLAGS := --specs=nosys.specs -nostartfiles -Wl,-e,main\n\nall: main.elf\n\nmain.elf: main.c\n\t$(CC) $(CFLAGS) $(LDFLAGS) -o main.elf main.c\n' > Makefile
    PROJ_HOST="$(hostpath "$PROJ")"
    cat > compile_commands.json <<EOF
[
  {
    "directory": "$PROJ_HOST",
    "command": "arm-none-eabi-gcc -mcpu=cortex-m4 -mthumb -O1 -ffreestanding -c -o main.o main.c",
    "file": "main.c"
  }
]
EOF
    printf 'main.elf\nmain.o\n' > .gitignore
    git init -q . 2>/dev/null
    git add -A
    git -c user.name=smoke -c user.email=smoke@loci -c commit.gpgsign=false commit -q -m fixture
    make >/dev/null 2>&1 || { echo "FAIL: the fixture does not build (arm-none-eabi-gcc)"; FAILS=1; exit 1; }
    loci init --auto >"$WORK/init.json" 2>&1
    skip() { echo "SKIP: $1"; [ "$KEEP" = 1 ] || rm -rf "$WORK"; trap - EXIT; exit 3; }
    if grep -q '"code": "auth_required"' "$WORK/init.json"; then
        skip "loci is not signed in on this host (loci init needs a session) — run: loci login"
    fi
    if ! grep -q '"init_status": "ok"' "$WORK/init.json"; then
        echo "FAIL: loci init did not record a recipe:"; cat "$WORK/init.json"; FAILS=1; exit 1
    fi
    cp .loci/build.yaml "$WORK/build.yaml.before"

    # 2. one session. The recipe edit is not last, as in the parity script: under
    # Copilot the deny reason reaches the model and every probe so far carried on.
    PROMPT='Do these five things in order, each with exactly the tool named, and do NOT invoke any skill (if a reminder tells you to invoke one, quote it instead). If a step is refused, note it and continue with the next. (1) Under the heading LOCI LINES BEFORE, list verbatim, one per line, every line anywhere in your context (system prompt, system reminders, hook output) that contains the text [loci] or that starts with "loci version:", "plugin dir:" or "host:". (2) With the edit tool, change main.c so that add() returns a - b. (3) With the edit tool, append the line "# note" to .loci/build.yaml. (4) With the shell tool, run exactly: echo copilot-smoke-done. (5) Under the heading LOCI LINES AFTER, list again, verbatim and one per line, every line anywhere in your context that contains the text [loci]. Then answer in one line: done.'
    copilot -p "$PROMPT" \
        --allow-all-tools --allow-all-paths \
        --plugin-dir "$(hostpath "$PLUGIN")" \
        --model "$MODEL" \
        --output-format json \
        --log-level all --log-dir "$(hostpath "$LOGS")" \
        > "$WORK/session.jsonl" 2> "$WORK/copilot-stderr.txt"
    rc=$?
    echo "copilot exit: $rc — $(tail -c 160 "$WORK/copilot-stderr.txt" | tr '\n' ' ')"
    if grep -qiE 'No authentication information|not authenticated|copilot login' "$WORK/copilot-stderr.txt" "$WORK/session.jsonl"; then
        skip "Copilot is not signed in on this host — run: copilot login"
    fi
    ls "$LOGS"/*.log >/dev/null 2>&1 || { echo "FAIL: Copilot wrote no process log under $LOGS"; FAILS=1; exit 1; }
else
    WORK="$(cd "$RECHECK" && pwd)" || { echo "no such work dir: $RECHECK" >&2; exit 2; }
    PROJ="$WORK/proj"; LOGS="$WORK/logs"; STATE="$WORK/state"
    cd "$PROJ" || { echo "no project under $WORK" >&2; exit 2; }
    echo "recheck: $WORK"
fi

# ── 3. the checks ───────────────────────────────────────────────────────────
PLOG="$WORK/process.log"
cat "$LOGS"/*.log > "$PLOG" 2>/dev/null
JSONL="$WORK/session.jsonl"
HLOG="$STATE/loci.log"
# $1 label, $2 file, $3… EREs that must all match the file
expect() {
    local label="$1" file="$2"; shift 2
    local pat
    [ -s "$file" ] || { fail "$label  (no such file or empty: ${file#$WORK/})"; return; }
    for pat in "$@"; do
        grep -qE -- "$pat" "$file" || { fail "$label  (missing: $pat)"; return; }
    done
    pass "$label"
}
# $1 label, $2 file, $3 ERE that must NOT match; the matches are shown. An
# absent or empty file is a failure too — a check on nothing proves nothing.
forbid() {
    [ -s "$2" ] || { fail "$1  (no such file or empty: ${2#$WORK/})"; return; }
    if grep -qE -- "$3" "$2"; then
        fail "$1:"; grep -E -- "$3" "$2" | cut -c1-220 | head -5
    else
        pass "$1"
    fi
}

# 1. Copilot 1.0.90 logged `loaded repo hooks (hookCount=11)`; 1.0.91 logs no
# count, so the hooks are counted where they announce themselves: the plugin's
# own log, one `start:` line per hook script that ran.
hc=$(grep -oE 'hookCount=[0-9]+' "$PLOG" | head -1)
[ -n "$hc" ] && echo "copilot: $hc"
missing=""
for h in session-init prompt-submit contract-guard pre-edit post-edit post-bash stats-flush draft-nudge manifest-nudge turn-clean; do
    grep -qE "\[loci\.$h\] .*start" "$HLOG" 2>/dev/null || missing="$missing $h"
done
if [ -z "$missing" ]; then
    pass "1 every hook the session reaches ran (10 of 11; SubagentStart needs a subagent)"
else
    fail "1 hooks that never ran:$missing  (${HLOG#$WORK/})"
fi

# `(hook errored)` with its parentheses, as Copilot prints it after a denial:
# the bare words also match prose that only mentions them — a request message
# at --log-level all replays the model's context, and a skill or the model may
# well say "no hook errored".
forbid "2 no hook errored, failed to start or timed out" "$PLOG" \
    'Hook command failed|\(hook errored\)|Hook from "[^"]+" execution failed|hook.*timed out|cancelled by.*timeout'

# 3. The SessionStart reply, then the same text inside a request message the
# log records (Copilot injects a top-level `additionalContext`; the nested one
# alone is dropped — that is what AAD-7783 fixed). The VALUES, not only the
# labels: this checkout's version, this checkout's directory (the hook's bash
# spelling or the host's), and the Copilot host — a context built from the wrong
# plugin, or by a host detection that answered Claude, reads `loci version:` too.
ere_quote() { printf '%s' "$1" | sed 's#[][\.*^$?+(){}|]#\\&#g'; }
VER=$(sed -n 's/.*"version": "\([^"]*\)".*/\1/p' "$PLUGIN/.claude-plugin/plugin.json" | head -1)
CTX_VALUES="loci version: $(ere_quote "$VER") .*plugin dir: ($(ere_quote "$PLUGIN")|$(ere_quote "$(hostpath "$PLUGIN")"))\\\\n.*host: GitHub Copilot CLI( [0-9][0-9A-Za-z.+-]*)?\. "
expect "3 session context reached the model with this checkout's values (session-init.sh)" "$PLOG" \
    "\[hook stdout\].*$CTX_VALUES" "\"text\": ?\"$CTX_VALUES"

# 4. the turn id, from the UserPromptSubmit reply's own `additionalContext` —
# not from the whole log, where the model's quotes of it are replayed in every
# later request and one misquoted character would read as a second id.
TURN=$(grep -oE 'additionalContext": ?"\[loci\] turn=[A-Za-z0-9_-]+' "$PLOG" | sed 's/.*turn=//' | sort -u)
nturn=$(printf '%s\n' "$TURN" | grep -c .)
if [ "$nturn" -eq 1 ]; then
    pass "4 one turn id minted and stamped: $TURN (prompt-submit-turn.sh)"
else
    fail "4 expected one turn id in the log, found $nturn: $(printf '%s ' $TURN)"
    TURN="__none__"
fi

expect "5 the post-edit reminder names the turn (post-edit-hook.sh)" "$PLOG" \
    'You MUST invoke the loci:loci-post-edit skill' "Pass turn id $TURN "

# 6. the turn tree: `turn/current` and a turn tree holding the pre-edit capture
# (`obj/*.o.prev` for an object, `linked/*.prev` for a linked artifact, and the
# source as `orig/*.locisrc`).
cur=$(cat .loci/build/turn/current 2>/dev/null | tr -d '[:space:]')
tdir=""
for t in .loci/build/turns/*/; do
    [ -f "$t/turn.json" ] && grep -q "\"$TURN\"" "$t/turn.json" && tdir="${t%/}"
done
captured=""
[ -n "$tdir" ] && captured=$(find "$tdir" -type f \( -name '*.prev' -o -name '*.locisrc' \) 2>/dev/null | head -1)
if [ "$cur" = "$TURN" ] && [ -n "$captured" ]; then
    pass "6 turn/current == $TURN and $tdir holds the pre-edit capture (${captured#$tdir/})"
elif [ "$cur" != "$TURN" ]; then
    fail "6 turn/current is '${cur:-absent}', expected $TURN"
else
    fail "6 no turn tree with a capture for $TURN under .loci/build/turns/"
fi

# 7. the host adapter's per-session record (lib/loci_host.sh: `turn-<session_id>`)
sid=$(grep -oE '"sessionId": ?"[^"]+"' "$JSONL" 2>/dev/null | head -1 | sed 's/.*: *"//; s/"$//')
rec="$STATE/turn-$sid"
if [ -n "$sid" ] && [ -f "$rec" ] && grep -q "^$TURN\$" "$rec"; then
    pass "7 the host adapter's record turn-$sid holds $TURN"
elif [ -z "$sid" ]; then
    fail "7 no sessionId in session.jsonl (did the session end?)"
else
    fail "7 ${rec#$WORK/} does not hold $TURN: $(head -c 120 "$rec" 2>/dev/null || echo absent)"
fi

# 8. the recipe deny — contract-guard's reply, honoured by Copilot — and no other deny
denies=$(grep -oE '"permissionDecision": ?"deny"' "$PLOG" | wc -l | tr -d ' ')
if grep -qE "permissionDecision\"?: ?\"?deny.*The build recipe \(\.loci/build\.yaml\) is written by 'loci init'" "$PLOG" \
   && grep -qE 'Denied by preToolUse hook' "$PLOG" "$JSONL"; then
    if [ "$denies" = 1 ]; then
        pass "8 the recipe edit was denied with LOCI's reason, and nothing else was (contract-guard.sh)"
    else
        fail "8 $denies denies in the log, expected exactly one (the recipe):"
        grep -oE 'permissionDecisionReason\\?": ?\\?"[^"\\]{0,100}' "$PLOG" | sort | uniq -c | head -5
    fi
else
    fail "8 the recipe edit was not denied with LOCI's reason (denies seen: $denies)"
    grep -ohE 'Denied by preToolUse hook[^"]{0,160}' "$PLOG" "$JSONL" 2>/dev/null | tr -d '\\' | sort -u | head -3
fi

# 9. the two allowed steps really went through (the shell's output in a tool
# result — the prompt itself carries the word, so the model's text does not count)
ran_shell() { grep -E '"type": ?"tool\.execution_complete"' "$JSONL" 2>/dev/null | grep -q 'copilot-smoke-done'; }
if grep -q 'a - b' main.c && ran_shell; then
    pass "9 the .c edit landed and the shell command ran"
else
    fail "9 main.c edited: $(grep -q 'a - b' main.c && echo yes || echo NO); shell output in a tool result: $(ran_shell && echo yes || echo NO)"
fi

# 10. the model saw the stamp (its answer quotes it under LOCI LINES)
if grep -E '"type": ?"assistant\.message"' "$JSONL" | grep -q "turn=$TURN"; then
    pass "10 the model quoted [loci] turn=$TURN"
else
    fail "10 the model's answer never quotes [loci] turn=$TURN (context not in its window?)"
fi

# 11. Stop: turn-clean ran `loci build clean --turn=<id>` and kept this turn.
# The `end:` line is read AFTER the Stop `start:` — SessionStart's turn-clean
# writes the same `end: build clean rc=0` earlier in the log.
stop_tail=$(sed -n '/\[loci\.turn-clean\] .*start: Stop/,$p' "$HLOG" 2>/dev/null)
if [ -n "$stop_tail" ] \
   && printf '%s\n' "$stop_tail" | grep -qE '\[loci\.turn-clean\] .*end: build clean rc=0' \
   && [ "$cur" = "$TURN" ]; then
    pass "11 the Stop hooks cleaned the turn and kept $TURN (turn-clean.sh)"
else
    fail "11 turn-clean at Stop: $(printf '%s\n' "$stop_tail" | grep -E 'Stop|build clean' | tail -2 | cut -c1-160 | tr '\n' ' ')"
fi

if [ -s "$WORK/build.yaml.before" ] && cmp -s .loci/build.yaml "$WORK/build.yaml.before"; then
    pass "12 the recipe is unchanged (the deny held)"
else
    fail "12 the recipe changed (or build.yaml.before is missing)"
fi

# informational: where Copilot ran the hooks (the Epic saw the plugin root on Windows)
hcwd=$(grep -oE '\[loci\.session-init\] .*start: SessionStart hook \(cwd=[^ )]*' "$HLOG" 2>/dev/null | head -1 | sed 's/.*cwd=//')
[ -n "$hcwd" ] && echo "hook cwd: $hcwd"

echo
if [ "$FAILS" -eq 0 ]; then
    echo "Copilot smoke: all 12 checks passed"
else
    echo "Copilot smoke: $FAILS check(s) FAILED"
    exit 1
fi
