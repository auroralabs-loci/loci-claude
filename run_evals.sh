#!/usr/bin/env bash
# Run the LOCI plugin skill eval suite.
#
# Usage:
#   ./run_evals.sh --ble-root "C:\Playground\BLE"              # all evals
#   ./run_evals.sh --ble-root "C:\Playground\BLE" --skill char-counter  # one skill
#   ./run_evals.sh --ble-root "C:\Playground\BLE" --eval-id pf-simple-3 # one eval
#   ./run_evals.sh --ble-root "C:\Playground\BLE" --eval-id "pf-critical-*" # glob pattern
#   ./run_evals.sh --ble-root "C:\Playground\BLE" -j 4                  # 4 parallel jobs
#   ./run_evals.sh --ble-root "C:\Playground\BLE" --list                 # list all eval IDs
#   ./run_evals.sh --ble-root "C:\Playground\BLE" --verbose              # real-time output
#   LOCI_TEST_BLE_ROOT="C:\Playground\BLE" ./run_evals.sh               # env var
#
# Each eval is run via `claude -p` with the skill's SKILL.md injected as a
# system prompt.  A second `claude -p --model sonnet` call grades the response
# against the expectations in evals.json.
#
# Results are written to eval-results/<timestamp>/.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
BLE_ROOT="${LOCI_TEST_BLE_ROOT:-/home/melisa/BLE}"
FILTER_SKILL=""
FILTER_EVAL_ID=""
LIST_MODE=false
VERBOSE=false
MAX_JOBS=4
EVAL_TIMEOUT=600   # seconds per claude -p call
GRADE_TIMEOUT=120  # seconds per grader call

# Well-known BLE artifacts (relative to BLE_ROOT)
BLE_BASIC_BLE="examples/rtos/LP_EM_CC2340R5/ble5stack/basic_ble/freertos/ticlang/basic_ble.out"
BLE_DATA_STREAM="examples/rtos/LP_EM_CC2340R5/ble5stack/data_stream/freertos/ticlang/data_stream.out"

# ---------------------------------------------------------------------------
# Parse flags (same style as run_tests.sh)
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --ble-root)   BLE_ROOT="$2"; shift 2 ;;
    --ble-root=*) BLE_ROOT="${1#*=}"; shift ;;
    --skill)      FILTER_SKILL="$2"; shift 2 ;;
    --skill=*)    FILTER_SKILL="${1#*=}"; shift ;;
    --eval-id)    FILTER_EVAL_ID="$2"; shift 2 ;;
    --eval-id=*)  FILTER_EVAL_ID="${1#*=}"; shift ;;
    -j)           MAX_JOBS="$2"; shift 2 ;;
    -j=*)         MAX_JOBS="${1#*=}"; shift ;;
    --timeout)    EVAL_TIMEOUT="$2"; shift 2 ;;
    --timeout=*)  EVAL_TIMEOUT="${1#*=}"; shift ;;
    --sequential) MAX_JOBS=1; shift ;;
    --list)       LIST_MODE=true; shift ;;
    --verbose|-v) VERBOSE=true; shift ;;
    -h|--help)
      head -15 "$0" | tail -14
      exit 0
      ;;
    *) echo "Unknown flag: $1"; exit 1 ;;
  esac
done

# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------
if ! command -v claude >/dev/null 2>&1; then
  echo "ERROR: 'claude' CLI not found on PATH."
  exit 1
fi
if ! command -v jq >/dev/null 2>&1; then
  echo "ERROR: 'jq' is required but not found."
  exit 1
fi

if [[ -z "$BLE_ROOT" ]]; then
  echo "ERROR: BLE root not configured."
  echo "  Use --ble-root <path> or set LOCI_TEST_BLE_ROOT."
  exit 1
fi
if [[ ! -d "$BLE_ROOT" ]]; then
  echo "ERROR: BLE root is not a directory: $BLE_ROOT"
  exit 1
fi

# Resolve to absolute path
BLE_ROOT="$(cd "$BLE_ROOT" && pwd)"

echo "BLE root: $BLE_ROOT"

# Check for the primary test ELF
BLE_ELF="$BLE_ROOT/$BLE_BASIC_BLE"
if [[ ! -f "$BLE_ELF" ]]; then
  echo "WARNING: Primary BLE ELF not found: $BLE_ELF"
  echo "  Some evals may fail."
fi

# ---------------------------------------------------------------------------
# MCP config — written to a temp file so claude -p can connect
# ---------------------------------------------------------------------------
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
RESULTS_DIR="$SCRIPT_DIR/eval-results/$TIMESTAMP"
mkdir -p "$RESULTS_DIR"

MCP_CONFIG=""
# The loci plugin ships its MCP server config in .mcp.json at the plugin root.
# (marketplace.json / plugin.json do NOT carry an mcpServers block.) Prefer
# .mcp.json, falling back to the older locations for forward/backward compat.
PLUGIN_MCP_JSON="$(find ~/.claude/plugins/cache/loci -name .mcp.json 2>/dev/null | sort -V | tail -1)"
[[ -z "$PLUGIN_MCP_JSON" ]] && PLUGIN_MCP_JSON="$(find ~/.claude/plugins/cache/loci -name marketplace.json 2>/dev/null | sort -V | tail -1)"
[[ -z "$PLUGIN_MCP_JSON" ]] && PLUGIN_MCP_JSON="$(find ~/.claude/plugins/cache/loci -name plugin.json 2>/dev/null | sort -V | tail -1)"
if [[ -z "${ANTHROPIC_API_KEY:-}" && -n "$PLUGIN_MCP_JSON" ]]; then
  # Browser OAuth: reuse the plugin's MCP config (no Bearer token needed —
  # Claude's OAuth session authenticates with the MCP server directly).
  MCP_CONFIG="$RESULTS_DIR/.mcp-config.json"
  # Extract the mcpServers block with jq (NOT python -c): jq runs in the MSYS
  # shell and resolves the /c/... path that `find` emits, whereas Windows-native
  # python's open() rejects MSYS paths and dies with FileNotFoundError.
  #   .mcp.json / plugin.json: servers at top-level .mcpServers
  #   marketplace.json:        servers under .plugins[0].mcpServers
  jq '{mcpServers: (.mcpServers // .plugins[0].mcpServers // {})}' \
    "$PLUGIN_MCP_JSON" > "$MCP_CONFIG"
  echo "MCP: using plugin config ($PLUGIN_MCP_JSON, OAuth session auth)"
else
  echo "MCP: skipped (skills will use fallback paths)"
fi

# ---------------------------------------------------------------------------
# Stale-artifact fixture (the reported bug, staged deterministically)
# ---------------------------------------------------------------------------
# A tester reported an execution trace based on a linked ELF older than the edit
# that prompted it. Reproducing that needs three artifacts in a specific mtime
# relationship, which git cannot carry (it does not preserve mtimes), so the tree
# is built here from committed sources rather than checked in:
#
#   kernel.elf                     linked from blink_pre.c    — 300 s old
#   blink.c                        == blink_post.c (the edit)  —  75 s old
#   .loci-build/armv6-m/blink.o    compiled from blink.c       —  60 s old
#
# The ELF is 225 s older than its own source and describes a different program
# (`build_pattern` and `render_frame` do not exist in it), and the freshest
# artifact in the tree is the object a post-edit run would have written. Numbers
# the evals assert, all re-derived from this fixture:
#
#   gcc -fstack-usage   kernel_main 8, build_pattern 3088, render_frame 24
#   relinked ELF        3120 B = 152.3% of a 2048 B budget → FAIL
#   the fresh .o alone  8 B, path [kernel_main] → PASS, has_unknown_callees false
#
# That last line is why the eval demands 3120 rather than merely "not the stale
# answer": in a relocatable object the `bl` is an unapplied relocation, so switching
# to the fresh `.o` loses the call edge and hides the buffer entirely. 3120 can only
# come from a relink, so it proves both halves of the fix.
#
# The buffer is 3072 bytes, NOT the 4096 of the scenario this reconstructs, because
# the eval system prompt now inlines SKILL.md — which carries a worked example of
# the real 4096-byte case. Reusing those constants would let the assertions be
# satisfied by transcription instead of measurement.
STALE_ROOT=""
stage_stale_tree() {
  local src="$SCRIPT_DIR/evals/fixtures/stale-artifact"
  local cc; cc=$(command -v arm-none-eabi-gcc 2>/dev/null) || return 1
  [[ -d "$src" ]] || return 1
  local dir="$RESULTS_DIR/stale-artifact-tree"
  rm -rf "$dir"
  mkdir -p "$dir/.loci-build/armv6-m" || return 1
  cp "$src/startup.c" "$src/fixture.ld" "$src/blink_pre.c" "$src/blink_post.c" \
     "$dir/" || return 1

  local cf=(-g -nostartfiles -O0 -mcpu=cortex-m0plus -mthumb)
  # Each step checked on its own. An earlier revision wrapped these in
  # `( set -e; … ) || return 1`, where the `set -e` is DEAD: the `||` puts the
  # subshell in a context that suppresses errexit, and so does the
  # `if stage_stale_tree; then` caller. Failure detection collapsed to "did the LAST
  # command succeed", so a broken linker script produced a 0-byte kernel.elf that
  # was announced as a valid fixture — the linker error swallowed by `2>&1`, `touch`
  # happy on an empty file, and the nm guard below unable to tell "nm failed" from
  # "no match". Exactly the vacuous guard the comment there warns about.
  local log="$dir/stage.log"
  (
    cd "$dir" || exit 1
    # 1. The artifact that goes stale: linked from the PRE source.
    cp blink_pre.c blink.c || exit 1
    "$cc" "${cf[@]}" -Wl,-T,fixture.ld blink.c startup.c -o kernel.elf || exit 1
    # 2. The edit.
    cp blink_post.c blink.c || exit 1
    # 3. The object a post-edit run would have written from the edited source.
    "$cc" "${cf[@]}" -c blink.c -o .loci-build/armv6-m/blink.o || exit 1
    # 4. The two variants are scaffolding, not part of the tree under test: leaving
    #    them hands the model the entire edit as a diff, and breaks any `gcc *.c`
    #    build with "multiple definition of kernel_main".
    rm -f blink_pre.c blink_post.c || exit 1
  ) >"$log" 2>&1 || {
    echo "  fixture rejected: staging failed, see $log" >&2
    return 1
  }
  # Non-empty outputs, since a linker can "succeed" into nothing useful.
  for f in kernel.elf .loci-build/armv6-m/blink.o; do
    [ -s "$dir/$f" ] || { echo "  fixture rejected: $f is empty" >&2; return 1; }
  done

  # Backdate into the reported relationship. Absolute epochs, not `touch -r`, so
  # the 225 s gap the eval quotes is exact rather than however long the build took.
  # GNU coreutils take `-d @<epoch>`; macOS/BSD touch does not, and wants
  # `-t [[CC]YY]MMDDhhmm[.SS]` — same GNU-first split as `_freshest_elf`'s stat.
  local now; now=$(date +%s)
  _set_mtime() {
    local epoch="$1" path="$2" stamp
    touch -d "@$epoch" "$path" 2>/dev/null && return 0
    stamp=$(date -r "$epoch" +%Y%m%d%H%M.%S 2>/dev/null) || return 1
    touch -t "$stamp" "$path" 2>/dev/null
  }
  _set_mtime "$((now - 300))" "$dir/kernel.elf" || return 1
  _set_mtime "$((now - 75))"  "$dir/blink.c" || return 1
  _set_mtime "$((now - 60))"  "$dir/.loci-build/armv6-m/blink.o" || return 1

  # Prove the fixture really is in the failing state before any eval trusts it. A
  # fixture that quietly staged the *fresh* ELF would make both evals pass for the
  # wrong reason — the exact class of vacuous guard this repo has been bitten by.
  #
  # Positive control first: nm must SEE the symbol that is supposed to be there.
  # `grep -q` returns 1 for "no match" and for "nm printed nothing at all", so
  # without this the absence check below passes when nm is missing or the ELF is
  # unreadable — which is how a broken fixture would look exactly like a good one.
  local syms
  syms=$(arm-none-eabi-nm "$dir/kernel.elf" 2>/dev/null) || {
    echo "  fixture rejected: nm could not read kernel.elf" >&2; return 1; }
  printf '%s' "$syms" | grep -q "kernel_main" || {
    echo "  fixture rejected: kernel.elf has no kernel_main (nm broken, or a bad link)" >&2
    return 1; }
  if printf '%s' "$syms" | grep -qE "build_pattern|render_frame"; then
    echo "  fixture rejected: kernel.elf already contains the post-edit functions" >&2
    return 1
  fi
  # GNU-first, BSD fallback — same split as `_freshest_elf` and
  # `find_loci_artifacts`. `stat -c` alone rejected a perfectly good fixture on
  # macOS (BSD stat has no -c), so both regression evals silently never ran there.
  _mtime_of() { stat -c %Y "$1" 2>/dev/null || stat -f %m "$1" 2>/dev/null; }
  local e_mt s_mt o_mt
  e_mt=$(_mtime_of "$dir/kernel.elf") || return 1
  s_mt=$(_mtime_of "$dir/blink.c") || return 1
  o_mt=$(_mtime_of "$dir/.loci-build/armv6-m/blink.o") || return 1
  [ -n "$e_mt" ] && [ -n "$s_mt" ] && [ -n "$o_mt" ] || {
    echo "  fixture rejected: could not read mtimes (no usable stat)" >&2; return 1; }
  if (( e_mt >= s_mt || s_mt >= o_mt )); then
    echo "  fixture rejected: mtimes are not elf < source < object" >&2
    return 1
  fi

  # A project context, because B2 reads `elf_files` / `loci_artifacts` from one and
  # SESSION_CONTEXT points the model at this path. Without it, B2 ranks 2-3 are
  # unfollowable inside the very eval meant to test them. Generated by the real
  # detector, so the schema cannot drift from production.
  ( cd "$dir" && bash "$SCRIPT_DIR/lib/detect-project.sh" \
      > "$dir/.loci-build/context.json" 2>/dev/null ) || true
  if ! jq -e '.loci_artifacts' "$dir/.loci-build/context.json" >/dev/null 2>&1; then
    echo "  fixture rejected: could not generate a project context" >&2
    return 1
  fi

  # These evals exercise the *gated* path, so the `loci` on PATH must have the gate.
  # Without this check a CLI-version gap reports as a skill FAIL — and the contract
  # explicitly tells the model NOT to run the gate on an old CLI, so the assertion
  # "runs loci build fresh" would be demanding the opposite of the rule.
  if ! loci build fresh --elf "$dir/kernel.elf" >/dev/null 2>&1; then
    echo "  fixture rejected: the loci on PATH has no working \`build fresh\`" \
         "($(loci --version 2>&1 | head -1)) — needs the CLI these skills pin" >&2
    return 1
  fi

  STALE_ROOT="$dir"
  return 0
}

# `--list` prints names and exits; staging first cost a ~27 s cross-compile and left
# a tree nothing read. STALE_ROOT stays empty, which only affects the skip message.
if $LIST_MODE; then
  echo "Stale-artifact fixture: not staged (--list)"
elif stage_stale_tree; then
  echo "Stale-artifact fixture: $STALE_ROOT (kernel.elf 225s older than blink.c)"
else
  echo "Stale-artifact fixture: unavailable — sd-5/sd-6 will be skipped"
fi

# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------
GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[0;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

REPORT="$RESULTS_DIR/report.md"
cat > "$REPORT" <<EOF
# Eval Report — $TIMESTAMP

| Skill | Eval | Verdict | Notes |
|-------|------|---------|-------|
EOF

echo -e "${BOLD}Skill Eval Runner${NC}  ($TIMESTAMP)"
echo "Results → $RESULTS_DIR/"
echo "Parallelism: $MAX_JOBS jobs"
$VERBOSE && echo "Verbose: ON (real-time output to terminal)"
echo ""

# ---------------------------------------------------------------------------
# Build session context that evals expect to be present
# ---------------------------------------------------------------------------
SESSION_CONTEXT="BLE project root: $BLE_ROOT
Primary test ELF: $BLE_ELF
plugin dir: $SCRIPT_DIR"
if [[ -n "$STALE_ROOT" ]]; then
  # The stale-artifact evals need a resolved LOCI target the way a real session
  # gets one from SessionStart; without it the skill has to guess an --arch.
  # `Build:` must not claim `make` — there is no Makefile in the staged tree, and
  # B3's rebuild step 1 would send the model at a build system that does not exist
  # ("No targets specified and no makefile found"). `direct` matches reality: the
  # eval prompt carries the exact compiler command line instead.
  SESSION_CONTEXT="$SESSION_CONTEXT
Stale-artifact fixture root: $STALE_ROOT
  Target: armv6-m, Compiler: arm-none-eabi-gcc, Build: direct
  LOCI target: armv6-m
  project context: $STALE_ROOT/.loci-build/context.json"
fi

# ---------------------------------------------------------------------------
# print_error_detail — structured diagnostics for ERROR outcomes
#   $1: stage        ("claude-exec" | "timeout" | "empty-response" | "grade")
#   $2: exit code    (numeric, or empty)
#   $3: stderr file  (path, or empty string if none)
#   $4: tag          (SKILL:EVAL_ID)
#   $5: mcp config   (path, for next-steps hints)
#   $6: response file (path, for next-steps hints; may not exist yet)
# ---------------------------------------------------------------------------
print_error_detail() {
  local STAGE="$1"
  local EXIT_CODE="$2"
  local STDERR_F="$3"
  local TAG="$4"
  local MCP_CFG="${5:-}"
  local RESPONSE_F="${6:-}"

  echo "    ── Error Detail [$TAG] ──────────────────────────────────"
  echo "    Stage:    $STAGE"

  case "$STAGE" in
    claude-exec)
      echo "    Observed: claude CLI exited with code $EXIT_CODE"
      if [[ -n "$STDERR_F" && -s "$STDERR_F" ]]; then
        echo "    Stderr (first 5 lines):"
        head -5 "$STDERR_F" | sed 's/^/      /'
      else
        echo "    Stderr:   (empty)"
      fi
      echo "    Likely causes:"
      echo "      • Auth failure or expired API key"
      echo "      • Token / rate-limit exhaustion"
      echo "      • Network or DNS error reaching Anthropic API"
      echo "      • MCP server unreachable (config: ${MCP_CFG:-unknown})"
      echo "      • Claude CLI bug or version mismatch"
      echo "    Next steps:"
      echo "      1. Run 'claude -p \"hello\"' manually to verify auth"
      if [[ -n "$STDERR_F" && -s "$STDERR_F" ]]; then
        echo "      2. Inspect full stderr: cat $STDERR_F"
      fi
      echo "      3. Verify MCP server is up: curl ${MCP_CFG:+see $MCP_CFG}"
      ;;
    timeout)
      echo "    Observed: no response within ${EVAL_TIMEOUT}s (exit 124)"
      echo "    Likely causes:"
      echo "      • Anthropic backend delay or overload"
      echo "      • Very large prompt pushing context limits"
      echo "      • MCP tool call hanging (check MCP server logs)"
      echo "      • Network congestion or DNS timeout"
      echo "    Next steps:"
      echo "      1. Re-run with a higher --timeout value"
      echo "      2. Check MCP server health"
      echo "      3. Try a minimal prompt to isolate the hang"
      ;;
    empty-response)
      echo "    Observed: claude exited 0 but produced no output"
      echo "    Likely causes:"
      echo "      • Prompt triggered a content refusal with no text output"
      echo "      • System prompt conflict suppressing all output"
      echo "      • Claude CLI piping issue swallowing stdout"
      echo "    Next steps:"
      echo "      1. Run the prompt manually: claude -p \"<prompt>\" to see raw output"
      echo "      2. Simplify the system prompt and retry"
      ;;
    grade)
      echo "    Observed: grader claude call failed (exit $EXIT_CODE)"
      if [[ -n "$STDERR_F" && -s "$STDERR_F" ]]; then
        echo "    Stderr (first 5 lines):"
        head -5 "$STDERR_F" | sed 's/^/      /'
      else
        echo "    Stderr:   (empty)"
      fi
      echo "    Likely causes:"
      echo "      • Same as claude-exec errors (auth, rate limit, network)"
      echo "      • Grader prompt too large (response + expectations exceed context)"
      if [[ -n "$RESPONSE_F" ]]; then
        echo "      • Response file: $RESPONSE_F"
      fi
      echo "    Next steps:"
      if [[ -n "$RESPONSE_F" && -f "$RESPONSE_F" ]]; then
        echo "      1. Check response size: wc -c $RESPONSE_F"
      fi
      echo "      2. Re-run the grader manually against the saved response file"
      ;;
  esac
  echo "    ─────────────────────────────────────────────────────────"
}

# ---------------------------------------------------------------------------
# Graders (deterministic). Defined in lib/eval-graders.sh so they can be sourced
# by a unit test — this file cd's into the fixture root at the top level, so it
# cannot be sourced itself.
# ---------------------------------------------------------------------------
# shellcheck source=lib/eval-graders.sh
source "$SCRIPT_DIR/lib/eval-graders.sh"
# ---------------------------------------------------------------------------
# run_one_eval — runs a single eval (prompt + grade) and writes result files
#   Called either inline (sequential) or as a background job (parallel).
#   All output goes to a log file; the caller prints it.
# ---------------------------------------------------------------------------
run_one_eval() {
  local SKILL_NAME="$1"
  local EVAL_ID="$2"
  local PROMPT="$3"
  local EXPECTED="$4"
  local EXPECTATIONS="$5"
  local SYSTEM_PROMPT="$6"
  local MCP_CONFIG="$7"
  local RESULTS_DIR="$8"
  local EVAL_TIMEOUT="$9"
  local GRADE_TIMEOUT="${10}"
  local EVAL_FILE_NAME="${11}"
  local JOB_NUM="${12}"
  local GRADING_MODE="${13:-claude}"
  local SHOULD_TRIGGER="${14:-true}"
  local FLOW="${15:-single}"
  local SOURCE_FILE="${16:-}"
  local APPROVE_PROMPT="${17:-Approved. Implement the plan exactly as described now. Edit the source file directly.}"
  # Only the literal "true" turns these on — see lib/eval-graders.sh for why an
  # absent value must not be read as "a baseline is required".
  local EXPECT_BASELINE="${18:-false}"
  local EXPECT_NO_CHANGE="${19:-false}"

  local TAG="${EVAL_FILE_NAME} > ${EVAL_ID}"
  local PROG_PFX="[${JOB_NUM}/${TOTAL}]"
  local RESPONSE_FILE="$RESULTS_DIR/${SKILL_NAME}_eval${EVAL_ID}_response.txt"
  local STDERR_FILE="$RESULTS_DIR/${SKILL_NAME}_eval${EVAL_ID}_stderr.txt"
  local GRADE_FILE="$RESULTS_DIR/${SKILL_NAME}_eval${EVAL_ID}_grade.txt"
  local VERDICT_FILE="$RESULTS_DIR/${SKILL_NAME}_eval${EVAL_ID}_verdict.txt"
  local LOG_FILE="$RESULTS_DIR/${SKILL_NAME}_eval${EVAL_ID}_log.txt"
  local MASTER_LOG="$RESULTS_DIR/master.log"

  # Leading slash commands are interactive-session affordances that have no
  # meaning when piped to `claude -p` — headless mode treats `/plan ...` as an
  # unavailable slash command and answers "/plan isn't available in this
  # environment" without ever running the prompt. So we translate a leading
  # `/plan` into the real headless equivalent: strip the token and start the
  # session in plan mode via `--permission-mode plan` (set below). That is the
  # signal the preflight skill gates on ("MANDATORY in /plan mode") and keeps
  # the invoke / no-invoke pairs distinct. Any OTHER leading slash command
  # (/review, etc.) is simply stripped, since it has no bearing on these evals.
  local PLAN_MODE=false
  if [[ "$PROMPT" == "/plan "* || "$PROMPT" == "/plan" ]]; then
    PLAN_MODE=true
    PROMPT="${PROMPT#/plan}"
    PROMPT="${PROMPT# }"
  else
    PROMPT=$(echo "$PROMPT" | sed 's|^/[a-zA-Z_-]* ||')
  fi

  # log_eval: writes to both the per-eval log and master log.
  # In verbose mode, also writes to stderr (which reaches the terminal).
  log_eval() {
    local ts
    ts="$(date +%H:%M:%S)"
    local line="[$ts] $PROG_PFX $*"
    echo "$line" >> "$LOG_FILE"
    echo "$line" >> "$MASTER_LOG"
    if $VERBOSE; then
      echo -e "$line" >&2
    fi
  }

  # Reset log file
  : > "$LOG_FILE"

  log_eval "START  $TAG"
  log_eval "Prompt: ${PROMPT:0:120}..."
  echo "${PROG_PFX} START    ${TAG}" >> "$PROGRESS_LOG"

  # ── Combined two-turn flow (preflight in /plan → resume+edit → post-edit) ──
  # Self-contained path: runs both turns, grades the JOINED transcript, and
  # restores the edited source. Kept separate from the single-turn code below so
  # the existing (working) preflight/post-edit evals are untouched.
  #
  # Faithful to the manual workflow: turn 1 runs in plan mode (preflight is
  # MANDATORY, read-only — no edit); turn 2 RESUMES the same session with
  # acceptEdits + an approval message (the programmatic equivalent of the user
  # clicking "approve"), which exits plan mode, makes the real edit, and fires
  # post-edit. Both turns run from inside BLE_ROOT with the loci plugin loaded
  # (NEVER --bare here — the pre-edit hook must capture .o.prev for a real % diff).
  if [[ "$FLOW" == "two-turn" ]]; then
    local T1_JSON="$RESULTS_DIR/${SKILL_NAME}_eval${EVAL_ID}_turn1.json"
    local T2_JSON="$RESULTS_DIR/${SKILL_NAME}_eval${EVAL_ID}_turn2.json"
    local JSON_FILE="$RESULTS_DIR/${SKILL_NAME}_eval${EVAL_ID}_full.json"
    local SRC_ABS="$BLE_ROOT/$SOURCE_FILE"
    local SRC_BACKUP="$RESULTS_DIR/${SKILL_NAME}_eval${EVAL_ID}_src.bak"

    if [[ -z "$SOURCE_FILE" || ! -f "$SRC_ABS" ]]; then
      log_eval "ERROR: source_file missing or not found: ${SRC_ABS:-<unset>}"
      echo "ERROR|source_file missing or not found: ${SRC_ABS:-<unset>}" > "$VERDICT_FILE"
      echo "${PROG_PFX} DONE     ${TAG}  ERROR (no source)" >> "$PROGRESS_LOG"
      return
    fi
    # Snapshot exact pre-run contents so we restore whatever was there (committed
    # OR uncommitted) — safer than `git checkout`, which would discard any local
    # edits the user already had in the file.
    cp "$SRC_ABS" "$SRC_BACKUP"

    # NON-bare: plugin + hooks must load. stream-json carries every turn.
    local C_ARGS=(-p --dangerously-skip-permissions --output-format stream-json --verbose)
    [[ -n "$MCP_CONFIG" ]] && C_ARGS+=(--mcp-config "$MCP_CONFIG")

    # ── Turn 1: plan mode → preflight, NO edit ──
    # --dangerously-skip-permissions is required so preflight's Bash (asm-analyze)
    # and MCP calls run unattended — but skip-permissions also DEFEATS plan mode's
    # read-only guard, so without further restriction the model would edit (and even
    # run post-edit) in this turn, collapsing the gated two-turn flow. So we
    # explicitly disallow the edit tools here: preflight still runs, but any edit is
    # blocked until the resume turn. --permission-mode plan stays on because the
    # preflight skill GATES on plan mode ("MANDATORY in /plan mode").
    log_eval "TURN 1 (plan mode → preflight, edits blocked)"
    echo "${PROG_PFX} RUNNING  ${TAG}  [turn1/plan]" >> "$PROGRESS_LOG"
    local T1_EXIT=0 T1_START T1_END
    T1_START=$(date +%s)
    ( cd "$BLE_ROOT" && echo "$PROMPT" | timeout --kill-after=10 "$EVAL_TIMEOUT" \
        claude "${C_ARGS[@]}" --permission-mode plan \
        --disallowedTools Edit Write NotebookEdit ) >"$T1_JSON" 2>"$STDERR_FILE" || T1_EXIT=$?
    T1_END=$(date +%s)
    log_eval "turn1 exit $T1_EXIT after $((T1_END - T1_START))s"

    local SID R1=""
    SID=$(jq -rs '[.[]|select(.type=="system" and .subtype=="init")|.session_id]|last // empty' "$T1_JSON" 2>/dev/null || true)
    R1=$(jq -rs '[.[]|select(.type=="assistant")|.message.content[]?|select(.type=="text")|.text]|join("\n")' "$T1_JSON" 2>/dev/null || true)
    log_eval "turn1 session_id: ${SID:-<none>}, response ${#R1} chars"

    # Guard: plan mode must NOT have edited the file. If it did, note it (the
    # restore below still cleans up, but it signals a plan-mode violation).
    if ! diff -q "$SRC_ABS" "$SRC_BACKUP" >/dev/null 2>&1; then
      log_eval "WARNING: source changed during plan turn — plan mode should be read-only"
    fi

    # ── Turn 2: resume + acceptEdits → real edit → post-edit ──
    local R2="" T2_EXIT=0 T2_TOOLS=""
    if [[ -n "$SID" ]]; then
      log_eval "TURN 2 (resume + acceptEdits → edit → post-edit)"
      echo "${PROG_PFX} RUNNING  ${TAG}  [turn2/edit]" >> "$PROGRESS_LOG"
      local T2_START T2_END
      T2_START=$(date +%s)
      ( cd "$BLE_ROOT" && echo "$APPROVE_PROMPT" | timeout --kill-after=10 "$EVAL_TIMEOUT" \
          claude "${C_ARGS[@]}" --resume "$SID" --permission-mode acceptEdits ) >"$T2_JSON" 2>>"$STDERR_FILE" || T2_EXIT=$?
      T2_END=$(date +%s)
      R2=$(jq -rs '[.[]|select(.type=="assistant")|.message.content[]?|select(.type=="text")|.text]|join("\n")' "$T2_JSON" 2>/dev/null || true)
      T2_TOOLS=$(jq -rs '[.[]|select(.type=="assistant")|.message.content[]?|select(.type=="tool_use")|.name]|unique|join(", ")' "$T2_JSON" 2>/dev/null || true)
      log_eval "turn2 exit $T2_EXIT after $((T2_END - T2_START))s, response ${#R2} chars"
      log_eval "turn2 tools: ${T2_TOOLS:-<none>}"
    else
      log_eval "ERROR: no session_id from turn 1 — cannot resume into the edit turn"
    fi

    # ── Restore source (hermetic; .loci-build artifacts are left — they are
    #    overwritten next run, and a blanket rm could wipe a seeded flags.json) ──
    cp "$SRC_BACKUP" "$SRC_ABS"
    log_eval "restored source: $SOURCE_FILE"

    # ── Join both turns → grade ──
    RESPONSE="$R1
$R2"
    echo "$RESPONSE" > "$RESPONSE_FILE"
    cp "$T2_JSON" "$JSON_FILE" 2>/dev/null || cp "$T1_JSON" "$JSON_FILE" 2>/dev/null || true

    if [[ -z "${R1// }" && -z "${R2// }" ]]; then
      log_eval "ERROR: empty response from both turns (t1 exit $T1_EXIT, t2 exit $T2_EXIT, sid=${SID:-none})"
      echo "ERROR|empty response from both turns (t1=$T1_EXIT, t2=$T2_EXIT, sid=${SID:-none})" > "$VERDICT_FILE"
      echo "${PROG_PFX} DONE     ${TAG}  ERROR (empty)" >> "$PROGRESS_LOG"
      return
    fi

    local CV VERDICT REASON
    CV=$(grade_bash_combined "$RESPONSE")
    echo "$CV" > "$GRADE_FILE"
    VERDICT="${CV%%|*}"; REASON="${CV#*|}"
    echo "${VERDICT}|${REASON}" > "$VERDICT_FILE"
    echo "${PROG_PFX} DONE     ${TAG}  ${VERDICT}" >> "$PROGRESS_LOG"
    log_eval "VERDICT: $VERDICT — $REASON"
    return
  fi

  # ── Single-turn edit flow (real edit → post-edit auto-fires) ──
  # Tests loci-post-edit in ISOLATION — no plan, no preflight. One acceptEdits
  # turn runs a natural change request (a client-style ticket). With the loci
  # plugin loaded (cwd = BLE_ROOT, NOT --bare), the pre-edit hook captures
  # <basename>.o.prev BEFORE the Edit, Claude edits the real source, and the
  # SessionStart auto-run rule ("after any Edit you MUST invoke loci-post-edit")
  # fires the skill, which emits its '## Post-Edit:' report + 'Verdict:' line
  # with a real % diff against the captured baseline. The source is backed up
  # and restored so the run is hermetic. This is exactly the two-turn flow's
  # SECOND turn, standalone — same realism, but post-edit is the only skill
  # under test. Graded by grade_bash_post_edit. No SKILL.md is injected as a
  # system prompt here: the real plugin must auto-invoke post-edit on its own,
  # which is the behavior being tested.
  if [[ "$FLOW" == "edit" ]]; then
    local JSON_FILE="$RESULTS_DIR/${SKILL_NAME}_eval${EVAL_ID}_full.json"
    local SRC_ABS="$BLE_ROOT/$SOURCE_FILE"
    local SRC_BACKUP="$RESULTS_DIR/${SKILL_NAME}_eval${EVAL_ID}_src.bak"

    if [[ -z "$SOURCE_FILE" || ! -f "$SRC_ABS" ]]; then
      log_eval "ERROR: source_file missing or not found: ${SRC_ABS:-<unset>}"
      echo "ERROR|source_file missing or not found: ${SRC_ABS:-<unset>}" > "$VERDICT_FILE"
      echo "${PROG_PFX} DONE     ${TAG}  ERROR (no source)" >> "$PROGRESS_LOG"
      return
    fi
    # Snapshot exact pre-run contents so we restore whatever was there
    # (committed OR uncommitted) — safer than `git checkout`.
    cp "$SRC_ABS" "$SRC_BACKUP"

    # NON-bare: plugin + hooks must load so the pre-edit hook captures .o.prev
    # and the auto-run rule fires post-edit. stream-json carries every turn.
    local C_ARGS=(-p --dangerously-skip-permissions --output-format stream-json --verbose)
    [[ -n "$MCP_CONFIG" ]] && C_ARGS+=(--mcp-config "$MCP_CONFIG")

    log_eval "EDIT turn (acceptEdits → real edit → post-edit auto-fires)"
    echo "${PROG_PFX} RUNNING  ${TAG}  [edit]" >> "$PROGRESS_LOG"
    local E_EXIT=0 E_START E_END
    E_START=$(date +%s)
    ( cd "$BLE_ROOT" && echo "$PROMPT" | timeout --kill-after=10 "$EVAL_TIMEOUT" \
        claude "${C_ARGS[@]}" --permission-mode acceptEdits ) >"$JSON_FILE" 2>"$STDERR_FILE" || E_EXIT=$?
    E_END=$(date +%s)
    log_eval "edit turn exit $E_EXIT after $((E_END - E_START))s"

    local RESPONSE E_TOOLS
    RESPONSE=$(jq -rs '[.[]|select(.type=="assistant")|.message.content[]?|select(.type=="text")|.text]|join("\n")' "$JSON_FILE" 2>/dev/null || true)
    E_TOOLS=$(jq -rs '[.[]|select(.type=="assistant")|.message.content[]?|select(.type=="tool_use")|.name]|unique|join(", ")' "$JSON_FILE" 2>/dev/null || true)
    log_eval "edit turn tools: ${E_TOOLS:-<none>}, response ${#RESPONSE} chars"

    # The whole point is "post-edit fires AFTER a change" — confirm an edit
    # actually landed. If the file is unchanged, post-edit had nothing to react
    # to; note it (the grader will FAIL on a missing report regardless).
    if diff -q "$SRC_ABS" "$SRC_BACKUP" >/dev/null 2>&1; then
      log_eval "WARNING: source unchanged — no edit was made; post-edit had nothing to react to"
    fi

    # Restore source (hermetic; .loci-build artifacts are left — overwritten
    # next run, and a blanket rm could wipe a seeded flags.json).
    cp "$SRC_BACKUP" "$SRC_ABS"
    log_eval "restored source: $SOURCE_FILE"

    echo "$RESPONSE" > "$RESPONSE_FILE"

    if [[ -z "${RESPONSE// }" ]]; then
      log_eval "ERROR: empty response from edit turn (exit $E_EXIT)"
      echo "ERROR|empty response from edit turn (exit $E_EXIT)" > "$VERDICT_FILE"
      echo "${PROG_PFX} DONE     ${TAG}  ERROR (empty)" >> "$PROGRESS_LOG"
      return
    fi

    local PV VERDICT REASON
    PV=$(grade_bash_post_edit "$RESPONSE" "$SHOULD_TRIGGER" "$EXPECT_BASELINE" "$EXPECT_NO_CHANGE")
    echo "$PV" > "$GRADE_FILE"
    VERDICT="${PV%%|*}"; REASON="${PV#*|}"
    echo "${VERDICT}|${REASON}" > "$VERDICT_FILE"
    echo "${PROG_PFX} DONE     ${TAG}  ${VERDICT}" >> "$PROGRESS_LOG"
    log_eval "VERDICT: $VERDICT — $REASON"
    return
  fi

  # ── Step 1: Run the eval prompt ────────────────────────────
  # --bare skips hooks/plugins so eval measures the skill, not setup overhead.
  # NOTE: --bare disables OAuth/keychain auth — only use it when ANTHROPIC_API_KEY
  # is set (API billing). With browser-based OAuth, omit --bare so auth works.
  local CLAUDE_ARGS=(-p --dangerously-skip-permissions)
  if $PLAN_MODE; then
    # Headless equivalent of typing /plan in an interactive session — puts the
    # run in plan mode so the preflight skill's "MANDATORY in /plan mode" gate
    # actually fires. This replaces the (unsupported) /plan prompt prefix.
    CLAUDE_ARGS+=(--permission-mode plan)
  fi
  if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
    CLAUDE_ARGS+=(--bare)
  fi
  if [[ -n "$MCP_CONFIG" ]]; then
    CLAUDE_ARGS+=(--mcp-config "$MCP_CONFIG")
  fi
  if [[ -n "$SYSTEM_PROMPT" ]]; then
    # Pass the system prompt via a FILE, not an inline argv string. The
    # preflight SKILL.md is ~33KB and Windows caps the whole CreateProcess
    # command line at 32,767 chars — an inline --append-system-prompt blows
    # that limit, so the MSYS `timeout` can't exec claude.exe (E2BIG) and
    # exits 126 ("Argument list too long") before the model ever runs. The
    # *-file variant passes only the path, sidestepping the limit entirely.
    local SYSPROMPT_FILE="$RESULTS_DIR/${SKILL_NAME}_eval${EVAL_ID}_sysprompt.txt"
    printf '%s' "$SYSTEM_PROMPT" > "$SYSPROMPT_FILE"
    CLAUDE_ARGS+=(--append-system-prompt-file "$SYSPROMPT_FILE")
  fi

  # ALWAYS request JSON output. `claude -p` plain-text stdout is only the FINAL
  # turn's text — but the preflight skill can emit its "## Preflight:" report in
  # an intermediate turn and then keep narrating, so the final turn alone drops
  # the report and the grader sees nothing. JSON carries every assistant turn,
  # so we can grade the COMPLETE output (see extraction below). --verbose just
  # adds internal debug logging to stderr; keep it gated behind $VERBOSE.
  # Use stream-json, NOT plain json. `--output-format json` collapses the run
  # to a SINGLE result object ({"type":"result","result":"..."}) — the final
  # turn only — so the all-turns join below would miss a report emitted in an
  # intermediate turn. Worse, the jq queries iterate `.[]` expecting an event
  # ARRAY; over a lone object `.[]` walks its scalar VALUES, matches no
  # assistant/result event, and yields "" → a complete response is misgraded as
  # an empty-response ERROR. stream-json emits every event as JSONL (one object
  # per line), which the `-s`-slurped queries below grade correctly. stream-json
  # REQUIRES --verbose, so it's always on here (gated $VERBOSE only adds the
  # stderr debug echo).
  local JSON_FILE="$RESULTS_DIR/${SKILL_NAME}_eval${EVAL_ID}_full.json"
  CLAUDE_ARGS+=(--output-format stream-json --verbose)

  log_eval "Executing: timeout ${EVAL_TIMEOUT}s claude ${CLAUDE_ARGS[*]:0:6} ..."
  echo "${PROG_PFX} RUNNING  ${TAG}" >> "$PROGRESS_LOG"

  # Write stdout directly to file so partial output survives timeout.
  local CLAUDE_EXIT=0
  local T_START T_END T_ELAPSED
  T_START=$(date +%s)
  echo "$PROMPT" | timeout --kill-after=10 "$EVAL_TIMEOUT" claude "${CLAUDE_ARGS[@]}" \
    >"$RESPONSE_FILE" 2>"$STDERR_FILE" || CLAUDE_EXIT=$?
  T_END=$(date +%s)
  T_ELAPSED=$((T_END - T_START))

  log_eval "claude exited with code $CLAUDE_EXIT after ${T_ELAPSED}s"

  # Extract plain text from JSON output and log tool usage.
  local RESPONSE=""
  local TOOL_CALLS="(no tool calls captured)"
  if [[ -s "$RESPONSE_FILE" ]]; then
    cp "$RESPONSE_FILE" "$JSON_FILE"

    # JSON output is an array of events: system, user, assistant, result.
    # Grade the COMPLETE output: join EVERY assistant text block across all
    # turns. The skill may print its "## Preflight:" report (header + Safety/
    # Performance/Energy table + Execution fit verdict) in an intermediate turn
    # and then keep narrating; the final-turn `.result` alone would miss it.
    # Joining all assistant text mirrors what a user sees in an interactive
    # session. Fall back to `.result` only if no assistant text was captured.
    RESPONSE=$(jq -rs '[.[] | select(.type == "assistant") | .message.content[]? | select(.type == "text") | .text] | join("\n")' "$JSON_FILE" 2>/dev/null || true)
    if [[ -z "$RESPONSE" ]]; then
      RESPONSE=$(jq -rs '[.[] | select(.type == "result") | .result // empty] | last // empty' "$JSON_FILE" 2>/dev/null || true)
    fi

    # Log tool usage from assistant messages
    local tool_summary
    tool_summary=$(jq -rs '
      [.[] | select(.type == "assistant") | .message.content[]? | select(.type == "tool_use") | .name] |
      if length > 0 then "Tools (" + (length | tostring) + "): " + (. | join(", ")) else empty end
    ' "$JSON_FILE" 2>/dev/null || true)
    if [[ -n "$tool_summary" ]]; then
      log_eval "$tool_summary"
    fi

    # …and make the tool CALLS graded, not just logged. Assertions of the form
    # "runs `loci build fresh`" or "rebuilds/relinks" describe *behaviour*, and the
    # grader only ever saw assistant text — so a model that merely narrated "the ELF
    # looks older, I'll relink" passed, while one that ran the gate silently failed.
    # Bash commands and file paths are what those assertions are actually about.
    TOOL_CALLS=$(jq -rs '
      [ .[] | select(.type == "assistant") | .message.content[]?
        | select(.type == "tool_use")
        | .name + ": " + ((.input.command // .input.file_path // .input.pattern // "")
                          | tostring | .[0:400]) ]
      | if length > 0 then join("
") else "(no tool calls)" end
    ' "$JSON_FILE" 2>/dev/null || echo "(tool calls unavailable)")

    # Log cost/usage from result event
    local usage_info
    usage_info=$(jq -rs '
      .[] | select(.type == "result") |
      "Turns: \(.num_turns // "?"), Cost: $\(.total_cost_usd // "?"), Duration: \((.duration_ms // 0) / 1000 | floor)s, Stop: \(.stop_reason // "?")"
    ' "$JSON_FILE" 2>/dev/null || true)
    if [[ -n "$usage_info" ]]; then
      log_eval "$usage_info"
    fi

    # Write plain text for grading
    if [[ -n "$RESPONSE" ]]; then
      echo "$RESPONSE" > "$RESPONSE_FILE"
    fi
  else
    RESPONSE=$(cat "$RESPONSE_FILE" 2>/dev/null || true)
  fi

  # Always log stderr (contains --verbose debug output in verbose mode)
  if [[ -s "$STDERR_FILE" ]]; then
    local stderr_bytes stderr_lines
    stderr_bytes=$(wc -c < "$STDERR_FILE" | tr -d ' ')
    stderr_lines=$(wc -l < "$STDERR_FILE" | tr -d ' ')
    log_eval "Stderr: ${stderr_bytes} bytes, ${stderr_lines} lines → $STDERR_FILE"
    if $VERBOSE; then
      log_eval "--- stderr (last 30 lines) ---"
      while IFS= read -r stderr_line; do
        log_eval "  $stderr_line"
      done < <(tail -30 "$STDERR_FILE")
      log_eval "--- end stderr ---"
    fi
  else
    log_eval "Stderr: (empty — claude produced no diagnostic output)"
  fi

  if [[ $CLAUDE_EXIT -ne 0 ]]; then
    # Check for partial output even on timeout
    local partial_bytes=0
    if [[ -s "$RESPONSE_FILE" ]]; then
      partial_bytes=$(wc -c < "$RESPONSE_FILE" | tr -d ' ')
      log_eval "Partial output: ${partial_bytes} bytes → $RESPONSE_FILE"
    fi

    if [[ $CLAUDE_EXIT -eq 124 || $CLAUDE_EXIT -eq 137 ]]; then
      log_eval "ERROR: timed out after ${EVAL_TIMEOUT}s (exit $CLAUDE_EXIT, partial: ${partial_bytes} bytes)"
      print_error_detail "timeout" "$CLAUDE_EXIT" "$STDERR_FILE" "$TAG" "$MCP_CONFIG" "$RESPONSE_FILE" >> "$LOG_FILE" 2>&1
      echo "TIMEOUT|eval exceeded ${EVAL_TIMEOUT}s (killed after ${T_ELAPSED}s, partial: ${partial_bytes}B)" > "$VERDICT_FILE"
      echo "${PROG_PFX} DONE     ${TAG}  ERROR (timeout ${T_ELAPSED}s)" >> "$PROGRESS_LOG"
    else
      log_eval "ERROR: claude exited with code $CLAUDE_EXIT"
      print_error_detail "claude-exec" "$CLAUDE_EXIT" "$STDERR_FILE" "$TAG" "$MCP_CONFIG" "$RESPONSE_FILE" >> "$LOG_FILE" 2>&1
      echo "ERROR|claude exited with code $CLAUDE_EXIT" > "$VERDICT_FILE"
      echo "${PROG_PFX} DONE     ${TAG}  ERROR (exit ${CLAUDE_EXIT})" >> "$PROGRESS_LOG"
    fi
    return
  fi

  if [[ -z "$RESPONSE" ]]; then
    log_eval "ERROR: claude exited 0 but returned empty response"
    print_error_detail "empty-response" "0" "" "$TAG" "$MCP_CONFIG" "$RESPONSE_FILE" >> "$LOG_FILE" 2>&1
    echo "ERROR|empty response despite exit code 0" > "$VERDICT_FILE"
    echo "${PROG_PFX} DONE     ${TAG}  ERROR (empty response)" >> "$PROGRESS_LOG"
    return
  fi
  local BYTES
  BYTES=$(echo "$RESPONSE" | wc -c | tr -d ' ')
  log_eval "Response: ${BYTES} bytes → $RESPONSE_FILE"

  # ── Step 2: Grade the response ─────────────────────────────
  local VERDICT REASON
  if [[ "$GRADING_MODE" == "bash" ]]; then
    log_eval "Grading (bash — should_trigger=$SHOULD_TRIGGER)"
    local BASH_VERDICT
    if [[ "$SKILL_NAME" == "loci-post-edit" ]]; then
      BASH_VERDICT=$(grade_bash_post_edit "$RESPONSE" "$SHOULD_TRIGGER" "$EXPECT_BASELINE" "$EXPECT_NO_CHANGE")
    else
      BASH_VERDICT=$(grade_bash "$RESPONSE" "$SHOULD_TRIGGER")
    fi
    echo "$BASH_VERDICT" > "$GRADE_FILE"
    VERDICT="${BASH_VERDICT%%|*}"
    REASON="${BASH_VERDICT#*|}"
  else
    log_eval "Grading response (timeout ${GRADE_TIMEOUT}s)..."
    local GRADE_PROMPT="You are an eval grader. Determine if the response PASSES or FAILS.

## Eval prompt
$PROMPT

## Expected behavior
$EXPECTED"

  if [[ -n "$EXPECTATIONS" ]]; then
    GRADE_PROMPT="$GRADE_PROMPT

## Specific expectations (ALL must be met to pass)
$EXPECTATIONS"
  fi

  GRADE_PROMPT="$GRADE_PROMPT

## Actual response
$RESPONSE

## Tool calls the response actually made
(Assertions about what the response *runs* — a command, a rebuild — must be judged
from this list, not from the narration above.)
$TOOL_CALLS

## Instructions
Evaluate whether the response meets the expected behavior and all expectations.
For each expectation, note PASS or FAIL with a brief reason.

Reply in EXACTLY this format:

EXPECTATION_RESULTS:
- [PASS|FAIL] <expectation>: <reason>

VERDICT: PASS or FAIL
REASON: <one-line summary>"

    local GRADE_STDERR_FILE="$RESULTS_DIR/${SKILL_NAME}_eval${EVAL_ID}_grade_stderr.txt"
    local GRADE GRADE_EXIT=0
    local GRADER_BARE_FLAG=()
    if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
      GRADER_BARE_FLAG=(--bare)
    fi
    T_START=$(date +%s)
    GRADE=$(echo "$GRADE_PROMPT" | timeout --kill-after=10 "$GRADE_TIMEOUT" claude -p ${GRADER_BARE_FLAG[@]+"${GRADER_BARE_FLAG[@]}"} --model sonnet 2>"$GRADE_STDERR_FILE") || GRADE_EXIT=$?
    T_END=$(date +%s)
    T_ELAPSED=$((T_END - T_START))

    log_eval "Grader exited with code $GRADE_EXIT after ${T_ELAPSED}s"

    if [[ -s "$GRADE_STDERR_FILE" ]]; then
      log_eval "Grade stderr ($(wc -c < "$GRADE_STDERR_FILE" | tr -d ' ') bytes):"
      while IFS= read -r stderr_line; do
        log_eval "  $stderr_line"
      done < <(head -10 "$GRADE_STDERR_FILE")
    fi

    if [[ $GRADE_EXIT -ne 0 ]]; then
      log_eval "GRADE ERROR: grader call failed (exit $GRADE_EXIT after ${T_ELAPSED}s)"
      print_error_detail "grade" "$GRADE_EXIT" "$GRADE_STDERR_FILE" "$TAG" "$MCP_CONFIG" "$RESPONSE_FILE" >> "$LOG_FILE" 2>&1
    [[ ! -s "$GRADE_STDERR_FILE" ]] && rm -f "$GRADE_STDERR_FILE"
    echo "GRADE_ERROR|grader exited with code $GRADE_EXIT" > "$VERDICT_FILE"
    echo "${PROG_PFX} DONE     ${TAG}  ERROR (grade fail)" >> "$PROGRESS_LOG"
    return
  fi
  [[ ! -s "$GRADE_STDERR_FILE" ]] && rm -f "$GRADE_STDERR_FILE"

    echo "$GRADE" > "$GRADE_FILE"
    VERDICT=$(echo "$GRADE" | sed -n 's/^VERDICT:[[:space:]]*\([^[:space:]]*\).*/\1/p' | head -1)
    VERDICT="${VERDICT:-UNKNOWN}"
    REASON=$(echo "$GRADE" | sed -n 's/^REASON:[[:space:]]*//p' | head -1)
    REASON="${REASON:-could not extract reason}"
  fi

  echo "${VERDICT}|${REASON}" > "$VERDICT_FILE"
  echo "${PROG_PFX} DONE     ${TAG}  ${VERDICT}" >> "$PROGRESS_LOG"

  log_eval "VERDICT: $VERDICT — $REASON"
  if [[ "$VERDICT" == "FAIL" ]]; then
    log_eval "Grader explanation (first 8 lines):"
    head -8 "$GRADE_FILE" | while IFS= read -r grade_line; do
      log_eval "  $grade_line"
    done
  elif [[ "$VERDICT" != "PASS" ]]; then
    log_eval "WARNING: could not parse verdict from grader output"
  fi
}

# ---------------------------------------------------------------------------
# Collect all evals into a job list, then run them
# ---------------------------------------------------------------------------
EVAL_FILES=$(find "$SCRIPT_DIR/skills" -name "*evals.json" 2>/dev/null | sort)

if [[ -z "$EVAL_FILES" ]]; then
  echo "No *evals.json files found under skills/"
  exit 1
fi

# Collect eval jobs as arrays of parameters
declare -a JOB_SKILLS=()
declare -a JOB_IDS=()
declare -a JOB_FILES=()
declare -a JOB_PROMPTS=()
declare -a JOB_EXPECTED=()
declare -a JOB_EXPECTATIONS=()
declare -a JOB_SYSPROMPTS=()
declare -a JOB_GRADINGMODES=()
declare -a JOB_SHOULDTRIGGER=()
declare -a JOB_FLOWS=()
declare -a JOB_EXPECTBASELINE=()
declare -a JOB_EXPECTNOCHANGE=()
declare -a JOB_SOURCEFILES=()
declare -a JOB_APPROVE=()
# Evals the host could not run. Surfaced in the summary and report.md, so a run
# that skipped a regression guard can never look like a clean pass.
declare -a SKIPPED_EVALS=()

for EVAL_FILE in $EVAL_FILES; do
  SKILL_NAME=$(jq -r '.skill_name' "$EVAL_FILE")

  if [[ -n "$FILTER_SKILL" && "$SKILL_NAME" != "$FILTER_SKILL" ]]; then
    continue
  fi

  EVAL_COUNT=$(jq '.evals | length' "$EVAL_FILE")

  # Load skill instructions
  SKILL_DIR=$(dirname "$(dirname "$EVAL_FILE")")
  SKILL_MD="$SKILL_DIR/SKILL.md"
  SYSTEM_PROMPT=""
  if [[ -f "$SKILL_MD" ]]; then
    # Inline the shared runtime contract. Every SKILL.md opens by telling the model
    # to read `<plugin-dir>/skills/_shared/loci-runtime-contract.md`, but an eval
    # has no session context to resolve `<plugin-dir>` from — so anything the
    # contract owns (Step 0 Pattern B's artifact selection and freshness gate, the
    # arch gate, the envelope rules) was silently absent from every eval, and an
    # assertion about it could never fail for the right reason.
    SYSTEM_PROMPT="You are running a skill eval. Follow the skill instructions below EXACTLY.

--- SESSION CONTEXT ---
$SESSION_CONTEXT
--- END SESSION CONTEXT ---

--- SHARED RUNTIME CONTRACT (referenced by the skill as <plugin-dir>/skills/_shared/loci-runtime-contract.md) ---
$(cat "$SCRIPT_DIR/skills/_shared/loci-runtime-contract.md" 2>/dev/null)
--- END SHARED RUNTIME CONTRACT ---

--- SKILL INSTRUCTIONS ---
$(cat "$SKILL_MD")
--- END SKILL INSTRUCTIONS ---"
  fi

  for (( i=0; i<EVAL_COUNT; i++ )); do
    EVAL_ID=$(jq -r ".evals[$i].id" "$EVAL_FILE")

    # shellcheck disable=SC2053
    if [[ -n "$FILTER_EVAL_ID" && "$EVAL_ID" != $FILTER_EVAL_ID ]]; then
      continue
    fi

    PROMPT=$(jq -r ".evals[$i].prompt" "$EVAL_FILE")
    EXPECTED=$(jq -r ".evals[$i].expected_output" "$EVAL_FILE")
    EXPECTATIONS=$(jq -r ".evals[$i].assertions // [] | .[].text" "$EVAL_FILE" 2>/dev/null || true)
    GRADING_MODE=$(jq -r ".evals[$i].grading_mode // \"claude\"" "$EVAL_FILE")
    # Use null check — jq's // operator treats false as falsy and would substitute the default
    SHOULD_TRIGGER=$(jq -r ".evals[$i].should_trigger | if . == null then true else . end" "$EVAL_FILE")
    # Combined two-turn flow fields (single-turn evals leave these at defaults).
    FLOW=$(jq -r ".evals[$i].flow // \"single\"" "$EVAL_FILE")
    EXPECT_BASELINE=$(jq -r ".evals[$i].expect_baseline // false | tostring" "$EVAL_FILE")
    EXPECT_NO_CHANGE=$(jq -r ".evals[$i].expect_no_change // false | tostring" "$EVAL_FILE")
    SOURCE_FILE=$(jq -r ".evals[$i].source_file // \"\"" "$EVAL_FILE")
    APPROVE_PROMPT=$(jq -r ".evals[$i].approve_prompt // \"Approved. Implement the plan exactly as described now. Edit the source file directly.\"" "$EVAL_FILE")

    # An eval may need a fixture the host cannot provide. Skip rather than fail: a
    # missing cross-compiler is an environment gap, not a skill regression, and a
    # red suite for that reason trains people to ignore the suite.
    #
    # Guard the PLACEHOLDER, not a `requires` literal. A typo'd `requires`
    # ("stale-root", "stale_root ", or omitted) fell straight through, and the
    # placeholder then expanded to the empty string — asking the model about
    # "kernel_main in /blink.c" and grading the confusion as a skill failure.
    SKIP_REASON=""
    if [[ "$PROMPT$EXPECTED$EXPECTATIONS" == *'$LOCI_TEST_STALE_ROOT'* \
          && -z "$STALE_ROOT" ]]; then
      if $LIST_MODE; then
        SKIP_REASON="stale-artifact fixture not staged (--list)"
      else
        SKIP_REASON="stale-artifact fixture unavailable (see the staging message above)"
      fi
    fi
    # --list is discovery, not execution: an eval that cannot RUN here still exists
    # and must be listed. Skipping it before the job list made the two evals this
    # change adds undiscoverable, and `--list --eval-id sd-5` exit 1.
    if [[ -n "$SKIP_REASON" ]] && $LIST_MODE; then
      SKIP_REASON=""
    fi
    if [[ -n "$SKIP_REASON" ]]; then
      echo -e "${YELLOW}SKIP ${SKILL_NAME}:${EVAL_ID} — ${SKIP_REASON}${NC}"
      # Recorded, not erased. Dropping it before the job list meant a run that
      # executed neither regression guard still printed all-green and exited 0, with
      # nothing in report.md to say they had never run.
      SKIPPED_EVALS+=("${SKILL_NAME}:${EVAL_ID} — ${SKIP_REASON}")
      continue
    fi

    # Expand $LOCI_TEST_BLE_ROOT in prompt/expected to the actual BLE_ROOT path
    PROMPT="${PROMPT//\$LOCI_TEST_BLE_ROOT/$BLE_ROOT}"
    EXPECTED="${EXPECTED//\$LOCI_TEST_BLE_ROOT/$BLE_ROOT}"
    EXPECTATIONS="${EXPECTATIONS//\$LOCI_TEST_BLE_ROOT/$BLE_ROOT}"

    # …and $LOCI_TEST_STALE_ROOT to the fixture staged above.
    PROMPT="${PROMPT//\$LOCI_TEST_STALE_ROOT/$STALE_ROOT}"
    EXPECTED="${EXPECTED//\$LOCI_TEST_STALE_ROOT/$STALE_ROOT}"
    EXPECTATIONS="${EXPECTATIONS//\$LOCI_TEST_STALE_ROOT/$STALE_ROOT}"

    JOB_SKILLS+=("$SKILL_NAME")
    JOB_IDS+=("$EVAL_ID")
    JOB_FILES+=("$(basename "$EVAL_FILE")")
    JOB_PROMPTS+=("$PROMPT")
    JOB_EXPECTED+=("$EXPECTED")
    JOB_EXPECTATIONS+=("$EXPECTATIONS")
    JOB_SYSPROMPTS+=("$SYSTEM_PROMPT")
    JOB_GRADINGMODES+=("$GRADING_MODE")
    JOB_SHOULDTRIGGER+=("$SHOULD_TRIGGER")
    JOB_FLOWS+=("$FLOW")
    JOB_EXPECTBASELINE+=("$EXPECT_BASELINE")
    JOB_EXPECTNOCHANGE+=("$EXPECT_NO_CHANGE")
    JOB_SOURCEFILES+=("$SOURCE_FILE")
    JOB_APPROVE+=("$APPROVE_PROMPT")
  done
done

TOTAL=${#JOB_SKILLS[@]}
if [[ $TOTAL -eq 0 ]]; then
  if [[ ${#SKIPPED_EVALS[@]} -gt 0 ]]; then
    echo -e "${RED}No evals ran — every match was skipped:${NC}"
    for s in "${SKIPPED_EVALS[@]}"; do echo "  - $s"; done
    # Exit 2 = "guards did not run", the same code the end-of-run path uses, and
    # honour the same opt-out. This branch previously exited 1 — i.e. "a skill
    # failed" — for exactly the case the 2 was introduced to distinguish.
    if [[ "${LOCI_EVALS_ALLOW_SKIPS:-}" == "1" ]]; then
      echo "  (LOCI_EVALS_ALLOW_SKIPS=1 — treating as success)"
      exit 0
    fi
    exit 2
  fi
  echo "No evals matched the filters."
  exit 0
fi

if $LIST_MODE; then
  echo "Available eval IDs ($TOTAL total):"
  echo ""
  CURRENT=""
  for (( j=0; j<TOTAL; j++ )); do
    if [[ "${JOB_SKILLS[$j]}" != "$CURRENT" ]]; then
      CURRENT="${JOB_SKILLS[$j]}"
      echo -e "${CYAN}  $CURRENT${NC}"
    fi
    echo "    ${JOB_IDS[$j]}"
  done
  exit 0
fi

# Combined two-turn AND single-turn edit evals make REAL edits to source files
# in the BLE tree and touch shared build state (.loci-build, the resumed
# session). Two of them running at once would clobber the same file and race
# the restore. If any edit-making eval is in the batch, force sequential.
# The stale-artifact evals share one mutable tree and both rebuild inside it. Run
# concurrently, one eval's relink refreshes the artifact the other is asserting is
# stale — so sd-5 could pass without ever detecting staleness.
if [[ -n "$STALE_ROOT" && $MAX_JOBS -ne 1 ]]; then
  for (( j=0; j<${#JOB_PROMPTS[@]}; j++ )); do
    if [[ "${JOB_PROMPTS[$j]}" == *"$STALE_ROOT"* ]]; then
      echo -e "${YELLOW}NOTE: stale-artifact evals share one tree — forcing sequential (-j 1).${NC}"
      MAX_JOBS=1
      break
    fi
  done
fi

for f in "${JOB_FLOWS[@]}"; do
  if [[ ( "$f" == "two-turn" || "$f" == "edit" ) && $MAX_JOBS -ne 1 ]]; then
    echo -e "${YELLOW}NOTE: edit-making evals present (two-turn/edit) — forcing sequential (-j 1) to avoid source-file races.${NC}"
    MAX_JOBS=1
    break
  fi
done

echo "Running $TOTAL evals..."
echo ""

# ---------------------------------------------------------------------------
# Launch jobs with concurrency limit
# ---------------------------------------------------------------------------
PROGRESS_LOG="$RESULTS_DIR/.progress"
MASTER_LOG="$RESULTS_DIR/master.log"
touch "$PROGRESS_LOG" "$MASTER_LOG"
tail -f "$PROGRESS_LOG" &
TAIL_PID=$!

# Trap to ensure tail -f is killed on exit/interrupt
cleanup_tail() {
  kill "$TAIL_PID" 2>/dev/null
  wait "$TAIL_PID" 2>/dev/null
}
trap cleanup_tail EXIT

RUNNING=0
declare -a PIDS=()

for (( j=0; j<TOTAL; j++ )); do
  run_one_eval \
    "${JOB_SKILLS[$j]}" \
    "${JOB_IDS[$j]}" \
    "${JOB_PROMPTS[$j]}" \
    "${JOB_EXPECTED[$j]}" \
    "${JOB_EXPECTATIONS[$j]}" \
    "${JOB_SYSPROMPTS[$j]}" \
    "$MCP_CONFIG" \
    "$RESULTS_DIR" \
    "$EVAL_TIMEOUT" \
    "$GRADE_TIMEOUT" \
    "${JOB_FILES[$j]}" \
    "$((j+1))" \
    "${JOB_GRADINGMODES[$j]}" \
    "${JOB_SHOULDTRIGGER[$j]}" \
    "${JOB_FLOWS[$j]}" \
    "${JOB_SOURCEFILES[$j]}" \
    "${JOB_APPROVE[$j]}" \
    "${JOB_EXPECTBASELINE[$j]}" \
    "${JOB_EXPECTNOCHANGE[$j]}" &

  PIDS[$j]=$!
  RUNNING=$((RUNNING + 1))

  # Throttle: wait for a slot if we hit the limit
  if (( RUNNING >= MAX_JOBS )); then
    wait -n 2>/dev/null || true
    RUNNING=$((RUNNING - 1))
  fi
done

# Kill tail FIRST — so the subsequent `wait` only blocks on eval jobs.
sleep 0.3
kill "$TAIL_PID" 2>/dev/null
wait "$TAIL_PID" 2>/dev/null || true
trap - EXIT

# Now wait for remaining eval jobs (tail is already gone).
for pid in "${PIDS[@]}"; do
  wait "$pid" 2>/dev/null || true
done
echo ""

# ---------------------------------------------------------------------------
# Collect results and print output
# ---------------------------------------------------------------------------
PASSED=0; FAILED=0; ERRORED=0; BLOCKED=0
CURRENT_SKILL=""

for (( j=0; j<TOTAL; j++ )); do
  SKILL_NAME="${JOB_SKILLS[$j]}"
  EVAL_ID="${JOB_IDS[$j]}"
  EVAL_FILE_NAME="${JOB_FILES[$j]}"

  # Print skill header on change
  if [[ "$SKILL_NAME" != "$CURRENT_SKILL" ]]; then
    SKILL_EVAL_COUNT=0
    for s in "${JOB_SKILLS[@]}"; do
      [[ "$s" == "$SKILL_NAME" ]] && SKILL_EVAL_COUNT=$((SKILL_EVAL_COUNT + 1))
    done
    echo -e "${CYAN}━━━ Skill: $SKILL_NAME ($SKILL_EVAL_COUNT evals) ━━━${NC}"
    CURRENT_SKILL="$SKILL_NAME"
  fi

  # Print buffered log (kept on disk for post-mortem)
  LOG_FILE="$RESULTS_DIR/${SKILL_NAME}_eval${EVAL_ID}_log.txt"
  if [[ -f "$LOG_FILE" ]]; then
    cat "$LOG_FILE"
  fi

  # Read verdict
  VERDICT_FILE="$RESULTS_DIR/${SKILL_NAME}_eval${EVAL_ID}_verdict.txt"
  if [[ ! -f "$VERDICT_FILE" ]]; then
    echo -e "  ${EVAL_FILE_NAME}  ${EVAL_ID}: ${RED}ERROR${NC} — no verdict produced"
    ERRORED=$((ERRORED + 1))
    echo "| $EVAL_FILE_NAME | $EVAL_ID | ERROR | no verdict produced |" >> "$REPORT"
    continue
  fi

  VERDICT_LINE=$(cat "$VERDICT_FILE")
  rm -f "$VERDICT_FILE"
  VERDICT="${VERDICT_LINE%%|*}"
  REASON="${VERDICT_LINE#*|}"

  if [[ "$VERDICT" == "PASS" ]]; then
    echo -e "  ${EVAL_FILE_NAME}  ${EVAL_ID}: ${GREEN}✓ PASSED${NC} — $REASON"
    PASSED=$((PASSED + 1))
  elif [[ "$VERDICT" == "FAIL" ]]; then
    echo -e "  ${EVAL_FILE_NAME}  ${EVAL_ID}: ${RED}✗ FAILED${NC} — $REASON"
    FAILED=$((FAILED + 1))
  elif [[ "$VERDICT" == "BLOCKED" ]]; then
    echo -e "  ${EVAL_FILE_NAME}  ${EVAL_ID}: ${YELLOW}⊘ BLOCKED${NC} — $REASON"
    BLOCKED=$((BLOCKED + 1))
  elif [[ "$VERDICT" == "TIMEOUT" ]]; then
    echo -e "  ${EVAL_FILE_NAME}  ${EVAL_ID}: ${YELLOW}⏱ ERROR (timeout)${NC} — $REASON"
    ERRORED=$((ERRORED + 1))
  elif [[ "$VERDICT" == "ERROR" || "$VERDICT" == "GRADE_ERROR" ]]; then
    echo -e "  ${EVAL_FILE_NAME}  ${EVAL_ID}: ${RED}ERROR${NC} — $REASON"
    ERRORED=$((ERRORED + 1))
  else
    echo -e "  ${EVAL_FILE_NAME}  ${EVAL_ID}: ${YELLOW}? UNKNOWN${NC} — could not parse verdict"
    ERRORED=$((ERRORED + 1))
    VERDICT="UNKNOWN"
  fi
  echo "| $EVAL_FILE_NAME | $EVAL_ID | $VERDICT | $REASON |" >> "$REPORT"
  echo ""
done

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
cat >> "$REPORT" <<EOF

## Summary
- Total: $TOTAL
- Passed: $PASSED
- Failed: $FAILED
- Blocked: $BLOCKED  (preflight invoked but couldn't analyze — environment/setup gap, not a skill fail)
- Skipped: ${#SKIPPED_EVALS[@]}  (fixture unavailable on this host — did NOT run)
- Errors: $ERRORED
EOF

echo -e "${BOLD}━━━ Summary ━━━${NC}"
echo -e "  Total:   $TOTAL"
echo -e "  ${GREEN}Passed:  $PASSED${NC}"
echo -e "  ${RED}Failed:  $FAILED${NC}"
echo -e "  ${YELLOW}Blocked: $BLOCKED${NC}  (preflight invoked but couldn't analyze — environment/setup gap)"
if [[ ${#SKIPPED_EVALS[@]} -gt 0 ]]; then
  echo -e "  ${YELLOW}Skipped: ${#SKIPPED_EVALS[@]}${NC}  (fixture unavailable — these did NOT run)"
  for s in "${SKIPPED_EVALS[@]}"; do echo "    - $s"; done
fi
echo -e "  ${YELLOW}Errors:  $ERRORED${NC}"
echo ""
echo "Report: $RESULTS_DIR/report.md"
echo "Master log: $MASTER_LOG"

# BLOCKED does NOT fail the suite — it flags an environment gap (missing build
# flags / compiled-out function), not a skill defect. Only real FAILs and
# ERRORs set a non-zero exit.
if (( FAILED + ERRORED > 0 )); then
  exit 1
fi

# A skipped eval must not read as a pass. `SKIPPED_EVALS` reached the report body and
# the console summary but not the exit code, so on any host without
# `arm-none-eabi-gcc` or without the pinned CLI both stale-artifact regression guards
# skipped and the suite exited 0 with an all-green summary — and CI reads the exit
# code, not the summary. Exit 2 to distinguish "guards did not run" from "a skill
# failed" (1), so a caller can choose to tolerate it deliberately.
if [[ ${#SKIPPED_EVALS[@]} -gt 0 ]]; then
  if [[ "${LOCI_EVALS_ALLOW_SKIPS:-}" == "1" ]]; then
    echo -e "${YELLOW}${#SKIPPED_EVALS[@]} eval(s) never ran (LOCI_EVALS_ALLOW_SKIPS=1 — exiting 0).${NC}"
  else
    # Announce the code actually being used: this printed "Exiting 2" even when the
    # opt-out then made it exit 0.
    echo -e "${YELLOW}Exiting 2: ${#SKIPPED_EVALS[@]} eval(s) never ran.${NC}"
    echo "  Set LOCI_EVALS_ALLOW_SKIPS=1 to treat this as success."
    exit 2
  fi
fi
