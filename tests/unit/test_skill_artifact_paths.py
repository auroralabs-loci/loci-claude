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
SCRIPT_REL = "lib/compile-and-read-back.sh"

# Skills that consume compile artifacts. `loci-preflight` is included: it does not
# read a baseline through the script, but it does compile, and it spelled the flat
# object and sidecar paths in two places.
ARTIFACT_CONSUMERS = (
    "loci-post-edit",
    "loci-preflight",
    "exec-trace",
    "control-flow",
    "stack-depth",
    "memory-report",
)

# The four whose Incremental Path used to invoke a compiler directly.
INCREMENTAL_SKILLS = ("exec-trace", "control-flow", "stack-depth", "memory-report")

# An assembled LOCI object/sidecar path: `.loci-build/` + a target-like segment + a
# filename ending in `.o` or `.meta.json`. It must NOT flag the legitimate mentions
# that remain:
#   * `.loci-build/elf/…`     — where the CLI spills CFG/timing files, read by path
#   * `.loci-build/flags.json`, `.loci-build/cargo/` — user-facing config/cache
#   * `.loci-build/` alone    — prose about the directory
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
ASSEMBLED_OBJECT_RE = re.compile(
    r"\.loci-build/"
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


def _rel(path: Path) -> str:
    # `as_posix()`: on Windows `relative_to` yields backslashes, so a set comparison
    # against the forward-slash labels these lints build by hand never matched.
    return path.relative_to(PLUGIN_ROOT).as_posix()


def test_the_script_the_skills_point_at_exists_and_is_executable():
    """Six documents name this path. A rename that misses them turns every
    Incremental Path into `bash: no such file`."""
    script = PLUGIN_ROOT / SCRIPT_REL
    assert script.is_file(), f"{SCRIPT_REL} is missing but skills invoke it"
    assert script.read_text(encoding="utf-8").startswith("#!"), (
        "the script has no shebang"
    )


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
        path, text = _doc(name)
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
        "these skills still invoke a compiler directly for the Incremental Path. "
        f"Route it through {SCRIPT_REL} so a sidecar is written and the object's "
        "real path comes back:\n" + "\n".join(offenders)
    )


def test_the_incremental_skills_invoke_the_script():
    """Naming the contract section is not enough — the skill has to run the thing.
    The withdrawn rounds each described the right idea and then pasted a compiler
    line underneath it."""
    missing = [n for n in INCREMENTAL_SKILLS if SCRIPT_REL not in _doc(n)[1]]
    assert not missing, (
        f"these skills never invoke {SCRIPT_REL}: " + ", ".join(missing)
    )
    also = "loci-post-edit"
    assert SCRIPT_REL in _doc(also)[1], f"{also} never invokes {SCRIPT_REL}"


def test_every_consumer_names_where_its_paths_come_from():
    """Five skills were rewritten to stop assembling paths; each has to say what
    replaced that, or the rewrite reads as an unexplained deletion and the next
    editor puts the flat path back. `loci-preflight` satisfies this by naming the
    script to explain why it is the one that does NOT use it."""
    missing = [
        name for name in ARTIFACT_CONSUMERS
        if "compile-and-read-back" not in _doc(name)[1]
    ]
    assert not missing, (
        "these skills consume compile artifacts but never mention "
        "compile-and-read-back: " + ", ".join(missing)
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


def test_the_contract_anchor_the_skills_link_to_still_exists():
    text = CONTRACT.read_text(encoding="utf-8")
    assert 'id="compile-and-read-back"' in text, (
        "the compile-and-read-back anchor is gone from the runtime contract; six "
        "SKILL.md files reference it"
    )


def test_the_contract_says_values_do_not_cross_fences():
    """Every fenced block the model runs is a separate Bash call, so nothing the
    script sets survives into the next one — which is why it prints. Two withdrawn
    rounds shipped skills that set `$OBJ` in one fence and *branched on it* in
    another, where nothing had ever printed the value."""
    text = CONTRACT.read_text(encoding="utf-8")
    body = text[text.index('id="compile-and-read-back"'):]
    # `\**` tolerates the markdown emphasis the prose actually carries
    # (`a *separate* Bash call`); spelling the phrase without it made an earlier
    # version of this assertion unsatisfiable, which is how that was caught.
    assert re.search(r"separate\**\s+Bash call", body), (
        "the contract no longer warns that values do not survive between fenced "
        "blocks — the defect that withdrew phase 02 twice"
    )


def test_the_contract_does_not_paste_optional_syntax_into_a_command():
    """`[--phase <phase>]` inside a runnable block reaches argparse verbatim:
    `unrecognized arguments: [--phase post-edit]`, exit 2, and no envelope to
    explain it. Optionality belongs in prose, not in the command."""
    text = CONTRACT.read_text(encoding="utf-8")
    body = text[text.index('id="compile-and-read-back"'):]
    fences = re.findall(r"^```\n(.*?)^```$", body, re.S | re.M)
    assert fences, "the recipe section has no fenced command"
    offenders = [
        line.strip()
        for fence in fences[:1]
        for line in fence.splitlines()
        if re.search(r"\[--[a-z-]+|<[a-z-]+\|[a-z-]+>", line)
    ]
    assert not offenders, (
        "the invocation contains optional-syntax brackets or an alternation that a "
        "model will paste verbatim into a shell:\n  " + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# The documented invocation itself
#
# Five skills retype this command, so the *document's* syntax is as load-bearing as
# the script's code — and it was unguarded. An independent campaign deleted the
# quotes around every value, deleted `--project-root` outright, and left `--turn`
# with no value, and all nine lints stayed green. The last of those lands on a
# real hang, and unquoting `<source>` re-creates the original phase-02 defect one
# level up: with a Windows-normal root the model writes
# `--source /c/Users/First Last/proj/blink.c` and the script's own parser answers
# `FAILED - unknown argument: Last/proj/blink.c`.
# ---------------------------------------------------------------------------

# Values that are file paths or free-form tokens: a space in any of them is ordinary
# on Windows, so each must be quoted wherever it is shown.
_MUST_QUOTE = ("<source>", "<project-context>", "<project_root>", "<turn-id>",
               "<plugin-dir>")

# Flags the script needs on every call. `--turn` is deliberately absent: it is
# legitimately omitted when there is no id.
_REQUIRED_FLAGS = ("--source", "--loci-target", "--project-root")


def _invocation_fences() -> list[tuple[str, str]]:
    """Every fenced block that invokes the script, across the contract and the skills
    that call it. Returns (label, fence-text)."""
    out: list[tuple[str, str]] = []
    docs = [(_rel(CONTRACT), CONTRACT)] + [
        (f"skills/{n}/SKILL.md", SKILLS_DIR / n / "SKILL.md")
        for n in ("loci-post-edit", *INCREMENTAL_SKILLS)
    ]
    for label, path in docs:
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"```[a-z]*\n(.*?)^\s*```", text, re.S | re.M):
            if SCRIPT_REL in m.group(1):
                out.append((label, m.group(1)))
    return out


def test_every_documented_invocation_exists_and_is_found():
    """A guard that scans nothing passes for the wrong reason. Six documents call the
    script; if the fence-matching regex or the tag convention drifts, say so here
    rather than silently linting an empty list."""
    fences = _invocation_fences()
    labels = {label for label, _ in fences}
    expected = {"skills/_shared/loci-runtime-contract.md"} | {
        f"skills/{n}/SKILL.md" for n in ("loci-post-edit", *INCREMENTAL_SKILLS)
    }
    assert labels == expected, (
        f"the invocation was not found in every caller. found={sorted(labels)} "
        f"expected={sorted(expected)}"
    )


def test_every_documented_invocation_quotes_its_path_values():
    offenders: list[str] = []
    for label, fence in _invocation_fences():
        for token in _MUST_QUOTE:
            for m in re.finditer(re.escape(token), fence):
                lo = fence.rfind("\n", 0, m.start()) + 1
                line = fence[lo:fence.find("\n", m.start())]
                # Quoted if the placeholder is inside a double-quoted run on its line.
                quoted = re.search(r'"[^"\n]*' + re.escape(token) + r'[^"\n]*"', line)
                if not quoted:
                    offenders.append(f"  {label}: {line.strip()[:88]}")
    assert not offenders, (
        "these documented commands leave a path value unquoted, so any project or "
        "user directory containing a space splits into two arguments — ordinary on "
        "Windows (`C:\\Users\\First Last\\…`):\n" + "\n".join(sorted(set(offenders)))
    )


def test_every_documented_invocation_passes_the_required_flags():
    offenders: list[str] = []
    for label, fence in _invocation_fences():
        missing = [f for f in _REQUIRED_FLAGS if f not in fence]
        if missing:
            offenders.append(f"  {label}: missing {', '.join(missing)}")
    assert not offenders, (
        "these documented commands omit a flag the script needs. Without "
        "`--project-root` the CLI falls back to the shell's own directory, so a "
        "skill whose shell sits anywhere else writes a second `.loci-build/` tree "
        "and reports the real baseline as absent:\n" + "\n".join(offenders)
    )


# Flags of `compile-and-read-back.sh` that genuinely take NO value. Named explicitly
# rather than inferred, and deliberately short: this list is the only thing standing
# between the lint and the defect it exists for, so every entry has to be checked
# against the script's own `case` arm. A flag listed here that really does take a
# value gets its broken spelling blessed in prose — which is precisely the half-edit
# that hung the script forever with zero bytes on stdout and stderr.
_VALUELESS_FLAGS = frozenset({"--reconstruct"})


def test_the_valueless_flags_really_are_valueless_in_the_script():
    """The exemption above is only safe while it matches the script. A `case` arm
    that shifts twice takes a value, and one that shifts once does not."""
    text = (PLUGIN_ROOT / SCRIPT_REL).read_text(encoding="utf-8")
    for flag in _VALUELESS_FLAGS:
        m = re.search(re.escape(flag) + r"\)[^\n]*", text)
        assert m, f"{flag} is exempted from the value lint but the script has no arm for it"
        assert "shift 2" not in m.group(0), (
            f"{flag} is exempted from the value lint but its arm shifts twice, so it "
            f"DOES take a value: {m.group(0).strip()}"
        )
        assert "need_value" not in m.group(0), (
            f"{flag} is exempted from the value lint but its arm requires a value"
        )


def test_no_documented_invocation_leaves_a_flag_without_a_value():
    """A trailing flag used to hang the script forever with no output at all. It now
    answers `FAILED  -  <flag> needs a value`, but a document that ships the broken
    call is still shipping a failure."""
    offenders: list[str] = []
    for label, fence in _invocation_fences():
        joined = re.sub(r"\\\n\s*", " ", fence)
        for line in joined.splitlines():
            if SCRIPT_REL not in line and not line.strip().startswith("--"):
                continue
            toks = line.split()
            for i, tok in enumerate(toks):
                if not tok.startswith("--") or tok in _VALUELESS_FLAGS:
                    continue
                nxt = toks[i + 1] if i + 1 < len(toks) else None
                if nxt is None or nxt.startswith("--"):
                    offenders.append(f"  {label}: {tok} has no value in: {line.strip()[:80]}")
    assert not offenders, (
        "a documented command leaves a flag with no value:\n" + "\n".join(offenders)
    )


def test_the_header_route_ships_a_copyable_reconstruct_invocation():
    """The one fenced command in shipped prose that spells `--reconstruct`.

    Everything about the header route is prose a model retypes, and the campaign
    found that half unguarded: deleting `--reconstruct` from this fence, deleting
    Step 0b's heading, and changing "at most three" to "at most thirty" all left the
    suite green. A model following the fence without `--reconstruct` compiles the
    translation unit with no Before at all and reports an After-only measurement —
    which looks exactly like a header edit that changed nothing.

    `--turn` rides with it because the CLI's `--baseline` refuses without one, so a
    fence carrying the flag but not the id documents a call that cannot work."""
    text = CONTRACT.read_text(encoding="utf-8")
    fences = [f for _label, f in _invocation_fences() if "--reconstruct" in f]
    assert fences, (
        "no documented invocation passes --reconstruct, so the header route has no "
        "copyable spelling anywhere in shipped prose"
    )
    for fence in fences:
        assert "--turn" in fence, (
            "a --reconstruct fence without --turn documents a call the CLI refuses: "
            "the pre-edit copies are stored per turn"
        )
    assert "#header-edits" in text, (
        "the anchor the compile section and the post-edit skill both link to is gone"
    )
