"""Lint: skills take artifact paths from the compile they ran, never by assembling
them, and never invoke a compiler directly.

This is the property phase 02b establishes and phase 02c *depends on*: the object
layout is about to be keyed on the source's own path, so `modA/util.c` and
`modB/util.c` stop aliasing onto one `util.o`. Every skill that spells
`.loci-build/<loci_target>/<basename>.o` for itself silently measures the wrong file
the moment that lands — and four of them did. A prose rule alone cannot hold it: it
held for a while, then two withdrawn rounds put the constructed paths straight back.

Scope note. The *behaviour* now lives in `lib/compile-and-read-back.sh` and is tested
against a stubbed CLI in `test_compile_read_back.py`. What is left here is what only
a document can get wrong: naming a path, telling the model to run a compiler, or
failing to say where the paths come from.
"""

from __future__ import annotations

import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
SKILLS_DIR = PLUGIN_ROOT / "skills"
CONTRACT = SKILLS_DIR / "_shared" / "loci-runtime-contract.md"

# Skills that consume compile artifacts. `loci-preflight` is included: it does not
# read a baseline through the script, but it does compile, and it spelled the flat
# object and sidecar paths in two places.
ARTIFACT_CONSUMERS = (
    "loci-post-edit",
    "loci-preflight",
    "exec-trace",
)

# The skills whose Incremental Path used to invoke a compiler directly. `stack-depth`,
# `memory-report` and `control-flow` left this list when `analyse stack` / `analyse
# memory` / `analyse cfg` took the artifact ladder into the CLI: they compile nothing,
# resolve nothing, and name no object path at all, so there is no path for them to
# assemble wrongly.
# Measures a change through `loci analyse prepare`; must not type a compiler line.
INCREMENTAL_SKILLS = ("exec-trace",)

# ...which makes the *absence* of a path the property to hold for those three.
VERB_OWNED_SKILLS = ("stack-depth", "memory-report", "control-flow")

# An assembled LOCI object/sidecar path: EITHER build root + a target-like segment
# + a filename ending in `.o` or `.meta.json`.
#
# Both roots, because the layout moved (`.loci-build/` → `.loci/build/`) and a lint
# that knows only the old name is a lint that stops working the moment the prose is
# rewritten to the new one — which is exactly what the skill-slimming tasks are
# about to do. The rule is unchanged: never guess where the CLI put something it
# wrote. Only the spelling of "where" got a second form.
#
# It must NOT flag the legitimate mentions that remain:
#   * `<root>/dumps/…`       — where the CLI spills CFG/timing files, read by path
#   * `<root>/flags.json`, `<root>/cargo/` — user-facing config/cache
#   * `<root>/` alone         — prose about the directory
#   * `<output>.meta.json`    — the sidecar's relation to the envelope's own
#                               `output`, which is not an assembled path
#
# `.elf` is deliberately NOT matched. The rule is "never guess where the CLI put
# something it wrote", and `loci build compile` only ever writes objects — it passes
# `-c`. An `.elf` under `.loci-build/` is something the skill or the user *linked
# itself* (no `loci` verb links), so the skill chose that location and nothing
# resolves it from an envelope. `memory-report` says so at its link step, and
# Pattern B's B4 example names exactly such a path.
# The target segment may be a placeholder, a literal target, or a shell variable
# (`$loci_target`, `${T}`); the filename may be a placeholder, a shell substitution
# (`$(basename <source> .c)`), a variable, or a plain concrete stem. An independent
# mutation campaign slipped five spellings past a narrower version of this — including
# `$(basename <source> .c).o`, which is the canonical shell idiom for the very defect,
# and a flat concrete `blink.o.meta.json`. So anything under a target-like segment
# that ends in `.o` or `.meta.json` counts, whatever the middle looks like.
# Named once: three lints below need "either build root", and a second spelling of
# the alternation is the same defect this file exists to prevent, one level up.
BUILD_ROOT_RE = r"(?:\.loci-build|\.loci/build)"

ASSEMBLED_OBJECT_RE = re.compile(
    BUILD_ROOT_RE + r"/(?:objects/)?"
    r"(?:<[a-z_]+>|\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|aarch64|armv7e-m|armv6-m|tc399)/"
    # The filename part: ordinary characters, OR a `$(…)` command substitution, which
    # contains spaces and a `)` and so was excluded by a plain character class —
    # letting `$(basename <source> .c).o` through, the single most likely shell
    # spelling of this defect.
    r"(?:[^\s`)\]]|\$\([^)\n]*\))*\.(?:o|meta\.json)(?![A-Za-z])"
)


def _doc(name: str) -> tuple[Path, str]:
    path = SKILLS_DIR / name / "SKILL.md"
    return path, path.read_text(encoding="utf-8")


def _joined(text: str) -> str:
    """Shell line-continuations folded away — **inside fenced blocks only**.

    `[^\n]` cannot cross a newline, so both compiler screens were blind to the
    house style the contract's own preamble fence uses:

        arm-none-eabi-gcc <flags> <objects> -T <linker.ld> \\
            -o <binary>

    But a trailing backslash means two different things either side of a fence. In
    a shell it continues the command; in Markdown prose it is a **hard line
    break**, and review used exactly that: a sentence ending `…do not stop there:
    \\` followed by a command on the next line. Folding those into one line
    manufactured the proximity the negation exemption then honoured — the screen
    was defeated with its own normalisation. So the fold happens only where a
    backslash is shell syntax.
    """
    out: list[str] = []
    in_fence = False
    pending = False
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            pending = False
            out.append(line)
            continue
        if pending:
            out[-1] = out[-1] + " " + line.lstrip()
        else:
            out.append(line)
        body = out[-1].rstrip("\r\n")
        pending = in_fence and body.endswith("\\")
        if pending:
            out[-1] = body[:-1]
    return "".join(out)


def _rel(path: Path) -> str:
    # `as_posix()`: on Windows `relative_to` yields backslashes, so a set comparison
    # against the forward-slash labels these lints build by hand never matched.
    return path.relative_to(PLUGIN_ROOT).as_posix()


def test_no_skill_assembles_a_loci_build_artifact_path():
    """A path a skill builds from `<basename>` is a path that breaks silently the
    next time the layout changes."""
    offenders: list[str] = []
    for name in ARTIFACT_CONSUMERS:
        path, text = _doc(name)
        for m in ASSEMBLED_OBJECT_RE.finditer(text):
            lineno = text.count("\n", 0, m.start()) + 1
            offenders.append(f"  {_rel(path)}:{lineno} -> {m.group(0)}")

    assert not offenders, (
        "these skills assemble a LOCI artifact path instead of reading it back from "
        "the compile they ran. Phase 02c keys the object path on the source's own "
        "path, so every one of these measures the wrong file once it lands:\n"
        + "\n".join(offenders)
    )


def test_the_lint_would_catch_an_assembled_path_under_either_root():
    """A guard that cannot fire is not a guard.

    `test_no_skill_assembles_a_loci_build_artifact_path` passes today because no
    skill assembles one — which is indistinguishable from a regex that matches
    nothing at all, and the regex just grew an alternation. So drive it: the same
    defect written against each root has to be caught, and the mentions that are
    deliberately legal have to survive."""
    caught = [
        ".loci-build/armv6-m/blink.o",
        ".loci/build/objects/armv6-m/blink.o",
        ".loci/build/armv6-m/blink.o",
        ".loci/build/objects/<loci_target>/<basename>.o",
        ".loci/build/<loci_target>/<basename>.o",
        ".loci/build/$loci_target/$(basename <source> .c).o",
        ".loci/build/objects/armv7e-m/app_data.o.meta.json",
    ]
    for spelling in caught:
        assert ASSEMBLED_OBJECT_RE.search(spelling), (
            f"the lint no longer catches {spelling!r}")

    allowed = [
        ".loci/build/dumps/app_data-abc123/stack-analysis.json",
        ".loci/build/turns/2f1c/dumps/app_data-abc123/control-flow.txt",
        ".loci/build/flags.json",
        ".loci/build/cargo/",
        ".loci-build/elf/blink/relinked.elf",
        "the `.loci/build/` tree",
        "<output>.meta.json",
    ]
    for spelling in allowed:
        assert not ASSEMBLED_OBJECT_RE.search(spelling), (
            f"the lint now flags the legitimate {spelling!r}")


# The hooks are edge adapters: they map payload fields onto `loci` flags and read
# whatever the CLI reports back. A hook that spelled an object path for itself
# would be the skills' defect in the one place no skill lint looks — and unlike a
# skill, a hook has no model to notice the file is missing.
HOOK_SCRIPTS = ("post-edit-hook.sh", "post-bash-bypass.sh", "pre-edit-hook.sh", "turn-clean.sh",
                "draft-pending-nudge.sh", "session-init.sh", "contract-guard.sh",
                "ensure-loci-cli.sh",
                # PR #259: the turn stamp and the manifest nudge. Both are one
                # `loci` call each and spell no artifact path of their own.
                "prompt-submit-turn.sh", "manifest-status-nudge.sh",
                # The Stop impact flush, a `hooks.json` command string until it
                # needed a log line of its own. Same shape: one `loci` call.
                "stats-flush.sh")


def test_every_linted_hook_exists():
    """The list above is written by hand, so a renamed hook would silently drop
    out of the lint below rather than fail it."""
    missing = [n for n in HOOK_SCRIPTS
               if not (PLUGIN_ROOT / "hooks" / n).is_file()]
    assert not missing, f"hooks named in the lint are gone: {missing}"
    on_disk = {p.name for p in (PLUGIN_ROOT / "hooks").glob("*.sh")}
    assert on_disk == set(HOOK_SCRIPTS), (
        f"a hook is not covered by the lint: {sorted(on_disk - set(HOOK_SCRIPTS))}")


def test_no_hook_assembles_a_loci_build_artifact_path():
    offenders: list[str] = []
    for name in HOOK_SCRIPTS:
        path = PLUGIN_ROOT / "hooks" / name
        text = path.read_text(encoding="utf-8")
        for m in ASSEMBLED_OBJECT_RE.finditer(text):
            offenders.append(
                f"  {_rel(path)}:{text.count(chr(10), 0, m.start()) + 1}"
                f" -> {m.group(0)}")
    assert not offenders, (
        "these hooks assemble a LOCI artifact path instead of passing the project "
        "root and letting the CLI resolve it:\n" + "\n".join(offenders))


def test_the_verb_owned_skills_resolve_no_artifact_of_their_own():
    """`analyse stack` / `analyse memory` own the B1-B4 ladder now.

    A skill that still spells a compile, a `.prev` or an artifact path has two
    resolvers, and the one that loses is the one with the freshness gate in it.
    """
    offenders: list[str] = []
    for name in VERB_OWNED_SKILLS:
        path, text = _doc(name)
        for pattern in (r"compile-and-read-back", r"loci build compile",
                        r"loci build fresh", r"\.o\.prev", r"\bPREV_META\b"):
            for m in re.finditer(pattern, text):
                offenders.append(
                    f"  {_rel(path)}:{text.count(chr(10), 0, m.start()) + 1}"
                    f" -> {m.group(0)}")
    assert not offenders, (
        "these skills still resolve an artifact themselves; the verb does it, and "
        "two resolvers means the un-gated one can win:\n" + "\n".join(offenders))


def test_the_verb_owned_skills_call_the_verb_with_turn_and_caller():
    for name, verb in (("stack-depth", "loci analyse stack"),
                       ("memory-report", "loci analyse memory"),
                       ("control-flow", "loci analyse cfg")):
        text = _doc(name)[1]
        assert verb in text, f"{name} never calls `{verb}`"
        assert "--turn" in text and "--caller" in text, (
            f"{name} omits a flag the verb refuses without")


def test_the_incremental_skills_do_not_raw_compile():
    """A raw `<compiler> … -c … -o` writes no `.meta.json` sidecar, so the next
    `loci build snapshot` refuses outright and the turn loses its baseline. Worse, a
    sidecar left by an earlier `loci build compile` then describes a *different*
    object, and `loci build diff` compares the two sidecars, finds them identical,
    and reports `match: true` on a mismatched pair."""
    # Any compiler-ish leader, not just the literal `<compiler>` placeholder: a
    # concrete driver name (`arm-none-eabi-gcc -g -O2 -c x.c -o x.o`) survived the
    # narrower version, and that is the form a model is most likely to write.
    raw_compile = re.compile(
        r"(?:<compiler>"
        r"|\b(?:arm-none-eabi|aarch64-linux-gnu|tricore-elf)-g(?:cc|\+\+)"
        r"|\bg(?:cc|\+\+)\b|\bclang(?:\+\+)?\b|\biccarm\b|\barmclang\b)"
        r"[^\n`]*\s-c\s[^\n`]*-o\s",
        re.M)
    offenders: list[str] = []
    for name in INCREMENTAL_SKILLS:
        path, raw = _doc(name)
        text = _joined(raw)
        for m in raw_compile.finditer(text):
            # The prose that *forbids* the shape has to name it, so an exemption is
            # needed — but it must be a negation POSITIONED BEFORE the command, not
            # any occurrence of "never" anywhere on the line. A whole-line keyword
            # match let `<compiler> … -o out.o   # never mind the sidecar` through.
            line_start = text.rfind("\n", 0, m.start()) + 1
            line = text[line_start:text.find("\n", m.start())]
            before = line[:m.start() - line_start]
            if re.search(r"\bnot a raw\b|\bnever\b|\bdo not\b|\binstead of\b",
                         before, re.I):
                continue
            offenders.append(
                f"  {_rel(path)}:{text.count(chr(10), 0, m.start()) + 1}"
                f" -> {line.strip()[:90]}"
            )

    assert not offenders, (
        "these skills still invoke a compiler directly. Route it through "
        "`loci analyse prepare --source` so a sidecar is written and the object's "
        "real path comes back:\n" + "\n".join(offenders)
    )


#: The anchor in the shared contract that owns the three-step preamble. T12
#: replaced four byte-identical copies of it — `.o.prev` rule, invocation fence,
#: `key<TAB>value` table, `FAILED` branch — with a link to this.

#: A fenced block in **either** CommonMark fence style. Round 1 of T12's review
#: pasted the whole invocation back into `control-flow` inside a `~~~` fence and
#: every invocation lint stayed green — while `_split_sections` in the same test
#: package had toggled on `~~~` for years, so the repo already knew tilde fences
#: render here. One spelling now, used by every fence scan in this file.
FENCE_RE = re.compile(r"^[ \t]*(?:```|~~~)[a-z]*\n(.*?)^[ \t]*(?:```|~~~)",
                      re.S | re.M)


def _fences(text: str) -> list[str]:
    return FENCE_RE.findall(text)


def test_the_fence_scanner_sees_both_commonmark_fence_styles():
    """A guard that reads one fence style is a guard one character wide.

    Driven rather than assumed: the tilde form is what defeated three lints in
    review, so it is asserted here directly, and the indented form because these
    recipes sit inside numbered list items.
    """
    for style in ("```", "~~~"):
        assert _fences(f"text\n{style}\nbash x.sh --source y\n{style}\nmore"), (
            f"the scanner cannot see a {style} fence")
        assert _fences(f"1. item\n   {style}\n   bash x.sh\n   {style}\n"), (
            f"the scanner cannot see an indented {style} fence")


#: A compiler invoked to produce a BINARY — a link, not a compile. `-c` is
#: deliberately absent from this pattern, which is the whole point:
#: `test_the_incremental_skills_do_not_raw_compile` requires ` -c ` between the
#: driver and `-o`, so it is blind to a link line by construction, and the four
#: skills' Full Compilation Paths were exactly link lines. Review restored
#: `<compiler> <flags> -o <binary> <source>` to `exec-trace` and every lint in the
#: repo stayed green — a binding design constraint ("the raw `<compiler> <flags>
#: -o` lines → rebuild via the recipe's `full_build`") with nothing behind it.
_RAW_LINK = re.compile(
    r"(?:<compiler>|\$\{?CC\}?|\$\(CC\)"
    r"|\b(?:arm-none-eabi|aarch64-linux-gnu|tricore-elf)-(?:g(?:cc|\+\+)|ld)"
    r"|\bg(?:cc|\+\+)\b|\bclang(?:\+\+)?\b|\biccarm\b|\barmclang\b"
    r"|\bld\b|\bcc\b|\bc\+\+\b|\barmlink\b|\bilinkarm\b)"
    # Backticks are ordinary in this prose (`-o` is written in code spans), so
    # they cannot end the run — only a newline can, and `_joined` has already
    # folded continuations into one line by the time this runs.
    r"[^\n]{0,200}?\s-o\s",
    re.M)


def test_no_skill_links_a_binary_with_a_compiler_line():
    """No `loci` verb links, and the recipe records no link line — so a skill that
    spells one is telling the model to invent the command the design says it may
    not invent, and to produce a binary whose flags nothing vouches for.

    Scoped to the skills that build (the four Pattern-B ones plus the two that
    compile). The exemption is the same shape as the raw-compile lint's: prose that
    FORBIDS the line has to be able to name it, so a negation positioned before the
    command on the same line is allowed.
    """
    offenders: list[str] = []
    for name in ARTIFACT_CONSUMERS:
        path, raw = _doc(name)
        text = _joined(raw)
        for m in _RAW_LINK.finditer(text):
            line_start = text.rfind("\n", 0, m.start()) + 1
            eol = text.find("\n", m.start())
            line = text[line_start:eol if eol != -1 else len(text)]
            before = line[:m.start() - line_start]
            # The exemption must cover how a PROHIBITION is written and nothing
            # else. `without`, `is not` and `are not` were on this list and are
            # ordinary English: review exempted a plain instruction to hand-link
            # with "Reproduce the image **without** the recipe: <compiler> … -o".
            # They are gone. What remains is explicit refusal vocabulary, and it
            # must sit within ~90 characters of the command rather than anywhere
            # earlier on a line — an unbounded window is how a fold, or a long
            # sentence, lends its negation to a command that has none.
            near = before[-90:]
            if re.search(r"\bnot a raw\b|\bnever\b|\bdo not\b|\bdon't\b"
                         r"|\binstead of\b|\brather than (?:assembl|writ|invent)"
                         r"|\bno `?loci`? verb links\b|\bmay not\b"
                         r"|\bis not something you may\b|\bforbidden\b",
                         near, re.I):
                continue
            offenders.append(
                f"  {_rel(path)}:{text.count(chr(10), 0, m.start()) + 1}"
                f" -> {line.strip()[:90]}")
    assert not offenders, (
        "these skills spell a compiler line that produces a binary. The build is "
        "the recipe's `build.full_build`, and where the recipe has none the answer "
        "is to say so — not to assemble a link:\n" + "\n".join(offenders))


def test_the_raw_link_lint_would_catch_the_line_it_exists_for():
    """It passes today because no skill spells one, which is indistinguishable
    from a pattern that matches nothing. Drive both directions."""
    for spelling in ("<compiler> <flags> -o <binary> <source>",
                     "arm-none-eabi-gcc -g -O0 -Wl,-T,x.ld a.c -o kernel.elf",
                     "clang++ -target aarch64 main.cpp -o app.elf",
                     "arm-none-eabi-ld -T fixture.ld a.o -o app.elf",
                     "$(CC) $(LDFLAGS) objs -o firmware.elf",
                     "cc main.c -o a.out"):
        assert _RAW_LINK.search(spelling), f"the lint misses {spelling!r}"
    # The folded form, which is how the house style writes it — INSIDE a fence,
    # because that is the only place a trailing backslash is shell syntax.
    fenced = ("```\n"
              "arm-none-eabi-gcc <flags> <objects> -T <linker.ld> \\\n"
              "    -o <binary>\n"
              "```\n")
    assert _RAW_LINK.search(_joined(fenced)), (
        "a backslash continuation inside a fence still hides a link line")
    # …and OUTSIDE a fence the same backslash is a Markdown hard break, so folding
    # it would manufacture the proximity the negation exemption honours. Review
    # defeated both compiler screens exactly that way.
    prose = ("Where the recipe records no `full_build`, this is not a guess: \\\n"
             "`arm-none-eabi-gcc <flags> <objects> -o <binary>` is the link.\n")
    folded = _joined(prose)
    assert "\n" in folded.rstrip("\n"), (
        "a hard line break in prose is being folded into one line, which lends "
        "the first line's negation to a command on the second")
    for legal in ("loci build compile --source x.c --loci-target armv6-m",
                  "`build.full_build` out of the recipe",
                  "loci elf memmap --elf <PREV> --comparing-elf <OBJ>"):
        assert not _RAW_LINK.search(legal), f"the lint flags the legal {legal!r}"


def test_every_consumer_names_where_its_paths_come_from():
    """Five skills were rewritten to stop assembling paths; each has to say what
    replaced that, or the rewrite reads as an unexplained deletion and the next
    editor puts the flat path back. `loci-preflight` satisfies this by naming the
    script to explain why it is the one that does NOT use it; `loci-post-edit` by
    naming `loci analyse prepare`, whose envelope is where its paths come from."""
    missing = [
        name for name in ARTIFACT_CONSUMERS
        if "compile-and-read-back" not in _doc(name)[1]
        and "loci analyse prepare" not in _doc(name)[1]
    ]
    assert not missing, (
        "these skills consume compile artifacts but never say where their paths "
        "come from (compile-and-read-back or loci analyse prepare): " + ", ".join(missing)
    )


def test_no_skill_tells_the_model_to_pass_meta_prev():
    """`--meta-prev` names a pre-edit sidecar, so passing it by hand means building
    exactly the path these skills may not build — and on a cargo crate it overrides
    the CLI's own refusal to offer a pair whose recorded package or target differs.
    Choosing whether to pass it is the script's job, which knows which CLI it has."""
    offenders: list[str] = []
    for name in ARTIFACT_CONSUMERS:
        path, text = _doc(name)
        for m in re.finditer(r"^.*--meta-prev.*$", text, re.M):
            line = m.group(0)
            # The exemption must be a negation OF PASSING IT, not any negation
            # anywhere before it. `If the CLI does not report a pair, pass
            # --meta-prev "<prev>" yourself.` satisfied both a whole-line match on
            # "not" and a looser before-the-flag match on "does not", while
            # instructing the exact opposite. So the verb has to be in the pattern.
            before = line[:line.index("--meta-prev")]
            if re.search(r"\b(?:never|do not|don't|stop|no longer|without)\s+"
                         r"(?:\w+\s+){0,2}?(?:pass|passing|name|naming|supply|"
                         r"supplying|construct|constructing|build|building)\b",
                         before, re.I):
                continue    # prose forbidding it
            offenders.append(
                f"  {_rel(path)}:{text.count(chr(10), 0, m.start()) + 1}"
                f" -> {line.strip()[:90]}"
            )
    assert not offenders, (
        "these skills instruct the model to pass --meta-prev itself:\n"
        + "\n".join(offenders)
    )


_VALUELESS_FLAGS: frozenset[str] = frozenset()




def test_no_shipped_fence_drives_the_leaf_verbs_the_pair_replaced():
    """Two routes used to be prose-driven leaf calls: post-edit's header route
    (`build affected`, the script with `--reconstruct`) and exec-trace's whole run
    (`elf asm`, `elf diff`, `loci timing`, `build fresh`, `stats measure`). Both are
    `prepare` → `measure` now; a fence that sends a model back to a leaf is the
    ping-pong returning."""
    banned = ("loci build affected", "--reconstruct", "--baseline", "loci timing",
              "loci elf asm", "loci build fresh", "stats measure", "compile-and-read-back")
    for name in ("loci-post-edit", "exec-trace"):
        path, text = _doc(name)
        for fence in _fences(text):
            for token in banned:
                assert token not in fence, f"{_rel(path)}: a fence invokes `{token}`"
    skill = _doc("loci-post-edit")[1]
    assert "data.headers" in skill and "data.units" in skill, (
        "post-edit's Step 0b no longer reads the header account `prepare` returns")
    assert 'id="header-edits"' in CONTRACT.read_text(encoding="utf-8") and "#header-edits" in skill


def test_exec_trace_runs_the_pair_and_nothing_else():
    text = _doc("exec-trace")[1]
    verbs = {m.group(1) for f in _fences(text)
             for m in re.finditer(r"\bloci (analyse \w+|stats \w+|\w+ \w+)", f)}
    # `stats record` left the fences in 051 — the call is written once in
    # `_shared/verdicts.md` and this skill links to it — so it is checked as a link
    # rather than as a fence. The property here is unchanged: no OTHER verb.
    assert verbs == {"analyse prepare", "analyse measure"}, verbs
    assert "verdicts.md#recording-the-verdict" in text, (
        "exec-trace no longer routes to the shared recording section, so its verdicts "
        "and its sentence never reach the record")
    for token in ("--elf", "--functions", "fabricated", "data.not_found",
                  "Artifact provenance (mandatory)"):
        assert token in text, f"exec-trace lost {token!r}"


def test_no_skill_reads_a_bare_exit_number_as_stale():
    """`measure` exited 3 on a stale manifest and 4 on a bad `--select` — the same
    numbers as `auth_required` and `quota_exceeded`. A skill told "3 → re-run
    prepare" looped on an expired login. Stale/invalid are 6/7 with `error.code`s
    now, and every skill branches on the code before the number."""
    for name in ("loci-post-edit", "loci-preflight", "exec-trace"):
        path, text = _doc(name)
        assert "manifest_stale" in text, f"{_rel(path)} never names error.code manifest_stale"
        assert "Branch on `$?` first" not in text and \
               "Branch on `$?` before you parse anything" not in text, (
            f"{_rel(path)} still branches on the exit number before the envelope")
        for stale_as_3 in re.finditer(r"(\| `3` \|[^\n]*[Ss]tale|`3` the tree moved)", text):
            raise AssertionError(f"{_rel(path)}: {stale_as_3.group(0)!r}")
    contract = CONTRACT.read_text(encoding="utf-8")
    assert "manifest_stale" in contract and "exit 6" in contract


# ── the legacy build root in prose ───────────────────────────────────────────
#
# The CLI writes under `.loci/build/` since the layout moved, read `.loci-build/`
# beside it for one soak, and since T14 (Phase 4) reads nothing there: the
# directory is a pre-move CLI's litter. Prose that names it as a place anything
# is read from or written to is wrong — and the merge of the pair branch once put
# two such sentences back (post-edit's `analyse cfg` out-dir, the contract's
# bug-report plumbing line) after the lint that would have noticed them went out
# with Pattern B. So: every `.loci-build` in shipped prose must sit in a sentence
# that is ABOUT the legacy root being unread. The allow-list is by phrase rather
# than by file so a sentence that changes has to be re-read.

LEGACY_ROOT_MENTIONS = {
    # bug-report: the turn-tree walk names the one root and says what the other is
    "A `.loci-build/` beside it is a pre-move CLI's\n    leftover; nothing reads it",
    # bug-report check 7: the artifact glob does not descend into it
    "A `.loci-build/` beside it is a pre-move CLI's leftover and is not searched",
    # the contract: what to tell a user whose `loci` predates the `set` verb —
    # THAT CLI reads only the pre-move location, and the sentence is about it
    "`.loci-build/flags.json`, not `.loci/build/flags.json`",
}


def test_the_legacy_root_is_named_only_where_the_sentence_is_about_it():
    offenders: list[str] = []
    for path in sorted(SKILLS_DIR.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"\.loci-build", text):
            window = text[max(0, m.start() - 90): m.end() + 90]
            flat = window.replace("\r\n", "\n")
            if any(phrase in flat for phrase in LEGACY_ROOT_MENTIONS):
                continue
            line = text.count("\n", 0, m.start()) + 1
            offenders.append(f"  {_rel(path)}:{line} -> …{' '.join(window.split())[:120]}…")
    assert not offenders, (
        "`.loci-build` named in prose that is not about the legacy root -- the CLI "
        "writes under `.loci/build/` now; spell that, or register the sentence in "
        "LEGACY_ROOT_MENTIONS after reading it:\n" + "\n".join(offenders))


def test_every_registered_legacy_mention_still_exists():
    """The allow-list must not outlive the sentences it allows."""
    corpus = "\n".join(p.read_text(encoding="utf-8").replace("\r\n", "\n")
                       for p in sorted(SKILLS_DIR.rglob("*.md")))
    stale = sorted(p for p in LEGACY_ROOT_MENTIONS if p not in corpus)
    assert not stale, f"registered legacy-root sentences no longer exist: {stale}"
