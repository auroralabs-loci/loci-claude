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
# The recipe-flow assertion every graded report owes (T14). Since Phase 4 every
# measurement rests on a recipe, and the one thing about that a bash grader can
# judge — deterministic, about the FLOW rather than a number — is whether init ran:
#
#   _init_flow_failure       a coded `not_initialized` in the transcript must NOT
#                            be followed by a `loci init` CALL.
#
# **This assertion is inverted from what it was**, and the inversion is the point
# rather than a fix to it. It used to demand the auto-init the init rule then
# prescribed, and treated "relayed the code and stopped" as the failure. Adopting
# a project is the USER'S decision: `loci init` writes four files into their tree
# and records `confirmed_by_user: false`, so a skill that runs it because an edit
# touched a `.c` file has adopted a repo nobody pointed LOCI at. Relaying the
# code, naming `/loci:init` and stopping is now the correct flow, and an init call
# the user did not ask for is the failure this exists to catch. Checked right
# after the header/verdict checks, on the tool calls the harness hands over
# (`LOCI_EVAL_TOOL_CALLS`); text only as a fallback.
#
# The repair half of `not_initialized` (a recipe IS on disk, degraded state) does
# still invoke init, and this grader cannot see which half a scenario is in. It
# does not need to: every eval that reaches it starts from an uninitialized
# fixture, so a `loci init` call in one of these transcripts is the adoption
# case by construction. A repair-flow eval would need its own grader.
#
# The `Recipe:` provenance line is NOT a bash grader's to demand. The two skills
# these graders grade print none by design — preflight ("One exception, one
# line", pinned by pf-recipe-1) and post-edit ("reports a delta … Never print the
# full absolute-verb line", pinned by its own evals) — so a gate on it here
# failed every correct report (review round 3). The line belongs to the absolute
# verbs (exec-trace, stack-depth, memory-report), whose evals are claude-graded
# and assert it in their own words (stack-depth's sd-5/sd-6 name the recipe's
# target and tier).
# ---------------------------------------------------------------------------
_init_flow_failure() {
  local RESPONSE="$1"
  echo "$RESPONSE" | grep -q 'not_initialized' || return 1
  if [[ -n "${LOCI_EVAL_TOOL_CALLS:-}" ]]; then
    # The harness hands over the transcript's tool calls (`Bash: <command>`, one
    # per line — `tool_calls_of` in run_evals.sh). Init counts only as a CALL,
    # which is what makes the inverted check safe: prose about running it does
    # not count, and neither does the CLI's own refusal sentence naming
    # `loci init --auto`, which a model pastes verbatim. So relaying the refusal
    # — the correct flow now — cannot be misread as having run init.
    printf '%s\n' "$LOCI_EVAL_TOOL_CALLS" | _names_an_init_call || return 1
  else
    # No tool calls to read (a grader driven on its own): the refusal's recovery
    # sentence is stripped before the text is read for an init — on the text
    # joined into one line first, since a pasted envelope wraps where it likes,
    # and with or without its backticks — so a pasted `not_initialized` alone is
    # never mistaken for an init call. The text has no `Bash:` prefixes, so the
    # word itself is what is looked for.
    printf '%s\n' "$RESPONSE" | tr '\n' ' ' \
      | sed -E 's#Run `?/loci:init`? \(or `?loci init --auto`?\)[^.]*\.##g' \
      | grep -E 'loci(\.exe)? +init([[:space:]]|$)' \
      | grep -vE 'loci(\.exe)? +init +(probe|set|add-file)([[:space:]]|$)' \
      | grep -q . || return 1
  fi
  echo "FAIL|recipe flow: the CLI answered not_initialized on an uninitialized project and the transcript RAN \`loci init\` — adopting a project is the user's decision, so the flow is to name /loci:init and stop"
  return 0
}

# stdin (the harness's tool calls, one per line) → does any BASH call run init?
#
# What counts: `loci` or `loci.exe` — bare, path-qualified (`~/.local/bin/loci`,
# `./.venv/Scripts/loci.exe`, `C:/x/loci.exe`) or quoted — at a COMMAND position
# (the start of the command, or after `;`, `&&`, `||`, `|`, `(`, `$(`), followed
# by `init` and then anything but a subcommand: `probe` is read-only and the init
# skill's own first step, `set` and `add-file` record one knob or one file,
# `--help` records nothing — and the CLI accepts global options before the
# subcommand (`loci init --project-root x probe`), so the subcommand is looked
# for anywhere after `init`, not only next to it. What does not count: a `Grep:`
# or `Read:` whose pattern says `loci init`; an `echo "loci init --auto"` (not at a
# command position once the quotes are dropped); prose.
#
# Known residuals, accepted: `uv run loci init` / `timeout 60 loci init` (the
# contract says call `loci` bare, and the plugin never spells it otherwise), and a
# path containing a space.
_names_an_init_call() {
  grep -E '^Bash: ' | tr -d "\"'" \
    | grep -E '(^Bash:[[:space:]]*|[;&|(][[:space:]]*)([^[:space:]]*/)?loci(\.exe)?[[:space:]]+init([[:space:]]|$|[;&|>)])' \
    | grep -vE 'loci(\.exe)?[[:space:]]+init([[:space:]]+[^[:space:];&|>)]+)*[[:space:]]+(probe|set|add-file|--help|-h)([[:space:]]|$|[;&|>)])' \
    | grep -q .
}

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
  # Two verdict vocabularies since 2026-09-03 (`_shared/verdicts.md`): a contract
  # bound JUDGES — `**PASS|CAUTION|FAIL**` — and with no bound the skill ARGUES —
  # `⚑ flagged —` / `○ cleared —`. Both are the real verdict line; the glyph is
  # matched as "not a letter or digit" so the check does not depend on the
  # terminal's UTF-8 handling.
  echo "$RESPONSE" | grep -qiE 'execution[[:space:]]+fit:[[:space:]]*(\**[[:space:]]*(pass|caution|fail)\b|[^[:alnum:]]*(flagged|cleared)\b)' && HAS_VERDICT=true

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
    local RF; if RF=$(_init_flow_failure "$RESPONSE"); then echo "$RF"; return; fi
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
  # Both vocabularies (see grade_bash): a judged `**PASS|CAUTION|FAIL**` or an
  # argued `⚑ flagged` / `○ cleared`.
  echo "$RESPONSE" | grep -qiE '(^[[:space:]]*verdict:[[:space:]]*(\**[[:space:]]*(pass|caution|fail)\b|[^[:alnum:]]*(flagged|cleared)\b)|loci[[:space:]]+post-edit[[:space:]]*·)' && HAS_VERDICT=true
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
    local RF; if RF=$(_init_flow_failure "$RESPONSE"); then echo "$RF"; return; fi
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
  echo "$RESPONSE" | grep -qiE 'execution[[:space:]]+fit:[[:space:]]*(\**[[:space:]]*(pass|caution|fail)\b|[^[:alnum:]]*(flagged|cleared)\b)' && HAS_PF_VERDICT=true
  # Post-edit presence: a markdown header OR the LOCI post-edit footer line.
  echo "$RESPONSE" | grep -qiE '(^[[:space:]]*#{2,}[[:space:]]*post-edit|loci[[:space:]]+post-edit)' && HAS_PE_HEADER=true
  # Post-edit verdict: the body 'Verdict: **PASS|CAUTION|FAIL**' line OR the footer scalar.
  echo "$RESPONSE" | grep -qiE '(^[[:space:]]*verdict:[[:space:]]*(\**[[:space:]]*(pass|caution|fail)\b|[^[:alnum:]]*(flagged|cleared)\b)|loci[[:space:]]+post-edit[[:space:]]*·)' && HAS_PE_VERDICT=true

  local MISSING=""
  $HAS_PF_HEADER  || MISSING="$MISSING preflight-header"
  $HAS_PF_VERDICT || MISSING="$MISSING preflight-verdict"
  $HAS_PE_HEADER  || MISSING="$MISSING post-edit-header"
  $HAS_PE_VERDICT || MISSING="$MISSING post-edit-verdict"

  if [[ -n "$MISSING" ]]; then
    echo "FAIL|pipeline incomplete — missing:${MISSING}"; return
  fi
  local RF; if RF=$(_init_flow_failure "$RESPONSE"); then echo "$RF"; return; fi
  echo "PASS|full pipeline fired — preflight report+verdict AND post-edit report+verdict present in joined transcript"
}

