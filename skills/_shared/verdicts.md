# LOCI verdict vocabulary (shared)

**Two vocabularies on the wire, two columns for the reader.** Every LOCI skill
that judges a run — preflight, post-edit, exec-trace, stack-depth,
memory-report, control-flow — closes on a verdict composed from two things: the
`STATUS` arithmetic computed, and the assessment **you** wrote about the same
row. You never write a measured word, and the reader never sees a wire spelling.

## `STATUS`: what the run concluded

`PASS` / `CAUTION` / `FAIL` are the CLI's words wherever a bound computed them,
and they require one of the two sources the house rules' **A measurement
inherits a verdict from a bound, never from a band** lists: an enabled contract
entry with a computable `bound`, or a bound the user stated in this request
(quoted back, never persisted). Nothing else qualifies, and neither is a number
LOCI chose — inferring a requirement from the size of a measurement is what this
column rules out. **A structural hazard is not a third source.** Recursion,
indirect calls and unknown callees are measured on every run whether or not
anyone asked; the count is a fact and belongs in the Note, and where no entry
covers it the word comes from your assessment like any other.

**On a `none` envelope the column is yours** — see [No contract: the agent fills
`STATUS`](#no-contract) below. Nothing above changes for a repo that has a
contract, and the prohibition on inventing a band is not lifted by either
route.

| Icon | `STATUS` | Means |
|:--:|---|---|
| ✅ | `PASS` | Every compared bound holds. |
| 🔶 | `CAUTION` | Worth a look, not blocking: tight against a bound, a regression inside what an entry allows, or a benign hazard. |
| ❌ | `FAIL` | A bound is breached. |
| — | *(nothing computed)* | No bound behind the row, or a comparison that could not be made. An em dash, never a word. |

**Those four are the whole column.** `STATUS` takes `PASS`, `CAUTION`, `FAIL` or
`—` and nothing else — not `not seen`, not `unknown`, not `n/a`, not a phrase
describing why you could not fill it. A reader scans that column for one of four
shapes; a fifth one is unreadable to them and unparseable to every surface that
renders it. **If you do not have the tool's word, the cell is `—` and the Note says
why** — and before you write that em dash, go and get the word: a measured run's
verdict is on disk, and the house rules' **Output: the JSON envelope** says how to
read it back for free.

**A lower bound is not one of these.** A figure that can only grow is a
*soundness* caveat about the measurement, not a requirement someone wrote, so it
travels in your column and the Note — **Needs attention**, with the reason —
never as a measured `CAUTION`. The composition then lifts the row to `CAUTION`
if the `STATUS` was `PASS`, which is the same outcome by the right route. At the
two checkpoints, a minimum the change did not cause is a Note only.

**There is no fourth source, and you supply no band.** A percentage, a delta or
an absolute figure reaches none of these words by its size, however large it is:
on a contracted run with no entry behind it its `STATUS` is `—`, and on an
uncontracted one the word comes from your assessment of the row, which is a
reading you can name a block for — never a threshold you chose. Which numbers a skill
may still state is the house rules' **A measurement inherits a verdict from a bound,
never from a band**. Prose copies are how the old
bands drifted from each other.

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
row on the screen. **`○`, `·`, `flagged`, `cleared` and `no_opinion` appear
in nothing a user reads**, and `⚑` only as preflight's ADJUST PLAN.

## The row verdict: the two columns composed

| `STATUS` | + Needs attention | + Looks good | + As reported |
|---|---|---|---|
| `PASS` | `CAUTION` | `PASS` | `PASS` |
| `CAUTION` | `CAUTION` | `CAUTION` | `CAUTION` |
| `FAIL` | `FAIL` | `FAIL` | `FAIL` |
| `—` | `CAUTION`¹ | `PASS` | `INCOMPLETE` |

¹ **`FAIL` where the entry is prose and its author wrote `severity: fail`.** Nothing
measures an entry with no `signal`, so you are its only judge and no arithmetic needs
protecting. The `fail` is the user's — they wrote the sentence and the severity; you
supply the finding, not the authority. It cannot reach the **exit code**, which a
measured breach earns.

**You escalate and never de-escalate.** Looking good and having no opinion move
no computed status; on a row that computed, a flag moves only `PASS → CAUTION` and
can never talk a `FAIL` down. `contract.compose_row` is the table, so you compose no row by hand —
but the reader sees both halves, and which one moved the run.

**A row with nothing computed and nothing raised is a `PASS`.** What carries the difference is not a weaker word: it is `run["contract"]`,
which says whether a bound was behind the `STATUS` cell, and the coverage count
below.

## The run verdict

**The worst row verdict, ranked `FAIL > CAUTION > PASS`, with `INCOMPLETE` rows
excluded from the worst-of.** One edit measures the entries its own translation
unit reaches, so on a contract of any size most rows are out of scope every run,
and rolling them in lets a single untouched entry outrank every judged one. A run
reads `INCOMPLETE` only when **no** row reached a word.

State it as the run's answer, not as one row among the rows. **It is printed once,
directly under the conclusion table, icon first.** Rows that reached no word become
a count beside it:

```
🔶 Verdict: **CAUTION** — worst path +19.1% in the new bounds check at block 4 (3 of 14 judged)
```

`✅` PASS, `🔶` CAUTION, `❌` FAIL; `INCOMPLETE` takes the word and no icon. The
footer repeats the icon and the word and **never the sentence** — a second, shorter
description of one run drifts from the first, and a `≥` surviving in one and not the
other teaches a reader to trust neither.

The count is required wherever any row went unjudged; without it a reader cannot
tell a contract three rules wide from one they saw three rules of.

What you may reason from, and what a `PASS` with nothing behind it has to say,
are the house rules' **Your verdicts are `flagged` / `cleared`** section —
stated once, there.

## The line

```
<prefix>: **<PASS|CAUTION|FAIL|INCOMPLETE>** <figure or — cause> [(<N> of <M> judged)]
```

- `<prefix>` is `Verdict` for every skill except `loci-preflight`, which uses
  `Execution fit` because it judges a plan rather than a measurement.
- `loci-preflight` and `loci-post-edit` print their own word for it: each skill's
  **Checkpoint words**. `STATUS` and the wire keep these four.
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
Execution fit: **STOP** — unbounded recursion in parser_descend
Verdict: **FLAG** — timing +147% past the 200 ns budget
Verdict: **CAUTION** — worst path grew 340 ns, in the snprintf at block 7 of adc_format
Verdict: **PASS** — 312 B measured; no contract covers stack_depth
```

<a id="recording-the-verdict"></a>

## Recording it: one call, on every run that reaches a verdict

The cockpit shows this run as the CLI's token plus the sentence **you** wrote. Both
travel in one call, their only route out of the turn.

**Compose the `Verdict:` line, record it, then print the report** with that line
exactly as recorded, so the cockpit shows the sentence the user reads. Nothing after
the footer restates, summarises or recaps the report; a standalone run ends its turn
on it. `loci-preflight` alone records later, once its plan is settled.

```
loci stats record --context-file "<project-context>" --run "<run id>" --agent-note "<the cause clause of your Verdict line>" --agent-judged '[{"entry_key":"<from the entry>","verdict":"flagged","note":"block 0x1a4 calls snprintf on the hot path","fn":"<function>"}]'
```

- **`--run`** is the id your verb returned — the manifest id from `analyse prepare` /
  `analyse measure`, or `data.run` from `analyse stack` / `analyse memory`. An
  unknown id is refused and the error names the ids it does know.
- **Call it on every branch that composed a `Verdict:` line**, a run that measured
  nothing included: that branch's whole content *is* your sentence, and a manifest
  prepared and never measured has its run line written by this call.
- **`--agent-note` is the cause clause only** — no `PASS`/`CAUTION`/`FAIL` token, no
  icon, no restated figure. The token stays the CLI's and the cockpit draws it in its
  own column, so a token in your sentence is a second verdict beside the real one.
  Kept to 200 characters, **cut at the last sentence that fits** and rendered
  verbatim — so put the load-bearing sentence first. A trim with no sentence end inside
  the budget is marked with an ellipsis. Send it on every run, clean ones included.
- **The note is that clause, never a second one** — a concern the measurement did
  not raise included. Where a bound passed and you read the loop as costing far more
  at a realistic trip count, the `STATUS` stays `PASS`, your assessment is `flagged`,
  and the note says why. The envelope echoes `agent_note` **as stored**: print that clause.
- **`--agent-judged`** carries your assessment per row: one
  `{entry_key, verdict, note, fn}` each, `verdict` being the wire spelling
  (`flagged` / `cleared` / `no_opinion`) of what the `AGENT ASSESSMENT` column
  rendered. **Send it for every row you reached, computed or not.** A flag on a row
  whose bound PASSED is the ordinary case rather than an exception — it is how a
  `PASS` beside **Needs attention** becomes a `CAUTION` row — and it is the half
  most often dropped, which leaves the cockpit showing the tool's word alone. Leave out the rows you did not reach; they stay `pending`,
  which is honest and is what the coverage count counts. `--run` with none of these
  flags is refused, because then there is nothing to write.
- **`--agent-verdict <flagged|cleared|no_opinion>`** carries one assessment for the
  RUN, for a reading no offered row carries. Prefer `--agent-judged` wherever a row
  was offered — it says which rule your reading is about. **Send `flagged` whenever
  your verdict rests on something LOCI did not compute** — a `≥` figure, an
  unresolved trip count, anything your reading made you close on. It needs
  `--agent-note` in the same call: that sentence IS its reasoning. It composes as a
  row does, raising a `pass` run to `CAUTION` and softening nothing.
- **`cleared` is not decoration on a run with no contract.** With a gate computed it
  changes nothing — `cleared`, `no_opinion` and sending nothing all leave the run
  `pass`, so only `flagged` earns the call. With **no** gate, the word IS the verdict:
  `cleared` records `pass` and sending nothing records **`unjudged`**, which reads as
  never judged rather than fine. Row assessments roll up and usually cover it, so this
  bites the run that drew **no rows at all** — send `cleared` there yourself.
- **When a parent skill escalated into you**, pass `--parent-run "<the parent's run
  id>"` to `analyse stack` / `analyse memory`. Your run keeps its own row, verdict
  and note; the pointer is what draws the two together in the cockpit feed instead of
  leaving your run looking unrelated to the edit that caused it.

<a id="conclusion-table"></a>

## The conclusion table

**A row is one contract entry, drawn.** Its identity is the entry's own —
function, signal and kind — so a gate two requirements reach draws **two rows**,
not one. A ceiling and a regression on the same signal are two requirements and
each gets its own word; two signals that happen to share a gate are two rows for
the same reason. Nothing is merged, and no row's word covers a requirement the
reader cannot see.

Five columns, in this order — the cockpit's two verdict columns between an
identity and a cause:

| ENTRY | FUNCTION | STATUS | AGENT ASSESSMENT | NOTE |
|---|---|---|---|---|
| `Performance (Hot-Path)` | `decode_frame` | `PASS` | Looks good | 186 ns of the 200 ns ceiling |
| `Performance (Hot-Path)` | `decode_frame` | `FAIL` | Needs attention | +14.1% vs 163 ns, past the 10% allowance — resampler at `decode.c:212` |
| `Stack (Depth)` | `decode_frame` | `CAUTION` | Looks good | 1904 B of 2048 B (93%) |
| `Memory (ROM)` | — | `CAUTION` | Looks good | 1.84 MB of 2.00 MB (88%) |

Two rows on one entry name, one function, two words — the first is the ceiling,
the second the regression, and the Note is what tells them apart.

### The `ENTRY` vocabulary is closed: eleven `Gate (Signal)` names

One name per signal, so nothing ever needs qualifying:

| Gate | Names |
|---|---|
| Performance | `Performance (Hot-Path)` · `Performance (Worst-Path)` |
| Stack | `Stack (Depth)` · `Stack (Frame)` |
| Memory | `Memory (ROM)` · `Memory (RAM)` |
| Safety | `Safety (Recursion)` · `Safety (Indirect Calls)` · `Safety (Unknown Callees)` · `Safety (Unbounded Recursion)` |
| Energy | `Energy` — bare, the one gate with a single signal |

Nothing outside this list is an `ENTRY`. The gate keeps only its section role: it
is where a row is filed, never the unit that carries a verdict.

**A text-only entry has no signal: its row is `<Gate> (Rule)`** — `Safety (Rule)`
on a `gate_fallback` — and its Note quotes the entry's `text`, then your cause.

**`FUNCTION` is what the row is bounded on**, an em dash where the row bounds the
whole artifact. Two columns rather than one mashed cell: the cockpit mashes them
because a terminal panel is width-bound, a report is not, and a reader scanning
for one function should not have to read every label to find it.

**There is no `KIND` column.** Two rows sharing an `ENTRY` and a `FUNCTION` are
told apart by their notes, because a budget and a regression do not read alike —
*186 ns of the 200 ns ceiling* against *+14.1% vs 163 ns*. A column reading
`budget` on eight rows out of nine to carry one distinction is width spent badly.
What this costs is the ability to scan for "show me the regressions"; that was
judged the right trade.

### The rules for each column

- **`STATUS` is the CLI's word, your composed word on a `none` envelope, or an
  em dash.** Never a blank, and never a word you reached by picking a threshold.
- **`AGENT ASSESSMENT` is the display word.** Needs attention, Looks good, As
  reported — never the wire spelling and never a glyph.
- **`NOTE` always carries the figure its `STATUS` is about.** Not only on a
  `CAUTION`, a `FAIL` or a Needs attention, where a reason has always been
  required — on a `PASS` too, because two passing rows on one entry name are
  otherwise indistinguishable. Every worked example in the corpus already does
  this; it is written down here so the closed vocabulary above is enough on its
  own.
- **A word with no cause cannot be acted on.** An entry's declared `severity` is
  **not rendered** — not in a column, not in NOTE. It decides how loudly a breach
  is surfaced, and on a `FAIL` `STATUS` already carries that.
- **A `CAUTION` says which of its two sources it came from.** `STATUS` cannot: a
  measurement inside its bound and close to it reads the same word as one past a
  bound the project declared advisory. So the Note says whether the limit was
  crossed, in words and not only as a percentage over 100 — *1,600 B of a 2,048 B
  bound* against *2,200 B, past the 2,048 B bound*. One is a warning, the other is
  a breach the project chose not to block on, and a reader acts differently on
  each.
- **An `INCOMPLETE` row is never drawn.** It becomes the coverage count beside
  the run verdict. Drawing it puts back, in the one place meant to carry verdicts,
  the rows already excluded from the worst-of — and on a contract of any size most
  entries are untouched by any one edit, so the table would be mostly inventory. A
  run where **no** row reached a word draws no rows at all: the verdict and the
  count say it.

### Three column shapes, and which to draw

The five columns above are the absolute shape. A delta report inserts the two
figures it compared, and a report with no comparable Before has only one:

| Shape | Columns | Drawn by |
|---|---|---|
| **absolute** | `ENTRY` `FUNCTION` `STATUS` `AGENT ASSESSMENT` `NOTE` | a standalone question — exec-trace, stack-depth, memory-report, control-flow |
| **delta** | …`FUNCTION` **`BEFORE`** **`AFTER`** `STATUS`… | an edit with a Before — post-edit, and exec-trace on an edit in flight |
| **after-only** | …`FUNCTION` **`AFTER`** `STATUS`… | a change measured against no comparable Before |

`BEFORE` and `AFTER` go **after `FUNCTION`**, before the verdict columns, so the
two verdict columns stay adjacent in every shape. Nothing else varies: the same
`ENTRY` vocabulary, the same `STATUS` words, the same Note rule.

### Who composes it

**Every skill composes the table, on every envelope.** `data.rows` is not
rendered by anyone — it merges two bounds into one row, which is the thing this
section removes — and it stays in the envelope, unused, until that change has
settled.

So on both branches you build the rows yourself, from `data.paths`,
`data.judgements`, `data.unjudged` and the stubs in `data.agent_judged`. **With a
contract, one row per entry** — signal, function and kind, **measured or not, and
whatever a skill's catalogue says about when that row appears**: a visibility
trigger decides rows no entry covers, and never suppresses one an entry does. A
requirement that was checked and met is the answer the reader asked for. **Without
one, one row per (signal, function) you reasoned about**, which is the same rule
arriving at a simpler answer: with no entries there are no two requirements on a
signal, so there is nothing that could have been merged. On a `none` envelope each row's
`STATUS` is the word your assessment maps to under [No contract: the agent fills
`STATUS`](#no-contract), with that section's caption under the rows.
Never fall back to a stacked `ENTRY: … / FUNCTION: …` field list — a reader
compares rows by scanning a column, and a list of fields cannot be scanned.

### An entry nothing measured: three states, not one

They are surfaced three different ways because they are three different states,
each addressed to a different reader:

| State | What it is | Surfaced as | Whose move |
|---|---|---|---|
| **routine** (`reason_code: no_measurement`) | the bound is fine; nothing measured it *this run*. Most of a contract, most runs | no row; it joins the coverage count | nobody's — it is normal |
| **rejected** (`not_enforced`) | the bound is **broken**: blocking lint refuses the entry, so no run can ever enforce it | its own line above the table, on every run including clean ones, with the reason | the **user's**, by fixing the entry |
| **pending classification** | the binary **was** read and the figures are in hand; the verb declines to guess the one thing it cannot see | a row, with its count, never as unmeasured | **yours**, by reading the source and patching the answer back |

Do not fold them together. A rejected entry is a fact about the contract file
rather than about the code, and it is invisible unless it is said out loud; a
pending classification was measured, so calling it unmeasured is false.

### A worked example is normative

**An example in this corpus is a rule, not an illustration.** It is what a model
copies, so a stale one ships as behaviour. Changing a rule therefore means
changing every example of it in the same edit — and an example that disagrees
with the prose beside it is a defect in the example until someone decides
otherwise, never a licence to follow it.

This is written down because the corpus has already failed it twice in one week:
the 15 September composition change reached nine files of rule text and six of
examples, and three skills kept printing an em dash beside **Looks good** on runs
their own verdict lines called uncontracted; and a bounded recursion cycle kept
its amber in a worked example for a week after D3 decided it carries no status.

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

`warn` is retired — never write it; an existing `severity: warn` reads as
`caution` and lint says to fix it. Unrelated `warn`s stay, none of them
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

## Old patterns

Kept so a reader meeting an older report or an older branch can place it. **None
of this is current, and none of it is a rule** — the sections above are.

- **One verdict word, no agent column.** Before the two-vocabulary split, a run
  closed on a single word and there was no way to say whether a bound or a
  reading had reached it. The two display columns, the composition matrix and the
  run's worst-of superseded it.
- **`severity: warn`.** The retired third severity, above.
