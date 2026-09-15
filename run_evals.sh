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
#   ./run_evals.sh --ble-root "C:\Playground\BLE" --installed-plugin     # QA: the plugin users have
#   LOCI_EVALS_ALLOW_PLUGIN_SKEW=1 ./run_evals.sh --installed-plugin  # run edit/two-turn evals even
#                                                    # when the installed plugin is not this tree.
#                                                    # Their verdicts are then facts about the
#                                                    # INSTALLED plugin, not about your working
#                                                    # tree. Only an --installed-plugin run can skew.
#   ./run_evals.sh --ble-root "C:\Playground\BLE" --model opus           # pin the model under test
#   ./run_evals.sh --ble-root "C:\Playground\BLE" --dry-run              # print the argv, run nothing
#   LOCI_TEST_BLE_ROOT="C:\Playground\BLE" ./run_evals.sh               # env var
#
# Each eval is run via `claude -p --model <--model, default sonnet>` with the
# skill's SKILL.md injected as a system prompt.  A second `claude -p --model
# <--grader-model, default sonnet>` call grades the response against the
# expectations in evals.json.  BOTH are pinned: an unpinned run inherits
# whatever the operator's default model happens to be, which makes its result
# unattributable after the fact.
#
# Results are written to eval-results/<timestamp>/, one `*_metrics.json` per
# eval (model, usage, num_turns, total_cost_usd, wall clock, which plugin
# answered, and what the run changed in the fixture) and, where a run measured a
# manifest, `match-rate.json` — how often the agent confirmed the top-ranked
# hot-path candidate.
#
# ── Three decisions this script makes, and why (2026-09-07) ─────────────────
#
# **A plan-mode eval cannot edit — and is FAILED if it does anyway.** Plan mode
# is passed as `--permission-mode plan`, but every eval also passes
# `--dangerously-skip-permissions` — which preflight's Bash and MCP calls
# genuinely need — and that combination does NOT stop the model editing.
# Measured: `pf-critical-1` edited the BLE fixture on three of four T14 passes,
# and on the fourth it edited BEFORE planning and then invoked post-edit instead
# of preflight. So the edit tools are denied at the TOOL layer, with
# `--disallowedTools` (see `PLAN_MODE_DENY`).
#
# That is half an answer, and the first real run of it said so: with Edit,
# Write, MultiEdit and NotebookEdit all denied, the model wrote the guard with a
# **Bash** command instead, and then ran post-edit on the grounds that "this was
# a direct edit outside /plan mode". Bash cannot be denied — preflight's own
# analysis runs through it. So the other half is a grading fact: a plan-mode run
# that changed a tracked file in the fixture is FAILED, with the paths named, in
# both the single-turn and two-turn flows. Real plan mode blocks edits; an eval
# that lets the model edit measures the model's restraint rather than the skill,
# and does not reproduce.
#
# **Evals test this working tree by default.** `--plugin-dir "$SCRIPT_DIR"` on
# every flow, with the installed marketplace copy behind `--installed-plugin`
# for QA. The old default was the other way round, and the cost of it was the
# whole build-and-detection overhaul: the skew guard SKIPPED every edit and
# two-turn eval for the entire initiative, so not one of them ever ran. The
# guard itself stays — it is still the honest answer in `--installed-plugin`
# mode, and it is still the reason a released-plugin pass cannot be cited as
# evidence about a branch.
#
# **Every eval is hermetic.** Whatever a run changes in the fixture's TRACKED
# files is put back before the next eval starts and named in that eval's
# `*_metrics.json` as `fixture_dirty_after_run`; whatever it creates is named
# and left alone. See the fixture section of `lib/eval-metrics.sh`.
# `revert_eval_changes.sh` stays as the MANUAL backstop — for a run someone
# killed between the `claude` call and the sweep, which is the one case no
# in-process restore can cover.

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
# Both halves are pinned. Only the GRADER used to be, so every recorded result
# was silently attributed to whatever `claude` defaulted to on the machine that
# ran it — two runs of the same suite were not comparable and neither said so.
MODEL="${LOCI_EVAL_MODEL:-sonnet}"
GRADER_MODEL="${LOCI_EVAL_GRADER_MODEL:-sonnet}"
DRY_RUN=false
# false ⇒ the working tree answers, loaded with --plugin-dir (the default; see
# the header). true ⇒ the installed marketplace plugin answers and the skew
# guard applies. Nothing else differs between the two modes.
INSTALLED_PLUGIN_MODE=false

# The tools a plan-mode eval may not call, spelled the way `claude --help`
# spells them: "--disallowedTools, --disallowed-tools <tools...>   Comma or
# space-separated list of tool names to deny". Space-separated here, the form
# the two-turn flow's first turn has used since it shipped. Applied to
# plan-mode evals ONLY — an edit-flow eval exists precisely to make an edit.
PLAN_MODE_DENY=(--disallowedTools Edit Write MultiEdit NotebookEdit)

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
    --model)      MODEL="$2"; shift 2 ;;
    --model=*)    MODEL="${1#*=}"; shift ;;
    --grader-model)   GRADER_MODEL="$2"; shift 2 ;;
    --grader-model=*) GRADER_MODEL="${1#*=}"; shift ;;
    --dry-run)    DRY_RUN=true; shift ;;
    --installed-plugin) INSTALLED_PLUGIN_MODE=true; shift ;;
    --sequential) MAX_JOBS=1; shift ;;
    --list)       LIST_MODE=true; shift ;;
    --verbose|-v) VERBOSE=true; shift ;;
    -h|--help)
      # The whole leading comment block, however long it grows. The old
      # `head -26 | tail -25` printed a fixed window and silently truncated
      # the usage the first time a line was added above it.
      awk 'NR > 1 { if (/^#/) print; else exit }' "$0"
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

if [[ -z "$MODEL" || -z "$GRADER_MODEL" ]]; then
  echo "ERROR: --model and --grader-model cannot be empty."
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
echo "Model:    $MODEL (grader: $GRADER_MODEL)"
if $DRY_RUN; then
  echo "DRY RUN:  resolving each eval's argv only — no claude call, nothing billed."
fi

# Check for the primary test ELF
BLE_ELF="$BLE_ROOT/$BLE_BASIC_BLE"
if [[ ! -f "$BLE_ELF" ]]; then
  echo "WARNING: Primary BLE ELF not found: $BLE_ELF"
  echo "  Some evals may fail."
fi

# ---------------------------------------------------------------------------
# The BLE fixture's recipe (T14). Every measurement rests on one, so the harness
# initializes the fixture ONCE, the way the plugin's auto-init would: a plain
# `loci init --auto`. On a checkout measured by a pre-move CLI — this one has its
# sidecars under `.loci-build/` — that is the double jump, seeded from those
# sidecars with no question asked. An eval must never run against a fixture the
# CLI could not initialize, so the outcome is checked, not assumed; `--list` and
# `--dry-run` skip it, since neither runs a skill.
# ---------------------------------------------------------------------------
BLE_RECIPE="$BLE_ROOT/.loci/build.yaml"
if ! $LIST_MODE && ! $DRY_RUN; then
  if [[ ! -f "$BLE_RECIPE" ]]; then
    _ble_init=$(cd "$BLE_ROOT" && loci init --project-root "$BLE_ROOT" --auto 2>/dev/null) || true
    if jq -e '.ok == true and .data.init_status == "ok"' <<<"$_ble_init" >/dev/null 2>&1; then
      echo "BLE recipe: written by loci init ($(jq -r '.data.target' <<<"$_ble_init"), $(jq -r '.data.validated' <<<"$_ble_init"))"
    else
      echo "WARNING: \`loci init --auto\` did not initialize the BLE fixture:" \
           "$(jq -c '.error.code // .error.message // "no envelope"' <<<"$_ble_init" 2>/dev/null)"
      echo "  Every measuring eval will answer not_initialized and exercise auto-init instead."
    fi
  else
    echo "BLE recipe: $BLE_RECIPE (already initialized)"
  fi
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
# `|| true` on each, and it is load-bearing rather than defensive habit. `set -o
# pipefail` is on: with no `~/.claude/plugins/cache/loci` — a machine where the plugin
# was never installed, or any sandbox with its own HOME — `find` exits 1, pipefail
# makes the pipeline 1, the assignment inherits it and `set -e` kills the script
# BEFORE the run header prints. The symptom is `./run_evals.sh` producing three lines
# and stopping with no error, which reads as a hang or a bad filter.
#
# It comes from THE PLUGIN UNDER TEST. In the default mode that is this working
# tree, so its own `.mcp.json` / `marketplace.json` is read first: the two are
# byte-identical today, but the moment a branch moves the server URL, taking the
# installed copy's block would point a run banner-labelled "working tree" at the
# released server — the same class of quiet mis-attribution the skew guard
# exists for. `--installed-plugin` reads the installed copy, as it always did.
PLUGIN_MCP_JSON=""
if ! $INSTALLED_PLUGIN_MODE; then
  # The first tree file that EXISTS wins — not the first that declares a server.
  # Requiring a non-empty `mcpServers` looked stricter and was the bug: no tree
  # file has one today, so the loop never fired and the installed cache always
  # won, which is precisely the mis-attribution this block was added to stop.
  # "This tree declares no MCP server" is the tree's answer and the run should
  # use it; falling back to the released copy the day the two differ would hand
  # a run banner-labelled "working tree" the released server block.
  for _cand in "$SCRIPT_DIR/.mcp.json" "$SCRIPT_DIR/.claude-plugin/marketplace.json" \
               "$SCRIPT_DIR/.claude-plugin/plugin.json"; do
    if [[ -f "$_cand" ]]; then
      PLUGIN_MCP_JSON="$_cand"
      break
    fi
  done
fi
[[ -n "$PLUGIN_MCP_JSON" ]] || PLUGIN_MCP_JSON="$(find ~/.claude/plugins/cache/loci -name .mcp.json 2>/dev/null | sort -V | tail -1 || true)"
[[ -z "$PLUGIN_MCP_JSON" ]] && PLUGIN_MCP_JSON="$(find ~/.claude/plugins/cache/loci -name marketplace.json 2>/dev/null | sort -V | tail -1 || true)"
[[ -z "$PLUGIN_MCP_JSON" ]] && PLUGIN_MCP_JSON="$(find ~/.claude/plugins/cache/loci -name plugin.json 2>/dev/null | sort -V | tail -1 || true)"
true    # the `[[ ]] &&` above returns 1 when the first find succeeded; do not let
        # that be this block's exit status.
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
  # …and say whether that block actually held a server. It does not today, on
  # either copy: `.mcp.json` was deleted from the plugin in f2c5512 ("Remove
  # unnecessary and unused legacy scripts") and neither `marketplace.json` nor
  # `plugin.json` has carried an `mcpServers` key since, so the file written
  # above is `{"mcpServers":{}}` and the line below used to announce it as "using
  # plugin config". Whether an empty `--mcp-config` merely adds nothing or
  # OVERRIDES the servers the loaded plugin would have brought is not settled
  # here, and it is not F01's question — but a run must not claim a config it
  # does not have.
  if jq -e '.mcpServers | length > 0' "$MCP_CONFIG" >/dev/null 2>&1; then
    echo "MCP: using plugin config ($PLUGIN_MCP_JSON, $(jq -r '.mcpServers | keys | join(", ")' "$MCP_CONFIG"), OAuth session auth)"
  else
    echo "MCP: $PLUGIN_MCP_JSON declares NO mcpServers — passing an empty --mcp-config." \
         "Skills needing the model may fall back or fail; this is a pre-existing gap, not this run's doing."
  fi
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
#   .loci/build/objects/armv6-m/blink.o    compiled from blink.c       —  60 s old
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
  # `.loci/build/objects/<target>/` is where the CLI writes since 0.1.136, so
  # that is where a post-edit run's object would be. (The soak-era stub
  # `.loci-build/` is gone: since T14 nothing reads or writes that directory.)
  mkdir -p "$dir/.loci/build/objects/armv6-m" || return 1
  cp "$src/startup.c" "$src/fixture.ld" "$src/blink_pre.c" "$src/blink_post.c" \
     "$dir/" || return 1

  local cf=(-g -nostartfiles -O0 -mcpu=cortex-m0plus -mthumb)
  # A build the recipe can record (T14). `loci init` derives `build.full_build`
  # from the project's build system, and a skill rebuilds a stale artifact ONLY
  # through that — the contract forbids a hand-typed link into LOCI's own
  # directory, which is what sd-6 tempts the model with. Three lines of Makefile
  # are this fixture's build; the compile database below is still handed over,
  # because init never runs a project's build itself. `$(CC)`/`$(CFLAGS)` are
  # make's, not the shell's: printf's format is single-quoted.
  printf 'CC := arm-none-eabi-gcc\nCFLAGS := %s\n\nall: kernel.elf\n\nkernel.elf: blink.c startup.c fixture.ld\n\t$(CC) $(CFLAGS) -Wl,-T,fixture.ld blink.c startup.c -o kernel.elf\n\n.PHONY: all\n' \
    "${cf[*]}" > "$dir/Makefile" || return 1
  # A build the recipe can record (T14). `loci init` derives `build.full_build`
  # from the project's build system, and a skill rebuilds a stale artifact ONLY
  # through that — the contract forbids a hand-typed link into LOCI's own
  # directory, which is what sd-6 tempts the model with. Three lines of Makefile
  # are this fixture's build; the compile database below is still handed over,
  # because init never runs a project's build itself. `$(CC)`/`$(CFLAGS)` are
  # make's, not the shell's: printf's format is single-quoted.
  printf 'CC := arm-none-eabi-gcc\nCFLAGS := %s\n\nall: kernel.elf\n\nkernel.elf: blink.c startup.c fixture.ld\n\t$(CC) $(CFLAGS) -Wl,-T,fixture.ld blink.c startup.c -o kernel.elf\n\n.PHONY: all\n' \
    "${cf[*]}" > "$dir/Makefile" || return 1
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
    "$cc" "${cf[@]}" -c blink.c -o .loci/build/objects/armv6-m/blink.o || exit 1
    # 4. The two variants are scaffolding, not part of the tree under test: leaving
    #    them hands the model the entire edit as a diff, and breaks any `gcc *.c`
    #    build with "multiple definition of kernel_main".
    rm -f blink_pre.c blink_post.c || exit 1
  ) >"$log" 2>&1 || {
    echo "  fixture rejected: staging failed, see $log" >&2
    return 1
  }
  # Non-empty outputs, since a linker can "succeed" into nothing useful.
  for f in kernel.elf .loci/build/objects/armv6-m/blink.o; do
    [ -s "$dir/$f" ] || { echo "  fixture rejected: $f is empty" >&2; return 1; }
  done
  # And the OBJECT is a real object, positively. `kernel.elf` has had an `nm`
  # control since the stale-artifact bug; the object had only "non-empty", so a
  # 21-byte text file at that path satisfied every check the staging made — and
  # this is the artifact the layout move relocated, i.e. the one whose path is
  # newly load-bearing. Same GNU-first shape as the `nm` control below.
  if ! arm-none-eabi-nm "$dir/.loci/build/objects/armv6-m/blink.o" 2>/dev/null \
       | grep -q "kernel_main"; then
    echo "  fixture rejected: .loci/build/objects/armv6-m/blink.o is not an object with" \
         "kernel_main in it (nm broken, or the compile wrote something else)" >&2
    return 1
  fi

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
  _set_mtime "$((now - 60))"  "$dir/.loci/build/objects/armv6-m/blink.o" || return 1

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
  # GNU-first, BSD fallback — the split every mtime read in this plugin makes.
  # `stat -c` alone rejected a perfectly good fixture on macOS (BSD stat has no
  # -c), so both regression evals silently never ran there.
  _mtime_of() { stat -c %Y "$1" 2>/dev/null || stat -f %m "$1" 2>/dev/null; }
  local e_mt s_mt o_mt
  e_mt=$(_mtime_of "$dir/kernel.elf") || return 1
  s_mt=$(_mtime_of "$dir/blink.c") || return 1
  o_mt=$(_mtime_of "$dir/.loci/build/objects/armv6-m/blink.o") || return 1
  [ -n "$e_mt" ] && [ -n "$s_mt" ] && [ -n "$o_mt" ] || {
    echo "  fixture rejected: could not read mtimes (no usable stat)" >&2; return 1; }
  if (( e_mt >= s_mt || s_mt >= o_mt )); then
    echo "  fixture rejected: mtimes are not elf < source < object" >&2
    return 1
  fi

  # A recipe, because since T14 every LOCI verb rests on one and there is no
  # scan-generated context to lean on. `loci init` refuses a tree with no
  # compile database, and this tree deliberately has no build system — so the
  # harness writes the ONE entry it already knows (the exact compile line
  # above) and hands it over. The recipe records `artifacts.elf`, which is what
  # leads the leaf verbs' artifact ladder; kernel.elf is STALE by design, so B3
  # still has to refuse it — that is the eval. Verified with the CLI's own
  # answer: a fixture init could not initialize must not run an eval that
  # assumes it did.
  # One argument per line through `jq -R`, so a flag such as `-mcpu=cortex-m0`
  # is data and never an option jq tries to parse.
  printf '%s\n' "$cc" "${cf[@]}" -c blink.c -o blink.o \
    | jq -R . | jq -s --arg d "$dir" '[{directory: $d, file: ($d + "/blink.c"),
        output: "blink.o", arguments: .}]' > "$dir/compile_commands.json" \
    || { echo "  fixture rejected: could not write the compile database" >&2; return 1; }
  local init_out
  init_out=$(cd "$dir" && loci init --project-root "$dir" --target=armv6-m \
               --compdb=compile_commands.json --set artifacts.elf=kernel.elf --auto \
               2>/dev/null) || true
  if ! jq -e '.ok == true and .data.init_status == "ok"' <<<"$init_out" >/dev/null 2>&1; then
    echo "  fixture rejected: \`loci init\` did not initialize the staged tree" \
         "($(jq -c '.error.code // .error.message // "no envelope"' <<<"$init_out" 2>/dev/null))" >&2
    return 1
  fi
  [ -f "$dir/.loci/build.yaml" ] || { echo "  fixture rejected: init said ok but wrote no recipe" >&2; return 1; }

  # These evals exercise the *gated* path, so the `loci` on PATH must have the gate.
  # Without this check a CLI-version gap reports as a skill FAIL — and the contract
  # explicitly tells the model NOT to run the gate on an old CLI, so the assertion
  # "runs loci build fresh" would be demanding the opposite of the rule.
  if ! loci build fresh --elf "$dir/kernel.elf" >/dev/null 2>&1; then
    echo "  fixture rejected: the loci on PATH has no working \`build fresh\`" \
         "($(loci --version 2>&1 | sed -n '1{p;q;}')) — needs the CLI these skills pin" >&2
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
# tool_calls_of <stream-json file>… — the transcript's tool calls, one per line
# (`Bash: <command>`, `Edit: <file>`, …), or `(none)` when the transcript made
# none. Exported as LOCI_EVAL_TOOL_CALLS before every grade: `_init_flow_failure`
# reads it for evidence that `loci init` RAN, since a model that pastes the CLI's
# `not_initialized` refusal has pasted its recovery sentence too. Empty only when
# jq could not read the transcript, which puts the grader on its text fallback.
# ---------------------------------------------------------------------------
tool_calls_of() {
  jq -rs '[ .[] | select(.type == "assistant") | .message.content[]?
            | select(.type == "tool_use")
            | .name + ": " + ((.input.command // .input.file_path // .input.pattern // "")
                              | tostring | gsub("\n"; " ; ") | .[0:400]) ]
          | if length > 0 then join("\n") else "(none)" end' "$@" 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# A BLE artifact the freshness gate accepts (P79/P83)
# ---------------------------------------------------------------------------
# Six evals — sd-1…sd-4, mr-1 and mr-2 — ask for numbers measured from
# `basic_ble.out`, and the four preflight criticals name it as the binary their
# functions are compiled into. On this checkout that artifact is STALE:
# `loci build fresh` names two SDK headers touched weeks after the link, so
# Pattern B's B3 refuses to report numbers from it — and refusing is correct. The
# evals were therefore red at every baseline since the gate shipped, asserting
# behaviour the design deliberately forbids, which makes them worse than useless:
# a permanently-red eval teaches people to skim past reds. (The preflight four
# additionally named a SECOND build, `CC2340R5/basic_ble.out`, that no checkout
# here carries at all — a missing fixture rather than a stale one. They are
# repointed at this copy, whose symbol table holds every function they name.)
#
# The fixture is what is wrong, not the evals. So stage one the gate accepts, the
# same way `stage_stale_tree` stages one it must refuse — and by the same rule:
# **verify the staged state with the CLI's own check, and reject the fixture if it
# is not in that state**, so an eval can never run against a fixture that quietly
# failed to stage.
#
# A COPY, never the user's checkout. The obvious shortcut is `touch` on
# `basic_ble.out` in place; it works, and it silently rewrites a timestamp in a
# tree this script was merely pointed at. The copy costs ~3 MB and nothing else.
FRESH_BLE_ROOT=""
FRESH_BLE_VERDICT=""
stage_fresh_ble() {
  local src="$BLE_ROOT/examples/rtos/LP_EM_CC2340R5/ble5stack/basic_ble/freertos/ticlang"
  [ -f "$src/basic_ble.out" ] || {
    echo "  fresh fixture rejected: no basic_ble.out under $src" >&2; return 1; }
  command -v loci >/dev/null 2>&1 || {
    echo "  fresh fixture rejected: no loci on PATH to verify freshness with" >&2
    return 1; }

  local dir="$RESULTS_DIR/ble-fresh"
  mkdir -p "$dir" || return 1
  cp -f "$src/basic_ble.out" "$dir/basic_ble.out" || return 1
  # The map travels with it: mr-2 asks for region budgets, which need one, and a
  # map from a different link would be a quietly wrong answer rather than a missing
  # one.
  [ -f "$src/basic_ble.map" ] && { cp -f "$src/basic_ble.map" "$dir/basic_ble.map" || return 1; }

  # Newer than every source the DWARF names. `cp` already stamps "now" on most
  # platforms, but not all (`cp -p` habits, some CI images), so say it explicitly.
  touch "$dir/basic_ble.out" 2>/dev/null || return 1
  [ -f "$dir/basic_ble.map" ] && touch "$dir/basic_ble.map" 2>/dev/null

  # THE CHECK, and it is the CLI's, not ours: whatever `loci build fresh` says is
  # what the skills will see, so nothing else is worth asserting.
  #
  # `false` OR `null`, because those are exactly B3's two PROCEED states, and
  # demanding `false` rejects every artifact this fixture can produce. Measured:
  # this ELF was linked on a build server, so 114 of the 189 sources its debug
  # info names are conan paths present on no developer machine and staleness can
  # never be *ruled out* here — the CLI answers `null`, "unknown, not confirmed",
  # which B3 says to proceed on while quoting `.data.reason`. What the staging
  # exists to rule out is `true`, the refuse state, which the un-re-stamped
  # original returns because two SDK headers are newer than the link.
  #
  # An error envelope or a missing field must NOT read as acceptable, and a `//`
  # default cannot express that: jq treats `false` and `null` alike as falsy, so
  # the two states this has to tell apart would collapse into the default. The
  # field's presence is asked for directly instead. (The same `//` trap is
  # commented three screens down at the `should_trigger` read; it caught this one
  # too — via the test, not the review.)
  local verdict
  verdict=$(cd "$dir" && loci build fresh --elf "basic_ble.out" 2>/dev/null \
            | jq -r 'if (has("data") and (.data|type == "object")
                         and (.data|has("stale")))
                     then (.data.stale|tostring) else "absent" end' \
              2>/dev/null) || verdict="absent"
  if [[ "$verdict" != "false" && "$verdict" != "null" ]]; then
    echo "  fresh fixture rejected: loci build fresh reports stale=${verdict} for the staged copy" >&2
    return 1
  fi
  FRESH_BLE_VERDICT="$verdict"

  FRESH_BLE_ROOT="$dir"
  return 0
}

if $LIST_MODE; then
  echo "Fresh BLE fixture: not staged (--list)"
elif stage_fresh_ble; then
  echo "Fresh BLE fixture: $FRESH_BLE_ROOT (basic_ble.out re-stamped; loci build fresh: stale=$FRESH_BLE_VERDICT)"
else
  echo "Fresh BLE fixture: unavailable — the evals that measure basic_ble.out will be skipped"
fi

# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------
GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[0;33m'

CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

# ---------------------------------------------------------------------------
# Which plugin do the `edit` / `two-turn` flows actually exercise?
# ---------------------------------------------------------------------------
# Those flows load the INSTALLED plugin (cwd = BLE_ROOT, NOT --bare) and inject no
# SKILL.md, because the thing under test is the real plugin auto-invoking post-edit.
# So they test `~/.claude/plugins/...`, never this working tree — and while a branch
# is in flight those are different code. A pass there is not evidence about the
# branch, and neither is a fail; a T11 reviewer found exactly that being cited as
# acceptance evidence.
#
# So: resolve the installed commit here, compare it to HEAD, and let the per-eval
# skip below record the mismatch. Same rule as the stale-artifact fixture — an eval
# that cannot test what it claims to test is SKIPPED and named, never silently run.
INSTALLED_PLUGIN_SHA=""
PLUGIN_MATCHES_TREE=unknown
PLUGIN_SKEW_NOTE=""
# `~` not `$HOME`: every other path in this script uses it, bash resolves it from the
# passwd entry when HOME is unset, and `$HOME` under `set -u` aborted the whole script
# — including `--list` — for anyone running without it.
_installed_json=~/.claude/plugins/installed_plugins.json
if [[ -f "$_installed_json" ]]; then
  # EVERY loci-prefixed entry, not `first`. With more than one installed, `first`
  # picked arbitrarily and could name a plugin unrelated to this run in the banner.
  # Distinct shas ⇒ we cannot say which one loads ⇒ `unknown` ⇒ skip.
  INSTALLED_PLUGIN_SHA=$(jq -r '
    [.plugins // {} | to_entries[] | select(.key | startswith("loci"))
     | .value[]? | .gitCommitSha // empty] | unique
    | if length == 1 then .[0] else "" end' \
    "$_installed_json" 2>/dev/null || true)
fi
_head_sha=$(git -C "$SCRIPT_DIR" rev-parse HEAD 2>/dev/null || true)
# A commit sha is a proxy for the wrong thing. The installed plugin is a frozen copy
# under `~/.claude/plugins/cache/`; the working tree is what a task edits. Matching
# HEAD while `skills/` is dirty means the eval runs against prose that CANNOT contain
# the edit under test, under a banner claiming the opposite — which is the failure this
# guard exists to prevent, so a dirty tree is `no`, not `yes`.
# `.claude-plugin` is in the list because `--plugin-dir` loads the WHOLE
# directory: an uncommitted version or description change in
# `.claude-plugin/plugin.json` is live in the session, and leaving it out let the
# metrics record a clean `plugin-dir:<sha>` for a tree that is not that sha.
_tree_dirty=$(git -C "$SCRIPT_DIR" status --porcelain -- skills hooks lib .claude-plugin 2>/dev/null || echo "?")
if [[ -z "$INSTALLED_PLUGIN_SHA" || -z "$_head_sha" ]]; then
  PLUGIN_MATCHES_TREE=unknown
elif [[ "$INSTALLED_PLUGIN_SHA" != "$_head_sha" ]]; then
  PLUGIN_MATCHES_TREE=no
elif [[ -n "$_tree_dirty" ]]; then
  PLUGIN_MATCHES_TREE=no
  PLUGIN_SKEW_NOTE=" (sha matches, but skills/hooks/lib are modified — the installed copy cannot contain those edits)"
else
  PLUGIN_MATCHES_TREE=yes
fi
# ── …and which one this run actually loads ─────────────────────────────────
# `claude --plugin-dir <path>` loads a plugin for one session only, and it
# DISPLACES an installed plugin of the same name rather than loading a second
# copy beside it. Measured on 2026-09-08 with the marketplace `loci` 0.1.130
# installed: `claude -p --plugin-dir C:/Projects/loci-claude-dev` listed exactly
# ONE `loci:stack-depth`, and its description was this tree's — it says
# "C/C++/Rust/Go", where 0.1.130 says "C/C++/Rust" — and exactly TWO SessionStart
# hooks fired, the two this tree's `hooks/hooks.json` declares, not four. Both
# halves matter: the skills come from the tree AND the tree's hooks are the ones
# that run, which is the half the edit flow rests on.
#
# So the working tree is the default in every flow, and `--installed-plugin` is
# the QA mode for what users actually have. The skew guard below belongs to that
# mode and to nothing else: there is nothing to skew against when the tree is
# what loaded. It is not being weakened, it is being pointed at the one run
# where it says something true.
PLUGIN_ARGS=()
PLUGIN_UNDER_TEST=""
_tree_desc="${_head_sha:0:7}"
[[ -n "$_head_sha" ]] || _tree_desc="no-git"
[[ -n "$_tree_dirty" ]] && _tree_desc="${_tree_desc}-dirty"
if $INSTALLED_PLUGIN_MODE; then
  # `unresolved` rather than a bare `installed:` — an empty sha is exactly the
  # case the guard SKIPS for, and the metrics field should say so out loud.
  PLUGIN_UNDER_TEST="installed:${INSTALLED_PLUGIN_SHA:0:7}"
  [[ -n "$INSTALLED_PLUGIN_SHA" ]] || PLUGIN_UNDER_TEST="installed:unresolved"
  case "$PLUGIN_MATCHES_TREE" in
    yes) PLUGIN_BANNER="Installed plugin: ${INSTALLED_PLUGIN_SHA:0:7} — matches HEAD and the tree is clean; edit-flow evals test this tree" ;;
    no)  PLUGIN_BANNER="Installed plugin: ${INSTALLED_PLUGIN_SHA:0:7} vs HEAD ${_head_sha:0:7}${PLUGIN_SKEW_NOTE} — edit/two-turn evals would test the INSTALLED plugin, not this tree; they will be SKIPPED" ;;
    *)   PLUGIN_BANNER="Installed plugin: could not be resolved — edit/two-turn evals will be SKIPPED rather than credited to this tree" ;;
  esac
  if [[ "${LOCI_EVALS_ALLOW_PLUGIN_SKEW:-0}" == "1" ]]; then
    PLUGIN_BANNER="$PLUGIN_BANNER  [LOCI_EVALS_ALLOW_PLUGIN_SKEW=1 — running them anyway]"
  fi
else
  PLUGIN_ARGS=(--plugin-dir "$SCRIPT_DIR")
  PLUGIN_UNDER_TEST="plugin-dir:${_tree_desc}"
  PLUGIN_BANNER="Working tree under test: $SCRIPT_DIR @ ${_tree_desc}, loaded with --plugin-dir${INSTALLED_PLUGIN_SHA:+ — which displaces the installed copy ${INSTALLED_PLUGIN_SHA:0:7}}. Every flow runs THIS tree and no eval is skipped for skew; pass --installed-plugin for a QA run against the plugin users have."
fi
# `--bare` and `--plugin-dir` are mutually exclusive by design: `claude --help`
# lists `--plugin-dir` among the things `--bare` skips, alongside hooks. The
# single-turn flow is the only one that ever passes `--bare`, and only when
# ANTHROPIC_API_KEY is set — so say which way each flow goes rather than
# implying one answer covers all three.
if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
  PLUGIN_BANNER="$PLUGIN_BANNER
  note: ANTHROPIC_API_KEY is set, so single-turn evals run --bare — NO plugin and NO hooks, only this tree's SKILL.md injected as a system prompt; their metrics record \`bare:no-plugin\` rather than the line above. The edit and two-turn flows never pass --bare and are unaffected."
else
  PLUGIN_BANNER="$PLUGIN_BANNER
  note: no ANTHROPIC_API_KEY, so --bare is not used and the plugin above is live in EVERY flow; single-turn evals additionally get this tree's SKILL.md as a system prompt."
fi
if $INSTALLED_PLUGIN_MODE && [[ "$PLUGIN_MATCHES_TREE" != "yes" ]]; then
  echo -e "${YELLOW}${PLUGIN_BANNER}${NC}"
else
  echo "$PLUGIN_BANNER"
fi

REPORT="$RESULTS_DIR/report.md"
cat > "$REPORT" <<EOF
# Eval Report — $TIMESTAMP

- Model under test: \`$MODEL\`  ·  grader: \`$GRADER_MODEL\`
- Per-eval cost and usage: \`*_metrics.json\` beside each transcript.

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
# The recipe line the session block of an initialized project carries (T14).
if [[ -f "$BLE_RECIPE" ]]; then
  _ble_target=$(sed -n '/^target:/{s/^target:[[:space:]]*//;p;q;}' "$BLE_RECIPE" | tr -d '\r')
  SESSION_CONTEXT="$SESSION_CONTEXT
LOCI target: ${_ble_target:-unknown}
recipe: $BLE_RECIPE"
fi
if [[ -n "$STALE_ROOT" ]]; then
  # The stale-artifact evals need a resolved LOCI target the way a real session
  # gets one from SessionStart; without it the skill has to guess an --arch.
  # `Build:` must not claim `make` — there is no Makefile in the staged tree, and
  # B3's rebuild step 1 would send the model at a build system that does not exist
  # ("No targets specified and no makefile found"). `direct` matches reality: the
  # eval prompt carries the exact compiler command line instead.
  # The line shape session-init emits since T08: `Target:` is gone (the target
  # is printed once, in the line every skill reads) and `Compiler:`/`Build:`
  # carry the rest. Grading a model against a context production no longer
  # produces measures the wrong thing.
  # …and `recipe:` rather than a `project context:` file: the session block of an
  # initialized project names the recipe (T14 removed the scan that used to
  # generate a context file for this fixture), and the skills read the target off
  # the `LOCI target:` line exactly as before.
  SESSION_CONTEXT="$SESSION_CONTEXT
Stale-artifact fixture root: $STALE_ROOT
  Compiler: arm-none-eabi-gcc, Build: make
  LOCI target: armv6-m
  recipe: $STALE_ROOT/.loci/build.yaml"
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
source "$SCRIPT_DIR/lib/eval-metrics.sh"

# The skill-run projections `analyse measure` appends to. Resolved once: where
# one exists, a manifest is scored only when a `measure` record carries the same
# (turn, manifest_id) pair — the pair, never a timestamp, is the attribution.
RUNS_FILES=()
while IFS= read -r _runs_file; do
  if [[ -n "$_runs_file" ]]; then RUNS_FILES+=("$_runs_file"); fi
done < <(skill_runs_files)
# ---------------------------------------------------------------------------
# run_one_eval — runs a single eval (prompt + grade) and writes result files
#   Called either inline (sequential) or as a background job (parallel).
#   All output goes to a log file; the caller prints it.
# ---------------------------------------------------------------------------
# dry_run_report — print the argv this eval WOULD run and stop.
#   Called with the flow's fully assembled claude args, so what it prints is
#   the real command line and not a second, drifting copy of it.
dry_run_report() {
  local TAG="$1" FLOW="$2" OUT="$3" LOG="$4" VERDICT="$5"; shift 5
  {
    echo "flow:  $FLOW"
    echo "model: $MODEL"
    echo "argv:  claude $*"
  } > "$OUT"
  echo "[dry-run] $TAG ($FLOW) → claude $*" >> "$LOG"
  echo "DRY_RUN|argv resolved, claude never called ($FLOW, --model $MODEL)" > "$VERDICT"
}

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
  local METRICS_FILE="$RESULTS_DIR/${SKILL_NAME}_eval${EVAL_ID}_metrics.json"
  # Fixture hygiene, per eval. FIXTURE_BAK holds copies of the tracked files
  # that were ALREADY modified when this eval started — the ones `git checkout`
  # must not be used on. FIXTURE_RESTORED is what the sweep put back, and it is
  # what reaches `fixture_dirty_after_run` in the metrics.
  local FIXTURE_BAK="$RESULTS_DIR/${SKILL_NAME}_eval${EVAL_ID}_fixture-pre"
  local FIXTURE_RESTORED=""
  local FIXTURE_UNTRACKED_PRE=""
  # Set once, right after the sweep; applied by `write_verdict` at every exit.
  local PLAN_VIOLATION=""
  # Attribution is by identity, not by time: a manifest belongs to this eval
  # when its `turn` is one this eval ran under, read from `.data.turn` in
  # `loci analyse prepare`'s envelope in the transcript. An mtime fence
  # cross-attributes the moment two evals share BLE_ROOT in parallel, which
  # they always do.
  # Appends are single short lines; the flows that can run in parallel write one
  # each.
  local MATCH_ROWS="$RESULTS_DIR/match-rows.jsonl"
  # Serial runs may also read `.loci/build/turn/current`; with -j > 1 that file
  # belongs to whichever eval submitted a prompt last.
  local TURN_STAMP=""
  if (( MAX_JOBS == 1 )); then
    TURN_STAMP=$(turn_id_from_stamp "$BLE_ROOT" || true)
  fi

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

  # write_verdict <verdict> <reason> [progress-label] — the ONE place a
  # single-turn verdict is written.
  #
  # It exists because the plan-mode override used to sit after grading, where
  # the timeout, non-zero-exit and empty-response paths had already returned. A
  # run that wrote to the fixture and then timed out reported `TIMEOUT`, with
  # the write named nowhere but the metrics — and that is the case that most
  # needs saying, since a model editing under plan mode is exactly the thing
  # this eval is checking for. Routing every exit through here makes the
  # override a property of the flow rather than of one branch of it.
  #
  # The original outcome is kept in the reason: "it also timed out" is context
  # worth having, but the write is the finding.
  write_verdict() {
    local v="$1" r="$2" label="${3:-}"
    if [[ -n "$PLAN_VIOLATION" ]]; then
      if [[ "$v" == "FAIL" ]]; then
        r="$PLAN_VIOLATION  (the grader also said: $r)"
      else
        r="$PLAN_VIOLATION  (the run also ended $v: $r)"
      fi
      v="FAIL"; label="FAIL (plan-mode write)"
    fi
    VERDICT="$v"; REASON="$r"
    echo "${v}|${r}" > "$VERDICT_FILE"
    echo "${PROG_PFX} DONE     ${TAG}  ${label:-$v}" >> "$PROGRESS_LOG"
    log_eval "VERDICT: $v — $r"
  }

  # fixture_arm / fixture_sweep — the eval leaves the tree as it found it.
  #
  # `fixture_sweep` is called IMMEDIATELY after the `claude` call in every flow,
  # before any early return. That placement is the whole point: `set -euo
  # pipefail` is on and the flows return early on timeout, on a non-zero exit
  # and on an empty response — and a timeout is precisely when a half-finished
  # edit is still sitting on disk. A sweep further down would be skipped in
  # exactly the cases that need it most.
  fixture_arm() {
    local root="${1:-}"
    rm -rf "$FIXTURE_BAK"
    FIXTURE_UNTRACKED_PRE=$(fixture_untracked "$root")
    fixture_snapshot "$root" "$FIXTURE_BAK"
    # Re-stamp the staged fresh ELF, per eval rather than once per run.
    #
    # That artifact is "fresh" only relative to the mtimes of the sources its
    # DWARF names — and those sources live in the very tree every eval now
    # restores. A restore, a `git checkout --`, or an editor that rewrote a file
    # and put it back all leave a source newer than the ELF, and the NEXT eval
    # measuring `$LOCI_TEST_BLE_FRESH/basic_ble.out` is then refused by Pattern
    # B for staleness the harness manufactured. `stage_fresh_ble` stamps it once
    # at run start and verifies it with `loci build fresh`; this keeps that
    # invariant true for the whole run, and costs one `touch`.
    if [[ -n "$FRESH_BLE_ROOT" ]]; then
      touch "$FRESH_BLE_ROOT/basic_ble.out" 2>/dev/null || true
      if [[ -f "$FRESH_BLE_ROOT/basic_ble.map" ]]; then
        touch "$FRESH_BLE_ROOT/basic_ble.map" 2>/dev/null || true
      fi
    fi
    return 0
  }
  fixture_sweep() {
    local root="${1:-}" _fp _added _n
    FIXTURE_RESTORED=$(fixture_restore "$root" "$FIXTURE_BAK")
    if [[ -n "$FIXTURE_RESTORED" ]]; then
      log_eval "fixture: restored $(printf '%s\n' "$FIXTURE_RESTORED" | grep -c .) tracked file(s) the run changed in $root"
      while IFS= read -r _fp; do
        [[ -n "$_fp" ]] && log_eval "  restored: $_fp"
      done <<< "$FIXTURE_RESTORED"
    else
      log_eval "fixture: no tracked file changed in $root"
    fi
    # Created, not changed. `.loci/` is the legitimate product of a measuring
    # eval and a blanket delete would take a seeded flags.json with it, so these
    # are NAMED and left where they are.
    _added=$(comm -13 <(printf '%s\n' "$FIXTURE_UNTRACKED_PRE" | sort -u) \
                      <(fixture_untracked "$root" | sort -u) 2>/dev/null || true)
    _n=$(printf '%s\n' "$_added" | grep -c . || true)
    if [[ "${_n:-0}" != "0" ]]; then
      log_eval "fixture: $_n untracked path(s) created and left in place:"
      printf '%s\n' "$_added" | sed -n '1,10p' | while IFS= read -r _fp; do
        [[ -n "$_fp" ]] && log_eval "  created: $_fp"
      done
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
    # `source_file` is BLE_ROOT-relative by convention, but T13's expansion loop
    # now resolves fixture placeholders in it too — and an expanded placeholder
    # is ABSOLUTE, so joining it onto BLE_ROOT produced `$BLE_ROOT/<abs path>`
    # and an `ERROR|source_file missing or not found`. Join only when relative.
    local SRC_ABS
    case "$SOURCE_FILE" in
      /*|[A-Za-z]:[\\/]*) SRC_ABS="$SOURCE_FILE" ;;
      *)                  SRC_ABS="$BLE_ROOT/$SOURCE_FILE" ;;
    esac
    local SRC_BACKUP="$RESULTS_DIR/${SKILL_NAME}_eval${EVAL_ID}_src.bak"

    if [[ -z "$SOURCE_FILE" || ! -f "$SRC_ABS" ]]; then
      log_eval "ERROR: source_file missing or not found: ${SRC_ABS:-<unset>}"
      echo "ERROR|source_file missing or not found: ${SRC_ABS:-<unset>}" > "$VERDICT_FILE"
      echo "${PROG_PFX} DONE     ${TAG}  ERROR (no source)" >> "$PROGRESS_LOG"
      return
    fi
    # Snapshot exact pre-run contents so we restore whatever was there (committed
    # OR uncommitted) — safer than `git checkout`, which would discard any local
    # edits the user already had in the file. `-p` because the MTIME is part of
    # "exactly what was there": `loci build fresh` compares the staged ELF against
    # the mtimes of the sources its DWARF names, and this is one of them, so a
    # restore that stamps `now` makes the NEXT eval's artifact read stale.
    cp -p "$SRC_ABS" "$SRC_BACKUP"
    fixture_arm "$BLE_ROOT"

    # NON-bare: plugin + hooks must load. stream-json carries every turn.
    # `--plugin-dir` makes that plugin THIS tree unless --installed-plugin was
    # passed; --bare is never used here, so the flag is always live.
    local C_ARGS=(-p --model "$MODEL" --dangerously-skip-permissions --output-format stream-json --verbose)
    C_ARGS+=(${PLUGIN_ARGS[@]+"${PLUGIN_ARGS[@]}"})
    [[ -n "$MCP_CONFIG" ]] && C_ARGS+=(--mcp-config "$MCP_CONFIG")

    if $DRY_RUN; then
      dry_run_report "$TAG" two-turn \
        "$RESULTS_DIR/${SKILL_NAME}_eval${EVAL_ID}_dryrun.txt" \
        "$LOG_FILE" "$VERDICT_FILE" "${C_ARGS[@]}" --permission-mode plan \
        "${PLAN_MODE_DENY[@]}"
      echo "${PROG_PFX} DONE     ${TAG}  DRY_RUN" >> "$PROGRESS_LOG"
      return
    fi

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
        "${PLAN_MODE_DENY[@]}" ) >"$T1_JSON" 2>"$STDERR_FILE" || T1_EXIT=$?
    T1_END=$(date +%s)
    log_eval "turn1 exit $T1_EXIT after $((T1_END - T1_START))s"

    local SID R1=""
    SID=$(jq -rs '[.[]|select(.type=="system" and .subtype=="init")|.session_id]|last // empty' "$T1_JSON" 2>/dev/null || true)
    R1=$(jq -rs '[.[]|select(.type=="assistant")|.message.content[]?|select(.type=="text")|.text]|join("\n")' "$T1_JSON" 2>/dev/null || true)
    log_eval "turn1 session_id: ${SID:-<none>}, response ${#R1} chars"

    # Guard: plan mode must NOT have written anything — and if it did, that is
    # the VERDICT, not a warning nobody reads. Same rule and same reason as the
    # single-turn plan-mode check further down: the deny list stops the edit
    # tools but cannot stop a write made through Bash, so the honest gate is to
    # fail the run and say what it wrote.
    #
    # A FULL sweep, not a `diff` of the one declared `source_file`. Turn 1 can
    # write a header, or a second `.c`, and the declared file would look
    # untouched — a plan turn that wrote something else is exactly as collapsed.
    # And it has to happen HERE, between the turns: the sweep after turn 2
    # cannot tell turn 1's violation from turn 2's legitimate edit.
    #
    # Restoring, not merely detecting: turn 2 must start from the Before this
    # run was set up with. Then re-arm, so turn 2's own sweep has a snapshot.
    local T1_EDITED=""
    fixture_sweep "$BLE_ROOT"
    if [[ -n "$FIXTURE_RESTORED" ]]; then
      T1_EDITED=$(printf '%s' "$FIXTURE_RESTORED" | tr '\n' ' ')
      T1_EDITED="${T1_EDITED% }"
    fi
    if ! diff -q "$SRC_ABS" "$SRC_BACKUP" >/dev/null 2>&1; then
      cp -p "$SRC_BACKUP" "$SRC_ABS"
      [[ -n "$T1_EDITED" ]] || T1_EDITED="$SOURCE_FILE"
    fi
    if [[ -n "$T1_EDITED" ]]; then
      log_eval "PLAN-MODE VIOLATION: the plan turn wrote $T1_EDITED — restored before turn 2"
    fi
    fixture_arm "$BLE_ROOT"

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

    # Both turns are done; nothing below returns before the metrics are written.
    fixture_sweep "$BLE_ROOT"

    eval_metrics_json "$T1_JSON" "$MODEL" "$((T1_END - T1_START))" two-turn \
      "" "$PLUGIN_UNDER_TEST" > "$METRICS_FILE.t1" || true
    # The fixture list rides on turn 2, the turn after which the sweep ran;
    # `eval_metrics_merge` hoists it to the top of the merged file so a reader
    # does not have to know that.
    eval_metrics_json "$T2_JSON" "$MODEL" "$(( ${T2_END:-0} - ${T2_START:-0} ))" two-turn \
      "$FIXTURE_RESTORED" "$PLUGIN_UNDER_TEST" > "$METRICS_FILE.t2" || true
    eval_metrics_merge "$METRICS_FILE" "turn1=$METRICS_FILE.t1" "turn2=$METRICS_FILE.t2" || true
    rm -f "$METRICS_FILE.t1" "$METRICS_FILE.t2"
    local TURNS
    TURNS=$(turn_ids_from_prepare "$T1_JSON" "$T2_JSON")
    [[ -n "${TURNS// }" ]] || TURNS="$TURN_STAMP"
    match_scan "$(build_dir_of "$BLE_ROOT")" "$BLE_ROOT" "$TAG" \
               "$MATCH_ROWS" "$TURNS" \
               ${RUNS_FILES[@]+"${RUNS_FILES[@]}"} || true

    # ── Restore source (hermetic; LOCI's build artifacts are left — they are
    #    overwritten next run, and a blanket rm could wipe a seeded flags.json) ──
    cp -p "$SRC_BACKUP" "$SRC_ABS"
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
    LOCI_EVAL_TOOL_CALLS=$(tool_calls_of "$T1_JSON" "$T2_JSON"); export LOCI_EVAL_TOOL_CALLS
    CV=$(grade_bash_combined "$RESPONSE")
    echo "$CV" > "$GRADE_FILE"
    VERDICT="${CV%%|*}"; REASON="${CV#*|}"
    if [[ -n "$T1_EDITED" ]]; then
      log_eval "PLAN-MODE VIOLATION overrides the verdict — was: $VERDICT — $REASON"
      VERDICT="FAIL"
      REASON="turn 1 wrote $T1_EDITED under plan mode (restored before turn 2) — the gated two-turn flow collapsed into one, so turn 2 planned and measured a change turn 1 had already made. (the grader also said: $REASON)"
    fi
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
    # `source_file` is BLE_ROOT-relative by convention, but T13's expansion loop
    # now resolves fixture placeholders in it too — and an expanded placeholder
    # is ABSOLUTE, so joining it onto BLE_ROOT produced `$BLE_ROOT/<abs path>`
    # and an `ERROR|source_file missing or not found`. Join only when relative.
    local SRC_ABS
    case "$SOURCE_FILE" in
      /*|[A-Za-z]:[\\/]*) SRC_ABS="$SOURCE_FILE" ;;
      *)                  SRC_ABS="$BLE_ROOT/$SOURCE_FILE" ;;
    esac
    local SRC_BACKUP="$RESULTS_DIR/${SKILL_NAME}_eval${EVAL_ID}_src.bak"

    if [[ -z "$SOURCE_FILE" || ! -f "$SRC_ABS" ]]; then
      log_eval "ERROR: source_file missing or not found: ${SRC_ABS:-<unset>}"
      echo "ERROR|source_file missing or not found: ${SRC_ABS:-<unset>}" > "$VERDICT_FILE"
      echo "${PROG_PFX} DONE     ${TAG}  ERROR (no source)" >> "$PROGRESS_LOG"
      return
    fi
    # Snapshot exact pre-run contents so we restore whatever was there
    # (committed OR uncommitted) — safer than `git checkout`. `-p` keeps the
    # mtime, which the staged fresh ELF's freshness is measured against.
    cp -p "$SRC_ABS" "$SRC_BACKUP"
    fixture_arm "$BLE_ROOT"

    # NON-bare: plugin + hooks must load so the pre-edit hook captures .o.prev
    # and the auto-run rule fires post-edit. stream-json carries every turn.
    # `--plugin-dir` makes those THIS tree's hooks unless --installed-plugin was
    # passed — which is the whole reason this flow can now run on a branch.
    local C_ARGS=(-p --model "$MODEL" --dangerously-skip-permissions --output-format stream-json --verbose)
    C_ARGS+=(${PLUGIN_ARGS[@]+"${PLUGIN_ARGS[@]}"})
    [[ -n "$MCP_CONFIG" ]] && C_ARGS+=(--mcp-config "$MCP_CONFIG")

    if $DRY_RUN; then
      dry_run_report "$TAG" edit \
        "$RESULTS_DIR/${SKILL_NAME}_eval${EVAL_ID}_dryrun.txt" \
        "$LOG_FILE" "$VERDICT_FILE" "${C_ARGS[@]}" --permission-mode acceptEdits
      echo "${PROG_PFX} DONE     ${TAG}  DRY_RUN" >> "$PROGRESS_LOG"
      return
    fi

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

    # After the edit check above, which needs the edited file still on disk, and
    # before the metrics, which record what the sweep put back. This flow makes
    # a real edit by design, so `fixture_dirty_after_run` naming `source_file`
    # here is the expected reading, not a warning.
    fixture_sweep "$BLE_ROOT"

    eval_metrics_json "$JSON_FILE" "$MODEL" "$((E_END - E_START))" edit \
      "$FIXTURE_RESTORED" "$PLUGIN_UNDER_TEST" > "$METRICS_FILE" || true
    local TURNS
    TURNS=$(turn_ids_from_prepare "$JSON_FILE")
    [[ -n "${TURNS// }" ]] || TURNS="$TURN_STAMP"
    match_scan "$(build_dir_of "$BLE_ROOT")" "$BLE_ROOT" "$TAG" \
               "$MATCH_ROWS" "$TURNS" \
               ${RUNS_FILES[@]+"${RUNS_FILES[@]}"} || true

    # Restore source (hermetic; LOCI's build artifacts are left — overwritten
    # next run, and a blanket rm could wipe a seeded flags.json).
    cp -p "$SRC_BACKUP" "$SRC_ABS"
    log_eval "restored source: $SOURCE_FILE"

    echo "$RESPONSE" > "$RESPONSE_FILE"

    if [[ -z "${RESPONSE// }" ]]; then
      log_eval "ERROR: empty response from edit turn (exit $E_EXIT)"
      echo "ERROR|empty response from edit turn (exit $E_EXIT)" > "$VERDICT_FILE"
      echo "${PROG_PFX} DONE     ${TAG}  ERROR (empty)" >> "$PROGRESS_LOG"
      return
    fi

    local PV VERDICT REASON
    LOCI_EVAL_TOOL_CALLS=$(tool_calls_of "$JSON_FILE"); export LOCI_EVAL_TOOL_CALLS
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
  local CLAUDE_ARGS=(-p --model "$MODEL" --dangerously-skip-permissions)
  if $PLAN_MODE; then
    # Headless equivalent of typing /plan in an interactive session — puts the
    # run in plan mode so the preflight skill's "MANDATORY in /plan mode" gate
    # actually fires. This replaces the (unsupported) /plan prompt prefix.
    CLAUDE_ARGS+=(--permission-mode plan)
  fi
  # `--bare` skips plugins entirely — `--plugin-dir` included, per `claude
  # --help` — so the two are exclusive and the metrics must not claim a plugin
  # answered when none did. FLOW_PLUGIN is what this call actually loaded.
  local FLOW_PLUGIN="$PLUGIN_UNDER_TEST"
  if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
    CLAUDE_ARGS+=(--bare)
    FLOW_PLUGIN="bare:no-plugin"
  else
    CLAUDE_ARGS+=(${PLUGIN_ARGS[@]+"${PLUGIN_ARGS[@]}"})
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

  # LAST, and only for plan mode. `--disallowedTools` takes a variadic list, so
  # putting it at the end leaves nothing after it for the list to swallow — the
  # same position the two-turn flow's first turn has always used.
  if $PLAN_MODE; then
    CLAUDE_ARGS+=("${PLAN_MODE_DENY[@]}")
  fi

  if $DRY_RUN; then
    dry_run_report "$TAG" single \
      "$RESULTS_DIR/${SKILL_NAME}_eval${EVAL_ID}_dryrun.txt" \
      "$LOG_FILE" "$VERDICT_FILE" "${CLAUDE_ARGS[@]}"
    echo "${PROG_PFX} DONE     ${TAG}  DRY_RUN" >> "$PROGRESS_LOG"
    return
  fi

  log_eval "Executing: timeout ${EVAL_TIMEOUT}s claude ${CLAUDE_ARGS[*]:0:6} ..."
  echo "${PROG_PFX} RUNNING  ${TAG}" >> "$PROGRESS_LOG"

  # The session opens IN the project the eval is about. session-init arms on the
  # cwd — the installed plugin's gate answers for it — and this script's own
  # directory is a plugin repo: `no_project`, and the disarm sentence then
  # overrides every skill's MANDATORY, so the model rightly refuses. (Before the
  # gate the scan armed this repo by mistake, which is how these evals passed.)
  # The stale-fixture evals run from their staged tree, every other eval from
  # BLE_ROOT — the directory the edit and two-turn flows already use.
  local EVAL_CWD="$BLE_ROOT"
  if [[ -n "$STALE_ROOT" && "$PROMPT" == *"$STALE_ROOT"* ]]; then EVAL_CWD="$STALE_ROOT"; fi
  log_eval "cwd: $EVAL_CWD"

  # A single-turn eval that names a file gets the same explicit backup the edit
  # and two-turn flows have always had — same `source_file` key, same "restore
  # exactly what was there" semantics. It is the belt to the sweep's braces: the
  # sweep needs a git checkout, this needs nothing.
  local SRC_ABS="" SRC_BACKUP=""
  if [[ -n "$SOURCE_FILE" ]]; then
    case "$SOURCE_FILE" in
      /*|[A-Za-z]:[\\/]*) SRC_ABS="$SOURCE_FILE" ;;
      *)                  SRC_ABS="$EVAL_CWD/$SOURCE_FILE" ;;
    esac
    if [[ -f "$SRC_ABS" ]]; then
      SRC_BACKUP="$RESULTS_DIR/${SKILL_NAME}_eval${EVAL_ID}_src.bak"
      # `-p`: the MTIME is part of "exactly what was there". `loci build fresh`
      # compares the staged ELF against the mtimes of the sources its DWARF
      # names, and these ARE those sources — restore one with a fresh timestamp
      # and the fixture the next eval measures reads `stale=true`, refused by
      # Pattern B for a change the harness made.
      cp -p "$SRC_ABS" "$SRC_BACKUP"
    else
      # Not an ERROR the way it is in the edit flows: there the file is the
      # thing under test, here it is only a file the prompt happens to name.
      log_eval "NOTE: source_file declared but not found, nothing to back up: $SRC_ABS"
      SRC_ABS=""
    fi
  fi
  fixture_arm "$EVAL_CWD"

  # Write stdout directly to file so partial output survives timeout.
  local CLAUDE_EXIT=0
  local T_START T_END T_ELAPSED
  T_START=$(date +%s)
  ( cd "$EVAL_CWD" && echo "$PROMPT" | timeout --kill-after=10 "$EVAL_TIMEOUT" claude "${CLAUDE_ARGS[@]}" ) \
    >"$RESPONSE_FILE" 2>"$STDERR_FILE" || CLAUDE_EXIT=$?
  T_END=$(date +%s)
  T_ELAPSED=$((T_END - T_START))

  log_eval "claude exited with code $CLAUDE_EXIT after ${T_ELAPSED}s"

  # ── Hermetic, HERE — before the timeout, non-zero-exit and empty-response
  #    returns below, all three of which used to leave the fixture dirty.
  fixture_sweep "$EVAL_CWD"
  # …and the declared source_file, ONLY if it actually changed. An unconditional
  # copy-back looks harmless and is not: it stamps a new mtime on a source the
  # staged fresh ELF's DWARF names, so the next eval measuring
  # `$LOCI_TEST_BLE_FRESH/basic_ble.out` gets `stale=true` and is refused —
  # `mr-4` declares `app_data.c` and runs before `mr-1`/`mr-2`, which measure
  # that ELF. `cp -p` on both halves keeps the mtime when a restore IS needed.
  if [[ -n "$SRC_ABS" && -n "$SRC_BACKUP" ]]; then
    if diff -q "$SRC_ABS" "$SRC_BACKUP" >/dev/null 2>&1; then
      log_eval "source_file unchanged, left untouched: $SOURCE_FILE"
    else
      log_eval "WARNING: the run modified its source_file ($SOURCE_FILE) — restoring it"
      cp -p "$SRC_BACKUP" "$SRC_ABS"
    fi
  fi
  # ── The plan-mode violation is decided HERE, next to the evidence ──────────
  # It is applied by `write_verdict` at every exit below, not just at the
  # graded one. Deciding it after grading left it unreachable on the three paths
  # that return first — and a run that wrote to the fixture and THEN timed out
  # is the case that most needs saying, since it reports as `TIMEOUT` with the
  # write mentioned nowhere but the metrics.
  PLAN_VIOLATION=""
  if $PLAN_MODE && [[ -n "$FIXTURE_RESTORED" ]]; then
    local _edited
    _edited=$(printf '%s' "$FIXTURE_RESTORED" | tr '\n' ' ')
    PLAN_VIOLATION="edited the fixture under plan mode (${_edited% }) — restored by the harness. Plan mode is read-only, and the edit tools were denied, so this went through Bash; whatever the response says about the skill was produced after a write that should not have happened."
    log_eval "PLAN-MODE VIOLATION: the run wrote to the fixture — ${_edited% }"
  fi

  # Metrics NOW, so a run that times out or answers nothing still records what
  # it did to the fixture and which plugin answered. Rewritten a few screens
  # down, with the same two values, once the transcript is in hand.
  eval_metrics_json "$JSON_FILE" "$MODEL" "$T_ELAPSED" single \
    "$FIXTURE_RESTORED" "$FLOW_PLUGIN" > "$METRICS_FILE" || true

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

    # The whole result event, kept — not just the line above. `*_full.json` is
    # the transcript; the numbers a comparison between two runs needs (model,
    # usage, turns, cost, wall clock) now survive next to it.
    eval_metrics_json "$JSON_FILE" "$MODEL" "$T_ELAPSED" single \
      "$FIXTURE_RESTORED" "$FLOW_PLUGIN" > "$METRICS_FILE" || true
    local TURNS
    TURNS=$(turn_ids_from_prepare "$JSON_FILE")
    [[ -n "${TURNS// }" ]] || TURNS="$TURN_STAMP"
    match_scan "$(build_dir_of "$BLE_ROOT")" "$BLE_ROOT" "$TAG" \
               "$MATCH_ROWS" "$TURNS" \
               ${RUNS_FILES[@]+"${RUNS_FILES[@]}"} || true

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
      write_verdict "TIMEOUT" \
        "eval exceeded ${EVAL_TIMEOUT}s (killed after ${T_ELAPSED}s, partial: ${partial_bytes}B)" \
        "ERROR (timeout ${T_ELAPSED}s)"
    else
      log_eval "ERROR: claude exited with code $CLAUDE_EXIT"
      print_error_detail "claude-exec" "$CLAUDE_EXIT" "$STDERR_FILE" "$TAG" "$MCP_CONFIG" "$RESPONSE_FILE" >> "$LOG_FILE" 2>&1
      write_verdict "ERROR" "claude exited with code $CLAUDE_EXIT" \
        "ERROR (exit ${CLAUDE_EXIT})"
    fi
    return
  fi

  if [[ -z "$RESPONSE" ]]; then
    log_eval "ERROR: claude exited 0 but returned empty response"
    print_error_detail "empty-response" "0" "" "$TAG" "$MCP_CONFIG" "$RESPONSE_FILE" >> "$LOG_FILE" 2>&1
    write_verdict "ERROR" "empty response despite exit code 0" "ERROR (empty response)"
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
    # TOOL_CALLS above is the same extraction, formatted for the report; the
    # grader gets it too, empty when the transcript could not be read.
    # `(no tool calls)` — jq's own zero case — stays: a transcript that called
    # nothing is evidence that init did not run. Only "could not read" is empty.
    case "$TOOL_CALLS" in
      "(tool calls unavailable)"|"(no tool calls captured)") LOCI_EVAL_TOOL_CALLS="" ;;
      *) LOCI_EVAL_TOOL_CALLS="$TOOL_CALLS" ;;
    esac
    export LOCI_EVAL_TOOL_CALLS
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
    GRADE=$(echo "$GRADE_PROMPT" | timeout --kill-after=10 "$GRADE_TIMEOUT" claude -p ${GRADER_BARE_FLAG[@]+"${GRADER_BARE_FLAG[@]}"} --model "$GRADER_MODEL" 2>"$GRADE_STDERR_FILE") || GRADE_EXIT=$?
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
    write_verdict "GRADE_ERROR" "grader exited with code $GRADE_EXIT" "ERROR (grade fail)"
    return
  fi
  [[ ! -s "$GRADE_STDERR_FILE" ]] && rm -f "$GRADE_STDERR_FILE"

    echo "$GRADE" > "$GRADE_FILE"
    # NO PIPE AT ALL — read the file written one line up.
    #
    # This began as `sed … | head -1`, on the theory that `head` closing the pipe
    # would SIGPIPE `sed` and abort the run under `pipefail`. Review measured it
    # and the theory was wrong in both directions: `sed`'s output for a repeated
    # verdict is ~13 bytes, far inside the pipe buffer, so the original never
    # aborted — while the `q` rewrite made **`echo`** the loser instead, exiting
    # 141 on grades past ~128 KB (Windows) / ~200 KB (Linux). A large grade is not
    # exotic — a grader quoting a long report produces one — and the abort lands
    # after the grade is in hand, with no message. Reading `$GRADE_FILE` leaves
    # nothing writing into a pipe, so neither failure exists, and `q` still stops
    # at the first match.
    # `|| true` on both: reading the file replaced a pipe whose no-match path was
    # `UNKNOWN|could not extract reason`, and an unreadable `$GRADE_FILE` would
    # otherwise make `sed` exit 2 and `set -e` kill the run — strictly worse than
    # what it replaced, for the one input the old form handled gracefully.
    VERDICT=$(sed -n '/^VERDICT:/{s/^VERDICT:[[:space:]]*\([^[:space:]]*\).*/\1/;p;q;}' "$GRADE_FILE" || true)
    VERDICT="${VERDICT:-UNKNOWN}"
    REASON=$(sed -n '/^REASON:/{s/^REASON:[[:space:]]*//;p;q;}' "$GRADE_FILE" || true)
    REASON="${REASON:-could not extract reason}"
  fi

  write_verdict "$VERDICT" "$REASON"
  if [[ "$VERDICT" == "FAIL" ]]; then
    log_eval "Grader explanation (first 8 lines):"
    head -8 "$GRADE_FILE" | while IFS= read -r grade_line; do
      log_eval "  $grade_line"
    done
  elif [[ "$VERDICT" == "UNKNOWN" ]]; then
    # UNKNOWN is the ONLY verdict here that means a parse failure: it is the
    # default the model-grader path falls back to when no `^VERDICT:` line
    # matched. Everything else reaching this branch parsed fine.
    log_eval "WARNING: could not parse verdict from grader output"
  elif [[ "$VERDICT" != "PASS" ]]; then
    # BLOCKED, and anything else a grader may add later. This used to print the
    # WARNING above, which was false and expensive: a bash-graded BLOCKED parses
    # perfectly (`${BASH_VERDICT%%|*}`) and needs no model call at all, yet every
    # one of them claimed the grader output was unreadable. A run where the
    # fixtures are uninitialized is ALL BLOCKED, so the log filled with a parse
    # error that had not happened, the summary read "0 passed, 0 failed", and the
    # combination was recorded as "the eval suite cannot produce a verdict
    # without ANTHROPIC_API_KEY" — a conclusion this message invented. Say what
    # the verdict is; the reason is already on the line above.
    log_eval "NOTE: verdict is $VERDICT (neither PASS nor FAIL) — reason above"
  fi
}

# ---------------------------------------------------------------------------
# Collect all evals into a job list, then run them
# ---------------------------------------------------------------------------
# `|| true` for the same reason the three plugin-cache finds carry it (P81): a
# `find` that exits non-zero — an unreadable directory is enough — makes the
# pipeline non-zero under `pipefail`, the assignment inherits it, and `set -e`
# kills the script before the check below can say what went wrong. The empty
# check IS the error path; the abort takes it away.
EVAL_FILES=$(find "$SCRIPT_DIR/skills" -name "*evals.json" 2>/dev/null | sort || true)

# …and every one of them has to PARSE, checked here rather than discovered by the
# first bare `jq` twenty lines down. This script reads each eval file through
# ~20 unguarded `X=$(jq …)` assignments; under `set -euo pipefail` one malformed
# file makes the first of them exit non-zero and kills the run with jq's own
# one-line parse error and nothing else — no harness message, no eval list, exit
# 5. That is byte-for-byte P81's original symptom, inside the class this task
# swept, so it is closed the same way: name the file and say what to run.
for _ef in $EVAL_FILES; do
  if ! jq empty "$_ef" >/dev/null 2>&1; then
    echo "ERROR: $_ef is not valid JSON — no evals can be read from it." >&2
    echo "  Run: jq . \"$_ef\"    (for the parse error and its line)" >&2
    exit 1
  fi
done

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
    # Fixture files this eval cannot do without, one per line, placeholders and
    # all. Absent field ⇒ empty ⇒ the screen below is a no-op for that eval.
    # `tr -d '\r'`: this list is fed to `read` at the requires check below,
    # and jq on Windows ends every line with CRLF. `$(...)` strips only the
    # last CR, so a second entry would be tested as `<path><CR>`, fail the
    # existence test and skip the eval on every Windows clone. Latent today
    # (every `requires_files` has one entry) and armed for the next one.
    REQUIRES_FILES=$(jq -r ".evals[$i].requires_files // [] | .[]" "$EVAL_FILE" 2>/dev/null | tr -d '\r' || true)
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
    # An `edit`/`two-turn` eval in `--installed-plugin` mode runs the installed
    # plugin. Unless that IS this tree, its verdict is a fact about the released
    # version and must not be reported as one about the branch.
    # `LOCI_EVALS_ALLOW_PLUGIN_SKEW=1` runs them anyway, for someone
    # deliberately testing the released plugin.
    #
    # In the DEFAULT mode there is nothing to skew: `--plugin-dir` loads this
    # tree, so the flow tests exactly what the guard was demanding. Skipping
    # there would be the old failure — the entire overhaul shipped with no edit
    # or two-turn eval ever run — wearing the guard's clothes.
    if [[ ( "$FLOW" == "edit" || "$FLOW" == "two-turn" ) \
          && "$INSTALLED_PLUGIN_MODE" == "true" \
          && "$PLUGIN_MATCHES_TREE" != "yes" \
          && "${LOCI_EVALS_ALLOW_PLUGIN_SKEW:-0}" != "1" ]]; then
      if [[ "$PLUGIN_MATCHES_TREE" == "no" ]]; then
        SKIP_REASON="installed plugin ${INSTALLED_PLUGIN_SHA:0:7} != HEAD ${_head_sha:0:7} — this flow would test the installed plugin, not this tree (drop --installed-plugin to run it against this tree)"
      else
        SKIP_REASON="installed plugin could not be resolved — this flow would not test this tree (drop --installed-plugin to run it against this tree)"
      fi
    fi
    # ⚠ EVERY field the expansion loop below touches. T13 widened that loop to
    # `approve_prompt` and `source_file` and left these two predicates reading
    # three fields, so a placeholder in either of the new ones expanded to the
    # EMPTY STRING with no skip and no warning — "measure /basic_ble.out and
    # report" — which is verbatim the failure this block's own comment says it
    # exists to prevent. Before the widening the model at least saw a literal
    # `$LOCI_TEST_BLE_FRESH` and said so.
    if [[ -z "$SKIP_REASON" ]] \
       && [[ "$PROMPT$EXPECTED$EXPECTATIONS$APPROVE_PROMPT$SOURCE_FILE" == *'$LOCI_TEST_STALE_ROOT'* \
          && -z "$STALE_ROOT" ]]; then
      if $LIST_MODE; then
        SKIP_REASON="stale-artifact fixture not staged (--list)"
      else
        SKIP_REASON="stale-artifact fixture unavailable (see the staging message above)"
      fi
    fi
    if [[ -z "$SKIP_REASON" ]] \
       && [[ "$PROMPT$EXPECTED$EXPECTATIONS$APPROVE_PROMPT$SOURCE_FILE" == *'$LOCI_TEST_BLE_FRESH'* \
          && -z "$FRESH_BLE_ROOT" ]]; then
      if $LIST_MODE; then
        SKIP_REASON="fresh BLE fixture not staged (--list)"
      else
        SKIP_REASON="fresh BLE fixture unavailable (see the staging message above)"
      fi
    fi
    # An eval that DECLARES a fixture file it cannot do without is skipped when the
    # host lacks it, rather than run and graded on the confusion. `mr-3` asks for a
    # delta between two builds and names a second BLE build this checkout does not
    # carry; `pf-critical-2` and `pf-critical-3` name the same missing ELF and went
    # non-deterministic because of it — the model takes long diagnostic paths and
    # sometimes drops the line the grader keys on (P79). All three were reported as
    # skill failures.
    #
    # DECLARED, not inferred. Scanning the prompt for paths and skipping on any
    # that is absent was the first cut, and it over-skips: fourteen evals on this
    # checkout name at least one path from the reference machine's BLE layout, and
    # most are *reasoning* evals whose grade does not depend on the file existing
    # ("grade only this reasoning; the agent need not execute any loci command").
    # Turning those into skips would trade a noisy red for a silent loss of
    # coverage, which is the cry-wolf failure this repo has deleted screens over.
    # An eval that genuinely needs a file says so.
    if [[ -z "$SKIP_REASON" && -n "$REQUIRES_FILES" ]]; then
      while IFS= read -r _req; do
        [ -n "$_req" ] || continue
        # An UNRECOGNISED placeholder would be left literal and then fail the
        # existence test, skipping the eval on every host forever — a typo that
        # silently retires an eval, which is the shape the `$LOCI_TEST_STALE_ROOT`
        # guard's own comment warns about ("a typo'd `requires` fell straight
        # through"). Fail loudly instead.
        # An entry must be a KNOWN placeholder or an absolute path. Two holes in
        # the first version, opposite directions and the same cause — it only
        # looked at entries beginning with `$`:
        #   * `$LOCI_TEST_BLE_FRESH` with no trailing `/` matched none of the
        #     `…/*` arms, fell through to `\$*`, and killed the WHOLE RUN over
        #     one eval's valid declaration;
        #   * an entry with no placeholder at all (`examples/rtos/…/x.out`)
        #     matched nothing, resolved to itself, and skipped the eval on every
        #     host forever — which is the silent-retirement this guard exists to
        #     stop.
        case "$_req" in
          \$LOCI_TEST_BLE_ROOT|\$LOCI_TEST_BLE_FRESH|\$LOCI_TEST_STALE_ROOT) ;;
          \$LOCI_TEST_BLE_ROOT/*|\$LOCI_TEST_BLE_FRESH/*|\$LOCI_TEST_STALE_ROOT/*) ;;
          /*|[A-Za-z]:*) ;;
          *)
            echo "ERROR: ${SKILL_NAME}:${EVAL_ID} declares requires_files entry" \
                 "'${_req}' — it is neither an absolute path nor one of" \
                 "\$LOCI_TEST_BLE_ROOT / \$LOCI_TEST_BLE_FRESH /" \
                 "\$LOCI_TEST_STALE_ROOT, so it can never resolve and the eval" \
                 "would skip on every host." >&2
            exit 1 ;;
        esac
        _resolved="${_req//\$LOCI_TEST_BLE_ROOT/$BLE_ROOT}"
        _resolved="${_resolved//\$LOCI_TEST_BLE_FRESH/$FRESH_BLE_ROOT}"
        _resolved="${_resolved//\$LOCI_TEST_STALE_ROOT/$STALE_ROOT}"
        if [ ! -e "$_resolved" ]; then
          SKIP_REASON="fixture missing on this host: ${_req}"
          break
        fi
      done <<< "$REQUIRES_FILES"
    fi
    # --list is discovery, not execution: an eval that cannot RUN here still exists
    # and must be listed. Skipping it before the job list made the two evals this
    # change adds undiscoverable, and `--list --eval-id sd-5` exit 1.
    # --dry-run is the same kind of thing: it resolves the argv and calls nothing,
    # so a fixture this host lacks or a plugin that is not this tree cannot make its
    # answer wrong -- and gating it on them made `--dry-run --eval-id pf-critical-1`
    # exit 2 ("every match was skipped") on any host without the fresh BLE build.
    if [[ -n "$SKIP_REASON" ]] && { $LIST_MODE || $DRY_RUN; }; then
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

    # Expand the three fixture placeholders in EVERY field that reaches the model
    # or the grader. `approve_prompt` and `source_file` were left out and are a
    # latent hole: a two-turn eval naming a fixture in its second turn would hand
    # the model the literal `$LOCI_TEST_BLE_FRESH`. No eval does that today —
    # which is exactly when it is cheap to close.
    #
    # Order between the three does not matter: no placeholder is a substring of
    # another, so none can eat another's name. The skips above guarantee the
    # staged roots are non-empty whenever an eval mentions them.
    for _var in PROMPT EXPECTED EXPECTATIONS APPROVE_PROMPT SOURCE_FILE; do
      _val="${!_var}"
      _val="${_val//\$LOCI_TEST_BLE_ROOT/$BLE_ROOT}"
      _val="${_val//\$LOCI_TEST_STALE_ROOT/$STALE_ROOT}"
      _val="${_val//\$LOCI_TEST_BLE_FRESH/$FRESH_BLE_ROOT}"
      printf -v "$_var" '%s' "$_val"
    done

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
    # …and into the report, which is what gets cited. This branch used to exit with
    # `report.md` holding a header and an empty table — indistinguishable from a run
    # in which nothing matched, and from one in which everything passed silently.
    {
      echo ""
      echo "## Summary"
      echo ""
      echo "**No evals ran — every match was skipped.**"
      echo ""
      echo "### Provenance"
      echo ""
      echo "$PLUGIN_BANNER"
      echo ""
      echo "### Skipped — these did NOT run"
      echo ""
      for s in "${SKIPPED_EVALS[@]}"; do echo "- $s"; done
    } >> "$REPORT"
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
# in the BLE tree and touch shared build state (LOCI's build directory, the resumed
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

# …and the same rule for the SINGLE-turn evals, now that they are hermetic too.
#
# A shared working copy cannot be restored concurrently, and this is not a
# theoretical race — it was reproduced against these very functions. Eval B arms
# while eval A's edit is still on disk, so B's backup captures A's edit as if it
# were a developer's own work; A sweeps and cleans the file; B sweeps and writes
# A's edit back, permanently. The same overlap makes a plan-mode B report
# `edited the fixture under plan mode (<A's file>)` for a write it never made,
# and can revert A's edit before A's own sweep sees it. Three different wrong
# answers, one cause.
#
# Before this change single-turn evals never touched the fixture, so `-j 4` was
# safe for them and the two rules above were enough. Arming them is what makes
# them share mutable state, so they join the rule that already governs everything
# else that does. In practice this costs the filtered runs only: any batch with
# an edit or two-turn eval in it — the full suite included — was already
# sequential.
if [[ $MAX_JOBS -ne 1 && $TOTAL -gt 1 && -n "$(fixture_git_root "$BLE_ROOT")" ]]; then
  echo -e "${YELLOW}NOTE: these evals share the BLE checkout and each restores it — forcing sequential (-j 1); a shared working copy cannot be restored concurrently.${NC}"
  MAX_JOBS=1
fi

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
PASSED=0; FAILED=0; ERRORED=0; BLOCKED=0; DRY=0
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
  elif [[ "$VERDICT" == "DRY_RUN" ]]; then
    echo -e "  ${EVAL_FILE_NAME}  ${EVAL_ID}: ${CYAN}· DRY RUN${NC} — $REASON"
    DRY=$((DRY + 1))
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
- Skipped: ${#SKIPPED_EVALS[@]}  (did NOT run — reason per eval below)
- Errors: $ERRORED
- Dry-run: $DRY  (argv resolved, claude never called)

### Provenance

$PLUGIN_BANNER
EOF

# The skipped evals BY NAME, in the artifact rather than only on the console. A
# reader of `report.md` could not previously learn that three post-edit criticals
# never ran, nor why — the count claimed "fixture unavailable" whatever the cause.
if [[ ${#SKIPPED_EVALS[@]} -gt 0 ]]; then
  {
    echo ""
    echo "### Skipped — these did NOT run"
    echo ""
    for s_entry in "${SKIPPED_EVALS[@]}"; do echo "- $s_entry"; done
  } >> "$REPORT"
fi

# ── Hot-path match rate ────────────────────────────────────────────────────
# How often the agent confirmed the candidate `loci analyse prepare` ranked
# first. This is the number the "heuristic default or agent confirmation every
# time?" question waits on — and it is a LOWER bound at -O0, which is why the
# caveat is printed with it rather than kept in a comment.
MATCH_ROWS_FILE="$RESULTS_DIR/match-rows.jsonl"
MATCH_SUMMARY="$RESULTS_DIR/match-rate.json"
match_rate_summary "$MATCH_ROWS_FILE" > "$MATCH_SUMMARY"
MATCH_LINE=$(match_rate_line "$MATCH_SUMMARY")
{
  echo ""
  echo "## Hot-path match rate"
  echo ""
  echo "$MATCH_LINE"
  echo ""
  echo "Per-run rows: \`match-rows.jsonl\` (proposed, selection, optimization level,"
  echo "\`attributed_by\`). A manifest belongs to an eval by turn id + manifest id —"
  echo "never by mtime; an eval whose turn id could not be learned is named above and"
  echo "its manifests are excluded rather than guessed at."
} >> "$REPORT"

echo -e "${BOLD}━━━ Summary ━━━${NC}"
echo -e "  Total:   $TOTAL"
echo -e "  ${GREEN}Passed:  $PASSED${NC}"
echo -e "  ${RED}Failed:  $FAILED${NC}"
echo -e "  ${YELLOW}Blocked: $BLOCKED${NC}  (preflight invoked but couldn't analyze — environment/setup gap)"
if [[ ${#SKIPPED_EVALS[@]} -gt 0 ]]; then
  echo -e "  ${YELLOW}Skipped: ${#SKIPPED_EVALS[@]}${NC}  (these did NOT run)"
  for s in "${SKIPPED_EVALS[@]}"; do echo "    - $s"; done
fi
echo -e "  ${YELLOW}Errors:  $ERRORED${NC}"
if (( DRY > 0 )); then
  echo -e "  ${CYAN}Dry-run: $DRY${NC}  (argv resolved, claude never called — nothing billed)"
fi
echo ""
echo -e "  ${BOLD}$MATCH_LINE${NC}"
echo ""
echo "Report: $RESULTS_DIR/report.md"
echo "Master log: $MASTER_LOG"

# BLOCKED does NOT fail the suite — it flags an environment gap (missing build
# flags / compiled-out function), not a skill defect. Only real FAILs and
# ERRORs set a non-zero exit.
# Skips are reported BEFORE the pass/fail exit, because a run with one genuine
# failure and eight never-ran guards used to exit 1 with the skips unmentioned — and
# exit 1 reads as "one thing is broken", not as "most of the suite did not execute".
if [[ ${#SKIPPED_EVALS[@]} -gt 0 ]]; then
  echo -e "  ${YELLOW}${#SKIPPED_EVALS[@]} eval(s) never ran:${NC}"
  for s_entry in "${SKIPPED_EVALS[@]}"; do echo "    - $s_entry"; done
fi

if $DRY_RUN; then
  echo "Dry run: no eval was executed and no model was called."
  exit 0
fi

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
