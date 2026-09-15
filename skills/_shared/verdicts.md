# LOCI verdict vocabulary (shared)

**Two vocabularies on the wire, two columns for the reader.** Every LOCI skill
that judges a run — preflight, post-edit, exec-trace, stack-depth,
memory-report, control-flow — closes on a verdict composed from two things: the
`STATUS` arithmetic computed, and the assessment **you** wrote about the same
row. You never write a measured word, and the reader never sees a wire spelling.

## `STATUS`: what the run concluded

`PASS` / `CAUTION` / `FAIL` are the CLI's words wherever a bound computed them,
and they require one of the three sources the runtime contract's **A measurement
inherits a verdict from a bound, never from a band** lists: an enabled contract
entry with a computable `bound`, a bound the user stated in this request (quoted
back, never persisted), or a directly observed structural hazard whose invariant
is zero by definition. Nothing else qualifies, and none of the three is a number
LOCI chose — inferring a requirement from the size of a measurement is what this
column rules out.

**On a `none` envelope the column is yours** — see [No contract: the agent fills
`STATUS`](#no-contract) below. Nothing above changes for a repo that has a
contract, and the prohibition on inventing a band is not lifted by either
route.

| Icon | `STATUS` | Means |
|:--:|---|---|
| ✅ | `PASS` | Every compared bound holds; no structural hazard was found. |
| 🔶 | `CAUTION` | Worth a look, not blocking: tight against a bound, a regression inside what an entry allows, a benign hazard, or a figure that is only a lower bound. |
| ❌ | `FAIL` | A bound is breached, or the result is structurally unsafe. |
| — | *(nothing computed)* | No bound behind the row, or a comparison that could not be made. An em dash, never a word. |

**There is no fourth source, and you supply no band.** A percentage, a delta or
an absolute figure reaches none of these words by its size, however large it is:
on a contracted run with no entry behind it its `STATUS` is `—`, and on an
uncontracted one the word comes from your assessment of the row, which is a
reading you can name a block for — never a threshold you chose. Where a bound
does yield a usage ratio, `contract.judge` bands it — one band, every signal,
every skill — so **no skill states a threshold in prose**. Prose copies are how
the old bands drifted from each other.

<a id="no-contract"></a>

### No contract: the agent fills `STATUS`

`data.contract` is `none` — no `.loci/contract.yaml`, so no entry judged
anything and the verb assembled no rows. The column is still drawn, and your
assessment is what fills it:

| `AGENT ASSESSMENT` | `STATUS` on a `none` envelope |
|---|:--:|
| **Needs attention** | `CAUTION` |
| **Looks good** | `PASS` |
| **As reported** | `—` |

**As reported keeps the em dash**, because that is the one assessment that
concluded nothing, and `—` is what says so. It should be **rare** here: this is
the route where your reading is the only word the reader gets, so a row you looked
at and raised nothing about is **Looks good**, not a shrug. A `—` on an
uncontracted run says the run gave you nothing to judge the row on — and since an
`INCOMPLETE` row is never drawn, reaching for it means the row leaves the table
and becomes a coverage count instead. Composition is then the identity —
the table below already maps `—` + Needs attention to `CAUTION`, `—` + Looks good
to `PASS`, and `—` + As reported to `INCOMPLETE` — so a run's verdict is the same
word either way. What changes is only where the reader finds it.

**Say it under the table, on every such run**, immediately after the rows:

```
No contract in this repo — every STATUS above is composed from the agent
assessment beside it, not from a bound. `/loci:contract` records the limits this
project actually has — a stack ceiling, a timing or energy budget, a ROM or RAM
region — and the next run judges these same figures against them.
```

**No marker in the cell.** A `CAUTION` reached this way reads as a `CAUTION`; the
caption is what qualifies the table, and the run's `contract` field is what
qualifies it durably. Never write `CAUTION*`, a footnote glyph or a `(agent)`
suffix into the column — the cell is one word wide in every renderer that reads
it.

**The wire does not move.** `loci stats record` still refuses `pass`, `caution`
and `fail` from you by name: your row assessments go to `--agent-judged` in their
wire spelling and the run's to `--agent-verdict`, exactly as on a contracted run.
Branch history keeps telling a checked run from an argued one by `run["contract"]`,
which is the field that carries that distinction — not by an emptied column.

## `AGENT ASSESSMENT`: what you said about the row

Three words, yours on every run — a contracted one where the entry computed, a
contracted one where it could not, and a repo with no contract at all. `loci
stats record` refuses `pass`, `caution` and `fail` from you by name in all three:
recording a reading as a measured word would leave branch history unable to tell
a checked run from an argued one.

| On the wire | The reader sees | Means |
|---|---|---|
| `agent_flagged` | **Needs attention** | You found something worth raising. **Requires** reasoning naming the specific block, callee, region or instruction. A flag with no reason cannot be acted on or dismissed, and `--agent-judged` refuses it. |
| `agent_cleared` | **Looks good** | You looked at this row and raised nothing. |
| `agent_no_opinion` | **As reported** | You read the row and the run gives you nothing to judge it on. No reasoning needed. Not a blank cell and not `cleared`: it says the row stands as measured. |

The display words are `contract.AGENT_DISPLAY` and the header is
`contract.AGENT_COLUMN` — the cockpit's panel renders the column from the same
two constants, so a skill that spells them its own way puts two answers for one
row on the screen. **`⚑`, `○`, `·`, `flagged`, `cleared` and `no_opinion` appear
in nothing a user reads.**

## The row verdict: the two columns composed

| `STATUS` | + Needs attention | + Looks good | + As reported |
|---|---|---|---|
| `PASS` | `CAUTION` | `PASS` | `PASS` |
| `CAUTION` | `CAUTION` | `CAUTION` | `CAUTION` |
| `FAIL` | `FAIL` | `FAIL` | `FAIL` |
| `—` | `CAUTION` | `PASS` | `INCOMPLETE` |

**You escalate and never de-escalate.** Looking good and having no opinion move
no computed status; a flag moves only `PASS → CAUTION` and can never talk a
`FAIL` down. `contract.compose_row` is the table, so you compose no row by hand —
but the reader sees both halves, and which one moved the run.

**A row with nothing computed and nothing raised is a `PASS`.** That reverses
what this file used to say about a cleared row ("not a pass, never render it as
✅"). What carries the difference is not a weaker word: it is `run["contract"]`,
which says whether a bound was behind the `STATUS` cell, and the coverage count
below.

## The run verdict

**The worst row verdict, ranked `FAIL > CAUTION > PASS`, with `INCOMPLETE` rows
excluded from the worst-of.** One edit measures the entries its own translation
unit reaches, so on a contract of any size most rows are out of scope every run,
and rolling them in lets a single untouched entry outrank every judged one. A run
reads `INCOMPLETE` only when **no** row reached a word.

State it as the run's answer — the report's prominent line — not as one row among
the rows. Rows that reached no word become a count beside it:

```
Verdict: **CAUTION** — worst path +19.1% in the new bounds check at block 4 (3 of 14 judged)
```

The count is required wherever any row went unjudged; without it a reader cannot
tell a contract three rules wide from one they saw three rules of.

What you may reason from, and what a `PASS` with nothing behind it has to say,
are the runtime contract's **Your verdicts are `flagged` / `cleared`** section —
stated once, there.

## The line

```
<prefix>: **<PASS|CAUTION|FAIL|INCOMPLETE>** <figure or — cause> [(<N> of <M> judged)]
```

- `<prefix>` is `Verdict` for every skill except `loci-preflight`, which uses
  `Execution fit` because it judges a plan rather than a measurement.
- The word is the **run** verdict, composed. It takes a figure (`**PASS** 15.2%`)
  or a cause, figure first where a skill has both, and a percentage only where a
  contract bound or a linker map region supplied the denominator. A run with no
  bound behind any row — a `none` envelope, or a contracted one whose entries all
  went unmeasured — always takes the cause: it is the whole of what the word rests
  on, whether the column reads `—` or carries your composed word.
- Bolding is each skill's own. The string passed to `loci stats record
  --verdict` is always unbolded, and your assessments go to `--agent-judged` /
  `--agent-verdict`, never to `--verdict`.

```
Execution fit: **FAIL** — unbounded recursion in parser_descend; do not write this as planned
Verdict: **FAIL** — timing +147% past the 200 ns budget
Verdict: **CAUTION** — worst path grew 340 ns, in the snprintf at block 7 of adc_format
Verdict: **PASS** — 312 B measured; no contract covers stack_depth
```

<a id="recording-the-verdict"></a>

## Recording it: one call, on every run that printed a verdict

The cockpit shows this run as the CLI's token plus the sentence **you** wrote. Both
travel in one call, the only route by which either outlives the turn:

```
loci stats record --context-file "<project-context>" --run "<run id>" --agent-note "<the cause clause of your Verdict line>" --agent-judged '[{"entry_key":"<from the entry>","verdict":"flagged","note":"block 0x1a4 calls snprintf on the hot path","fn":"<function>"}]'
```

- **`--run`** is the id your verb returned — the manifest id from `analyse prepare` /
  `analyse measure`, or `data.run` from `analyse stack` / `analyse memory`. An
  unknown id is refused and the error names the ids it does know.
- **Call it on every branch that printed a `Verdict:` line**, a run that measured
  nothing included: that branch's whole content *is* your sentence, and a manifest
  prepared and never measured has its run line written by this call.
- **`--agent-note` is the cause clause only** — no `PASS`/`CAUTION`/`FAIL` token, no
  icon, no restated figure. The token stays the CLI's and the cockpit draws it in its
  own column, so a token in your sentence is a second verdict beside the real one.
  Trimmed to 200 characters and rendered verbatim. Send it on every run, clean ones
  included.
- **Copy the clause you printed; do not compose a second one.** The two surfaces are
  required to agree, so the note is the same words the user just read — including a
  concern the measurement did not raise. Where a bound passed and you read the loop
  as costing far more at a realistic trip count, the `STATUS` stays `PASS`, your
  assessment is `flagged`, and the note says why. The envelope echoes `agent_note`
  **as stored**; if that differs from your sentence, the cockpit is what the reader
  sees.
- **`--agent-judged`** carries your assessment per row: one
  `{entry_key, verdict, note, fn}` each, `verdict` being the wire spelling
  (`flagged` / `cleared` / `no_opinion`) of what the `AGENT ASSESSMENT` column
  rendered. Send it for the rows you reached, computed or not — the composition
  needs both halves. Leave out the rows you did not reach; they stay `pending`,
  which is honest and is what the coverage count counts. `--run` with none of these
  flags is refused, because then there is nothing to write.
- **`--agent-verdict <flagged|cleared|no_opinion>`** carries one assessment for the
  RUN, for a reading no offered row carries. **Send it whenever what you printed is
  not what the arithmetic alone reached** — a `≥` figure, a trip count you could not
  resolve, anything your own reading made you close on. `flagged` needs
  `--agent-note` in the same call: that sentence IS its reasoning. It composes as a
  row does, raising a `pass` run to `CAUTION` and softening nothing. Prefer
  `--agent-judged` wherever a row was offered — it says which rule your reading is
  about.
- **When a parent skill escalated into you**, pass `--parent-run "<the parent's run
  id>"` to `analyse stack` / `analyse memory`. Your run keeps its own row, verdict
  and note; the pointer is what draws the two together in the cockpit feed instead of
  leaving your run looking unrelated to the edit that caused it.

## The conclusion table

Five columns, in this order — the cockpit's two verdict columns between an
identity and a cause:

| ENTRY | FUNCTION | STATUS | AGENT ASSESSMENT | NOTE |
|---|---|---|---|---|
| `Performance (Hot-Path)` | `find_binary` | `CAUTION` | Needs attention | worst path +19.1% vs last run, in the new bounds check at block 4 |
| `Stack` | `quicksort` | `FAIL` | As reported | 8352 B against the 4096 B bound |
| `Safety` | — | `PASS` | Looks good | no recursion cycles; indirect dispatch in `handler` is table-bound |

- **`ENTRY` is the qualified signal name** — `Performance (Hot-Path)`,
  `Performance (Worst-Path)`, `Stack`, `Energy`, `ROM Memory`, `RAM Memory`,
  `Safety` — and **`FUNCTION` is what the row is bounded on**, an em dash where
  the row bounds the whole artifact. A label four signals share names the one it
  is about (`Safety (Recursion)`, `Safety (Indirect Calls)`), or four Safety rows
  read alike. Two columns rather than one mashed cell: the
  cockpit mashes them because a terminal panel is width-bound, a report is not,
  and a reader scanning for one function should not have to read every label to
  find it.
- **`STATUS` is the CLI's word, your composed word on a `none` envelope, or an
  em dash.** Never a blank, and never a word you reached by picking a threshold.
- **`AGENT ASSESSMENT` is the display word.** Needs attention, Looks good, As
  reported — never the wire spelling and never a glyph.
- **Every `CAUTION`, `FAIL` and every Needs attention cites its reason in NOTE.**
  A word with no cause cannot be acted on. An entry's declared `severity` is **not
  rendered** — not in a column, not in NOTE. It decides how loudly a breach is
  surfaced, and `STATUS` already carries that.
- **An `INCOMPLETE` row is never drawn.** It becomes the coverage count beside
  the run verdict. Drawing it puts back, in the one place meant to carry verdicts,
  the rows already excluded from the worst-of — and on a contract of any size most
  entries are untouched by any one edit, so the table would be mostly inventory. A
  run where **no** row reached a word draws no rows at all: the verdict and the
  count say it.

**The table is drawn on every run, contract or not.** On a `project` envelope
its rows are `data.rows`, rendered as they come. On a `none` envelope the verb
assembles none, so you compose them: one row per (signal, function) you reasoned
about, from `data.paths`, `data.unjudged` and the stubs in `data.agent_judged`,
with your display word and the `STATUS` it maps to under [No contract: the agent
fills `STATUS`](#no-contract), and that section's caption under the rows. Never fall back to a
stacked `ENTRY: … / FUNCTION: …` field list — a reader compares rows by scanning a
column, and a list of fields cannot be scanned.

There is no separate per-row vocabulary and no `Basis` column. `WARNING` is not
a LOCI status. A `STATUS` may carry a parenthetical qualifier **in the report
body**, where there is room to explain it — `PASS (lower bound)`,
`CAUTION (acceptable)` — but it never replaces the word, and the footer and the
recorded string carry the status plus its cause in prose instead.

## Wire encoding

Statuses are lowercase on the wire, the same word uppercased for display:
`✅→pass · 🔶→caution · ❌→fail`. Covers `stats record --gates`, an entry's
`severity:`, and a run's `verdict`. Reading one is `.upper()`; `unjudged` is the
exception — it is `INCOMPLETE`, which is a coverage fact rather than a verdict,
and no row carrying it is drawn.

`warn` is retired (2026-09-03) — never write it; an existing `severity: warn`
reads as `caution` and lint says to fix it. Unrelated `warn`s stay, none of them
a verdict on code: *lint* findings, `loci doctor` checks, "just warn me".

Assessments do not go through `--gates`. They go to `--agent-judged` /
`--agent-verdict` in their wire spelling, and they are rendered through
`AGENT_DISPLAY` wherever a human reads them.

## Escalation

**The child keeps its own verdict; the parent does not inherit it.** The child
measured something, which deserves its own record and its own row, and it is
metered where the measurement happened. The parent keeps the word its own figures
earned, names the escalation in its NOTE, and **reuses** the child's figures
rather than re-measuring them — or one investigation is metered twice.

The conclusion table carries **both rows**, because the turn's verdict is the
worst over both and a headline may not come from a row nobody drew. The child's
`ENTRY` cell is marked with the `└ ` the cockpit feed already uses — `└ Stack` —
so the prose table and the panel read the same. A clean child gets a row too,
with its figures: the old `+stack-depth` suffix dropped them, so a child at 49%
of its budget and one at 3% printed identically.

**Name the child in the parent's cause sentence only where it moved the word.**
`Verdict: **CAUTION** — stack-depth: 202% of the 2 KB budget on comms_task` says
which escalation the parent's verdict rests on; where the child changed nothing,
the parent stays silent about it. Naming every child on every run is noise, and
it makes the one that decided the answer indistinguishable from the ones that
did not.

---

**Why the wire keeps two vocabularies.** One field for both claims either inflates
an argument into a fact or deflates a fact into an argument. `FAIL` says a
requirement the user wrote was breached; `agent_flagged` says a case the reader
can accept or reject on its merits. Bounds are what buy enforcement — which is
why the reader gets two columns rather than one word: a single word cannot say
whether a bound or a reading reached it.

Vocabulary decided 2026-08-14; the wire alignment and the two-vocabulary split
2026-09-03; the two display columns, the matrix and the run's worst-of
2026-09-11, superseding the earlier forms.
