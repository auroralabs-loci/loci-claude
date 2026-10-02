# What the user hears

The house voice, for every LOCI skill. It governs the lines a skill **prints** —
not what it runs, not what it asks, and no honesty is traded for a shorter
sentence. A skill with printing rules of its own keeps them beside its own steps;
this is what they all share.

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
And anything saying a measurement would be **less than it looks** — its notes,
warnings and `!` lines.

<a id="voice-remark"></a>

## The voice remark

Before the footer, one short remark — **max 15 words** — grounded in a specific
number from this run. Attribute an improvement to the user ("clean work", "smart
move", "tight code"); on a concern be honest and constructive, with specifics.

**Skip it** when the analysis produced no results, or when the user asked for raw
data only. **Never emit one in a failure context** — a diagnostic report is not
the place for it.

One line, never two. It is the only sentence in a LOCI report that is not a
measurement, and it earns its place by naming a figure the run actually produced.
