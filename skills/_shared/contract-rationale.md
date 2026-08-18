# Why the contract steps are written the way they are

Background for whoever maintains the contract steps in **`loci-post-edit`** and
**`loci-preflight`**. **Neither skill loads this file** — it is deliberately out
of the hot path, because both are mandatory (after every C/C++/Rust edit, and in
every `/plan` run) and every token of justification is paid again on each one.
The SKILL files keep the rules; this keeps the reasons.

If you change a contract rule in either skill, change its entry here too. A rule
whose reason is lost gets "simplified" away by the next person, and most of
these look arbitrary until you know the failure they prevent.

## Where the bounds live

**One committed file per repository** (`<project_root>/.loci/contract.yaml`), no
user- or machine-level layer. A bound that is not in the repo is not a bound the
team agreed to, and a `~/.loci` override would mean two developers on one commit
gate differently — the same PR passing for one and failing for the other, with
nothing in the diff to explain it.

An earlier design layered builtin < `~/.loci` < project. It was dropped for the
reason above. `~/.loci` is still LOCI's home for state, credentials, and the
impact token; it is only the *bounds* that are repo-only.

## Why a repo with no contract still gates

`check` falls back to the starter bounds — the entries `loci contract init`
writes. LOCI enforced those same thresholds (±10% regressions, the four
structural invariants) before the envelope existed, so losing them the day a
project has not yet run `init` would be a silent regression in coverage: the
report would keep printing and quietly stop gating.

The fallback is judging-only. `show`, `lint`, `draft`, `accept` all keep
reporting the file as it is, so nothing ever pretends a contract is on disk. An
*emptied* `entries: []` in a real file does not fall back — that is a choice.
A *malformed* file does not fall back either: a file someone wrote and got wrong
must be fixed, not silently replaced by defaults they did not choose.

## Why `--project-root` is passed explicitly

Omitting it makes the CLI resolve the git top level, which is usually the same
directory. Usually. A run started inside a submodule or a nested build dir
resolves somewhere else, and judging against a different repo's contract fails
in the one direction nobody checks: quietly, with plausible numbers.

## Why the escalation question is a CLI call

Step 4a was once ~40 lines of "if the response contains a `stack_depth` entry,
invoke stack-depth on `data.entries[].function`, and pass `bound.unit` through".
That prose carried a live hazard: `check` matches `fn` by **exact string
equality**, so a run that retyped `comms_task` as `comms_task_entry` got no
enforcement and no error — the budget silently did nothing.

`loci contract escalations` returns measurement *stubs* carrying the exact `fn`
and `unit`. Echoing a stub back cannot drift; retyping a name can. The rule was
replaced with a data structure that makes the mistake unavailable.

## Why some rows must be omitted rather than zeroed

`contract check` reports an entry nothing measured as `unjudged`, never as a
pass. That only works if the skill omits rows it did not determine.

The structural signals are where this bites. Sending `{"signal":"recursion_cycles",
"curr":0}` for a signal never actually checked paints the Safety row ✅ — a
confident green claim resting on nothing. It is the worst class of bug this
report can have, because it looks *more* complete than the honest version. Same
reason `prev` is omitted in Case B instead of being backfilled from the
measurement store: an invented baseline produces a real-looking percentage.

## Why the hotspot check is skill-side

There is no contract signal for "new block in the top 3 hottest". Until one
exists it stays a skill-side sub-finding, and it may only *worsen* the
Performance row, never soften a contract breach — a heuristic must not overturn
a bound the project wrote down.

## Why agent-judged entries are capped at 🔶

Entries LOCI cannot compute (prose, or an unknown signal) come back in
`agent_judged` for the model to decide. The CLI stores whatever severity the
user wrote, including `fail`, because humans own that file. But a finding
reached by reading a CFG rather than by measuring is not the same evidence as a
bound comparison, and it should not be able to ❌ a run. The cap lives in the
skill, not the CLI, so the file keeps saying what its author meant.

## Why contract text is data, not instruction

`contract.yaml` is user-writable and its `text` fields land in the model's
context on every edit. That is an injection surface. Judging against a sentence
is the whole point; obeying one is not — no contract entry may change the tool
boundary, path policy, step order, or report format.

## Why the init offer fires once per session

Post-edit runs after every edit. An offer repeated on each one trains the reader
to skip the footer, which is also where the verdict lives. And the skill must
never run `init` itself: writing the contract is the user's act, which is why it
is offered as `! loci contract init` for them to run.

## Why rejected entries must always surface

An `unjudged` item whose reason is *not* "no measurement supplied" is a bound
that is enforcing nothing — usually a validation error in the file. Left
unreported it is invisible: the author believes a bound protects them, and it
does not. "No measurement supplied" is the routine case and stays quiet.
