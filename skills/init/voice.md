# What init says while it works

Reference for `/loci:init`, read once at Step 1: what *this* skill prints, step by
step. Outcome before machinery, and two things never shrink — a refusal's own
`message` and `detail`, and anything saying the result is less than it looks. It
changes nothing about what init runs or asks: every fact a step requires is still
required.

## Step 1: say what you recognised

One line, before any build-system talk, out of `.data` alone: the kind of project,
its target, its language, its build. No probe mechanics, no candidate ladder, no
file inventory — if a candidate matters, it is Step 3's question, not narration.

Then what is still to come, in outcome terms: one build of theirs, then the setup for
them to confirm. **Never an ETA.** A synthesize on a firmware tree runs for minutes
and Step 1 cannot know which of those this is.

## Before you ask to run something

The user is being asked to let a command touch their tree, so open with what it buys
them and what it changes: LOCI needs the flags their code is really compiled with,
this runs their own build once, it writes build outputs and LOCI's local setup, and
it does not touch their source. Then the specifics the route actually carries —
`compdb.md`'s consent section is the honest list, and none of it is optional.

## While it runs — a checklist, not a narration

Print **the whole list, every time**, from the first moment the user is left waiting.
They are owed the shape of what is coming, which is the thing a running commentary
never tells them — so the stages still ahead are on screen from the start, and the
block is reprinted as each one lands:

```
Connecting LOCI to your firmware…
✓ Project detected — <build system>, <language>
✓ Target detected — <isa>
· Build configuration captured
· Build verified
· LOCI execution model ready
```

`✓` is a stage the envelope says has **completed**; `·` is one still ahead. Nothing is
ticked in advance of the fact that earns it — a `✓` the user cannot verify is the one
thing this block can get wrong. A stage that **fails** takes `✗` and the list stops
there: its command and its own error text follow, in full, and the run stops being a
checklist.

The lines are settled at the first print, from what **this** run will do, and never
change afterwards — same lines, same order, more ticks. A stage this project does not
have is not in the list at all rather than pending forever: a tree that needs no build
of its own carries no `Build verified`, and a project whose configuration was already
on disk carries no `Build configuration captured`.

Reprint when a tick changes, and only then: once per stage, never twice for the same
state, and never a sentence of commentary between two prints.

**Which call earns which tick**, so there is never a question of when to reprint:

| Line | Ticked by |
|---|---|
| Project detected | `loci init probe`'s envelope (Step 1) |
| Target detected | the same envelope — both land on one reprint |
| Build configuration captured | the compile database being in hand (Step 2); absent from the list where the project needs none |
| Build verified | `loci init` returning `ok` (Step 3), which is the call that runs their build |
| LOCI execution model ready | `loci init --confirmed` returning `ok` (Step 4) |

So the block is printed **four times** on a full run, not once: pending, after probe,
after the build, after the confirmation. The last three are the ones a run drops when
it prints the list at the start and never returns to it — which leaves the user
watching a checklist that stopped at two ticks while the work went on around it.

The wait for the user's confirmation sits between the third print and the fourth. Ask
there, and reprint once they answer; do not reprint while waiting.

Where the project is not firmware, the opening line's noun follows the project — its
binary, its library, its crate. The stage names do not change with it.

That last tick is the handover to Step 4, and the same fact as the readiness line
underneath it — so let the readiness line be where it is said in the user's terms, and
do not restate the tick as a sentence of its own.

You cannot un-print the commands themselves; the terminal shows every call and its
output either way. What this governs is **your** prose around them — which is what the
user reads as the story of the run, and which was the whole of the narration problem.

So: no restating what a command is about to do, no tool-selection reasoning, no
relaying build output that succeeded. Offer it instead of printing it — one line, once,
saying the details are there for the asking (the captured flags, the build output, the
commands you ran). On a failure the offer is not enough: the command that failed and
its own error text go in the transcript, whole.

## Step 4: ready, then the caveats, then the setup

Order: what they can now do → what qualifies it → the setup itself.

1. **One readiness line**, in their terms: this project is connected, and LOCI can
   reason about timing, stack and memory on the code that really runs on the target.
   Say **checked against your real build** only where `.data.validated` is true; where
   it is not, that line says instead what has not been checked. It carries no caveat
   of its own — a warning does not belong inside the sentence that says "ready".
2. **Then the notes, warnings and `!` lines**, in full and unsoftened, ahead of the
   detail layer. They are the reason a readiness line can mislead, so they follow it
   immediately rather than sitting at the bottom of a report.
3. **Then the setup**: the report and the fields Step 4 lists, the confirmation
   question, the plumbing still unprinted.
4. **Close with one invitation**, not a command list: name what this recipe records —
   the artifact, the target — and offer the measurement that fits it, with
   `/loci:exec-trace` and `/loci:stack-depth` as the words they can type. One
   sentence, and **not** a question-tool call: Step 3 and Step 4 spend this skill's
   one question between them.

Name no function of theirs. You have not read their code, and a plausible-sounding
function that is not in it is the most expensive sentence in this file.
