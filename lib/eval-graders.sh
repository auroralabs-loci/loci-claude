#!/usr/bin/env bash
# The eval suite's deterministic graders — the code that decides whether a skill
# run counts as a pass.
#
# Extracted from `run_evals.sh` so it can be SOURCED and unit-tested. It could
# not be before: `run_evals.sh` parses arguments and `cd`s into the BLE fixture
# at the top level, so sourcing it ran the whole suite. Nothing tested these
# functions, which is an odd place for a test suite to have a blind spot — a
# grader that says PASS for the wrong reason makes every eval above it
# decorative, and one of them did exactly that (see `grade_bash_post_edit`'s
# baseline handling).
#
# `run_evals.sh` sources this; so does `tests/unit/test_eval_graders.py`.
# Sourcing has no side effects: definitions only.

# ---------------------------------------------------------------------------
# grade_bash — deterministic Bash-based grader for should_trigger tests
#   $1: response text
#   $2: should_trigger ("true" | "false")
#   Writes "PASS|reason" or "FAIL|reason" to stdout
# ---------------------------------------------------------------------------
grade_bash() {
  local RESPONSE="$1"
  local SHOULD_TRIGGER="$2"

  # Three DISTINCT preflight output states must be told apart — loose substring
  # matching conflated them and produced false positives (e.g. prose like "I'll
  # run the preflight analysis" or "report the execution fit" were scored as a
  # real header / verdict). We anchor on the actual SKILL.md output format:
  #
  #   HAS_HEADER  — a genuine markdown header at line start: "## Preflight: ..."
  #                 (NOT the prose word "preflight"). Proof the skill emitted a
  #                 report block of SOME kind.
  #   IS_BLOCKED  — the header is "## Preflight: STOPPED" or "## Preflight:
  #                 BLOCKED ...". The skill invoked but COULD NOT analyze
  #                 (missing/empty .o, unresolved flags, artifacts unavailable).
  #                 This is NOT a completed analysis and carries no verdict.
  #   HAS_VERDICT — a genuine verdict LINE: "Execution fit: **PASS|CAUTION|
  #                 FAIL**". Requires the verdict token right after "fit:", so a
  #                 sentence merely containing "execution fit" does not match.
  #
  # A clean PASS for should_trigger=true needs a real header AND a real verdict.
  # A STOPPED/BLOCKED run is reported as BLOCKED — an environment/setup gap
  # (no build flags, function compiled out), NOT a skill pass or fail.
  local HAS_HEADER=false IS_BLOCKED=false HAS_VERDICT=false
  echo "$RESPONSE" | grep -qiE '^[[:space:]]*#{2,}[[:space:]]*preflight:' && HAS_HEADER=true
  echo "$RESPONSE" | grep -qiE '^[[:space:]]*#{2,}[[:space:]]*preflight:[[:space:]]*(stopped|blocked)' && IS_BLOCKED=true
  echo "$RESPONSE" | grep -qiE 'execution[[:space:]]+fit:[[:space:]]*\**[[:space:]]*(pass|caution|fail)\b' && HAS_VERDICT=true

  if [[ "$SHOULD_TRIGGER" == "true" ]]; then
    if $IS_BLOCKED; then
      echo "BLOCKED|preflight invoked but could not analyze (## Preflight: STOPPED/BLOCKED) — missing build artifacts/flags; environment gap, not a skill failure"; return
    fi
    if ! $HAS_HEADER; then
      echo "FAIL|skill did not invoke — no '## Preflight:' header (prose mentions don't count)"; return
    fi
    if ! $HAS_VERDICT; then
      echo "FAIL|invoked but produced no real 'Execution fit: PASS|CAUTION|FAIL' verdict line"; return
    fi
    echo "PASS|preflight invoked and completed — header + Execution fit verdict present"
  else
    if $IS_BLOCKED; then
      echo "FAIL|should NOT invoke (not /plan mode) but ran preflight anyway (## Preflight: STOPPED/BLOCKED)"; return
    fi
    if $HAS_HEADER; then
      echo "FAIL|should NOT invoke (not /plan mode) but emitted a '## Preflight:' header"; return
    fi
    if $HAS_VERDICT; then
      echo "FAIL|should NOT invoke (not /plan mode) but emitted an 'Execution fit:' verdict"; return
    fi
    echo "PASS|correctly stayed silent — no preflight invocation outside /plan mode"
  fi
}

# ---------------------------------------------------------------------------
# grade_bash_post_edit — deterministic Bash-based grader for post-edit tests
#   $1: response text
#   $2: should_trigger ("true" | "false")
#   $3: expect_baseline ("true" → a missing Before is a FAILURE; anything else →
#       today's behaviour, where the report may legitimately have no Before)
#   $4: expect_no_change ("true" → the edit's net effect on the compiled object
#       is nothing, and the report must SAY so rather than invent a delta)
#   Writes "PASS|reason" or "FAIL|reason" to stdout
#
# On $3 and $4, and why they are opt-in rather than inferred: an eval that edits
# the first file ever touched in a fresh tree has no pre-edit object to snapshot,
# so "no baseline" there is the truth and failing it would turn an environment
# gap into a skill regression. An eval that edits a file the fixture has already
# built is the opposite: a missing Before is precisely the defect this whole
# branch exists to remove, and passing it made the eval decorative. Only the
# eval knows which it is, so only the eval may say — and it says it as the
# positive literal `true`, never as an absence.
# ---------------------------------------------------------------------------
grade_bash_post_edit() {
  local RESPONSE="$1"
  local SHOULD_TRIGGER="$2"
  local EXPECT_BASELINE="${3:-false}"
  local EXPECT_NO_CHANGE="${4:-false}"

  # Anchored on the CURRENT loci-post-edit SKILL.md output (Step 6), NOT the
  # obsolete "Happy path / Worst path / ### Control Flow" prose the skill no
  # longer emits. The skill now renders a Gate conclusion table headed
  # "## Post-Edit: <fn>" with Performance/Energy rows (Before/After +(±%) in the
  # Note) and a "Verdict: **PASS|CAUTION|FAIL**" footer line, plus a one-line
  # "<icon> LOCI post-edit · …" footer. CFG no longer surfaces as its own
  # section — it feeds the Note column (e.g. "new hot-path block bb_0x1ea").
  # Three structural states are told apart:
  #
  #   HAS_HEADER   — a real markdown header "## Post-Edit:" at line start, OR the
  #                  "LOCI post-edit" footer line. Proof the report was emitted;
  #                  prose like "I'll run the post-edit analysis" does NOT count.
  #   HAS_VERDICT  — a real verdict LINE "Verdict: **PASS|CAUTION|FAIL**", OR the
  #                  footer scalar "… LOCI post-edit ·".
  #   HAS_DIFF     — a "%" appears (the ±X% timing/energy diff in the Note column
  #                  or the footer "(-17%, …)"). Required ONLY when a baseline
  #                  exists (a pre-edit .o.prev).
  #   NO_BASELINE  — the report states it has no pre-edit baseline (SKILL.md emits
  #                  "(no pre-edit artifact — …)" / "no preflight baseline" and
  #                  reports absolute values only, so there is no % diff to assert).
#   NO_CHANGE    — the report states the compiled functions did not change. Since
#                  phase 11 that is a real answer with its own shape (an empty
#                  changed-function list gates the metered half and Step 2a
#                  reports ROM/RAM and frames instead), so it is NOT a missing
#                  measurement and must not be graded as one.
  local HAS_HEADER=false HAS_VERDICT=false HAS_DIFF=false NO_BASELINE=false NO_CHANGE=false
  echo "$RESPONSE" | grep -qiE '(^[[:space:]]*#{2,}[[:space:]]*post-edit|loci[[:space:]]+post-edit)' && HAS_HEADER=true
  echo "$RESPONSE" | grep -qiE '(^[[:space:]]*verdict:[[:space:]]*\**[[:space:]]*(pass|caution|fail)\b|loci[[:space:]]+post-edit[[:space:]]*·)' && HAS_VERDICT=true
  # A SIGNED percentage next to a digit, not a bare `%`. The old test matched any
  # `%` anywhere in the transcript — a `printf("%d")` in the quoted diff, a
  # "100% of the callees", or the model saying "I'm 90% sure" all satisfied it,
  # so "the report carries a Before→After delta" was pinned by nothing.
  echo "$RESPONSE" | grep -qE '[+-][0-9]+(\.[0-9]+)?[[:space:]]*%|[0-9](\.[0-9]+)?[[:space:]]*%[[:space:]]*(faster|slower|more|less)' && HAS_DIFF=true
  echo "$RESPONSE" | grep -qiE 'no pre-edit artifact|no preflight baseline|first[ -]?edit measurement|first measurement|absolute values only' && NO_BASELINE=true
  echo "$RESPONSE" | grep -qiE 'no (net )?change|unchanged|identical|0 changed functions|no functions? changed|nothing changed' && NO_CHANGE=true

  if [[ "$SHOULD_TRIGGER" == "true" ]]; then
    if ! $HAS_HEADER; then
      echo "FAIL|skill did not invoke — no '## Post-Edit:' header or 'LOCI post-edit' footer (prose mentions don't count)"; return
    fi
    if ! $HAS_VERDICT; then
      echo "FAIL|invoked but produced no 'Verdict: PASS|CAUTION|FAIL' line or footer scalar"; return
    fi
    # Checked BEFORE the no-baseline pass below, and that order is the whole
    # change: an eval that declares a baseline must exist is one where a report
    # saying "no pre-edit artifact" is the failure under test, not a licence to
    # skip the rest of the grading.
    if [[ "$EXPECT_BASELINE" == "true" ]] && $NO_BASELINE; then
      echo "FAIL|a baseline was required for this eval and the report says it had none — the pre-edit capture did not survive to the measurement"; return
    fi
    if [[ "$EXPECT_NO_CHANGE" == "true" ]]; then
      # The edit-and-revert case. The object is byte-for-byte what the baseline
      # was, so the honest report is "nothing changed" — and a report that
      # invents a delta here is worse than one that says nothing, because the
      # number is fabricated rather than merely missing.
      if ! $NO_CHANGE; then
        echo "FAIL|the turn's net effect on the object was nothing and the report does not say so"; return
      fi
      if $HAS_DIFF; then
        echo "FAIL|the turn's net effect was nothing, yet the report carries a signed % delta"; return
      fi
      if $NO_BASELINE; then
        echo "FAIL|reported as a no-baseline run: with no Before it cannot have established that nothing changed"; return
      fi
      echo "PASS|edit-and-revert reported as no net change, with a baseline and no invented delta"; return
    fi
    if $NO_BASELINE; then
      echo "PASS|post-edit invoked (no baseline) — header + verdict present, absolute values only"; return
    fi
    if $NO_CHANGE && ! $HAS_DIFF; then
      # Phase 11: an empty changed-function list is an answer, and it legitimately
      # carries no percentage. Distinguished from the failure below by the report
      # SAYING so — silence still fails.
      echo "PASS|post-edit invoked and reported the object's functions unchanged (no delta to show)"; return
    fi
    if ! $HAS_DIFF; then
      echo "FAIL|baseline run but no signed % diff present in the report"; return
    fi
    echo "PASS|post-edit invoked and completed — header + verdict + % diff present"
  else
    if $HAS_HEADER; then
      echo "FAIL|should NOT invoke but emitted a '## Post-Edit:' header/footer"; return
    fi
    echo "PASS|correctly did not invoke post-edit"
  fi
}

# ---------------------------------------------------------------------------
# grade_bash_combined — deterministic grader for the end-to-end combined flow
#   (loci-preflight in plan mode → resume+edit → loci-post-edit). Graded on the
#   JOINED transcript of BOTH turns. A clean PASS needs all four:
#     • preflight ran    — a real '## Preflight:' header (line-start, not prose)
#     • preflight verdict — an 'Execution fit: PASS|CAUTION|FAIL' line
#     • post-edit ran    — a real '## Post-Edit' header OR the 'LOCI post-edit' footer
#     • post-edit verdict — a 'Verdict: PASS|CAUTION|FAIL' line OR the footer scalar
#   Numbers are NOT asserted — the model writes the code, so timing/energy values
#   are non-deterministic. This grades that the WHOLE pipeline fired and emitted
#   well-formed reports, which is the behavior under test.
#   $1: joined response text   →   writes "PASS|reason" or "FAIL|reason" to stdout
# ---------------------------------------------------------------------------
grade_bash_combined() {
  local RESPONSE="$1"
  local HAS_PF_HEADER=false HAS_PF_VERDICT=false
  local HAS_PE_HEADER=false HAS_PE_VERDICT=false

  echo "$RESPONSE" | grep -qiE '^[[:space:]]*#{2,}[[:space:]]*preflight:' && HAS_PF_HEADER=true
  echo "$RESPONSE" | grep -qiE 'execution[[:space:]]+fit:[[:space:]]*\**[[:space:]]*(pass|caution|fail)\b' && HAS_PF_VERDICT=true
  # Post-edit presence: a markdown header OR the LOCI post-edit footer line.
  echo "$RESPONSE" | grep -qiE '(^[[:space:]]*#{2,}[[:space:]]*post-edit|loci[[:space:]]+post-edit)' && HAS_PE_HEADER=true
  # Post-edit verdict: the body 'Verdict: **PASS|CAUTION|FAIL**' line OR the footer scalar.
  echo "$RESPONSE" | grep -qiE '(^[[:space:]]*verdict:[[:space:]]*\**[[:space:]]*(pass|caution|fail)\b|loci[[:space:]]+post-edit[[:space:]]*·)' && HAS_PE_VERDICT=true

  local MISSING=""
  $HAS_PF_HEADER  || MISSING="$MISSING preflight-header"
  $HAS_PF_VERDICT || MISSING="$MISSING preflight-verdict"
  $HAS_PE_HEADER  || MISSING="$MISSING post-edit-header"
  $HAS_PE_VERDICT || MISSING="$MISSING post-edit-verdict"

  if [[ -n "$MISSING" ]]; then
    echo "FAIL|pipeline incomplete — missing:${MISSING}"
  else
    echo "PASS|full pipeline fired — preflight report+verdict AND post-edit report+verdict present in joined transcript"
  fi
}

