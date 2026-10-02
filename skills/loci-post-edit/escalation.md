# What the escalated skills still answer

Reference for `/loci:loci-post-edit`. Step 4a in full — which signals an object cannot answer, when to escalate, how the child's row is drawn, and when to offer a relink.

---

## Step 4a: what the escalated skills still answer

`data.escalations` (on `prepare` and `measure`) is `[{skill, requests}]`: the
requests `measure` never fills — stack depth and frame size, the four structural
invariants, ROM and RAM — grouped by the skill that does. Invoke a listed skill only on SKILL.md Step 4a's
triggers; one run answers every request it lists. Each emits its own report, and
its one-line summary becomes a Stack / Safety / Memory row here. Increment `R` by 1 at the trigger
and again after reasoning over the result.

**With no contract, the decision is yours.** There are no entries, so there is no
request demanding a stack or memory measurement and the CLI proposes nothing.
Call `stack-depth` or `memory-report` yourself — with `--parent-run` — when what
you just read argues for it: a frame delta on a deep call chain, a ROM total that
moved on an edit that should not have touched it, or new recursion, a call through
a function pointer or a callee this project does not define — only `stack-depth`
judges those. The argument is the whole gate.
A child run costs latency and context, not money — `analyse stack` and
`analyse memory` are unmetered end to end — so argue for it from what it would
tell you, and "just to check" is still not that.

**Hand the escalated skill this run's manifest id as its `--parent-run`.** It records
its own run either way; the pointer is what puts that run under yours in the cockpit
feed rather than beside it as an unrelated entry, and without it nothing on the wire
says the two answered for one edit. Same on Step 2a, where the id is the one `prepare`
returned.

**The child keeps its own verdict and you keep yours.** It gets a row of its own
in your conclusion table, its `ENTRY` cell under a `└ `, with its figures — clean
or not — and you name the escalation in that row's Note. Never re-measure what it
measured: a second identical run answers the question you already have, and two
rows for one figure is what a reader cannot resolve.

**Name the child in your `Verdict:` line only where it moved the word** —
`Verdict: **CAUTION** — stack-depth: 202% of the 2 KB budget on comms_task`. A
child that changed nothing gets no mention there; its row already carries its
figures. Naming every escalation on every run makes the one that decided the
answer indistinguishable from the ones that did not.

Nothing coming back is the common case and needs no apology.

<a id="when-to-offer-a-relink"></a>
## When to offer a relink

After an edit the linked binary is older than its sources, so the child falls back
to the object and `stack_depth` goes unmeasured. A relink rewrites the user's build
outputs, so you offer it, never run it unasked:

1. **Only when `data.artifact.before_saved` is `true`** (`error.before_saved` on a
   refusal). `false`: a relink destroys the only Before — report `before_reason`.
2. **Only where the edit plausibly moves a bounded figure** (a function on the
   entry's call graph; a new call, local array, recursion or indirect call) **and
   the tree builds as it stands**.
3. **Ask once per turn with the question tool**
   ([house rules](../_shared/house-rules.md#question-tool)), naming the entry and
   `data.artifact.relink`. A yes covers the turn; a no is not asked again; with
   nobody to answer, skip.
4. **Yes:** run it, then the child again with the same `--turn` and `--parent-run`.
   **No:** record the entry `no_opinion`, `relink declined`; its row stays `—`.
