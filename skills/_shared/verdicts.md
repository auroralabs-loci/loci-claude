# LOCI verdict vocabulary (shared)

**Three statuses. The same three everywhere.** Every LOCI skill that judges a
run — preflight, post-edit, exec-trace, stack-depth, memory-report,
control-flow — closes on one of these and no others.

| Icon | Status | Means |
|:--:|---|---|
| ✅ | `PASS` | Nothing to act on. Within bounds, no structural hazard, no regression beyond the skill's band. |
| 🔶 | `CAUTION` | Real and worth a look, not blocking. Tight against a bound, a regression inside budget, a benign structural hazard, or a result that is only a lower bound. |
| ❌ | `FAIL` | Act now. A bound is breached, or the result is structurally unsafe. |

Do not invent a fourth word, and do not rename these per skill. A skill that
needs a different *emphasis* puts it in the cause clause, not in the status:

```
Execution fit: **FAIL** — unbounded recursion in parser_descend; do not write this as planned
Verdict: **FAIL** — timing +147% past the 200 ns budget
```

## The line

```
<prefix>: **<PASS|CAUTION|FAIL>** <figure or — cause>
```

- `<prefix>` is `Verdict` for every skill except `loci-preflight`, which uses
  `Execution fit` because it judges a plan rather than a measurement.
- A figure (`**PASS** 15.2%`) or an em-dash cause (`**CAUTION** — <one
  sentence>`); skills that have both put the figure first.
- Whether the status word is bolded is **not settled here** — each skill keeps
  what it renders today (ADR 07 Q7.4). The string passed to `loci stats record
  --verdict` is always unbolded.

## Rows use the same three words

The per-gate status column inside a conclusion table uses the identical icons
and, when spelled out, the identical words. There is no separate row
vocabulary — `WARNING` is not a LOCI status.

## Body-only long forms

A status may carry a parenthetical qualifier **in the report body** where there
is room to explain it — `PASS (lower bound)`, `CAUTION (acceptable)`. The
qualifier never replaces the status word, and the footer and the recorded
`--verdict` string carry the status plus its cause in prose, not the
parenthetical.

## Wire encoding

`stats record --gates` keeps its lowercase encoding: `✅→pass · 🔶→warn ·
❌→fail`. That is the existing wire format for the dashboard and is out of scope
for this alignment.

## Escalation

A child skill hands its status back unchanged; the parent renders it in the
same three words, so no translation is needed:

```
stack:  ≥1912 B (≥93%) — CAUTION, lower bound: indirect call in dispatch
memory: ROM 42% / RAM 58% — PASS
```

---

Decided 2026-08-14 — see `.local/docs/adr-verdicts/01-label-vocabulary.md`
(Q1.1–Q1.5). Thresholds, regression-vs-absolute gating, contract authority and
the persistence format are **not** settled by this file; those remain open in
ADRs 02–09.
