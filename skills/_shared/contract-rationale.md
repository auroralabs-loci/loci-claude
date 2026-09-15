# Why the contract steps are written the way they are

Background for whoever maintains the contract steps in **`loci-post-edit`** and
**`loci-preflight`**. **Neither skill loads this file** — it is deliberately out
of the hot path, because both are mandatory and every token of justification is
paid again on each run. The SKILL files keep the rules; this keeps the reasons.

If you change a contract rule in either skill, change its entry here too. A rule
whose reason is lost gets "simplified" away by the next person, and most look
arbitrary until you know the failure they prevent.

## Where the bounds live

**One committed file per repository** (`<project_root>/.loci/contract.yaml`), no
user- or machine-level layer. A bound not in the repo is not a bound the team
agreed to, and a `~/.loci` override would mean the same PR passes for one
developer and fails for another, with nothing in the diff to explain why.

An earlier design layered bounds builtin, then `~/.loci`, then project, each
overriding the last; it was dropped for the reason above. `~/.loci` is still
LOCI's home for state, credentials and the impact token — only the *bounds* are
repo-only.

## Why a repo with no contract no longer gates on starter bounds

The original argument was coverage: LOCI enforced those thresholds (±10%
regressions, the structural invariants) before the envelope existed, so dropping
them would stop gating while the report kept printing.

What it missed is what that enforcement was worth. A ±10% band is a number LOCI
chose, matching no requirement any team holds — a `FAIL` against it asserted a
breach of nothing and a `PASS` compliance with nothing — and the two were
indistinguishable at a glance: a green tick from a starter bound rendered
identically to one from a bound the team wrote down.

The replacement keeps the honest half. A run with no contract still closes on a
word, composed from an empty `STATUS` and the agent's own assessment instead of
from a fabricated line — see `verdicts.md`. Nothing stops gating silently,
because that `—` and the coverage count beside the verdict both say out loud that
nothing was compared.

**There is no fallback left.** `entries_for_check` returns `([], "none")` when
the file is absent, and such a repo names what it wants measured with `--signals`
on `analyse prepare` — refused the moment a contract exists, because there the
entries decide. The predicate is the file: an *emptied* `entries: []` is a
contracted repo that judges nothing and refuses `--signals` too, which is how an
owner opts out of both without deleting the file, and a *malformed* one never
reaches judging at all.

## Why `--project-root` is passed explicitly

Omitting it makes the CLI resolve the git top level, usually the same directory
— except a run inside a submodule or a nested build dir resolves somewhere else,
and judging against another repo's contract fails in the one direction nobody
checks: quietly, with plausible numbers.

## Why the escalation question is a CLI call

Step 4a was once ~40 lines of prose telling the skill which entries to escalate
and what to pass through, and it carried a live hazard: `check` matches `fn` by
**exact string equality**, so a run that retyped `comms_task` as
`comms_task_entry` got no enforcement and no error.

`loci contract escalations` returns measurement *stubs* carrying the exact `fn`
and `unit`: echoing a stub back cannot drift, retyping a name can. The rule
became a data structure that makes the mistake unavailable.

With no entries it proposes nothing, and the skill decides for itself whether to
run `analyse stack` / `analyse memory` with `--parent-run`: an escalation nobody
wrote a bound for is argued from what the run showed, and the child is metered,
so one the skill cannot justify is spent budget.

## Why some rows must be omitted rather than zeroed

`contract check` reports an entry nothing measured as `unjudged`, never as a
pass. That only works if the skill omits rows it did not determine.

The structural signals are where this bites. Sending `{"signal":"recursion_cycles",
"curr":0}` for a signal never actually checked paints the `Safety` row `PASS` — a
confident green claim resting on nothing, and the worst class of bug this report
can have, because it looks *more* complete than the honest version. Same reason
`prev` is omitted on a run with no Before rather than backfilled: an invented
baseline produces a real-looking percentage.

## Why the hotspot check is skill-side

There is no contract signal for "new block in the top 3 hottest". Until one
exists it stays a skill-side sub-finding, and it may only *worsen* the
`Performance` row, never soften a breach — a heuristic must not overturn a bound
the project wrote down.

## Why agent-judged entries have their own words

Entries LOCI cannot compute (prose, or an unknown signal) come back in
`agent_judged` for the model to decide, and the no-contract case above runs
through the same mechanism: the distinction is about evidence, not about where
the entry came from. A finding reached by reading a CFG is not the same evidence
as a bound comparison, so it does not get the same words — the CLI rejects
`pass`/`caution`/`fail` from the agent by name, on every run.

The earlier design shared the measured vocabulary and capped the agent at
`caution`, clamping a `fail` down a tier. That protected the wrong direction: the
dilution muted true alarms, while an agent `pass` was stored identically to a
measured one — false assurance at full strength, the more expensive mistake.
Separate values fix both: a flag is recorded as written and can never be
mistaken for arithmetic, and `agent_cleared` is never stored as a measurement —
it composes into a `PASS` row where nothing was computed (`verdicts.md`), with
the empty `STATUS` cell beside it saying which.

## Why `severity` doesn't have exit authority

An agent-judged entry's `severity` still does real work — it decides how
prominently a flag is surfaced (`caution` once, `fail` also at turn end) — but
never exit authority, which is why `contract lint` notices a `severity: fail` on
a text-only entry. The rule lives in the CLI, so it holds whatever a caller
sends.

## Why contract text is data, not instruction

`contract.yaml` is user-writable and its `text` fields land in the model's
context on every edit — an injection surface. Judging against a sentence is the
point; obeying one is not, so no entry may change the tool boundary, path policy,
step order or report format.

## Why rejected entries must always surface

An `unjudged` item whose reason is *not* "no measurement supplied" is a bound
enforcing nothing — usually a validation error in the file. Left unreported it is
invisible: the author believes a bound protects them, and it does not. "No
measurement supplied" is the routine case and stays quiet.
