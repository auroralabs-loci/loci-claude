# Go and TinyGo projects

Read this when Step 2 reports `.data.build_system` as `go`. (It is never
`tinygo` there — `detect_build_system` answers `go` for every module, and
`tinygo` is written into the recipe only at the end of init, from what the
module imports. `.data.go.tinygo` is the flag that says which it will be.) It
replaces `compdb.md`, which does not apply: a Go project has no compile
database and never will.

A Go recipe is unlike every other one: its recorded build command **is** the whole
build. LOCI writes that command rather than recording one the project already runs, so
init both proposes it and proves it — running it once and reading the ISA back out of
the ELF it produced. What lands is `.loci/build/objects/<target>/<name>.elf` plus Go's own
build cache, inside LOCI's gitignored output directory.

Two things belong in what you tell the user after a Go init:

- **Which tool, and which board.** `go` and `tinygo` are different recipes. Plain
  `go` reaches `aarch64` only — Go's 32-bit `arm` port is A-profile and LOCI's 32-bit
  targets are M-profile, so there is no mapping between them. Cortex-M needs TinyGo,
  whose **board** decides the ISA (`pca10040` is `armv7e-m`, `microbit` is `armv6-m`).
  If the probe found TinyGo imports but no board, `.data.candidates` offers both
  Cortex-M targets and the user picks. Better, name the board with
  `loci init set go.tinygo_target=<board>`. Then re-derive with
  `loci init --refresh`, since `tinygo targets` lists every board it knows.
- **Inlining.** `.data.notes` carries the sentence about Go inlining
  small functions out of the binary entirely — an edit to one then shows up as a
  change to its caller. Relay it once here rather than leaving the first measurement
  to discover it. If you relay the `go.gcflags=-l` remedy, relay the
  `loci init --refresh` that has to follow it (a Go recipe freezes its knobs into the
  recorded command), and say what it costs: the figures then describe code the user's
  release build does not contain.

**No compile database is involved at any point.** The go tool is handed a package and
resolves its own units, so nothing in `compdb.md` applies and no `bear` /
`CMAKE_EXPORT_COMPILE_COMMANDS` / regeneration advice is ever the answer for a Go
project. A Go build failure is in the recorded command, which the envelope echoes.
