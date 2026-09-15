# What the user hears

Reference for `/loci:init`, read once at Step 1. It governs the lines this skill
**prints** — what you say on recognising the project, when you ask to run something,
while it runs, and when it is done. It changes nothing about what init runs or asks:
every fact a step requires is still required, and no honesty below is traded for a
shorter sentence.

## Outcome first, machinery where they must act on it

The user typed one command to find out what LOCI can tell them about their code, and
what they meet first is a build-system investigation: tools, databases, translation
units, tiers. Keep all of it. Say it **second**, in their terms first.

| Say this | Not this, unprompted |
|---|---|
| the exact flags your code is really compiled with | compile database, `bear`/`compiledb`, synthesized |
| the file that gets compiled | translation unit |
| checked against your real build | replay-compare, validation tier |
| what LOCI recorded about this project | recipe, envelope, integrity record |

The right column is not banned, it is **second**. Name the machinery the moment the
user has to act on it — a tool of theirs to install, a command to approve, a build of
theirs that is broken — the moment they ask for it, and in anything they will type
into a terminal. A name they cannot act on is narration: drop it.

Two things never shrink. A **refusal's own `message` and `detail`**: it names the fix
in the CLI's words, and paraphrasing it is how a user ends up fixing the wrong thing.
And anything saying a measurement would be **less than it looks** — Step 4's notes,
warnings and `!` lines.

## Step 1 — say what you recognised

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

## Step 4 — ready, then the caveats, then the setup

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
   sentence, and **not** an `AskUserQuestion`: Step 3 and Step 4 spend this skill's
   one question between them.

Name no function of theirs. You have not read their code, and a plausible-sounding
function that is not in it is the most expensive sentence in this file.
