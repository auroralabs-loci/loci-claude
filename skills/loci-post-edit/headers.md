# Measuring a header edit

Reference for `/loci:loci-post-edit`. Step 0b in full — how a header reaches `prepare`, which units it is measured through, and what the per-unit report says.

---

## Step 0b: a header is passed to `prepare` like any other source

**Applies when the `[loci]` reminder said the file "emits no object of its own".**
That sentence is the CLI's own answer (`loci scan`'s `measure_via`), carried through
by the hook. If you have no reminder to read (a manual invocation), the same is true
of any `.h` `.hpp` `.hxx` `.h++` `.hh` `.inc` `.ipp` `.tcc` `.inl` `.tpp` `.def`.
Otherwise skip this step entirely and go to Step 1.

Nothing changes about what you run. A header emits no object, so `prepare` measures
the translation units that `#include` it: it names them, takes the first three that
compile, rebuilds each one's Before against the header **as it was before this turn's
first edit**, and carries on exactly as for a `.c`. Pass the header as `--source` in
Step 1 and read two extra fields from the envelope — the full field meanings are in
**[Measuring a header edit](../_shared/compile-route.md#header-edits)**:

- **`data.headers[]`** — one account per edited header: `source`, `reached` (how
  many units include it), `measured[]`, `unaffected[]`, `unmeasured[]`
  (`{source, reason}`), `coverage_complete`, `confidence`, `warnings[]`.
- **`data.units`** — `{fn: translation unit}` for every function in `data.functions`.
  Step 6 groups rows by it: one heading naming the header, one sub-heading per unit.

`data.provenance[]` on this route carries `before_kind` — `reconstructed`, or
`snapshot` when the unit was itself edited this turn, in which case `note` says the
delta is the turn's, not the header's alone — and, for a rebuild, `verified`.
`verified: false` means the CLI could not prove the rebuild read the captured header:
report the numbers with that caveat and treat a reported "no change" with suspicion.
A line with no `before` and a `note` is a unit whose Before could not be rebuilt — the
After is measured alone, and the note is the CLI's reason.

Three reporting rules that are specific to this route:

**An empty `measured` is only "nothing is affected" when `coverage_complete` is true
AND `confidence` is `exact`.** In any other combination the search could not see the
whole project, and reporting silence would be the same silent skip that made header
edits invisible in the first place. Say what was found and what was not examined,
quoting the relevant `warnings[]` line.

**Say what you did not measure.** When `reached` exceeds the count of `measured`, one
line: "measured 3 of 7 translation units this header reaches". `--units N` raises the
cap when the user asks for it; never raise it on your own — every unit is a metered
measurement.

**A unit in `unaffected` is a result, not a gap.** It reads nothing that was edited
this turn — commonly a header change inside an `#ifdef` that unit does not take. List
it as unaffected; do not retry it, and do not present it as a failure. `unmeasured` is
the other thing: a unit that was reached and could not be measured (assembly, or a
compile that failed), with the reason to quote.

`data.duplicates[]` names a function defined in more than one measured unit (a
`static inline` the header itself defines, or two `static` helpers sharing a name): it
was measured in `measured_in` only. Say so in that row's Note.
