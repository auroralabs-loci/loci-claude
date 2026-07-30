# Bug list

Confirmed defects, parked. Not to be fixed pre-conference unless they block a
demo.

## Detection gate: `*.out` is too generic to be project evidence

`_has_source_evidence` (`lib/detect-project.sh:95`) accepts `*.out` as proof
that a tree is a C/C++/Rust project. It is the default `ld`/`a.out` name, but
also the extension of countless unrelated dumps and downloads. A single such
file is enough to claim a directory.

Found via `~`: one `~/Downloads/STM32F4_DISCO-Audio_playback_and_record.out`
was the sole evidence anchoring the whole home tree as a project. The `$HOME`
guard now blocks that specific path, but the extension is still loose for any
other directory.

Not fixed because narrowing it risks missing real embedded output — some
toolchains do emit `.out` as the linked image. Needs a call on whether to
require an ELF magic check, or only accept `*.out` alongside a second signal.

## Detection gate: subproject scan only looks one level down

`_list_subproject_roots` (`lib/detect-project.sh:104`) checks only immediate
subdirs for a `.git` or root build file, so `multi_project` cannot fire on a
container whose projects sit deeper. A `~/Projects/<repo>` layout reads as
"zero subproject roots" — indistinguishable from a real project dir — and then
gets the benefit of the depth-2 source fall-through.

Recursing a level or two would classify these correctly, but it is a behavior
change across every real project shape (monorepos, submodule trees, vendor SDK
checkouts) and needs its own test sweep.

## Freshness guard: the prose lint cannot carry the weight put on it

`tests/unit/test_freshness_contract.py` pins Pattern B's rules as text. Three
hostile-review rounds have each defeated it, and round 3 defeated it again after the
round-2 hardening: four rule-gutting rewrites passed all 32 tests while using none of
the forbidden patterns (an "escape hatch" permitting a labelled stale measurement;
"reuse is cheap and a cross-compile is slow, so when in doubt measure whatever the
tree already holds"; the object's `worst_case_depth` as a "provisional figure"; "try
this route first whenever a `.o` is already on disk"). A fenced code block labelled
"superseded guidance" also substitutes for the banned HTML comment.

Not fixed because it is not fixable by this mechanism: no lint can prove the absence
of a permissive sentence in prose. What was tightened is the mechanical part —
section bounds must now match exactly once, and every pinned phrase must be new in
the change — plus the vocabulary screen, which is explicitly documented as partial.

**The load-bearing guarantees are elsewhere, and they hold:** the CLI's
`data.source_provenance` field (a machine-readable verdict a reworded skill cannot
remove) and B4's mandatory `Artifact:` line (which makes a wrong selection visible
after the fact). Treat the lint as a speed bump, not a proof.

## Freshness guard: B2's architecture-scoping rule is not actionable

`skills/_shared/loci-runtime-contract.md` B2 rank 2 says a linked candidate outside
`.loci-build/<loci_target>/` "needs the ISA confirmed before you trust it", so a
relink left by a previous session under a *different* target is not measured with the
wrong `--arch`. No shipped tool answers the question: `loci_artifacts` entries carry
only `path`/`mtime`/`kind`; `loci build fresh` returns no arch; `loci elf memmap`
returns a *family* (`cortexm`), which cannot separate `armv6-m` from `armv7e-m` — the
most likely confusion, since both use `arm-none-eabi-g++`. And `readelf`/`file` are
banned by the tool-boundary section.

Read literally it also puts the hurdle on every `elf_files` entry (the user's own
`build/app.elf`) while exempting a `.loci-build/<loci_target>/` relink — a provenance
preference in favour of `.loci-build`, which contradicts B2's own filter-first rule
twelve lines later.

Needs either an arch field on `loci_artifacts` / `build fresh`, or the rule narrowed
to "prefer a candidate under `.loci-build/<loci_target>/`, and say which target
directory a candidate came from" — which is answerable from the path alone.

## Freshness guard: B2 rank 4 dead-ends when the only fresh artifact is an object

Shape: whole-program stack-budget question, `.loci-build/<target>/x.o` fresh, no
linked ELF anywhere (`make clean`), no build system, no linker script. B2 rank 4's
three exits are all void — "relink per B3's rebuild step" (that step is defined only
under the `stale == true` branch, and nothing here is stale), "cross-compile with the
defaults" (the defaults table gives compile flags only; no `loci` verb links), and
"ask the user which target" (the target is known; the missing input is the link line,
which no rule tells the model to ask for). It degrades to "say what is missing and
stop", which is safe but not what the text promises.

## Freshness guard: `loci-preflight` can still measure an unchecked binary

`skills/loci-preflight/SKILL.md` has a "Secondary path: existing binary" with no
freshness gate, no Pattern B reference and no `Artifact:` line — and it is a MANDATORY
auto-run skill in `/plan` mode. It is routed to Pattern A only, and
`PATTERN_B_SKILLS` in the lint suite excludes it, so no provenance assertion and no
wording screen reads that file.

Not fixed here because preflight's secondary path is a different flow (it compiles
the source itself and only falls back to a linked binary for cross-TU callees), so
bolting B1-B4 on needs its own design pass and its own evals.

## Freshness guard: minor, confirmed

- `provenance.py` (loci-cli): an unreadable `--source-root` subtree reports the
  *cap* reason ("reached its depth or file limit") because that is the only branch
  reachable for `onerror` truncation. Verdict is correct (`null`); the message sends
  the user to raise a limit that is not the problem.
- `provenance.py`: a located source whose `stat()` fails between the existence probe
  and the compare is dropped uncounted and unflagged — a fourth kind of
  named-but-uncompared source. Race window only; not deterministically reproducible.
- `provenance.py`: TI ticlang emits a CU named `__TI_internal` with no source file, so
  it lands in `primary_missing` and forces `stale: null` for any locally built TI CCS
  artifact. Null, not false — no invariant breach, but it makes the guard silent on a
  whole toolchain.
- `run_evals.sh`: the standard eval flow does not `cd`, so `claude -p` runs in the
  plugin repo and receives *two* session-context blocks — the installed hook's real
  one and the injected fixture one — describing different projects.
- `sd-6` is mostly a regression test for the asmslicer frame-sizing fix in another
  repo; only its `Artifact:` assertion is about this change. `sd-5` is a real
  regression test for it.
- `tests/unit/test_freshness_contract.py`: `_BASE_COMMIT` is a fixed SHA, so once this
  work merges "new in this change" quietly means "new since two changes ago". The
  guard also hard-fails in the published `loci-claude` mirror, whose squashed history
  cannot resolve that SHA (latent — no CI job runs pytest there today).
