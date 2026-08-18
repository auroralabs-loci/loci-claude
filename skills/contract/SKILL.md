---
name: contract
description: >
  Author and inspect this repository's Contract Envelope (.loci/contract.yaml) —
  the stack, timing, energy, memory and structural bounds every LOCI measurement
  is judged against.
when_to_use: >
  When user states a limit as a requirement: "set a budget", "add a bound", "no
  more than N bytes of stack for X", "max limit of N", "cap X at N", "X must not
  exceed N", "I want function X to stay under N", "fail the build if timing
  regresses". Also "what are my limits", "show the contract", or /contract; and
  when a LOCI skill reported that no bounds are set for this repository and the
  user wants to fix that. Requirement vs. measurement: if the user states a
  limit, use this skill (draft a bound). If they ask what the current usage or
  worst case actually is, that is stack-depth, exec-trace or memory-report
  instead — those measure, this one authors the bound they are judged against.
---

# LOCI Contract Envelope

`.loci/contract.yaml` holds the bounds this repository requires — one committed
file, a flat list of entries. Every LOCI skill reads it and judges its findings
against it.

**One rule governs this whole skill: you draft, the user applies.**

You may read (`loci contract show`, `lint`) and draft (`loci contract draft …`).
You may **not** run `accept`, `init`, or a bare `edit`/`disable`/`enable`, and you
may not edit the file with Edit/Write. All of those are denied by a hook, so
attempting one wastes a turn.

The reason is worth holding onto, because it is not arbitrary: an entry is a
**requirement**. Requirements have an author, and the only author is the repo
owner. A bound they never applied is one nobody set — and the portal renders this
file as what the repository requires, while the release evidence bundle cites it
to a certification reader. So a fabricated entry does not stay local. It also
means you never resolve a failing bound by moving it.

Every `loci` call prints one JSON envelope (`{ok,data}`); parse it with `jq` and
branch on `.ok`. These verbs need no sign-in.

## Step 1 — Read before proposing

```
loci contract show
```

- `data.exists: false` or `data.counts.enabled == 0` → **no active contract**
  (missing, empty, or everything disabled); go to Step 5.
- Otherwise show the user the entries that bear on what they asked, with their
  `index`. They cannot amend what they cannot see.
- `data.draft` present → a draft is already pending. Say what is in it
  (`loci contract draft show`) before adding to it. If `data.draft.stale` is
  true, the contract moved underneath it: run `loci contract draft clear` and
  start over.

## Step 2 — Structure the sentence

This is the one part only you can do. Turn what the user said into an entry
object:

```json
{
  "text": "comms_task must stay under 2 KB of stack",
  "kind": "budget",
  "function": "comms_task",
  "signal": "stack_depth",
  "bound": {"max": 2048, "unit": "B"},
  "severity": "fail"
}
```

Rules, in order of how often they are got wrong:

- **`text` is the user's sentence, verbatim.** Not a paraphrase, not tidied, not
  translated, not expanded into your own wording. It is their intent, it is what
  a verdict quotes back to them, and it is the entry's identity.
- **Never invent a number.** "Keep the stack small" is not a budget — ask what the
  limit is. An invented bound gets believed and then enforced.
- **Convert to the signal's canonical unit**; the sentence keeps the human form.
  `2 KB` → `{"max": 2048, "unit": "B"}` while `text` still says "2 KB".
  | signal | unit |
  |---|---|
  | `stack_depth`, `rom_size`, `ram_size` | `B` |
  | `exec_time` | `ns` |
  | `energy` | `uWs` |
- **A timing bound is always response time — say so when you draft one.**
  `exec_time` means worst-case latency from entry to exit *including callees* —
  the metric preflight and post-edit record. Tell the user that in the draft, so
  they know the budget covers the whole call and not the function's own code.
  Never draft `exec_time` against exec-trace's throughput time (self-time,
  callees excluded); it is a different metric and a bound written against it
  judges the wrong number. If the user's sentence is about a function's own code
  in isolation, say so and confirm the limit they mean is still end-to-end.
  `energy` shares that basis — exec-trace's `energy_uws` is self-scoped too — so
  an energy bound off an exec-trace figure is wrong the same way. The memory and
  stack signals have no such split and need no such heads-up.
- **Pick `kind` from the intent** — **required on every entry that has a
  `bound`**, and rejected without one: "must stay under / must never exceed" →
  `budget`; "must not regress more than" → `regression` (with
  `bound.max_delta`, e.g. `"+10 %"` — always against the previous version, and
  write the space so drafted entries match what `contract init` wrote);
  a structural property with zero tolerance (budget 0) → `invariant`. It is what
  tells a `max: 0` invariant from a budget whose limit is genuinely zero, and it
  is part of the entry's identity, so an entry without it is a second bound on
  the same thing.
  A text-only entry has no bound and takes no `kind`.
- **A `bound` needs a limit** — `max`, `min`, or `max_delta`. A `bound` carrying
  only a `unit` is rejected: it reads as enforceable and can never be judged.
- **Structural signals** (`unbounded_recursion`, `recursion_cycles`,
  `unresolved_indirect_calls`, `unknown_callees`) are `invariant` with
  `bound: {max: 0}` and no `function` — they cover the whole binary, and a
  `function` on one is rejected. `stack-depth` is what measures all four, over a
  linked binary; the count it reports is what the bound is judged against.
- **`severity` follows the modal in the sentence**, so the same wording always
  drafts the same severity and the user can predict it:
  | the sentence says | `severity` |
  |---|---|
  | must, shall, never, no more than, hard fail, fail the build | `fail` |
  | should, can, prefer, try to, ideally, nice to have, just warn me | `warn` |
  | no modal at all — "cap it at 2 KB", "budget is 200 ns" | omit it |
  Omitted means `warn`. There is no `defaults:` block; never write one.
  Two ways this goes wrong: a sentence carrying both ("should never exceed") is
  `warn` — **the weaker word governs**, because escalating a preference into a
  build failure is the costlier error; and a modal you are guessing at is one to
  ask about, not to pick. An explicit "hard fail" / "just warn me" always wins
  over the table.
- **What resists structure stays a sentence.** `{"text": "ISR handlers should
  avoid heap allocation entirely"}` is a complete, valid entry. Do not invent a
  `signal` to make an entry look finished — a text-only entry is judged by you at
  review time, which is the point.

## Step 3 — Draft it

```
echo '<entry json>' | loci contract draft add
```

An array drafts several at once, as one transaction. `draft edit --index <n>`,
`draft disable --index <n>`, `draft enable --index <n>` are the other ops.

On `ok: false`, read `error.message` — it names the field — and fix the object.
Do not re-send it unchanged. Two rejections are worth recognising:

- *duplicate wording* → the user is changing an existing entry, so use
  `draft edit --index <n>`, not `add`.
- *unit / kind disagreement* → your structuring is off, not the user's intent.

`data.warnings` with `signal_unknown` means the entry is kept but will be judged
by a model rather than computed. Say so plainly; don't silently swap the signal
for one that happens to exist.

## Step 4 — Review gate

Show each drafted entry in full — the sentence and every field — as a short list,
one entry per line. Then **stop and ask with `AskUserQuestion`**, not with prose.
A drafted bound buried in a paragraph gets skipped; a question box does not.

One question, header `Draft`, options in this order:

- **Looks right — give me the command** — "You apply it; nothing is written yet."
- **Change something** — "Re-draft before anything is written."
- **Discard** — "Clear the draft; nothing is written."

**No option may be worded as accepting.** The box is a review — the answer only
decides your next move, and applying the draft is still a command the user runs.
An option labelled "Accept" reads as the decision, so they approve it, feel done,
and the bound never lands.

Put the entries in the question text (or an option `preview` if they are long
enough to need side-by-side reading) so the user is deciding on what they can see.

Then, by answer:

- **Looks right** → give exactly one line and nothing else:

  ```
  ! loci contract accept
  ```

- **Change something** → ask what to change, then re-draft (`draft clear` and
  draft again, or add more ops) and return to this step.
- **Discard** → `loci contract draft clear`, confirm in one line.

Say what accepting does: the entries land in `.loci/contract.yaml`, unstaged, for
them to review and commit. **Do not run it. Do not offer to run it. Do not
present it as a formality** — this is where authorship transfers. No answer to the
box licenses you to run it yourself.

If they leave it unapplied and the conversation moves on, do not keep raising it —
the `Stop` hook nudges them every turn while a draft is pending, from the file
rather than from your memory of it.

Once they have accepted, show the change and leave the commit to them:

```
git diff --stat -- .loci/contract.yaml
```

Two confirmations, doing different jobs: `accept` authorizes the content, the
commit shares it with the team and the portal.

## Step 5 — No active contract

1. Say what the envelope is, in two sentences: the bounds LOCI judges every
   measurement against, committed so the whole team gets the same verdicts.
2. Invite intent in plain language. Ask the user to say what must hold true for
   this code, in their own words — "comms_task must stay under 2 KB of stack",
   "execution time must not regress more than 10%" — and turn each sentence
   into a draft (Steps 2–4). An open-ended ask on an empty contract often gets
   no answer, so offer a starting point with `AskUserQuestion` (header
   `First bound`, `multiSelect: true`): stack depth, execution time, energy,
   ROM/RAM. Their pick chooses the *signal* only — you still ask for the number
   and still use their sentence verbatim.
3. **Never invent their numbers.** A budget needs a limit only this project
   knows. If the user does not have one yet, `/stack-depth` or `/exec-trace`
   will measure a real value to bound.
4. If entries exist but are all disabled, say so and show them — re-enabling
   (`draft enable --index <n>`) may be all that is needed.
5. Nothing is written until they accept a draft.

Do not mention `loci contract init` or a starter set — that flow is internal
(reserved for seeding LOCI's internal gates later) and not user-facing yet.

## Step 6 — On the way out

If the draft carried more than one op, or you changed anything structural:

```
loci contract lint --draft
```

Report `data.findings` at `error` severity; `warn` and `info` only if they bear on
what the user asked.

## Reporting

Keep it short. Show the entry as it will read in the file, not as JSON, unless
the user asked for the object. Cite indices so a follow-up can address an entry.
One remark per report, max 15 words, grounded in what is actually in the file —
"six bounds, four of them structural" beats "your contract looks good".
