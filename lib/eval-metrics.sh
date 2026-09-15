#!/usr/bin/env bash
# Per-eval accounting for the eval suite: what a run cost, and how often the
# agent confirmed the hot-path candidate `loci analyse prepare` ranked first.
#
# Extracted from `run_evals.sh` for the same reason `lib/eval-graders.sh` was:
# that script parses arguments and stages fixtures at the top level, so it
# cannot be sourced, and nothing here could be tested without running a real
# eval. Sourcing this file has no side effects: definitions only.
#
# It also owns the fixture hygiene at the bottom of the file — the tree an eval
# ran in, put back the way it was found — for the same reason: `run_evals.sh`
# cannot be sourced, so a restore living there could only ever be verified by
# running a real eval against a real checkout.
#
# `run_evals.sh` sources it; so does `tests/unit/test_eval_harness_flags.py`.

# ---------------------------------------------------------------------------
# The caveat that must travel with every match-rate number.
# ---------------------------------------------------------------------------
# The eval fixtures compile at -O0, where only the STRUCTURAL heuristics act:
# the layout signals are allow-listed to gcc -O1/-O2/-Os and contribute nothing
# at -O0. A match rate measured here is therefore a LOWER BOUND — production
# objects build with the project's -Os/-O2 flags, where the layout signals are
# live too. Printed, not just commented, wherever the number is reported.
MATCH_RATE_CAVEAT="fixtures compile at -O0, where only the structural heuristics act (the layout signals need -O1/-O2/-Os) — this is a LOWER BOUND on the production match rate"

# ---------------------------------------------------------------------------
# eval_metrics_json <stream-json file> <model> <wall_clock_s> [flow]
#                   [fixture_dirty] [plugin_under_test]
#   The run's cost and shape, from the `result` event of a stream-json
#   transcript. Emits one JSON object on stdout; never fails the caller.
#
#   `fixture_dirty` is the newline-separated list `fixture_restore` printed: the
#   tracked files this run changed in the fixture and the harness put back. It
#   is always an ARRAY in the output, empty when the run touched nothing, so a
#   reader can tell "left the tree alone" from "never checked".
#
#   `plugin_under_test` says WHICH plugin answered — `plugin-dir:<sha>[-dirty]`
#   for this working tree, `installed:<sha>` for the marketplace copy, or
#   `bare:no-plugin` for a `--bare` run, which loads neither. A verdict is only
#   attributable to a branch if the artifact beside it says which code produced
#   it; that used to live in the run's console banner alone.
#
#   `claude -p --output-format json` collapses a run to that single result
#   object and would lose every intermediate assistant turn the graders read,
#   so the harness keeps stream-json and reads the same fields off its final
#   result event instead. Same numbers, nothing given up.
# ---------------------------------------------------------------------------
eval_metrics_json() {
  local FILE="$1" MODEL="${2:-}" WALL="${3:-0}" FLOW="${4:-single}"
  local DIRTY="${5:-}" PLUGIN="${6:-}"
  local body='{}'
  if [[ -s "$FILE" ]]; then
    body=$(jq -rs '
      ([.[] | select(.type == "result")] | last) as $r
      | {
          num_turns:      ($r.num_turns // null),
          total_cost_usd: ($r.total_cost_usd // null),
          duration_ms:    ($r.duration_ms // null),
          stop_reason:    ($r.stop_reason // null),
          is_error:       ($r.is_error // null),
          usage:          ($r.usage // null),
          session_id:     ($r.session_id // null),
          models: ([.[] | select(.type == "assistant") | .message.model?]
                   | map(select(. != null)) | unique)
        }' "$FILE" 2>/dev/null) || body='{}'
    [[ -n "$body" ]] || body='{}'
  fi
  jq -n --argjson m "$body" --arg model "$MODEL" --arg wall "$WALL" \
        --arg flow "$FLOW" --arg dirty "$DIRTY" --arg plugin "$PLUGIN" \
    '$m + {model_requested: $model, wall_clock_s: ($wall | tonumber? // 0),
           flow: $flow,
           plugin_under_test: (if $plugin == "" then null else $plugin end),
           fixture_dirty_after_run:
             ($dirty | split("\n") | map(select(length > 0)))}'
}

# ---------------------------------------------------------------------------
# eval_metrics_merge <out> <label>=<file> …
#   One metrics object per labelled turn, plus the totals across them. Used by
#   the two-turn flow, where a single eval is two billed `claude -p` calls.
# ---------------------------------------------------------------------------
eval_metrics_merge() {
  local OUT="$1"; shift
  local acc='{}'
  local pair label file
  for pair in "$@"; do
    label="${pair%%=*}"; file="${pair#*=}"
    acc=$(jq -n --argjson a "$acc" --arg k "$label" \
              --argjson v "$(cat "$file" 2>/dev/null || echo '{}')" \
              '$a + {($k): $v}')
  done
  # The two provenance fields are HOISTED off the turns rather than passed in a
  # second time: a two-turn eval is one run, against one plugin, in one fixture,
  # and a reader of the merged file should not have to know which turn happens
  # to carry them. Reading them back off the turns is also the only spelling
  # that cannot drift from what the turns actually recorded.
  jq -n --argjson t "$acc" '
    $t + {totals: {
      num_turns:      ([$t[] | .num_turns // 0]      | add),
      total_cost_usd: ([$t[] | .total_cost_usd // 0] | add),
      wall_clock_s:   ([$t[] | .wall_clock_s // 0]   | add)
    },
    plugin_under_test:
      ([$t[] | .plugin_under_test // empty] | first // null),
    fixture_dirty_after_run:
      ([$t[] | .fixture_dirty_after_run // []] | add // [] | unique)}' > "$OUT"
}

# ---------------------------------------------------------------------------
# turn_ids_from_prepare <stream-json file> …
#   The turn ids this eval ran under, read from `loci analyse prepare`'s own
#   envelope in the eval's transcript: `.data.turn`, the same spelling as the
#   manifest field and the run/measurement records. One id per line, unique.
#
#   Read from `prepare` rather than from the `[loci] turn=<id>` context line the
#   UserPromptSubmit hook injects: the envelope is output the eval produced, so
#   nothing depends on injected context reaching the transcript. It also lands
#   earliest — an eval that never got as far as `prepare` has no manifest to
#   attribute either, so no turn id is the right answer there.
#
#   This is what replaced the mtime fence. Manifests were previously attributed
#   to an eval by "written since this eval started", which cross-attributes as
#   soon as two single-turn evals run in parallel against the one shared
#   BLE_ROOT — they share one build directory entirely.
# ---------------------------------------------------------------------------
turn_ids_from_prepare() {
  local f
  for f in "$@"; do
    [[ -s "$f" ]] || continue
    # Every string in the transcript, decoded by jq, then `.data.turn` off the
    # ones that parse as an envelope — a tool result carries the envelope as an
    # escaped JSON string, so a raw grep would have to match both spellings.
    # The capture is the fallback for an envelope printed with text around it.
    # `|| true`: no match is not a failure, and the caller runs under `set -e`.
    jq -rs '[.. | strings]
            | map(select(test("\"turn\"")))
            | map(. as $s
                  | (try ($s | fromjson | .data.turn)
                     catch (try ($s
                       | capture("\"turn\"[ \n]*:[ \n]*\"(?<t>[^\"]+)\"").t)
                       catch empty)))
            | map(select(type == "string" and . != ""))
            | unique | .[]' "$f" 2>/dev/null || true
  done | tr -d '\r' | sort -u
}

# ---------------------------------------------------------------------------
# turn_id_from_stamp <project root>
#   `.loci/build/turn/current`, the id the same hook stamps on disk. Serial runs
#   only: the file is per-project, so with -j > 1 the last prompt to land wins
#   and the id would belong to another eval. run_evals.sh passes it only when
#   MAX_JOBS is 1.
# ---------------------------------------------------------------------------
turn_id_from_stamp() {
  # One root (T14): the CLI stamps `.loci/build/turn/current` and reads no other.
  local FILE="$1/.loci/build/turn/current"
  [[ -s "$FILE" ]] && tr -d '[:space:]' < "$FILE"
  return 0
}

# ---------------------------------------------------------------------------
# build_dir_of <project root>
#   The build directory `analyse prepare` writes under — `.loci/build`, the only
#   one since T14. Manifests sit in `turns/<key>/manifests/` under it, and
#   directly in `manifests/` for trees written before they moved into turns.
# ---------------------------------------------------------------------------
build_dir_of() {
  echo "$1/.loci/build"
}

# ---------------------------------------------------------------------------
# runs_manifest_ids <runs jsonl> <turn id>
#   The manifest ids `analyse measure` recorded for that turn. T6b stamps `turn`
#   onto the skill-run record next to `manifest_id`, so the (turn, manifest_id)
#   pair on the record is the attribution key; nothing here consults a timestamp.
# ---------------------------------------------------------------------------
runs_manifest_ids() {
  local RUNS="$1" TURN="$2"
  [[ -s "$RUNS" ]] || return 0
  jq -r --arg t "$TURN" 'select(.turn == $t and .manifest_id != null)
                         | .manifest_id' "$RUNS" 2>/dev/null | tr -d '\r' | sort -u
}

# ---------------------------------------------------------------------------
# skill_runs_files
#   The per-project skill-runs projections `analyse measure` appends to
#   (`stats.STATE_DIR`, one file per project+branch). Empty output is fine: the
#   manifest carries `turn` and `id` itself, and the run record is a cross-check.
# ---------------------------------------------------------------------------
skill_runs_files() {
  local DIR="${LOCI_STATE_DIR:-${HOME:-}/.loci/state}"
  [[ -d "$DIR" ]] || return 0
  ls -1 "$DIR"/loci-skill-runs-*.jsonl 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# opt_level_from_meta <meta.json>
#   The optimization level an object was compiled at, from `loci build
#   compile`'s meta sidecar (`flags`). The LAST -O wins, as it does on a real
#   command line. "unknown" when no sidecar or no -O flag.
# ---------------------------------------------------------------------------
opt_level_from_meta() {
  local META="$1"
  [[ -s "$META" ]] || { echo "unknown"; return; }
  local level
  level=$(jq -r '[.flags[]? | select(test("^-O"))] | last // ""' "$META" 2>/dev/null || echo "")
  # No -O flag at all is -O0: that is what the compiler does, and calling it
  # "unknown" would hide exactly the case the lower-bound caveat is about.
  [[ -n "$level" ]] && echo "$level" || echo "-O0"
}

# ---------------------------------------------------------------------------
# match_row <manifest.json> <project root> [tag] [basis]
#   One `(proposed, selection)` row for a measured manifest, with the
#   optimization level its artifacts were built at. Nothing on stdout for a
#   manifest that was never measured — an unmeasured manifest carries no
#   agent decision to score.
# ---------------------------------------------------------------------------
match_row() {
  local MANIFEST="$1" ROOT="$2" TAG="${3:-}" BASIS="${4:-manifest}"
  [[ -s "$MANIFEST" ]] || return 0
  local state
  state=$(jq -r '.state // ""' "$MANIFEST" 2>/dev/null || echo "")
  [[ "$state" == "measured" ]] || return 0

  local opt="unknown" rel
  while IFS= read -r rel; do
    # jq on Windows ends every line with CRLF. `$(...)` strips the CR; `read`
    # does not, and `comms.o\r.meta.json` is a file that does not exist -- which
    # is how every match row read `unknown` on a Windows clone.
    rel=${rel%$'\r'}
    [[ -n "$rel" ]] || continue
    opt=$(opt_level_from_meta "$ROOT/$rel.meta.json")
    break
  done < <(jq -r '.artifacts.after | keys[]?' "$MANIFEST" 2>/dev/null || true)

  jq -c --arg opt "$opt" --arg tag "$TAG" --arg caveat "$MATCH_RATE_CAVEAT" \
        --arg basis "$BASIS" '
    (.proposed // []) as $p
    | (.selection // []) as $s
    | {eval: $tag, manifest: .id, turn: .turn,
       attributed_by: $basis,
       proposed: $p, selection: $s,
       confirmed: ([$p[] | select(. as $x | $s | index($x))] | length),
       proposed_n: ($p | length),
       overridden: [$s[] | select(. as $x | $p | index($x) | not)],
       opt_level: $opt,
       lower_bound: ($opt == "-O0"),
       caveat: $caveat}' "$MANIFEST"
}

# ---------------------------------------------------------------------------
# match_scan <build dir> <project root> <tag> <out jsonl> <turn ids> [runs jsonl …]
#   Appends a row per manifest belonging to this eval's turn(s) — attribution by
#   identity, never by time. `<turn ids>` is whitespace-separated (see
#   turn_ids_from_transcript / turn_id_from_stamp).
#
#   With a runs projection, a manifest is scored only when a `measure` record
#   carries the same (turn, manifest_id) pair; without one, the manifest's own
#   `turn`/`id` stand in, and the row says which basis was used.
#
#   No turn id (no `prepare` envelope, and no stamp on a serial run) → nothing is
#   scored and a `no-turn-id` notice row is written, so
#   the report says the eval could not be attributed instead of quietly falling
#   back to mtime and possibly crediting another eval's manifest.
# ---------------------------------------------------------------------------
match_scan() {
  local DIR="$1" ROOT="$2" TAG="$3" OUT="$4" TURNS="$5"; shift 5
  local RUNS=("$@")

  if [[ -z "${TURNS// }" ]]; then
    jq -nc --arg tag "$TAG" --arg note \
      "no .data.turn from analyse prepare in the transcript and no turn stamp — the manifests of this eval are not attributed; mtime is never used as a fallback" \
      '{eval: $tag, attribution: "no-turn-id", note: $note}' >> "$OUT"
    echo "  match-rate: no turn id for $TAG — manifests not attributed" >&2
    return 0
  fi
  [[ -d "$DIR" ]] || return 0

  # `<basis> <id>` per line: the basis is decided per TURN, never once for the
  # scan. One eval can have a turn whose run record was found and another whose
  # was not, and a row must say which of the two answered for it.
  local pairs="" turn f row id basis
  for turn in $TURNS; do
    local found=""
    for r in ${RUNS[@]+"${RUNS[@]}"}; do
      [[ -n "$r" && -s "$r" ]] || continue
      found+=$'\n'"$(runs_manifest_ids "$r" "$turn")"
    done
    if [[ -n "${found//[[:space:]]/}" ]]; then
      basis="run-record"
    else
      basis="manifest"
      # `tr -d '\r'`, for the same reason `runs_manifest_ids` above does it:
      # jq on Windows ends every line with CRLF, and this list is fed to
      # `read`, which keeps the CR. `$(...)` strips only the last one, so
      # every id but the last read as `m-abcdef123456<CR>` -- a manifest
      # whose file is not found, silently dropped from the match rate.
      # Since manifests moved into the turn trees there is normally more
      # than one per turn, so this went from invisible to load-bearing.
      found=$(shopt -s nullglob
              files=( "$DIR"/turns/*/manifests/m-*.json "$DIR"/manifests/m-*.json )
              [[ ${#files[@]} -gt 0 ]] || exit 0
              jq -r --arg t "$turn" \
                'select(.turn == $t and .state == "measured") | .id' \
                "${files[@]}" 2>/dev/null | tr -d '\r' || true)
    fi
    while IFS= read -r id; do
      [[ -n "${id//[[:space:]]/}" ]] || continue
      pairs+="$basis $id"$'\n'
    done <<< "$found"
  done

  local seen=""
  while read -r basis id; do
    [[ -n "${id//[[:space:]]/}" ]] || continue
    case " $seen " in *" $id "*) continue ;; esac
    seen+=" $id"
    f=""
    for cand in "$DIR"/turns/*/manifests/"$id".json "$DIR/manifests/$id.json"; do
      [[ -f "$cand" ]] && { f="$cand"; break; }
    done
    [[ -n "$f" ]] || continue
    row=$(match_row "$f" "$ROOT" "$TAG" "$basis") || continue
    # `if`, not `[[ … ]] && echo`: an empty row is the normal unmeasured case,
    # and a failing last statement aborts the eval under the caller's `set -e`.
    if [[ -n "$row" ]]; then echo "$row" >> "$OUT"; fi
  done <<< "$pairs"
}

# ---------------------------------------------------------------------------
# match_rate_summary <rows jsonl>
#   The aggregate: how often the agent confirmed the top-ranked candidate.
#   Carries the caveat and the optimization levels the number was measured at,
#   so it can never be quoted without them.
# ---------------------------------------------------------------------------
match_rate_summary() {
  local ROWS="$1"
  if [[ ! -s "$ROWS" ]]; then
    jq -n --arg caveat "$MATCH_RATE_CAVEAT" \
      '{runs: 0, proposed: 0, confirmed: 0, match_rate: null,
        opt_levels: [], attributed_by: [], unattributed: [],
        lower_bound: true, caveat: $caveat}'
    return
  fi
  jq -s --arg caveat "$MATCH_RATE_CAVEAT" '
    ([.[] | select(.attribution == "no-turn-id") | .eval] | unique) as $miss
    | [.[] | select(.attribution == null)] as $r
    | {runs: ($r | length),
       proposed:  ([$r[].proposed_n] | add // 0),
       confirmed: ([$r[].confirmed]  | add // 0),
       opt_levels: ([$r[].opt_level] | unique),
       attributed_by: ([$r[].attributed_by] | map(select(. != null)) | unique),
       unattributed: $miss,
       overridden: ([$r[].overridden[]] | length)}
    | . + {match_rate: (if .proposed > 0 then (.confirmed / .proposed) else null end),
           lower_bound: (.opt_levels | (length == 0 or all(. == "-O0"))),
           caveat: $caveat}' "$ROWS"
}

# ---------------------------------------------------------------------------
# match_rate_line <summary json>
#   One human line. The caveat is part of the sentence, not a footnote.
# ---------------------------------------------------------------------------
match_rate_line() {
  local SUMMARY="$1"
  jq -r '(if (.unattributed // []) | length > 0 then
            " " + ((.unattributed | length) | tostring) +
            " eval(s) could not be attributed to a turn (" +
            (.unattributed | join(", ")) + ") — their manifests are excluded."
          else "" end) as $miss
         | (if .proposed == 0 then
             "Hot-path match rate: no measured manifests — nothing to report."
           else
             "Hot-path match rate: \(.confirmed)/\(.proposed) " +
             "(\((.match_rate * 1000 | round) / 10)% of top-ranked candidates confirmed)" +
             " across \(.runs) run(s) at \(.opt_levels | join(", "))" +
             " attributed by \((.attributed_by // ["turn"]) | join(", "))" +
             (if .lower_bound then " — " + .caveat else "" end) + "."
           end) + $miss' "$SUMMARY"
}

# ---------------------------------------------------------------------------
# Fixture hygiene — the tree an eval ran in, left the way it was found
# ---------------------------------------------------------------------------
# An eval runs a real agent inside a real checkout, so a model that edits a
# source file there leaves the NEXT eval measuring a tree nobody described. Not
# hypothetical: during T14 `pf-critical-1` edited `app_connection.c` under
# BLE_ROOT on three of four passes — in plan mode — and the pass in between then
# answered "nothing to implement", because the guard it was being asked to add
# was already in the file. Every one of those four verdicts was about the
# fixture rather than about the skill.
#
# The edit and two-turn flows already back up the one `source_file` they
# declare. These functions are the general case, for every flow: whatever the
# run changed, put back; whatever it created, named and left alone.
#
# Three rules, all load-bearing.
#
#   * ONLY a git checkout's own TOP LEVEL. `git` prints repo-relative paths, so
#     a fixture root one directory below the top would have every path resolved
#     against the wrong base — and a restore that guesses a base can reach
#     outside the tree it was pointed at. The staged fixtures (the stale tree,
#     the fresh BLE copy) are not checkouts at all and switch this off entirely,
#     which is right: the harness restages them per run.
#
#   * NEVER `stash`, `reset --hard` or `clean`. The BLE fixture is a shared
#     working copy that a developer may have their own edits in, and a run must
#     not touch a file it did not change. Named paths only.
#
#   * A file that was ALREADY modified before the run is restored from a COPY,
#     never with `git checkout --`: checkout would put back HEAD and throw that
#     developer's edit away, which is the same damage by a quieter route. This
#     is the rule the edit flow's `SRC_BACKUP` has always followed ("safer than
#     `git checkout`, which would discard any local edits the user already
#     had"); it is now general.
# ---------------------------------------------------------------------------

# fixture_git_root <dir>
#   <dir> when it is the top level of a git checkout, empty otherwise. Empty is
#   the OFF switch for everything below, so every function starts here.
fixture_git_root() {
  local dir="${1:-}" top
  [[ -n "$dir" && -d "$dir" ]] || return 0
  top=$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null) || return 0
  [[ -n "$top" && -d "$top" ]] || return 0
  # `-ef` — same device and inode — NOT a string compare. git answers in the
  # host's spelling (`C:/Users/…` under Git for Windows) while the harness asks
  # in the shell's (`/c/Users/…`), and MSYS canonicalises the two to DIFFERENT
  # strings: `cd C:/Users/User/AppData/Local/Temp && pwd -P` prints `/tmp`,
  # where `cd /c/Users/User/AppData/Local/Temp && pwd -P` prints itself. A
  # string compare therefore called every Windows fixture root a subdirectory
  # and switched the whole mechanism off — silently, on the one platform this
  # was written on. Caught by the tests below, which run against a real
  # checkout under pytest's tmp_path rather than a hand-made `/tmp` one.
  [[ "$dir" -ef "$top" ]] || return 0
  # The CALLER's spelling back, not git's: it is what `$root/$path` is joined
  # onto everywhere below, and what the harness already prints in its logs.
  printf '%s\n' "$dir"
}

# fixture_tracked_dirty <root>
#   Repo-relative paths of the TRACKED files that differ from HEAD — modified,
#   deleted, or staged — one per line. Untracked files are deliberately not
#   here: `.loci/` state is the legitimate product of a measuring eval.
fixture_tracked_dirty() {
  local root; root=$(fixture_git_root "${1:-}")
  [[ -n "$root" ]] || return 0
  # `diff … HEAD` rather than `status --porcelain`: it answers in plain
  # repo-relative paths with no status column to strip and no `"quoting"` of
  # names with spaces in them, and it covers the staged half too.
  git -C "$root" diff --name-only HEAD 2>/dev/null || true
}

# fixture_untracked <root>
#   Untracked paths, directories collapsed, one per line. Reported, never
#   removed.
fixture_untracked() {
  local root; root=$(fixture_git_root "${1:-}")
  [[ -n "$root" ]] || return 0
  git -C "$root" status --porcelain --untracked-files=normal 2>/dev/null \
    | sed -n 's/^?? //p' || true
}

# fixture_snapshot <root> <backup-dir>
#   Copy aside every tracked file that is ALREADY modified. Those are exactly
#   the files git cannot put back for us, because "back" for them is not HEAD.
#   Normally a no-op — the fixture is usually clean — and the copies are the
#   record, so nothing is printed.
fixture_snapshot() {
  local root; root=$(fixture_git_root "${1:-}")
  local bak="${2:-}" p
  [[ -n "$root" && -n "$bak" ]] || return 0
  while IFS= read -r p; do
    _fixture_path_is_safe "$p" || continue
    [[ -f "$root/$p" ]] || continue
    mkdir -p "$bak/$(dirname "$p")" 2>/dev/null || continue
    cp -p "$root/$p" "$bak/$p" 2>/dev/null || true
  done < <(fixture_tracked_dirty "$root")
  return 0
}

# _fixture_path_is_safe <repo-relative path>
#   A path git printed, re-checked before anything is written through it.
#   Absolute paths, anything with a backslash in it, and any `..` segment are
#   refused. git emits none of those, which is the point: this costs nothing and
#   it moves "the restore stays inside the fixture root" from a promise about
#   git's output to something the code checks. The path check alone is not the
#   whole of it — a symlink at the DESTINATION would still be written through,
#   which is why `fixture_restore` unlinks before it copies.
_fixture_path_is_safe() {
  local p="${1:-}"
  [[ -n "$p" ]] || return 1
  case "$p" in
    /*) return 1 ;;
    # Any backslash at all. That covers a drive-letter path (`C:\x`) AND
    # the traversal spelling the forward-slash test below waves through
    # (`..\x`). git prints `/` on every platform, Windows included, so a
    # backslash here means the path did not come from where this thinks it
    # did, which is reason enough on its own.
    *\\*) return 1 ;;
  esac
  case "/$p/" in
    */../*) return 1 ;;
  esac
  return 0
}

# fixture_restore <root> <backup-dir>
#   Put back every tracked file the run changed, and print one repo-relative
#   path per line for each one actually restored. Printing nothing means the run
#   left the tracked tree exactly as it found it — which is the normal, quiet
#   case and the one the metrics record as an empty list.
fixture_restore() {
  local root; root=$(fixture_git_root "${1:-}")
  local bak="${2:-}" p candidates
  [[ -n "$root" ]] || return 0
  # The UNION of "dirty now" and "was copied aside before the run". A run can
  # leave a file dirty, and it can just as easily REVERT a developer's
  # uncommitted edit — which afterwards shows up in `git diff` nowhere at all.
  # Both directions are damage, and only the union sees the second one.
  candidates=$( { fixture_tracked_dirty "$root"
                  if [[ -n "$bak" && -d "$bak" ]]; then
                    ( cd "$bak" && find . -type f 2>/dev/null \
                        | sed 's|^\./||' ) || true
                  fi
                } | sort -u )
  while IFS= read -r p; do
    _fixture_path_is_safe "$p" || continue
    if [[ -n "$bak" && -f "$bak/$p" ]]; then
      cmp -s "$bak/$p" "$root/$p" 2>/dev/null && continue
      mkdir -p "$(dirname "$root/$p")" 2>/dev/null || continue
      # Unlink before writing. `cp` FOLLOWS a symlink at the destination, so a
      # run that replaced a tracked file with a link to somewhere else would
      # have the restore write through it — the one way this could reach outside
      # the fixture after the path check above. Removing the entry first means
      # the copy always lands on a fresh regular file.
      rm -f "$root/$p" 2>/dev/null || true
      cp -p "$bak/$p" "$root/$p" 2>/dev/null || continue
    else
      git -C "$root" checkout -- "$p" >/dev/null 2>&1 || continue
    fi
    printf '%s\n' "$p"
  done <<< "$candidates"
  return 0
}
