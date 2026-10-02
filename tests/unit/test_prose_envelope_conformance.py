"""Lint: every `data.*` field the skills name must be one the CLI actually returns.

P9's check. Every other lint in this suite reads prose against prose; this one
reads prose against the tool it drives, which is the axis that let D31 ship
(control-flow instructed seven fields `analyse cfg` does not return) and D35
(`data.symbol_names`, promised by the shared contract and returned by nothing).

The allowlist below is the CLI's side of the contract at the pinned version. It is
generated, not hand-written — regenerate it when `LOCI_CLI_VERSION` moves, and the
diff is the review. A field a skill names that is not here is either a typo, a
field that was renamed, or a promise nothing keeps.
"""

from __future__ import annotations

import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
SKILLS_DIR = PLUGIN_ROOT / "skills"
SETUP_STEPS = PLUGIN_ROOT / "lib" / "setup-steps.sh"

DATA_FIELD_RE = re.compile(r"`data\.([a-z_]{2,30})")

# Payload keys the pinned CLI emits, from a scan of `src/loci/cli/*.py` dict
# literals. Sorted; regenerate wholesale rather than appending by hand.
CLI_FIELDS = {
    # Generated: every string key a dict literal assigns, plus `data[...] =`
    # assignments, across `loci-cli/src/loci/**/*.py` at the pinned version, and
    # the two tuple-named payload groups `elf memmap` emits.
    "added", "added_functions", "agent_judged", "escalations", "ambiguous_functions", "artifact",
    "baseline_selection", "baseline_withheld", "candidates", "checks",
    "compiled", "compiled_out", "context_file", "contract", "control_flow", "count",
    "counts", "daily",
    "demangle", "detail", "diff_file", "draft", "duplicates", "eligible", "exists", "files",
    "findings", "frame_deltas", "functions", "functions_not_found", "gates",
    "headers", "healthy", "id", "judgements", "loops", "measured", "meta_file",
    "meta_prev", "missing", "modified", "not_found", "orphaned_entries", "output",
    "output_prev",
    "path_moved",
    "paths", "payload", "project_root", "pending_classification", "plan", "proposed",
    "provenance", "recipe", "recipe_untrusted", "removed_functions", "report",
    "requests", "resolved",
    "rows",
    "run", "state_notice", "status", "summary", "symbol_deltas", "symbols", "symbols_file",
    "timing_architecture", "timing_csv", "trends", "unattributed_changes",
    "unchanged", "units", "unjudged", "unscoped_units", "unselectable", "verdict",
    "warnings",
}

# `loci usage` builds its payload from the backend's eligibility response
# (`usage.py`: "plan, eligible, daily, weekly, message?"), so these three are the
# backend's names rather than keys written in the CLI source. They are real.
BACKEND_FIELDS = {"plan", "eligible", "daily"}

# Fields the CLI passes straight through from asmslicer, so the CLI source is not
# where they are named and this check cannot confirm them. Each one needs a real
# call to settle — do not move a field in here to silence a failure.
UNVERIFIED_UPSTREAM = {
    # `data.summary_delta.rom_total` / `.ram_static_total` — the shared contract
    # and post-edit's quiet-run branch both instruct reading this. No `summary_delta`
    # key exists anywhere in the CLI; `rom_total` and `ram_static_total` are read off
    # `summary`. If the key is real it comes from asmslicer's memmap comparison mode,
    # which `_order_memmap` passes through untouched. Verify against a real
    # `memmap --comparing-elf` run before trusting either spelling. See todo [081].
    "summary_delta",
}


# Fields this BRANCH's CLI returns and the PINNED one does not. Prose may name them
# because they ship together: `skills-rewrite` cannot merge until the CLI is released
# and `LOCI_CLI_VERSION` moves, and that is the moment these fold into `CLI_FIELDS`.
# Kept apart rather than folded in early, because `CLI_FIELDS` is a claim about the
# version a user actually has — a field parked here is a promise nothing keeps YET,
# and the guard below is what stops it being forgotten.
# Empty since the pin moved to 0.2.42. `removed_functions` and `orphaned_entries`
# (todo [087]) shipped in loci-tools 0.2.41, published from loci-cli `002e737` with
# `skills-rewrite-consequences`, and moved into `CLI_FIELDS`.
# `missing` (`loci contract suggested`, product-verdicts) was parked against 0.2.53
# and shipped in loci-tools 0.2.56 (loci-cli PR #147); moved into `CLI_FIELDS` at the
# pin to 0.2.57. Empty again.
# `state_notice` (house rules' "data.state_notice", loci-cli PR #170 `fix/state-integrity`:
# a deleted or corrupt LOCI state file, on every project verb's envelope) was parked
# against 0.2.64 and shipped in loci-tools 0.2.67 (loci-cli `e1ed522`); moved into
# `CLI_FIELDS` at the pin to 0.2.67. Empty again.
UNRELEASED_FIELDS: set[str] = set()

#: The pin `UNRELEASED_FIELDS` was written against. When it moves, the release either
#: carries these fields — move them into `CLI_FIELDS` — or it does not, and the prose
#: naming them is a promise nothing keeps.
# Moved as main re-pinned underneath this branch: 0.2.29 -> 0.2.36 -> 0.2.37 ->
# 0.2.39 -> 0.2.41. Checked each time — no release carried either field until
# 0.2.41 did, and at the move to 0.2.42 both were folded into `CLI_FIELDS`.
PIN_WHEN_PARKED = "0.2.67"


def _pinned_version() -> str:
    text = SETUP_STEPS.read_text(encoding="utf-8")
    m = re.search(r'^LOCI_CLI_VERSION="([^"]+)"', text, flags=re.MULTILINE)
    assert m, f"no LOCI_CLI_VERSION in {SETUP_STEPS}"
    return m.group(1)


def test_the_unreleased_set_is_reviewed_when_the_pin_moves():
    """The self-clearing half. Without it a field parked as "ships with the next CLI"
    stays parked after that CLI ships, and the allowlist quietly stops describing the
    version users run — which is the whole thing this file exists to prevent."""
    if not UNRELEASED_FIELDS:
        return
    assert _pinned_version() == PIN_WHEN_PARKED, (
        f"LOCI_CLI_VERSION moved to {_pinned_version()} while "
        f"{sorted(UNRELEASED_FIELDS)} are still parked as unreleased. Check the "
        f"release: fields it carries move into CLI_FIELDS, fields it does not are "
        f"prose promising something nothing returns.")


def _named_fields() -> dict[str, set[str]]:
    """Every `data.<field>` a skill document names, mapped to the docs naming it."""
    out: dict[str, set[str]] = {}
    for doc in sorted(SKILLS_DIR.rglob("*.md")):
        for field in DATA_FIELD_RE.findall(doc.read_text(encoding="utf-8")):
            out.setdefault(field, set()).add(str(doc.relative_to(SKILLS_DIR)))
    return out


def test_every_named_data_field_is_one_the_cli_returns():
    unknown = {
        f: sorted(where)
        for f, where in _named_fields().items()
        if f not in CLI_FIELDS | BACKEND_FIELDS | UNVERIFIED_UPSTREAM | UNRELEASED_FIELDS
    }
    assert not unknown, (
        "these `data.*` fields are named in skill prose and are not payload keys "
        "of the pinned CLI — a model told to read one finds nothing:\n"
        + "\n".join(f"  data.{f} — named in {', '.join(w)}" for f, w in sorted(unknown.items()))
    )


def test_the_unverified_set_stays_small_and_explained():
    """A field parked as upstream must carry a comment saying how to settle it.

    The set exists so an unconfirmable field is visible rather than silently
    allowlisted. If it grows without comments, the check has stopped meaning
    anything.
    """
    src = Path(__file__).read_text(encoding="utf-8")
    block = src[src.index("UNVERIFIED_UPSTREAM = {"):src.index("def _named_fields")]
    for field in UNVERIFIED_UPSTREAM:
        assert f'"{field}"' in block, f"{field} is not listed in the block"
        assert block.count("#") >= len(UNVERIFIED_UPSTREAM), (
            "every parked field needs a comment naming how to verify it"
        )
    assert len(UNVERIFIED_UPSTREAM) <= 3, (
        "more than three unconfirmable fields means this check is being used to "
        "silence failures rather than to record them"
    )


# ── what a test run on 22 September cost, written down ───────────────────────

VERDICTS = SKILLS_DIR / "_shared" / "verdicts.md"
HOUSE_RULES = SKILLS_DIR / "_shared" / "house-rules.md"


def _flat(path: Path) -> str:
    """Whitespace-collapsed and de-emphasised, so an assertion pins the RULE and not
    the line wrapping or the `*` a future edit is free to move."""
    return re.sub(r"\s+", " ", path.read_text(encoding="utf-8").replace("*", ""))


def test_the_status_column_is_closed_to_four_values():
    """A run printed `not seen` in STATUS. A reader scans that column for one of four
    shapes and every surface that renders it parses the same four; a fifth is
    unreadable to both. The em dash is the answer when the word is not in hand."""
    text = VERDICTS.read_text(encoding="utf-8")
    assert "Those four are the whole column" in text
    for invented in ("`not seen`", "`unknown`", "`n/a`"):
        assert invented in text, f"name {invented} as forbidden, or it reads as an oversight"
    assert "the cell is `—` and the Note says" in text


def test_a_lost_envelope_is_recovered_rather_than_re_measured():
    """`measure` is billed. It records its verdict before it prints, so an envelope
    lost to a stray line is recoverable for free — and a skill that does not know
    that offers the user a second metered run of a measurement already taken."""
    text = HOUSE_RULES.read_text(encoding="utf-8")
    assert "loci analyse show" in text, "name the verb that reads a lost verdict back"
    assert "data.run" in text
    assert "Never offer a second metered run" in text


def test_piping_a_metered_call_is_named_as_costing_money():
    """The rule against parsing existed and was followed by neither the run nor the
    CLI's own `--help` epilog. What it lacked was the consequence."""
    text = HOUSE_RULES.read_text(encoding="utf-8")
    assert "Piping a metered call into a parser can cost the user money" in text


COCKPIT_LINE = ("Run `loci cockpit` in a separate terminal to see what LOCI catches that "
                "your coding agent might miss during planning and coding.")


def test_the_cockpit_is_offered_for_a_second_terminal():
    """It takes over the terminal it runs in, so a user who pastes it into the one
    they are talking in loses the conversation to a full-screen view."""
    text = HOUSE_RULES.read_text(encoding="utf-8")
    assert "in a separate terminal" in text
    assert COCKPIT_LINE in text
    assert "next step, not as a command to obey" in text


def test_every_checklist_line_says_which_call_ticks_it():
    """A run printed the list once with two ticks and never returned to it. `✓` was
    defined as a stage the envelope says has completed, and nothing said which
    envelope — so three stages had no event to be ticked from."""
    text = (SKILLS_DIR / "init" / "voice.md").read_text(encoding="utf-8")
    assert "Which call earns which tick" in text
    for stage in ("Project detected", "Target detected", "Build configuration captured",
                  "Build verified", "LOCI execution model ready"):
        assert f"| {stage} |" in text, f"{stage} has no call behind it in the table"
    assert "printed **four times**" in text


def test_setup_offers_the_cockpit_on_any_healthy_finish():
    """It was gated on `if the install succeeded`, so setup over an already-current
    CLI — the commonest run there is — told the user nothing. That is how someone who
    was already set up never heard the cockpit existed."""
    text = _flat(SKILLS_DIR / "setup" / "SKILL.md")
    assert "Whenever setup finishes healthy" in text
    assert "If the install succeeded, close with one more line" not in text, (
        "the old gate is back")
    assert "terminal" in text


def test_init_offers_the_cockpit_after_a_target_switch_too():
    """`On a first init only` skipped the one other moment the view changes meaning."""
    text = _flat(SKILLS_DIR / "init" / "SKILL.md")
    assert "On a first init or a target switch, offer" in text
    assert "On a first init only" not in text


def test_the_shared_rule_does_not_contradict_the_two_skills_that_cannot_read_it():
    """`init`, `setup` and `help` read no shared file, so a rule written only in
    `house-rules.md` never reaches them — and one saying "only when a run has
    produced something to look at" would have contradicted both if it ever did."""
    for skill in ("init", "setup", "help"):
        text = (SKILLS_DIR / skill / "SKILL.md").read_text(encoding="utf-8")
        assert "_shared/" not in text, (
            f"{skill} now reads a shared file — re-check the cockpit rule reaches it")
    assert "A measuring skill never offers it" in _flat(HOUSE_RULES)


def test_every_cockpit_offer_uses_the_one_line():
    """Each skill paraphrased the offer in its own words, and the paraphrases drifted
    into "this machine's LOCI measurements" — which is not what the view is for."""
    for skill in ("init", "setup", "contract"):
        text = (SKILLS_DIR / skill / "SKILL.md").read_text(encoding="utf-8")
        assert COCKPIT_LINE in text, f"{skill} offers the cockpit in other words"


def test_no_measuring_skill_offers_the_cockpit():
    """It belongs to the four that set LOCI up or describe it. A report that ends by
    pointing somewhere else is a report that did not finish its own job."""
    offenders = [s for s in JUDGING_SKILLS
                 if "loci cockpit" in (SKILLS_DIR / s / "SKILL.md").read_text(
                     encoding="utf-8")]
    assert not offenders, f"{offenders} still offer the cockpit"


def test_the_recursion_rule_says_the_bound_may_be_at_the_call_site():
    """A live run classified `quicksort` unbounded and FAILED a correct binary. The
    rule said "nothing in the code bounds it"; the model read "the code" as the
    recursing function, and quicksort's bound was its caller's `BUFFER_SIZE`."""
    text = _flat(SKILLS_DIR / "stack-depth" / "SKILL.md")
    assert "Read the call sites" in text, (
        "without this the bound is looked for in the function body alone")
    assert "a fixed size its caller passes" in text
    assert "nothing in the code bounds it" not in text, "the ambiguous phrasing is back"


def test_the_recursion_rule_separates_depth_from_termination():
    """The same run's model said afterwards: "Not in the sense of might never stop."
    It is not that sense — the signal asks whether the deepest DEPTH is knowable, and
    a recursion that always returns can still be unbounded."""
    text = _flat(SKILLS_DIR / "stack-depth" / "SKILL.md")
    assert "not whether the recursion terminates" in text
    assert 'never "it terminates"' in text, "a claimed bound has to name what bounds it"
    assert "may not terminate" in text, (
        "the row has to forbid the wording the model reached for")


def test_the_note_budget_tells_the_skill_to_front_load_the_sentence():
    """The cut lands on a sentence boundary, so where the clause's first sentence
    carries the point the recorded note is complete — and where it does not, the
    reader loses the part that mattered. The skill can only act on that if it is
    told, and `trimmed to 200 characters` did not tell it."""
    text = _flat(SKILLS_DIR / "_shared" / "verdicts.md")
    assert "cut at the last sentence that fits" in text
    assert "load-bearing sentence first" in text


# ── the agent's judgement has to reach the record ────────────────────────────

#: Every skill that prints a verdict and records it. Spelled out rather than globbed
#: so that a new judging skill fails here until someone adds it.
JUDGING_SKILLS = ("loci-post-edit", "loci-preflight", "exec-trace",
                  "control-flow", "stack-depth", "memory-report")


def test_no_skill_narrows_agent_judged_to_the_entries_the_cli_could_not_compute():
    """The defect this closes, from a live run: post-edit printed `PASS` beside
    **Needs attention**, filed neither half, and the cockpit showed `PASS` alone.

    Its Step 7 scoped `--agent-judged` to `data.agent_judged` — the entries LOCI
    could NOT compute — while the shared rule says every row you reached, computed
    or not. Four of six skills narrowed it the same way, so a flag on a computed row
    (the ordinary case: a lower bound on a bound that passed) had nowhere to go.
    """
    narrowing = (
        "words on `data.agent_judged`",
        "an entry the CLI could not compute",
        "entries LOCI could not compute",
    )
    offenders = []
    for skill in JUDGING_SKILLS:
        text = _flat(SKILLS_DIR / skill / "SKILL.md")
        i = text.find("--agent-judged")
        while i != -1:
            near = text[i:i + 160]
            if any(n.replace("`", "") in near for n in narrowing):
                offenders.append(f"{skill}: {near[:110]}")
                break
            i = text.find("--agent-judged", i + 1)
    assert not offenders, (
        "these tie `--agent-judged` to the entries the CLI could not compute, so a "
        "flag on a COMPUTED row has no route and the cockpit keeps the tool's word "
        "alone:\n  " + "\n  ".join(offenders))


def test_every_judging_skill_names_the_run_level_fallback():
    """`--agent-verdict` is the only route when a run offered no row to key a concern
    to. memory-report named neither route, so a concern it printed could not be
    recorded at all."""
    missing = [s for s in JUDGING_SKILLS
               if "--agent-verdict" not in _flat(SKILLS_DIR / s / "SKILL.md")]
    assert not missing, (
        f"{missing} never name `--agent-verdict`, so a run whose entries all "
        f"computed has no way to record what the skill closed on")


def test_the_shared_rule_says_a_flag_on_a_passing_row_is_ordinary():
    """Both routes existed and both excluded the common case by their own wording.
    The shared rule is what the six cite, so it carries the correction."""
    text = _flat(SKILLS_DIR / "_shared" / "verdicts.md")
    assert "Send it for every row you reached, computed or not" in text
    assert "whose bound PASSED is the ordinary case" in text


def test_no_skill_names_only_the_flagged_half_of_the_run_verdict():
    """`flagged` is the only value that changes a CONTRACTED run, so five skills told
    the agent to send that and nothing else. On a run with no contract the word IS the
    verdict: `cleared` records `pass` and sending nothing records `unjudged` — so a
    clean uncontracted run that drew no rows recorded as never judged."""
    offenders = []
    for skill in JUDGING_SKILLS:
        text = _flat(SKILLS_DIR / skill / "SKILL.md")
        if "--agent-verdict" not in text:
            continue
        if "cleared" not in text.split("--agent-verdict", 1)[1][:600] and \
           "the shared rule" not in text.split("--agent-verdict", 1)[1][:400]:
            offenders.append(skill)
    assert not offenders, (
        f"{offenders} name `--agent-verdict` only in its flagged case and never route "
        f"to the rule that carries the clean one, so an uncontracted run with no rows "
        f"records `unjudged` however clean the skill found it")


def test_the_shared_rule_carries_the_uncontracted_asymmetry():
    """The three values are NOT equivalent, and which ones matter depends on whether a
    gate computed. That is the fact every per-skill paragraph was missing."""
    text = _flat(SKILLS_DIR / "_shared" / "verdicts.md")
    assert "cleared` is not decoration on a run with no contract" in text
    assert "sending nothing records" in text and "unjudged" in text
    assert "drew no rows at all" in text


def test_the_run_verdict_rule_is_stated_once_and_cited():
    """It was written three different ways across six skills, which is how five of them
    drifted to the flagged half without anyone noticing the sixth had neither."""
    citing = [s for s in JUDGING_SKILLS
              if "--agent-verdict" in _flat(SKILLS_DIR / s / "SKILL.md")
              and "verdicts.md#recording-the-verdict" in (SKILLS_DIR / s / "SKILL.md")
              .read_text(encoding="utf-8")]
    assert len(citing) == len(JUDGING_SKILLS), (
        f"only {citing} route to the shared rule; the rest restate it, which is what "
        f"let three wordings of one rule diverge")


def test_no_skill_repeats_a_heading_back_to_back():
    """Four consecutive duplicate headings shipped on this branch — `## Step 5`
    twice in post-edit, `## Step 6` twice, `## LOCI footer` and `## Output format`
    twice in preflight — none of them on `main` or at the branch point. A heading
    pass inserted without removing, and nothing was watching. A fifth was printed
    twice on ONE line (`## LOCI footer## LOCI footer`), which a check of adjacent
    lines cannot see, so a heading holding a second heading marker is one too."""
    offenders = []
    for path in sorted(SKILLS_DIR.glob("*/*.md")):
        lines = path.read_text(encoding="utf-8").split("\n")
        for i, line in enumerate(lines):
            doubled = i > 0 and line == lines[i - 1]
            if line.startswith("#") and (doubled or re.search(r"[^#\s]#{2,6} ", line)):
                offenders.append(f"{path.relative_to(SKILLS_DIR)}:{i + 1} {line[:60]}")
    assert not offenders, "a heading is printed twice in a row:\n  " + "\n  ".join(offenders)
