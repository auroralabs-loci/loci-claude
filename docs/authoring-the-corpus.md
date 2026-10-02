# Authoring the corpus

Rules for **writing** the skill documents, not for running them. A model executing a
skill never relocates prose or rewrites a template, so these live here rather than in
`skills/_shared/`, where every run would load them.

Read this before editing anything under `skills/`.

---

## Where history may live

**A rule states what is true now.** It does not narrate what used to be true, and
it does not date itself: a marker like *since T14*, *as of 0.1.126* or *the older
CLI* is a sentence that will become wrong, sitting in the one place a model reads
to decide what to do.

Three destinations, and nothing else:

1. **The reasoning goes to the rationale documents** — `docs/contract-rationale.md`
   and its siblings, which exist for exactly this and are **not read during a
   run**. They sit outside `_shared/`, which is for what a skill loads. Why a rule
   is shaped as it is belongs there in full.
2. **What the agent must still recognise stays**, in its one-line form. A caveat
   about a CLI older than the pin is load-bearing while a user can be on one, so
   it stays — as a branch condition, not as a history.
3. **Anything else goes in an `## Old patterns` block at the end of the file that
   owns the rule**, collapsed and clearly marked, where it informs without
   competing with the instruction above it.

**A sample output carries no absolute date.** A template whose example reads
`linked 2026-07-28` teaches staleness to every model that copies it, and the date
was never the point — write `<build time>` and let the fact beside it ("sources
current") do the work.
