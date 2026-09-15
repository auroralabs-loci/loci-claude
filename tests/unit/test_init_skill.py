"""Lint: `/loci:init`'s prose, pinned where the model actually reads it.

`skills/init/` is four files: `SKILL.md` (the flow and every decision — the only one
that loads on every invocation) plus `bootstrap.md`, `compdb.md` and `recovery.md`,
which load only when a step sends the reader to them. Nothing executes any of it, so
these are the only tests it has.

**Every rule is pinned to the section that owns it, over a body with the frontmatter
and HTML comments stripped.** The first version of this file used whole-file
substring checks, and a hostile campaign applied *eleven* independent breakages at
once — deleting the `/tmp` prohibition, commenting out the whole Step 0 bootstrap,
inverting the headless rule, replacing the evidence budget with a compiler ladder —
with the entire prose-lint suite still green, because each pinned phrase was still
findable *somewhere*: in a comment, in the frontmatter, in an appendix, or in a decoy
table row above the real one. A keyword that can hide anywhere pins nothing.

Five consequences, all load-bearing:

* `_body()` strips frontmatter and `<!-- -->`, **including an unterminated `<!--`**,
  which comments out the remainder of a markdown file. A rule moved somewhere inert
  is a rule that is gone.
* `_section()` finds the rule inside the step that owns it. A window anchored to the
  text it is checking moves *with* it, which is how a commented-out bootstrap passed
  five assertions.
* `_row()` requires **exactly one** matching table row, because `next(...)` takes the
  first — and a compliant decoy above an inverted real one defeated three tests.
* `_bullet()` flattens a wrapped list item. `_row` returned only its first line, so
  assertions about the rest of a bullet passed because the text was not in the
  fragment being searched.
* `CONTRADICTIONS` lists, per section, the escape-hatch wordings a campaign used to
  reverse a rule while keeping every word the positive pattern looked for. Negation
  is an axis no keyword lint sees on its own.

The CLI cross-checks come from the **real parser**: `loci init [verb] --help`'s usage
block gives the flags each verb accepts and the `choices` each takes, which is the
only way to catch a flag used on the wrong subcommand or a value argparse rejects.
Source-level checks (`SET_KEYS`, `Seed.usable`, the envelope literals) read the
checkout. Both skip only when neither is available; a *configured but unusable* one
FAILS, because a silent skip is how the allowlists rot.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest


PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
SKILL_DIR = PLUGIN_ROOT / "skills" / "init"
SKILL = SKILL_DIR / "SKILL.md"
BOOTSTRAP = SKILL_DIR / "bootstrap.md"
COMPDB = SKILL_DIR / "compdb.md"
RECOVERY = SKILL_DIR / "recovery.md"
# T15. The Go counterpart of `compdb.md` — a language-specific procedure reached
# from Step 2, and the file `compdb.md` is skipped FOR. In `DOCS` so every prose
# screen in this file applies to it: a doc outside this tuple is a doc no flag
# check, verb check or contradiction screen ever reads.
GO = SKILL_DIR / "go.md"
# The messaging contract: what the skill PRINTS, from the recognition line to the
# close-out. In `DOCS` for the same reason `go.md` is — prose a model executes, so
# every screen in this file reads it — and read once at Step 1, because the lines it
# governs are produced by four different steps.
VOICE = SKILL_DIR / "voice.md"
DOCS = (SKILL, BOOTSTRAP, COMPDB, RECOVERY, GO, VOICE)
DETECT = PLUGIN_ROOT / "lib" / "detect-project.sh"
INSTALLER_REL = "hooks/ensure-loci-cli.sh"

#: Bytes for `SKILL.md` alone, LF normalised UP to CRLF so the number is the same on
#: both platforms and never an under-count (a fresh checkout is CRLF: global
#: `core.autocrlf=true`, and `.gitattributes` pins only `*.sh`).
#:
#: The task asks for ~10 KB in Scope and ~12 KB in acceptance criterion 3. **This is
#: 15 KiB and the criterion is NOT met** — the completion log records it as the one
#: open deviation, for Vladimir to rule on, with this accounting:
#:
#: Two hostile-review rounds (five reviewers) produced 9 CRITICAL and ~29 MAJOR
#: findings, most of them missing *rules* rather than wrong wording — the
#: stale-database gate, the compile-database coverage gate, `--project-root` on every
#: call, the stale-CLI branch, the escrow check on an existing recipe, the headless
#: confirmation rule, the `--accept-tier` decision, the transient-vs-permanent
#: distinction, the `arch_mismatch` four-cause routing, the Step 4 error branch. Each
#: was reproduced on a real tree by a reviewer who executed the prose.
#:
#: Every *procedure* that can live out of line already does: `bootstrap.md`,
#: `compdb.md` and `recovery.md` load only when a step links to them, which is what
#: the budget's stated purpose ("it loads only when invoked") actually asks for. What
#: remains inline is gates and decisions. A reviewer itemised ~650 bytes of genuinely
#: duplicated explanation and all of it has been moved or deduplicated.
#:
#: For scale: this repo's `loci-post-edit/SKILL.md` is 61 KB and the shared runtime
#: contract is 70 KB. The number below is a tripwire against this skill becoming the
#: detection prose the initiative deletes — **not** a claim that the criterion is met.
#:
#: It has moved six times (12 800 → 15 360 → 16 384 → 16 640 → 16 896 → 17 664), once
#: per review round.
#: The first three admitted rules a reviewer had just reproduced a wrong outcome
#: without; the fourth is Step 4's `loci cockpit` close-out line, which the owner asked
#: for and which is a report instruction rather than procedure, so it belongs here and
#: not in a reference file.
#: The fifth is Step 0's CLI-pin gate: three lines saying that a CLI behind
#: `LOCI_CLI_VERSION` is the one state routing through `/loci:setup` first — a gate,
#: which is what this file keeps inline, while the procedure (how to read both numbers,
#: and the floor rule that spares a newer CLI) went to `bootstrap.md`.
#: The sixth is the onboarding messaging change: Step 1 says what it recognised before
#: any build-system talk, and Step 4 orders what it prints — readiness line, then every
#: caveat, then the setup. Both are decisions about what the user is told, which is why
#: they are here; the vocabulary, the checklist shape and the close-out rules went to
#: `voice.md`, several times what they would have cost inline.
#: Recording that rather than quietly restating the latest
#: figure: a budget an author keeps raising is evidence about the budget, and the
#: completion log escalates it as the one unmet acceptance criterion rather than
#: treating the constant as the answer.
SIZE_BUDGET = 17 * 1024 + 256

#: Terminated comments, and an unterminated one, which hides the rest of the file.
_COMMENTS = (re.compile(r"<!--.*?-->", re.S), re.compile(r"<!--.*\Z", re.S))


def _raw(path: Path = SKILL) -> str:
    return path.read_text(encoding="utf-8")


def _frontmatter(path: Path = SKILL) -> str:
    text = _raw(path)
    assert text.startswith("---"), f"{path.name} must open with YAML frontmatter"
    end = text.find("\n---", 3)
    assert end != -1, f"{path.name}: unterminated YAML frontmatter"
    return text[3:end]


def _body(path: Path = SKILL) -> str:
    """The instructions, and nothing that only looks like them.

    Frontmatter is metadata, not a step; an HTML comment renders nowhere. A rule
    relocated into either is a rule the model does not act on — and a campaign used
    both to satisfy assertions on prose it had just removed. The unterminated form
    matters too: a stray `<!--` with no closer hides everything after it, and a
    non-greedy `<!--.*?-->` alone left such text visible to the lint.
    """
    text = _raw(path)
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4:]
    for pat in _COMMENTS:
        text = pat.sub(" ", text)
    return text


def _flat(path: Path = SKILL) -> str:
    """`_body` with runs of whitespace collapsed — phrase checks have to survive a
    re-wrap of the 80-column prose, and two guards that ran against the raw text
    missed `loci\\nbuild compile` for exactly that reason."""
    return re.sub(r"\s+", " ", _body(path))


#: `_section`'s name for the prose before the first `##`. It carries the consent
#: invariant, the one-decision claim and the envelope discipline, and it is not a
#: section — so without this it could take no `CONTRADICTIONS` entry, and a reviewer
#: reversed the consent invariant there with every test green.
INTRO = "<intro>"


def _section(heading: str, path: Path = SKILL) -> str:
    """The flattened body of the `##`/`###` section whose heading starts with
    ``heading``, through the next heading at the same level or shallower.

    ``heading=INTRO`` returns the prose before the first `##`.

    **Exactly one** matching heading, for the same reason `_row` requires exactly one
    row: `re.search` takes the first, so a compliant DUPLICATE section above the real
    one satisfies every assertion while the real section says the opposite. A reviewer
    proved it three times with zero tests red — including a duplicate `## Step 2` over
    a real Step 2 that gated on mere existence again, at a SMALLER byte count than the
    file it replaced. The hardening `_row` got had to come here too.
    """
    body = _body(path)
    if heading == INTRO:
        m = re.search(r"(?m)^## ", body)
        return re.sub(r"\s+", " ", body[: m.start()] if m else body)
    pat = re.compile(r"(?m)^(#{2,3}) " + re.escape(heading))
    found = list(pat.finditer(body))
    assert len(found) == 1, (
        f"{path.name}: expected exactly one section heading starting {heading!r}, "
        f"found {len(found)} — a duplicate heading un-pins every rule in this "
        f"section, because only the first is ever read")
    m = found[0]
    level = len(m.group(1))
    rest = body[m.end():]
    nxt = re.search(r"(?m)^#{1,%d} " % level, rest)
    return re.sub(r"\s+", " ", rest[: nxt.start()] if nxt else rest)


def _row(prefix: str, path: Path = SKILL) -> str:
    """The one table row starting with ``prefix``. Exactly one — `next(...)` takes
    the first, and a compliant decoy above an inverted real row defeated three of
    these tests. For a wrapped list item use `_bullet`."""
    rows = [l for l in _body(path).splitlines() if l.startswith(prefix)]
    assert len(rows) == 1, (
        f"{path.name}: expected exactly one line starting {prefix!r}, "
        f"found {len(rows)}")
    return rows[0]


def _bullet(prefix: str, path: Path = SKILL) -> str:
    """The one list item starting with ``prefix``, flattened across its wrap."""
    lines = _body(path).splitlines()
    starts = [i for i, l in enumerate(lines) if l.startswith(prefix)]
    assert len(starts) == 1, (
        f"{path.name}: expected exactly one list item starting {prefix!r}, "
        f"found {len(starts)}")
    indent = len(lines[starts[0]]) - len(lines[starts[0]].lstrip())
    out = [lines[starts[0]]]
    rest = lines[starts[0] + 1:]
    for i, line in enumerate(rest):
        if re.match(r"^[|#]", line):
            break
        if not line.strip():
            # A blank line does NOT end a list item if what follows is still indented
            # past the marker: Markdown renders that as part of the bullet, and a
            # reviewer appended such a paragraph under the `.data.candidates` bullet —
            # reversing a CRITICAL fix outside every assertion's reach, all green.
            nxt = next((l for l in rest[i + 1:] if l.strip()), "")
            if not nxt.startswith(" " * (indent + 1)):
                break
            continue
        # A list marker AT OR LEFT OF this bullet's own indent ends it; one further
        # right is a NESTED sub-bullet and belongs to it. Two reasons, both proven:
        # `  **Step 2**` continuing a wrapped bullet starts with `*`, so a bare
        # `[-*|#]` class truncated the fragment; and a reviewer appended a nested
        # `  - Where the regeneration would cost a full build, initializing over the
        # stale database is acceptable` under the stale-recipe bullet, reversing a
        # CRITICAL fix outside every assertion's reach, with all tests green.
        m = re.match(r"^(\s*)(?:[-+]\s|\*\s|\d+\.\s)", line)
        if m and len(m.group(1)) <= indent:
            break
        out.append(line)
    return re.sub(r"\s+", " ", " ".join(out))


# ── the CLI surface, from the real parser ───────────────────────────────────

INIT_VERBS = ("probe", "set", "add-file")

#: Envelope fields the docs read, and the codes they branch on. Checked as QUOTED
#: literals: the earlier alternation also accepted `\bname\s*=`, i.e. any local
#: assignment, and 10 of 24 names were self-satisfying — renaming
#: `data["initialized"]`, `{"message": …}` and `body["code"]` in a copy of the CLI
#: kept every test green while Step 1's gate and every `.error.code` branch read
#: `null`.
DATA_FIELDS = {
    "report", "recipe_summary", "recipe", "validated", "confirmed_by_user",
    "initialized", "recipe_untrusted", "existing_loci_state", "compdbs", "notes",
    "candidates", "unsupported_candidates", "warnings", "build_system", "languages",
    "binaries", "project_root",
    # T15: `probe` carries the `go_facts` block, and the init skill reads
    # `.data.go.tinygo` to tell a `go` recipe from a `tinygo` one — which is a
    # question `.data.build_system` cannot answer, since it says `go` for both.
    "go",
}
ERROR_FIELDS = {"code", "message", "candidates", "supported", "regen", "detail",
                "recorded_target", "target"}
#: Only ever `LociError(**details)` kwargs, so they have no quoted form to find —
#: `error_envelope` merges `err.details` into the body, so the key never appears as a
#: literal. Named individually rather than by loosening the pattern for all 24, which
#: is what made the earlier version self-satisfying. A name that DOES have a quoted
#: form must not sit here: `test_the_kwarg_exemption_is_not_a_laundry` enforces that,
#: because adding a real field here silently exempts it from the whole cross-check.
KWARG_ONLY = {"transient", "supported", "recorded_target"}
#: `detail` left this set with loci-tools 0.1.135 (T17): `_validate_go` now reads
#: the compiler's stderr back out with `(exc.details or {}).get("detail")`, so the
#: name has a quoted form in the CLI and can be cross-checked like any other field.
#: `test_the_kwarg_exemption_is_not_a_laundry` is what noticed, on the CLI release
#: that introduced the literal.
ERROR_CODES = {
    "init_needs_user", "init_unsupported", "init_failed", "auth_required",
    "not_initialized", "recipe_stale", "recipe_tampered", "recipe_invalid",
    "compiler_missing", "arch_mismatch", "outside_target",
}


def _configured() -> str | None:
    for var in ("LOCI_DEV_CLI_PATH", "LOCI_CLI_SRC"):
        if os.environ.get(var):
            return var
    return None


def _roots() -> list[str]:
    """Configured locations first, then the conventional sibling checkout — so the
    task's bare `./run_tests.sh` runs the cross-checks on a machine laid out the way
    this initiative's is, instead of silently skipping the strongest tests."""
    out = [os.environ[v] for v in ("LOCI_DEV_CLI_PATH", "LOCI_CLI_SRC")
           if os.environ.get(v)]
    out.append(str(PLUGIN_ROOT.parent / "loci-cli"))
    return out


def _cli_src() -> Path | None:
    for raw in _roots():
        root = Path(raw)
        for candidate in (root / "src" / "loci" / "cli", root):
            if (candidate / "init.py").is_file():
                return candidate
    return None


def _cli_env() -> dict[str, str]:
    env = dict(os.environ)
    for raw in _roots():
        src = Path(raw) / "src"
        if (src / "loci" / "cli" / "init.py").is_file():
            env["PYTHONPATH"] = str(src)
            break
    return env


def _cli_bin() -> str | None:
    """A `loci` executable whose parser has the `init` verb, or None."""
    seen: list[str] = []
    for raw in _roots():
        for rel in ("Scripts/loci.exe", "bin/loci"):
            cand = Path(raw) / ".venv" / rel
            if cand.is_file():
                seen.append(str(cand))
    found = shutil.which("loci")
    if found:
        seen.append(found)
    env = _cli_env()
    for exe in seen:
        try:
            out = subprocess.run([exe, "init", "--help"], capture_output=True,
                                 text=True, timeout=60, env=env)
        except (OSError, subprocess.SubprocessError):  # pragma: no cover
            continue
        if out.returncode == 0 and "usage: loci init" in out.stdout:
            return exe
    return None


def _help(exe: str, verb: str | None) -> str:
    argv = [exe, "init"] + ([verb] if verb else []) + ["--help"]
    return subprocess.run(argv, capture_output=True, text=True, timeout=60,
                          env=_cli_env()).stdout


def _usage(exe: str, verb: str | None) -> tuple[set[str], dict[str, set[str]]]:
    """`({long flags}, {flag: {choices}})` from `loci init [verb] --help`'s usage.

    The usage block, not the help body: the help *prose* names flags belonging to
    other subcommands (`set`'s epilog names `--refresh --target`), so scraping the
    page pools four parsers into one and cannot tell which verb accepts what.
    """
    block: list[str] = []
    for line in _help(exe, verb).splitlines():
        if line.startswith("usage:"):
            block.append(line)
        elif block and line.startswith(" "):
            block.append(line)
        elif block:
            break
    usage = " ".join(block)
    flags = set(re.findall(r"(?<![\w-])(--[a-z][a-z0-9-]*)", usage))
    choices = {f: set(g.split(","))
               for f, g in re.findall(r"(--[a-z][a-z0-9-]*) \{([^}]+)\}", usage)}
    return flags, choices


_HAVE_SRC = _cli_src() is not None
_HAVE_BIN = _cli_bin() is not None

needs_cli_src = pytest.mark.skipif(
    not _HAVE_SRC, reason="no loci-cli checkout (LOCI_DEV_CLI_PATH / sibling)")
needs_cli_bin = pytest.mark.skipif(
    not _HAVE_BIN, reason="no runnable loci CLI carrying the `init` verb")


def test_a_configured_cli_location_actually_resolves():
    """A set-but-wrong variable must FAIL, not skip.

    `LOCI_DEV_CLI_PATH=…/loci-cli-typo` used to give "26 passed, 4 skipped" with the
    reason "no loci-cli checkout" — false, and it disarms every cross-check on the
    only machines that run them.
    """
    var = _configured()
    if var is None:
        pytest.skip("no CLI location configured")
    assert _HAVE_SRC or _HAVE_BIN, (
        f"{var}={os.environ[var]!r} is set but no loci-cli source "
        f"(src/loci/cli/init.py) and no runnable `.venv` CLI was found under it. "
        f"Fix the path or unset it — a silent skip here rots every allowlist here.")


# ── 1. the surface the docs instruct ────────────────────────────────────────

def _invocations(path: Path) -> list[tuple[str | None, list[str]]]:
    """Every `loci init …` command in the doc, as `(verb, [flags])`, from the
    flattened body so an 80-column wrap cannot hide one."""
    out: list[tuple[str | None, list[str]]] = []
    flat = _flat(path)
    for m in re.finditer(r"loci init\b", flat):
        # Stop at a table-cell break or a sentence end, NOT at a backtick: a reviewer
        # hid a probe-only flag past one — ``then `loci init` with `--with-make` `` —
        # and the wrong-subcommand check never saw it. 160 chars is past any
        # invocation in these files and short of the next one.
        tail = flat[m.end(): m.end() + 160].split("|")[0].split(". ")[0]
        vm = re.match(r"\s+([a-z][a-z0-9-]*)", tail)
        verb = vm.group(1) if vm and not tail.lstrip().startswith(("-", "`-")) else None
        out.append((verb, re.findall(r"(?<![\w-])--[a-z][a-z0-9-]*", tail)))
    return out


def test_every_named_init_subcommand_is_one_the_cli_ships():
    used = {v for p in DOCS for v, _ in _invocations(p) if v}
    unknown = sorted(used - set(INIT_VERBS))
    assert not unknown, f"`loci init <verb>` for {unknown}, which the CLI lacks."


@needs_cli_bin
def test_the_declared_verb_list_is_the_parser_own():
    """`INIT_VERBS` gates the per-verb flag check, so on its own it is
    self-referential in the loose direction: a campaign ADDED `nonesuch` to it and
    nothing failed, because no doc used the bogus verb. The parser's own subcommand
    list is what makes the declaration answerable."""
    exe = _cli_bin()
    out = _help(exe, None)
    tail = out[out.index("<command>", out.index("positional arguments")):]
    real = set(re.findall(r"(?m)^\s{4}([a-z][a-z0-9-]*)\s", tail))
    assert real, "could not parse the subcommand list out of `loci init --help`"
    extra = sorted(set(INIT_VERBS) - real)
    assert not extra, f"INIT_VERBS names verbs the CLI does not ship: {extra}"


def test_the_only_loci_verbs_the_docs_invoke_are_init_login_doctor_and_cockpit():
    """A scope guard: these files record build facts. A `loci timing` or
    `loci build compile` here means the skill has stopped being the init skill.
    `cockpit` is named but never invoked — it is one of the three commands the user
    runs, and a fresh recipe is when there is first something in it to see."""
    verbs: set[str] = set()
    for path in DOCS:
        verbs |= {m.group(1) for m in re.finditer(r"loci ([a-z][a-z0-9-]*)",
                                                  _flat(path))}
    extra = sorted(verbs - {"init", "login", "doctor", "cockpit"})
    assert not extra, f"unexpected `loci` verbs: {extra}"


@needs_cli_bin
def test_every_flag_is_accepted_by_the_verb_it_is_used_with():
    """Per-verb, from the real parser's usage line.

    Pooling all four subparsers made five usage errors invisible, each an argparse
    exit 2 on every run: `loci init --with-make` (probe-only), `loci init probe
    --refresh` (init-only), and three more.
    """
    exe = _cli_bin()
    accepted = {verb: _usage(exe, verb)[0] for verb in (None,) + INIT_VERBS}
    offenders = [
        f"{path.name}: `loci init {verb or ''} {flag}` — "
        f"{verb or 'init'} does not accept it"
        for path in DOCS
        for verb, flags in _invocations(path)
        if verb in accepted
        for flag in flags
        if flag not in accepted[verb]
    ]
    assert not offenders, "\n".join(offenders)


@needs_cli_bin
def test_every_flag_value_is_one_the_parser_accepts():
    """`--compdb-kind=cmake` and `--target=detect` are argparse exit 2, and a
    surface test that ignores values cannot see either."""
    exe = _cli_bin()
    _flags, choices = _usage(exe, None)
    assert choices, "expected the init usage line to carry {choice} groups"
    offenders = []
    for path in DOCS:
        for flag, value in re.findall(
                r"(--[a-z][a-z0-9-]*)=([a-z][a-z0-9|.-]*)", _flat(path)):
            allowed = choices.get(flag)
            if allowed is None:
                continue
            for one in value.split("|"):
                if one not in allowed:
                    offenders.append(f"{path.name}: {flag}={one} — parser accepts "
                                     f"{sorted(allowed)}")
    assert not offenders, "\n".join(offenders)


@needs_cli_bin
def test_the_auto_target_sentinel_is_the_one_the_cli_ships():
    """`--target` deliberately has no argparse `choices` (the skill offers
    free-form), so its one magic value is checkable only against the help text."""
    out = _help(_cli_bin(), None)
    assert "`auto`" in out or "--target=auto" in out, (
        "the CLI's --target help no longer documents an `auto` sentinel, which "
        "SKILL.md and recovery.md both instruct")
    assert any("--target=auto" in _flat(p) for p in DOCS)


@needs_cli_src
def test_every_envelope_field_and_code_the_docs_read_exists_in_the_cli():
    src_dir = _cli_src()
    blob = "\n".join(
        (src_dir / name).read_text(encoding="utf-8")
        for name in ("init.py", "init_evidence.py", "init_migrate.py", "recipe.py",
                     "recipe_flags.py", "_errors.py", "_json.py", "_session.py")
        if (src_dir / name).is_file())
    wanted = (DATA_FIELDS | ERROR_FIELDS | ERROR_CODES) - KWARG_ONLY
    missing = sorted(n for n in wanted if f'"{n}"' not in blob)
    assert not missing, (
        f"the docs read envelope fields / error codes that appear nowhere as "
        f"literals in the CLI's init modules: {missing}")
    for name in KWARG_ONLY:
        assert re.search(rf"\b{name}\s*=", blob), (
            f"`{name}` is declared kwarg-only but is not passed as a kwarg either")


@needs_cli_src
def test_the_kwarg_exemption_is_not_a_laundry():
    """`KWARG_ONLY` exempts a name from the quoted-literal check, so it is a hole
    exactly the size of its contents. A campaign added `report` to it — a field with
    a perfectly good quoted form — and the cross-check went quiet about it."""
    blob = "\n".join(
        (_cli_src() / n).read_text(encoding="utf-8")
        for n in ("init.py", "init_evidence.py", "init_migrate.py", "recipe.py",
                  "recipe_flags.py", "_errors.py", "_json.py", "_session.py")
        if (_cli_src() / n).is_file())
    laundered = sorted(n for n in KWARG_ONLY if f'"{n}"' in blob)
    assert not laundered, (
        f"these names have a quoted form in the CLI and so must NOT be exempted "
        f"from the literal cross-check: {laundered}")


def test_the_declared_fields_are_the_ones_the_docs_use():
    """The sets above feed the cross-check, so alone they are self-referential.
    Scanning the docs is what makes them answerable to something."""
    used_data: set[str] = set()
    used_error: set[str] = set()
    for path in DOCS:
        flat = _flat(path)
        used_data |= set(re.findall(r"\.data\.([a-z_]+)", flat))
        used_error |= set(re.findall(r"\.error\.([a-z_]+)", flat))
    assert used_data <= DATA_FIELDS, f"undeclared: {sorted(used_data - DATA_FIELDS)}"
    assert used_error <= ERROR_FIELDS | KWARG_ONLY, (
        f"undeclared: {sorted(used_error - ERROR_FIELDS - KWARG_ONLY)}")


@needs_cli_src
def test_the_settable_keys_match_the_cli_whitelist():
    src = (_cli_src() / "init.py").read_text(encoding="utf-8")
    block = src[src.index("SET_KEYS"):]
    block = block[:block.index("\n}\n")]
    real = set(re.findall(r'"([a-z_]+(?:\.[a-z_]+)+)":', block))
    assert len(real) >= 10, f"SET_KEYS parse looks wrong: {sorted(real)}"
    listed = set(re.findall(r"`([a-z_]+(?:\.[a-z_]+)+)`",
                            _row("| `loci init set", RECOVERY)))
    assert listed == real, (
        f"recovery.md's settable keys vs the CLI: missing={sorted(real - listed)} "
        f"extra={sorted(listed - real)}")


@needs_cli_src
def test_the_seeded_state_disjuncts_are_the_cli_ones():
    src = (_cli_src() / "init_migrate.py").read_text(encoding="utf-8")
    assert "self.sidecars or self.flags_json or self.trace" in src, (
        "`Seed.usable`'s disjunction changed; Step 1's seeded-state condition "
        "(sidecars / flags_json / trace_kind) has to change with it")


# ── 2. no detection ladder ──────────────────────────────────────────────────

#: The compiler probes the pre-T14 session scan ran (`command -v <name>` in
#: `lib/detect-project.sh`), frozen when T14 deleted the scan. Together with the
#: bare driver names below they are the ladder these lints keep out of the docs:
#: a skill that names one is telling the model to go and find a compiler, which
#: is the recipe's job. Frozen rather than read, because the file no longer
#: probes anything — and a list read from a file that has none is empty, which
#: is the silent green `test_the_cascade_compiler_list_is_not_empty` exists to
#: refuse.
_SCAN_PROBES = frozenset({
    "aarch64-linux-gnu-g++", "aarch64-linux-gnu-gcc",
    "aarch64-unknown-linux-gnu-g++", "aarch64-unknown-linux-gnu-gcc",
    "arm-none-eabi-g++", "arm-none-eabi-gcc", "armcc", "armcl", "clang++",
    "g++", "iccarm", "rustc", "rustup", "tiarmclang", "tricore-elf-g++",
    "tricore-elf-gcc",
})


def _script_probes() -> set[str]:
    return set(_SCAN_PROBES)


def _cascade_compilers() -> set[str]:
    """The cascade's own probe list, plus the bare driver names it does NOT probe.

    `detect_compiler` probes `g++`/`clang++`, so a `name in text` guard built only
    from it missed `gcc`, `clang` and `cl` — and a size-neutral ladder naming all
    three plus five more passed every test in the file.
    """
    return _script_probes() | {
        "gcc", "clang", "cl.exe", "icx", "icc", "avr-gcc", "nvcc",
        "riscv64-unknown-elf-gcc", "riscv32-unknown-elf-gcc",
        "xtensa-esp32-elf-gcc", "msp430-elf-gcc", "arm-none-eabi-ld",
    }


def test_the_cascade_compiler_list_is_not_empty():
    """Guards the tests below against passing on an empty list — and pins that
    the list is frozen for the right reason: the detector probes nothing now."""
    assert len(_script_probes()) >= 10, sorted(_script_probes())
    assert "command -v" not in DETECT.read_text(encoding="utf-8"), (
        "detect-project.sh probes PATH again; the ladder is back in the plugin")


def test_the_heading_scans_see_an_indented_heading():
    """Both scans allow CommonMark's 0–3 leading spaces. Nothing in the four files is
    indented today, so deleting the ` {0,3}` from either regex changes no result — and a
    reviewer used exactly that gap: ` #### When the recipe already looks fine` renders as
    an h4, carried a rule reversal inside Step 4, and passed both scans. A guard whose
    only evidence is "the current files are clean" is not a guard, so the patterns are
    exercised directly."""
    for indent in ("", " ", "  ", "   "):
        assert re.search(r"(?m)^ {0,3}(#{4,}) (.+)$", f"{indent}#### Hidden\n"), (
            f"the `####` ban misses a heading indented by {len(indent)} spaces")
        assert re.search(r"(?m)^ {0,3}#{2,3} (.+)$", f"{indent}## Section\n"), (
            f"the completeness scan misses a heading indented by {len(indent)} spaces")
    # Four spaces is an indented code block in CommonMark, not a heading — so it is
    # correctly outside both.
    assert not re.search(r"(?m)^ {0,3}(#{4,}) (.+)$", "    #### Code\n")


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.name)
def test_no_heading_deeper_than_three(path: Path):
    """`####` is a hiding place, and every other skill in this repo uses `##`/`###`.

    `_section` deliberately includes a section's subsections, so text under a `####`
    inside `## Step 4` sits in the window while every positive assertion still matches
    the real prose above it. A reviewer inverted Step 4 and the Ninja rule that way with
    all tests green. Rather than teach `_section` to police depth, forbid the level: a
    rule worth stating belongs in a section a `CONTRADICTIONS` entry can name.
    """
    # CommonMark allows up to three leading spaces on an ATX heading, and a
    # column-0 anchor missed ` #### …` — which renders as an h4 and carried a rule
    # reversal inside a section, past both this test and the completeness one.
    deep = re.findall(r"(?m)^ {0,3}(#{4,}) (.+)$", _body(path))
    assert not deep, (
        f"{path.name}: headings deeper than `###` — {[h[1] for h in deep]}. Put the "
        f"rule in its own `##`/`###` section so it can be pinned.")


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.name)
def test_no_compiler_probe_ladder(path: Path):
    # Word-bounded: a bare `in` matched `clang` inside `clangd`, which appears in
    # compdb.md's explanation of what synthesizing is the honest version of.
    body = _body(path)
    offenders = sorted(
        n for n in _cascade_compilers()
        if re.search(r"(?<![\w.+-])" + re.escape(n) + r"(?![\w.+-])", body))
    assert not offenders, (
        f"{path.name} names compiler binaries ({offenders}). Evidence collection is "
        f"`loci init probe`'s job — the skill must not re-grow the ladder the recipe "
        f"replaces.")


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.name)
def test_no_vendor_directory_hunting(path: Path):
    """Six literal fragments let a ladder through naming `/usr/local/arm*/bin`,
    `C:\\Keil_v5\\…`, `/Applications/ARM/bin`, `~/.espressif/tools/*/bin` and
    `/opt/gcc-arm-*/bin`. Shapes, not literals."""
    body = _body(path)
    patterns = {
        "windows drive path": r"[A-Za-z]:\\\\?[A-Za-z]",
        "Program Files": r"Program Files",
        "/opt or /usr/local": r"/(?:opt|usr/local)/",
        "an /Applications or home-dot toolchain dir": r"/Applications/|~/\.[a-z]+/",
        "a glob into a bin directory": r"\*[^\s`]*/bin\b",
        "a vendor SDK root": r"\b(?:Keil_v5|SEGGER|IAR Systems|ti/ccs|espressif)\b",
    }
    offenders = [why for why, pat in patterns.items() if re.search(pat, body)]
    assert not offenders, f"{path.name}: vendor install-path hunting — {offenders}"


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.name)
def test_no_trial_compile_or_flag_guessing(path: Path):
    """A ladder needs no compiler name and no path: "try the candidate flag sets in
    turn — widest first, then drop one group at a time until it compiles" named
    neither, and passed."""
    flat = _flat(path)
    banned = {
        "trial compiling": r"until it compiles|try (?:each|the) (?:flag|candidate)",
        "taking the first hit": r"first hit|take the first (?:one|match) that",
        "walking a ladder": r"walk (?:this|the) ladder|in turn,? widest first",
    }
    offenders = [why for why, pat in banned.items() if re.search(pat, flat)]
    assert not offenders, f"{path.name}: flag guessing — {offenders}"


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.name)
def test_command_v_is_only_used_for_the_three_host_tools(path: Path):
    probed = set(re.findall(r"command -v ([\w.+-]+)", _body(path)))
    assert probed <= {"loci", "uv"}, (
        f"{path.name} probes {sorted(probed - {'loci', 'uv'})} with "
        f"`command -v`; only the two host tools are in scope")


def test_the_evidence_budget_is_stated_positively():
    """The negatives above are a fence; this is the rule. Replacing the paragraph
    with a ladder removed the rule and left the fence to catch it — which it did only
    for the names the fence happened to know."""
    step1 = _section("Step 1")
    assert "Add no evidence of your own" in step1
    for clause in ("no vendor-directory hunting", "no compiler-fallback ladder",
                   "no trial compiles", "no walking build trees"):
        assert clause in step1, f"Step 1's evidence budget must say {clause!r}"


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.name)
def test_never_python_or_a_direct_compiler_invocation(path: Path):
    flat = _flat(path)
    offenders = [f for f in ("python -c", "python3 -c", "pip install", "gcc -c",
                             "clang -c") if f in flat]
    assert not offenders, f"{path.name}: {offenders}"


# ── 3. the rules, each inside the section that owns it ──────────────────────

def test_the_frontmatter_carries_the_trigger_surface():
    fm = re.sub(r"\s+", " ", _frontmatter())
    assert re.search(r"name:\s*init\b", fm), "frontmatter needs `name: init`"
    assert "description:" in fm and "when_to_use:" in fm
    for phrase in ("initialize LOCI", "set up this project for LOCI", "/loci:init",
                   "switch the LOCI target", "not initialized", "not_initialized",
                   "recipe_stale", "arch_mismatch", "outside_target", "/loci:setup",
                   "/loci:contract"):
        assert phrase in fm, f"when_to_use/description must cover {phrase!r}"


def test_the_skill_does_not_disable_model_invocation():
    """Presence at all, not truthiness. The helper copied from the sibling lint
    accepted only true/yes/on, so `disable-model-invocation: 1` and `: !!bool true`
    both got through — and either hides the `/loci:init` command every coded refusal
    names."""
    for line in _frontmatter().splitlines():
        assert not re.match(r"\s*disable-model-invocation\s*:", line), (
            "SKILL.md must not carry `disable-model-invocation` at all")


def test_step_0_gates_on_both_tools_and_on_a_stale_cli():
    """The stale-CLI state is the one that actually occurs: a published CLI from
    before the recipe answers `invalid choice: 'init'` on stderr with no envelope on
    stdout, so there is nothing for `.ok` to be false in and no row in Step 3's table
    matches. Session start upgrades the CLI detached, so it is a live window on the
    first session after a plugin update."""
    step0 = _section("Step 0")
    for tool in ("`loci`", "`uv`"):
        assert tool in step0, f"Step 0 must probe {tool}"
    assert "`jq`" not in step0, (
        "`jq` is not a prerequisite since todo 044 — nothing the plugin ships runs "
        "one — so Step 0 must not stop a session for want of it")
    assert "command -v" in step0
    assert "bootstrap.md" in step0, "the procedure lives in bootstrap.md"
    assert "invalid choice" in step0, "name the stale-CLI symptom"
    assert re.search(r"empty stdout", step0)
    assert re.search(r"\*\*missing\*\*", step0) and "`/loci:setup`" in step0, (
        "a missing CLI now routes through `/loci:setup` first — Step 0 must say so. "
        "It used to say the opposite (`do not require /loci:setup first`) and run the "
        "installer inline, which checked no prerequisite and verified nothing")
    assert "not signed in" in step0, "the sign-in route belongs in the gate"
    # The over-broad form ("no JSON on stdout ⇒ stale CLI") sent every argparse error
    # through the global installer, which then reported the verb present — a loop.
    assert re.search(r"any \*other\* argparse complaint", step0), (
        "only no-JSON *and* `invalid choice` is a stale CLI; anything else is a "
        "malformed command")
    assert "do not run the installer" in step0


def test_step_0_gates_on_a_cli_behind_the_pin():
    """The third CLI state, and the only one that goes to `/loci:setup` BEFORE the
    flow: present, knows `init`, older build than the plugin was written against. It
    is not covered by either neighbouring rule — the installer route is for a
    MISSING cli, and Step 0's `/loci:setup` prohibition is about that same state, so
    a gate that does not name the pin reads as forbidden."""
    step0 = _section("Step 0")
    assert "LOCI_CLI_VERSION" in step0, "name the pin the comparison is against"
    assert re.search(r"run `/loci:setup` first[^.]*\bbehind\b[^.]*`LOCI_CLI_VERSION` "
                     r"pin|behind[^.]*`LOCI_CLI_VERSION` pin[^.]*run `/loci:setup` "
                     r"first", _flat(SKILL)), (
        "Step 0 must say that a CLI behind the pin runs `/loci:setup` first")
    assert "bootstrap.md" in step0, "the comparison itself lives in bootstrap.md"


def test_the_pin_the_docs_compare_against_is_the_one_the_plugin_declares():
    """Answerability: the gate is worth nothing if it names a constant no shell file
    defines, or a file that has moved. Both halves are checked against the tree."""
    steps = PLUGIN_ROOT / "lib" / "setup-steps.sh"
    assert steps.is_file(), "lib/setup-steps.sh is gone — the gate points nowhere"
    text = steps.read_text(encoding="utf-8")
    assert re.search(r'(?m)^LOCI_CLI_VERSION="[0-9.]+"', text), (
        "lib/setup-steps.sh no longer declares LOCI_CLI_VERSION as a dotted-numeric "
        "literal, so the `sed` in bootstrap.md reads nothing")
    sect = _section("`loci` behind", BOOTSTRAP)
    assert "lib/setup-steps.sh" in sect and "LOCI_CLI_VERSION" in sect


def test_bootstrap_treats_the_pin_as_a_floor_not_an_equality():
    """The failure mode of "different ⇒ reinstall": the CLI only moves forward, a
    `LOCI_DEV_CLI_PATH` checkout floats ahead by design, and a machine on the newest
    CLI reads ahead of an older plugin's pin — so an equality test sends healthy
    installs to `/loci:setup` on every invocation. An unreadable number is the same
    trap by another route."""
    sect = _section("`loci` behind", BOOTSTRAP)
    low = sect.lower()
    assert "older" in low, "the trigger is BEHIND the pin, not merely different"
    assert re.search(r"equal to or newer[^.]*\u2192 carry on|floor, not an equality",
                     low), "say that equal-or-newer carries on"
    assert "loci_dev_cli_path" in low, "name the install that floats ahead on purpose"
    assert re.search(r"unreadable|unknown", low), (
        "an unparseable version or a missing pin is an unknown, not a stale CLI")
    assert re.search(r"numerically", low), (
        "0.2.10 vs 0.2.9 — say the comparison is numeric, not string order")


def test_bootstrap_says_to_wait_and_how_to_resolve_the_plugin_dir():
    """Three non-overlapping claims. A single `\\bwait(s)? for\\b` was satisfied by
    any one of the three sentences: deleting the semantics left the imperative, and
    deleting the imperative left the semantics — while inverting "may already be
    running" to "is never already running" kept every word either looked for."""
    assert (PLUGIN_ROOT / INSTALLER_REL).is_file(), f"{INSTALLER_REL} is gone"
    sect = _section("`loci` missing", BOOTSTRAP)
    low = sect.lower()
    assert INSTALLER_REL in sect
    assert re.search(r"\bwait for it\b|\bdo not proceed until\b", low), (
        "the imperative is missing")
    assert re.search(r"waits for that one|rather than starting a second"
                     r"|instead of starting a second|not start a second", low), (
        "the semantics are missing — the call waits for an install already in flight "
        "rather than starting a second")
    assert re.search(r"may already be running|might already be running"
                     r"|already in flight", low), (
        "the reason is missing — an install MAY already be running")
    assert "never background" in low and "never run two" in low
    assert "never install the wheel yourself" in low
    assert re.search(r"re-probe `command -v loci`", sect), (
        "the installer is silent and always exits 0 — say to re-probe")
    assert "exit 0 without installing" in sect, (
        "it can also exit 0 having given up waiting on another install")
    assert "plugin dir:" in sect and "CLAUDE_PLUGIN_ROOT" in sect
    assert re.search(r"do not guess a path", sect)


def test_bootstrap_states_the_host_tool_policy():
    sect = _section("`uv` missing", BOOTSTRAP)
    assert "never installs host tools" in sect
    assert "password prompt" in sect
    assert re.search(r"do not\s*ask", sect)


def test_bootstrap_handles_a_present_but_too_old_cli():
    sect = _section("`loci` present but too old", BOOTSTRAP)
    assert "invalid choice" in sect
    assert "stderr" in sect and "no envelope on stdout" in sect
    assert "detached" in sect, "say why the window exists"
    assert "/loci:setup" in sect, "and where it ends"


def test_step_1_says_what_it_recognised_before_any_build_system_talk():
    """The first thing a new user hears used to be the investigation: databases,
    tools, translation units, tiers. The recognition line is the product moment this
    skill already had the facts for — `probe`'s `.data` — and never said."""
    step1 = _section("Step 1")
    assert "say what you recognised" in step1, (
        "Step 1 must say the recognition line out loud, not leave it implied")
    assert re.search(r"before any build-system talk", step1), (
        "its whole point is the ORDER — recognition ahead of the machinery")
    assert "voice.md" in step1, "and the words to use live in voice.md"
    assert "`.data`" in step1, (
        "out of the envelope, not out of a look around the tree — the evidence "
        "budget in this same step forbids the second")


def test_voice_puts_the_machinery_second_rather_than_deleting_it():
    """The failure mode of a messaging pass is a skill that stops saying true things.
    `voice.md` demotes the vocabulary; it does not drop it, and it names the two
    classes of sentence that never shrink."""
    flat = _flat(VOICE)
    assert re.search(r"[Ss]ay it \*\*second\*\*|it is \*\*second\*\*", flat), (
        "the technical vocabulary is deferred, not banned")
    for term in ("compile database", "translation unit", "validation tier"):
        assert term in flat, f"the table must name {term!r} to demote it"
    assert re.search(r"the moment the user has to act on it", flat), (
        "name the machinery where the user must act on it — a tool to install, a "
        "command to approve, a build of theirs that is broken")
    assert re.search(r"[Tt]wo things never shrink", flat)
    assert re.search(r"`message` and `detail`", flat), (
        "a refusal names the fix in the CLI's words; a paraphrase sends the user "
        "to fix the wrong thing")
    assert re.search(r"notes, warnings and `!` lines", flat), (
        "and the channels that say a measurement is less than it looks")


def test_voice_forbids_an_eta_and_an_invented_function():
    """Both are sentences the document asked for that the skill cannot honestly
    produce: a duration it cannot know (a synthesize on firmware runs minutes), and
    the `Audio_Process`-style function name that came from a session which had read
    the code — which Step 1's evidence budget forbids."""
    flat = _flat(VOICE)
    assert re.search(r"\*\*Never an ETA\.\*\*", flat), (
        "Step 1 cannot know whether this is a cached build or a ten-minute one")
    assert re.search(r"Name no function of theirs", flat), (
        "the close-out names what the RECIPE records; a plausible function that is "
        "not in their code is the most expensive sentence in the file")


def test_the_progress_checklist_has_a_shape_and_keeps_the_detail_reachable():
    """A rule with no template is a rule each run re-invents. The review asked for a
    specific artefact — a tick list — and for the detail to stay *accessible*, which
    "stays out of the transcript" alone does not provide.

    The list is printed WHOLE from the first wait, pending stages included: "the
    journey is invisible" is the first UX problem the review names, and a block that
    only grows answers it after the fact.
    """
    sect = _section("While it runs", VOICE)
    raw = _body(VOICE)
    for stage in ("Project detected", "Target detected", "Build configuration captured",
                  "Build verified", "LOCI execution model ready"):
        assert re.search(r"(?m)^[✓·] " + re.escape(stage), raw), (
            f"the block has to be copyable, not described: {stage!r}")
    assert re.search(r"Connecting LOCI", raw), "including the line that opens it"
    assert "✓" in raw and "·" in raw, (
        "both marks belong in the template — a pending stage with no mark is a mark "
        "the model invents per run")
    assert re.search(r"\*\*the whole list, every time\*\*", sect), (
        "the stages still ahead are on screen from the first wait")
    assert re.search(r"[Nn]othing is\s*ticked in advance", sect), (
        "a tick is a stage the envelope says completed; a tick for an intention is a "
        "claim the user cannot check")
    assert re.search(r"settled at the first print", sect), (
        "same lines, same order, more ticks — a list that reshuffles is not a plan")
    assert re.search(r"not in the list at all rather than pending forever", sect), (
        "a stage this project does not have never gets a pending line either")
    assert re.search(r"[Rr]eprint when a tick changes, and only then", sect), (
        "reprinting on anything else is the narration this block replaces")
    assert re.search(r"fails\*\* takes `✗`", sect), "and a failure is not a tick"
    assert re.search(r"readiness line be where it is said", sect), (
        "the fifth tick and Step 4's readiness line are one fact; the prose sentence "
        "is the readiness line's, so the tick is not restated underneath it")
    assert re.search(r"noun follows the project", sect), (
        "the opening line is the one firmware-specific string, and this skill also "
        "initializes crates and modules")
    assert re.search(r"Offer it instead of printing it", sect), (
        "'keep detailed logs accessible': the detail is offered, not deleted")
    assert re.search(r"cannot un-print the commands", sect), (
        "the skill governs its own prose — the harness renders the calls either way, "
        "and a rule that pretends otherwise cannot be followed")


def test_step_4_orders_readiness_then_caveats_then_the_setup():
    """The reorder the messaging change is actually made of — and its one risk: a
    user who reads the first line and stops must not read "ready" in place of "this
    database belongs to another checkout". So the caveats sit directly UNDER the
    readiness line, and never inside it."""
    step4 = _section("Step 4")
    assert "Order what you print" in step4
    assert re.search(r"readiness line", step4)
    assert re.search(r"checked against your real build\*\* only where "
                     r"`\.data\.validated`\s*is true", step4), (
        "the trust sentence is the strongest claim this skill makes; it is gated on "
        "the field that earns it, not printed on every run")
    assert re.search(r"then every note, warning and `!` line below it", step4), (
        "the three channels keep their place, immediately under the readiness line")
    assert re.search(r"never folded into it", step4), (
        "a caveat inside the readiness sentence is how a warning gets read as "
        "part of the good news")


def test_the_close_out_is_one_invitation_and_not_a_second_question():
    """The document's `[Analyze performance]` CTA, in a terminal and inside this
    skill's one-question discipline: an invitation naming what the recipe records,
    not another `AskUserQuestion` and not a command list."""
    step4 = _section("Step 4")
    assert "one short block" in step4, "the close-out block itself stays"
    assert re.search(r"\*\*one invitation\*\*", step4)
    assert re.search(r"naming this recipe's artifact and target", step4), (
        "project-specific from recipe facts — the only project facts this skill has")
    assert re.search(r"not a\s*command list", step4)
    close = _section("Step 4 — ready", VOICE)
    assert re.search(r"\*\*not\*\* an `AskUserQuestion`", close), (
        "Step 3 and Step 4 spend the one question between them; a CTA that asks is "
        "a second one")


def test_the_consent_opener_leads_with_what_it_buys_them():
    """Stage 3 of the review: ask in product language, keep every specific. The three
    honest specifics ARE the "what it changes" half — demoting them would be the
    messaging pass eating the disclosure."""
    sect = _section("Consent, before anything runs", COMPDB)
    assert "voice.md" in sect
    assert re.search(r"flags their code is really compiled with", sect), (
        "open with what the build buys them")
    assert re.search(r"does not touch their source", sect), (
        "and what it does not change — the question every user actually has")
    for kept in ("sticky", "never been configured", "full build"):
        assert kept in sect, (
            f"the specific {kept!r} must survive the reframing: it is what the user "
            f"is consenting TO")


def test_sign_in_is_framed_as_a_step_and_promises_no_resume():
    """Moving auth to the front is a flow change and out of scope; how it reads is
    not. What it must not become is a promise — the next invocation re-derives the
    evidence, and "I'll continue automatically" is a session this skill does not
    keep open."""
    sect = _section("Sign-in", BOOTSTRAP)
    assert re.search(r"one-time step it is, not as a failure", sect)
    assert re.search(r"`/loci:init` picks up from there", sect)
    assert re.search(r"Promise no resume you do not perform", sect), (
        "the review asked for continuity; this skill can only offer a re-run")


def test_step_1_routes_an_untrusted_recipe_and_a_stale_one_regenerates_first():
    """The CRITICAL this closes: Step 2's gate used to be "does a compile database
    exist". On a `recipe_stale` tree that is true of the STALE one, so init
    re-derived from it, recorded the stale flags at full tier and re-stamped the
    watch hashes — clearing the staleness signal for good, `init_status: ok`, no
    warning anywhere."""
    bullet = _bullet("- `.data.recipe_untrusted` present")
    assert "cannot be vouched" in bullet
    assert "recipe_stale" in bullet
    assert re.search(r"regenerate the compile database first", bullet)
    assert re.search(r"stale flags at full", bullet)
    assert "clears the staleness signal" in bullet


def test_step_1_routes_an_unconfirmed_recipe_and_distrusts_an_unvouched_one():
    """Three states behind a single "go to Step 5".

    A recipe nobody confirmed could never be confirmed (Step 5 has no confirm action).
    And a hand-written or copied-in `.loci/build.yaml` reads exactly like a healthy one
    — a missing escrow is deliberately not a refusal, so `recipe_untrusted` is null —
    so the skill reported "initialized for armv6-m, replay-compare, confirmed by user"
    about a file that invented every word, defeating the consent invariant the whole
    skill rests on.
    """
    bullet = _bullet("- `.data.initialized == true`")
    assert "confirmed_by_user: false" in bullet and "Step 4" in bullet
    assert re.search(r"`escrow` not `\"ok\"`", bullet), (
        "an unvouched recipe's `validated`/`confirmed_by_user` are claims, not facts")
    assert re.search(r"\*\*always go to Step 3\*\*", bullet), (
        "an unvouched recipe re-derives unconditionally — gating that on the target "
        "also being absent from the candidates left the ordinary case (a cleared "
        "`~/.loci`) reported as healthy, with the warning that sent the user here "
        "unclearable")
    assert "claims, not facts" in bullet
    assert re.search(r"forged `confirmed_by_user: true` reaches every", bullet), (
        "say what the forgery costs: it silences the one warning the skill must earn")
    assert ".data.candidates" in bullet, (
        "cross-check the recorded target against the tree's own candidates")
    assert "Step 3" in bullet, "an unvouched or contradicted recipe re-derives"


def test_step_1_skips_step_2_but_does_not_call_an_unsupported_tree_permanent():
    """Two errors in one bullet, one round apart.

    Reconfiguring the build tree before Step 3 has classified the project mutates it to
    learn nothing (a sticky CMake cache entry on a project LOCI then declares inactive).
    But the first fix over-corrected into *stopping* — and only a compile database or a
    cargo manifest makes "no supported ISA" permanent: a lone committed object makes it
    `init_failed`/**transient**, whose own message says to build the image and re-run.
    Stopping at Step 1 also skips the `init_unsupported` caveat that exists for it.
    """
    bullet = _bullet("- `.data.candidates` empty")
    assert "unsupported_candidates" in bullet
    assert re.search(r"skip Step 2", bullet), "do not mutate before Step 3 classifies"
    assert re.search(r"go straight to \*\*Step 3\*\*", bullet), (
        "the ROUTE, not just a mention of Step 3 — the bullet names Step 3 twice, so "
        "asserting the name let \"and stop here\" through")
    assert "stop here" not in bullet, (
        "stopping at Step 1 skips the init_unsupported caveat that exists for this case")
    assert "transient" in bullet and "permanent" in bullet, (
        "the bullet must not present an unsupported-looking tree as permanent")


def test_step_1_establishes_and_passes_the_project_root():
    """The plugin's own house rule (`_shared/loci-runtime-contract.md`: "Pass
    `--project-root` explicitly"), and the init skill had it nowhere. A session opened
    in `fw/src` of a healthy initialized tree probed as `initialized: true` with
    `compdbs: []` — so the skill asked to reconfigure a build tree in a directory with
    no CMakeLists.txt."""
    step1 = _section("Step 1")
    assert re.search(r"\*\*Establish the project root first and pass it to every call",
                     step1), (
        "the imperative was unpinned: replacing it with \"the current directory is "
        "fine\" left every other phrase in the paragraph intact and all tests green")
    assert "--project-root" in step1
    assert re.search(r"git rev-parse --show-toplevel", step1)
    assert re.search(r"shell's own directory", step1), "say what the default is"
    assert re.search(r"`probe` and the reference files' commands included"
                     r"|probe.{0,40}included", step1), (
        "the rule has to reach the commands in bootstrap.md / compdb.md / recovery.md, "
        "whose inline forms a fence-only test does not see")
    # And every invocation in every file carries it.
    for path in DOCS:
        for m in re.finditer(r"```bash\n(.*?)```", _raw(path), re.S):
            for line in m.group(1).splitlines():
                if (line.strip().startswith("loci init")
                        and "--help" not in line
                        and "init verb absent" not in line):  # the stale-CLI probe
                    assert "--project-root" in line or line.strip().endswith("\\"), (
                        f"{path.name}: fenced invocation without --project-root: "
                        f"{line.strip()!r}")


def test_the_root_rule_names_where_the_git_toplevel_is_wrong():
    """`git rev-parse --show-toplevel` exits 128 outside a repo (with output only on
    stderr), and in a submodule or a monorepo it can sit above the tree that actually
    builds — which is the shape `compdb.md`'s `direct` row exists for."""
    step1 = _section("Step 1")
    assert re.search(r"exits non-zero outside a repo", step1)
    assert re.search(r"submodule\s*or monorepo", step1)
    assert re.search(r"root build files are the\s*check|highest directory holding",
                     step1)


def test_step_1_sends_a_coded_refusal_to_its_recovery_first():
    """`--auto` recipes stay unconfirmed by design, so the unconfirmed→Step 4 route
    outranked the reason the user invoked the skill: an `arch_mismatch` or
    `outside_target` that a knob fixes got a re-derivation instead, which loses the
    confirmation and leaves the refusal exactly where it was."""
    step1 = _section("Step 1")
    assert re.search(r"[Ii]f a coded refusal sent you here", step1)
    assert "recovery.md" in step1
    assert re.search(r"loses the user's confirmation", step1)


def test_seeded_state_is_tested_by_contents_not_for_null():
    """`existing_loci_state` is an object on every project: a virgin tree probes as
    `{"sidecars": [], "flags_json": null, "trace_kind": null, …}`, so "non-null"
    announced a migration on every project it was ever asked about. The rule lives in
    recovery.md, beside `--from-existing`, because it prescribes no action of its own."""
    sect = _section("Seeding from LOCI's own records", RECOVERY)
    assert "non-null" not in sect
    assert re.search(r"never it\s*for null|test its \*contents\*", sect)
    for key in ("sidecars", "flags_json", "trace_kind"):
        assert f"`{key}`" in sect, f"the seeded-state condition must name `{key}`"


def test_step_2_is_skipped_for_cargo_and_says_it_runs_a_build():
    """Keyed on language, a cargo-dominant mixed tree (`.data.languages == ["c","rust"]`,
    `build_system: "cargo"`) was sent into compdb.md, where no matrix row applies and
    the synthesize route needs a build system that does not exist — while plain
    `loci init` succeeds instantly. And the cargo path runs a full cargo build with no
    consent gate anywhere, leaving an untracked `Cargo.lock`."""
    step2 = _section("Step 2")
    assert re.search(r"[Ss]kip this step when `\.data\.build_system` is `cargo`", step2)
    assert ".data.languages" in step2, (
        "the gate must be the build system, not the language list")
    assert "Cargo.lock" in step2 and re.search(r"runs a real build", step2)


def test_step_2_is_re_entered_after_the_target_pick():
    """On a multi-image repo the ISA is not settled until Step 3's question is
    answered, so a coverage gate evaluated before the pick can be right and still
    leave the wrong database recorded."""
    step2 = _section("Step 2")
    assert re.search(r"come back here after the pick", step2)


def test_step_2_gate_is_coverage_and_currency_not_existence():
    """The second CRITICAL: on a bootloader+app repo whose only database is the
    bootloader's, Step 2 saw one and skipped acquisition, the user answered the
    target question correctly, and init refused with an architecture mismatch — while
    the relayed message told them to abandon the image they had chosen."""
    step2 = _section("Step 2")
    assert re.search(r'not "does one exist"', step2)
    assert "`targets`" in step2, "the gate needs `.data.compdbs[].targets`"
    assert "recipe_stale" in step2
    assert "bootloader" in step2, "name the shape that dead-ends"
    # An empty list is "none probe could find", not "none exists": a deep build layout
    # reports `compdbs: []` with a valid database sitting in the tree, which reaches
    # replay-compare when handed over directly.
    assert re.search(r"none (?:that )?probe could \*find\*", step2), (
        "an empty list is what probe could not find, not what does not exist")
    assert "compdb.md" in step2


def test_consent_precedes_touching_the_build_tree_in_both_files():
    step2 = _section("Step 2")
    assert "get a yes" in step2
    assert "changes something outside LOCI" in step2
    assert "will not run configure for you" in step2
    consent = _section("Consent, before anything runs", COMPDB)
    assert "get a yes" in consent
    assert "sticky" in consent, (
        "CMAKE_EXPORT_COMPILE_COMMANDS persists in the build cache — say so")
    assert re.search(r"[Dd]o not fall back to guessing flags", consent)


def test_the_cmake_route_checks_the_generator_and_reuses_the_cache():
    """Under Visual Studio or Xcode the flag is accepted, cached, and produces
    nothing — so the agent has mutated the tree for no result. And guessing `-G`
    makes CMake tell the agent to delete CMakeCache.txt and CMakeFiles/."""
    sect = _section("CMake, rule 1", COMPDB)
    assert "CMAKE_GENERATOR" in sect
    assert re.search(r"`Makefile` or `Ninja` generator", sect)
    assert re.search(r"Visual Studio|Xcode", sect)
    # The cold tree is the one the first fix missed: with no cache there is nothing to
    # inherit, so plain `cmake -B` picks this machine's default — Visual Studio, i.e.
    # the host compiler — exits 0, writes a build tree that did not exist, and produces
    # no database. The escape hatch the fix offered then needs a toolchain file it is
    # forbidden to guess.
    assert re.search(r"No cache file", sect), "the never-configured tree needs a branch"
    assert re.search(r"first configure", sect)
    assert re.search(r"[Dd]o not run it", sect)
    assert re.search(r"project's own configure line", sect), (
        "a cold tree's configure line comes from the project or the user")
    assert re.search(r"ask the user for it", sect)
    assert re.search(r"re-uses the cached generator", sect), (
        "and the warm Makefile/Ninja case still has its safe form")
    delete = _section("CMake, rule 2", COMPDB)
    assert re.search(r"\*\*Never act on that\.\*\*", delete), (
        "deleting a configured build tree on a build tool's suggestion must be "
        "forbidden outright — and CMake suggests it for TWO different failures")
    assert "renamed or copied project" in delete


def test_the_cmake_form_is_cwd_independent():
    """`-B` alone takes the SOURCE directory from the current directory, not the cache.

    From anywhere but the project root the "safe form" fails — "does not appear to
    contain CMakeLists.txt", or, from a subdirectory that has its own CMakeLists.txt,
    "does not match the source … used to generate cache", which is rule 2's trigger. The
    agent then reports the user's configured tree as stale and offers a cold second
    build directory, for what was only its own cwd. And the whole point of round 2's
    `--project-root` fix is that the session may be opened anywhere.
    """
    sect = _section("CMake, rule 1", COMPDB)
    assert re.search(r"\*\*but pass `-S` explicitly\.\*\*", sect)
    assert re.search(r"takes the source directory\s*from the \*current directory\*",
                     sect)
    assert "rule 2's trigger" in sect, "name what the failure gets mistaken for"
    row = _row("| CMake |", COMPDB)
    assert '-S "<root>"' in row, "the matrix row must show the cwd-independent form"
    for m in re.finditer(r"cmake -B ", _body(COMPDB)):
        window = _body(COMPDB)[max(0, m.start() - 90): m.start()]
        assert "plain " in window or "-S" in window, (
            "a bare `cmake -B` outside the rule that explains it reads as the "
            "recommended form")


def test_a_found_database_is_not_promised_the_top_tier():
    """`generated` gives a compile-check FLOOR, not `replay-compare`: LOCI compiles with
    `-g`, so a release build reaches compile-check and no higher — the CLI's own note
    says so, and the skill relays that note."""
    sect = _section("Look before you generate", COMPDB)
    assert "strongest validation tier" not in sect
    assert re.search(r"cannot record `unvalidated`", sect)


def test_the_recorded_regen_is_read_before_it_is_run():
    """It is a *build* command on a CMake tree (`cmake --build build`) and the truncating
    redirect on a ninja-only one, and its paths are relative to the project root — so
    "run what the message names" is wrong three different ways."""
    stale = _section("Regenerating a database that has gone stale", COMPDB)
    assert re.search(r"\*\*Read the recorded command before running it\*\*", stale)
    assert re.search(r"depends on the\s*build system", stale)
    assert "cmake --build build" in stale and "truncating" in stale
    assert re.search(r"relative to the project root", stale)


def test_the_ninja_route_does_not_truncate_the_existing_database():
    """`>` truncates before ninja runs, so a wrong `<build>` guess leaves a 0-byte
    file where the user's good database was, and init then refuses to parse it."""
    sect = _section("Ninja: never redirect straight onto the destination", COMPDB)
    assert ".json.new" in sect
    assert re.search(r"\bmv\b", sect)
    assert "0-byte" in sect
    # The table row is a separate surface, and a campaign that changed only the row
    # left the section intact.
    # The redirect target itself, not just a `.new` somewhere in the section: the
    # first `compile_commands.json.new` is the fence's `>` destination, and changing
    # only that left the `mv` line to satisfy a looser check.
    assert re.search(r">\s*\"<build>/compile_commands\.json\.new\"", sect), (
        "the `>` destination must be the .new file, never the real database")
    row = _row("| Ninja, no CMake |", COMPDB)
    assert ".new" in row, "the matrix row must not show the truncating form"
    # And the recipe's own recorded `regen` IS the truncating form, so the stale
    # section cannot simply say "run what the message names".
    stale = _section("Regenerating a database that has gone stale", COMPDB)
    assert re.search(r"[Rr]ead the recorded command before running it", stale)
    assert re.search(r"truncating form", stale)


def test_the_synthesis_route_forces_a_build_and_stays_in_the_project():
    """"build once, capture the log" observes nothing on an already-built tree:
    `make` answers "Nothing to be done" and you get zero compile lines."""
    sect = _section("Synthesizing, where no generator exists", COMPDB)
    assert "already-built tree compiles nothing" in sect, (
        "name the warm-tree hazard as the claim, not only as a symptom")
    assert "make -B" in sect, "and give the force flag"
    assert ".loci/build/compile_commands.json" in sect
    assert "--compdb-kind=synthesized" in sect
    assert "NEVER" in sect
    assert re.search(r"[Nn]ever invent a flag", sect)
    assert re.search(r"[Cc]arry both flags", sect)
    assert "add-file" in sect


def test_the_tool_existence_rule_is_stated():
    """Step 0 probes `loci` and `uv` only. `meson`, `pio`, `west`, `cbuild`,
    `bear` and `compiledb` are all absent on this machine, and the matrix names all
    six — so the agent asked consent to run `pio run -t compiledb` on a machine
    without `pio`, and the prose had no next move."""
    sect = _section("Check the tool exists first", COMPDB)
    for tool in ("meson", "pio", "west", "cbuild", "bear", "compiledb"):
        assert tool in sect, f"the tool-existence rule must name {tool}"
    assert re.search(r"none of them is LOCI's to install", sect)
    assert re.search(r"before asking for consent", sect)


def test_the_path_quoting_rule_is_stated():
    """Probe returns absolute paths only, and this machine has project directories with
    spaces and non-ASCII characters. Unquoted, `ninja -C <abs> … > <abs>/…` word-split,
    produced no database, and left a junk file at an unrelated path in the user's tree —
    the very thing the out-of-project rule exists to prevent."""
    flat = _flat(COMPDB)
    assert re.search(r"\*\*Quote every path you substitute\.\*\*", flat)
    assert re.search(r"absolute paths", flat)
    assert re.search(r"word-split", flat)


def test_the_with_make_hazard_is_documented_where_the_make_rows_are():
    sect = _section("`loci init probe --with-make`", COMPDB)
    assert "runs the project's build system" in sect
    assert "go-ahead" in sect
    assert re.search(r"warm tree|does not exist", sect), (
        "it only works while the build directory does not exist — say so")


def test_step_2_carries_the_compdb_flags_through_every_reinvocation():
    """Measured: the target-pick re-invoke recorded a hand-synthesized database as
    `generated`, which lowers no floor and permanently breaks `loci init add-file`
    (its refusal for a generated database names a regeneration command; a synthesized
    database has none, so `.error.regen` is null)."""
    step2 = _section("Step 2")
    assert re.search(r"\*\*carry\s*every answer flag through every later init call",
                     step2)
    assert "--accept-tier" in step2, (
        "a tier accepted in Step 3 must ride every later call: dropping it on "
        "`--confirmed` meets the refusal that made you ask, and Step 4's error branch "
        "then loops")
    step3 = _section("Step 3")
    assert re.search(r"--compdb-kind=…|--compdb-kind=<kind>", step3), (
        "the re-invoke template must carry the compdb flags")


def test_step_3_branches_on_every_code_and_on_the_uncoded_refusal():
    step3 = _section("Step 3")
    for code in ("init_needs_user", "init_unsupported", "init_failed",
                 "auth_required"):
        assert f"`{code}`" in step3, f"Step 3 must branch on {code}"
    assert "no `code` at all" in step3
    row = _row("| `init_unsupported`")
    assert "never retried" in row and ".error.detail" in row
    assert "recovery.md" in row, "the permanent-vs-unbuilt caveat lives there"
    row = _row("| `init_failed`")
    assert "re-arms next session start" in row and "not loop" in row
    assert "recovery.md" in row, (
        "three init_failed shapes need more than relaying; the row must send the "
        "reader there BEFORE it stops")
    assert "no recipe was written" in row, (
        "'nothing was left behind' was false — init writes the keyed context on "
        "every outcome, refusals included")


def test_the_init_failed_shapes_are_ordered_and_discriminated():
    """The three shapes overlapped with no precedence, and shape 3's discriminator
    ("the message says `--accept-tier` cannot lower it") is a VERBATIM quote of the
    message shape 1 covers — so on round-1 CRITICAL #2's own fixture a bottom-up read
    picked shape 3, whose `--target=auto` re-asks the question the user just answered.
    """
    shapes = _section("`init_failed` shapes", RECOVERY)
    assert re.search(r"precedence order", shapes), "the list must be ordered"
    # Keyed on a FIELD, not on how the message reads. Every prose discriminator anyone
    # tried matched the wrong row: on the C path every contradiction names a file
    # (`_check_arch` prefixes it), so "the message names a source file" also caught the
    # board-changed checkout — whose fix is `--target=auto`, and whose row-1 treatment
    # reconfigures the user's tree on a false diagnosis and then loops.
    assert re.search(r"\*\*Take the first\s*row that matches, in this order\*\*", shapes)
    # COMPARED, not tested for null. `recorded_target` is set from the *previous
    # recipe's* target at every raise site, so it is non-null on any re-initialization —
    # including a deliberate `--refresh --target=<other>`, where a null-test routes the
    # switch to the board-changed row and `--target=auto` loops back to the question the
    # user just answered. Verified: switching armv7e-m → armv6-m gives
    # `target: "armv6-m"`, `recorded_target: "armv7e-m"`.
    assert re.search(r"`\.error\.recorded_target` equals `\.error\.target`", shapes), (
        "row 3 fires when the tree refuted the recipe's OWN recorded answer")
    assert re.search(r"is null \(a first initialization\) or differs", shapes), (
        "row 4 is null-or-differing, i.e. the target came from this run")
    assert re.search(r"\*\*Compare the two fields; do not test `recorded_target` for "
                     r"null alone\.\*\*", shapes), (
        "say why, or the next reader writes the null test again — two rounds did")
    assert re.search(r"previous recipe's\* target at every raise site", shapes)
    assert re.search(r"carry neither field", shapes), (
        "some init_failed raises have neither; relay-and-stop is right for them")
    assert "fix is Step 2" in shapes
    assert "--accept-tier" in shapes and "the user's call" in shapes
    assert "--target=auto" in shapes
    assert re.search(r"cannot \*help\* with it", shapes), (
        "the near-identical no-database wording needs its own row")
    assert re.search(r"outside this\s*project", shapes), (
        "a tier offered on a genuinely broken tree is not merely undemonstrated")
    assert re.search(r"discriminates nothing", shapes), (
        "say that the shared sentence separates nothing, or the next reader keys on it")
    assert re.search(r"carry `--accept-tier` on every later\s*init call", shapes)


def test_the_unsupported_caveat_distinguishes_permanent_from_unbuilt():
    """The message the skill relays says an unbuilt image is indistinguishable from a
    host-only project, and a reviewer initialized such a tree successfully after
    building it. "Permanent, never retried" is about LOCI's arming, not the
    project."""
    sect = _section("`init_unsupported`: permanent", RECOVERY)
    assert "unbuilt image" in sect
    assert re.search(r"does \*\*not\*\* mean the project can never be initialized",
                     sect)
    assert re.search(r"build the target image and run `/loci:init` again", sect)


def test_the_one_question_is_one_and_the_option_cap_is_stated():
    step3 = _section("Step 3")
    assert re.search(r"[Ee]xactly one `AskUserQuestion`", step3)
    assert "at most four supported ISAs" in step3, (
        "state why the list always fits: four supported ISAs, so no fold is needed")
    assert "no folding" in step3
    assert "order is not a recommendation" in step3, (
        "candidates tie alphabetically inside an evidence class")
    assert re.search(r'`"; "`-joined', step3), (
        "`evidence` is one joined string per candidate, not a list to index")
    assert "names the image" in step3, (
        "where one database builds both images the first evidence facts are near "
        "identical; the artifact path distinguishes them")
    intro = _flat()[:_flat().index("## Step 0")]
    assert "One decision about the recipe: the target ISA" in intro
    # Two things are NOT that decision, and the claim was flatly false until both were
    # carved out: a reviewer counted three AskUserQuestions on an ordinary path
    # (reconfigure consent, tier acceptance, the confirm) against a headline of "one
    # question, ever".
    assert re.search(r"[Tt]wo things are not that decision", intro)
    assert "permission" in intro and "weaker validation tier" in intro


def test_a_free_form_answer_has_a_documented_route():
    assert "free-form" in _section("Step 3")
    ff = _section("A free-form target answer", RECOVERY)
    assert re.search(r"no\*?\*? `error.code`", ff)
    assert "cortex-m4" in ff, "core/family aliases do map — say so"
    assert "re-ask that same question once" in ff
    assert "still one decision" in ff


def test_the_headless_rule_covers_the_target_question_and_the_confirmation():
    """`AskUserQuestion` does not exist under `claude -p` — verified empirically by a
    reviewer. The target question had a headless branch; Step 4's confirmation, which
    fires on every single-candidate project, had none, leaving the model pressed
    toward the one act the skill forbids absolutely."""
    headless = _section("Headless runs")
    assert "do not attempt `AskUserQuestion`" in headless
    assert "loci init --target=<isa>" in headless
    assert re.search(r"[Dd]o \*\*not\*\* pick a target yourself", headless)
    assert "wrong image" in headless
    assert "Step 4's confirmation" in headless, (
        "the confirmation question needs a headless branch too")
    assert "never pass `--confirmed`" in headless
    assert "unconfirmed" in headless


def test_a_first_init_points_at_the_cockpit():
    """`loci cockpit` is one of the three commands that are the user's to run, and a
    fresh recipe is the moment there is finally something in it to look at."""
    step4 = _section("Step 4")
    assert "loci cockpit" in step4
    assert "*separate* terminal" in step4
    assert "You never run it" in step4
    assert "On a first init only" in step4, (
        "a re-init or a knob fix must not repeat the line")


def test_confirmation_is_never_fabricated_or_carried_onto_a_rejected_setup():
    intro = _flat()[:_flat().index("## Step 0")]
    assert "Never invent the answer" in intro
    assert re.search(r"`--confirmed` goes on the command line only after", intro)
    row = _bullet("- **Wrong target**")
    assert re.search(r"no `--confirmed`", row, re.I), (
        "the user said the setup was wrong — that is not consent for the next one")
    assert "probe" in row and ".data.candidates" in row, (
        "an already-initialized ok envelope reports `candidates: []`, so the "
        "alternatives have to come from probe")
    assert "--target=auto" in row, "and a fallback when probe offers none"


def test_step_4_relays_the_notes_and_names_the_authoritative_field():
    step4 = _section("Step 4")
    assert re.search(r"every line of `\.data\.notes`", step4), (
        "`.data.notes` is the only place init says a recipe it could not vouch for "
        "was replaced; a trim that drops it reads as a clean first run")
    assert re.search(r"top-level field is authoritative", step4)
    assert "recipe_summary" in step4
    assert "one short block" in step4
    assert re.search(r"cargo recipe has none", step4), (
        "the trim list must not demand an artifact line a cargo report lacks")


def test_step_4_has_an_error_branch_for_its_own_commands():
    """Both of Step 4's commands re-derive, so they meet every state Step 3 does — and
    a refusal from `--confirmed` on an unconfirmed recipe with a stale `prefer_output`
    left the agent in a loop, because Step 4's only exit was back to its own question.
    """
    step4 = _section("Step 4")
    assert re.search(r"[Ee]ither of this step's commands can refuse", step4)
    assert re.search(r"back to Step\s*3's table", step4)


def test_step_4_relays_the_warning_channels_a_trim_always_drops():
    """`.data.notes` is empty in exactly the cases the report carries an `!` line, and
    `.data.warnings` is a populated channel the skill never read: a database belonging
    to another checkout produced `notes: []`, two warnings and one `!` line, so a trim
    to the headline fields reported a clean five-line summary."""
    step4 = _section("Step 4")
    assert re.search(r"every line of\s*`\.data\.notes`", step4)
    assert re.search(r"every line of `\.data\.warnings`", step4)
    assert re.search(r"every `!` line in the report", step4)
    # The three are independent. Claiming `.data.notes` is empty "in exactly the cases"
    # an `!` line exists was false both ways — a clean first init has neither — and a
    # biconditional licenses checking one channel and inferring the others.
    assert re.search(r"independent\s*channels", step4)
    assert "in exactly the cases" not in step4
    assert re.search(r"absent rather than empty|absent, not empty", step4), (
        "`.data.warnings` is emitted on the already-initialized envelope only")


def test_step_4_says_the_report_can_overstate_the_confirmation():
    """`_reestablish` renders `.data.report` from the recipe *document*, so after init
    re-establishes a missing integrity record the report reads
    `confirmed by user: True` one line above a `.data.confirmed_by_user` of `false`.
    Round 3's escrow route is what first sends an agent to that envelope, so the
    tie-breaker has to cover the report and not just `recipe_summary`."""
    step4 = _section("Step 4")
    assert re.search(r"the \*\*report\*\*,\s*which renders the document", step4)
    assert re.search(r"Relay the report, then correct it from the field", step4)


def test_step_4_qualifies_what_takes_effect_immediately():
    """The unqualified claim was false: the hooks read the keyed context per fire,
    but the measurement skills take `<loci_target>` from the SessionStart line, so a
    mid-session switch does not reach `/loci:exec-trace` until a restart."""
    step4 = _section("Step 4")
    assert "governs the hooks immediately" in step4
    assert re.search(r"session-start line", step4)
    assert re.search(r"until the session\s*restarts", step4)
    # It does not silently measure the old target — it REFUSES. Saying "does not
    # reach" understated it and left the user unprepared for the refusal.
    assert re.search(r"\*\*refuses\*\* with `arch_mismatch`", step4)


def test_outside_target_is_the_knob_and_arch_mismatch_is_four_things():
    """Round 1 fixed a mis-route by over-correcting into the opposite universal.

    `outside_target` has one raise site and `prefer_output` is its fix. `arch_mismatch`
    has FOUR (`recipe_flags.py:303`, `:958`, `:1106`, `cargo.py:1216`) and only `:958`
    names that knob; two of the others name `/loci:init --refresh` in their own
    message, and on a cargo project `set …prefer_output` is *refused* ("there is no
    compile database for it to select from") — leaving the agent in recovery.md's
    "never re-send it unchanged" with the real fix forbidden.
    """
    sect = _section("`arch_mismatch` and `outside_target`", RECOVERY)
    assert re.search(r"\*\*`outside_target` has one cause and one fix", sect)
    assert "build.compdb.select.prefer_output" in sect
    assert re.search(r"\*\*`arch_mismatch` has four causes", sect), (
        "and only one of them is that knob")
    for cause in ("this compile asked for one target",
                  "`mode:\"replace\"` pin", "rust.triple"):
        assert cause in sect, f"the four-cause table must carry {cause!r}"
    assert re.search(r"no compile database here", sect), (
        "the cargo row must say why the knob cannot apply")
    assert "mid-session target switch" in sect, (
        "Step 4's own instruction produces one of these four")
    step5 = _section("Step 5")
    assert "arch_mismatch" in step5 and "four causes" in step5, (
        "the pointer must not restate the false universal")
    refresh = _row("| `loci init --refresh`", RECOVERY)
    assert "arch_mismatch" not in refresh and "outside_target" not in refresh, (
        "the --refresh row must not claim these two wholesale")


def test_the_variants_table_has_exactly_four_rows():
    """`_row`'s exact prefix requires the closing backtick, so a fifth row —
    `| `loci init --refresh --target=auto` | … | the recovery `arch_mismatch` and
    `outside_target` name |` — was invisible to the assertion above and restored the
    mis-route with every test green."""
    rows = [l for l in _body(RECOVERY).splitlines()
            if l.startswith("| `loci init")]
    assert len(rows) == 4, (
        f"the four-variants table must have exactly four rows, found {len(rows)}: "
        f"{[r[:40] for r in rows]}")


def test_the_refresh_row_still_claims_the_four_codes_it_does_fix():
    refresh = _row("| `loci init --refresh`", RECOVERY)
    for code in ("recipe_stale", "recipe_tampered", "recipe_invalid",
                 "compiler_missing"):
        assert code in refresh, f"--refresh IS the recovery {code} names"
    assert "--target=auto" in refresh
    assert re.search(r"regenerate the compile database \*\*before\*\*", refresh)


def test_every_variant_is_documented_with_its_consent_rule():
    for variant in ("| `loci init --refresh`", "| `loci init set <key>=<value>`",
                    "| `loci init add-file <src>`", "| `loci init --from-existing`"):
        _row(variant, RECOVERY)
    setter = _row("| `loci init set <key>=<value>`", RECOVERY)
    assert "confirm the value with the user before invoking" in setter
    assert "does not mark the recipe confirmed" in setter
    assert "`target` is not settable" in setter
    seed = _row("| `loci init --from-existing`", RECOVERY)
    assert "installed **now**" in seed and "say which compiler it will use" in seed
    addf = _row("| `loci init add-file <src>`", RECOVERY)
    assert "**synthesized** databases only" in addf and ".error.regen" in addf


def test_add_file_does_not_promise_a_regeneration_command():
    """`.error.regen` is null whenever the recipe records none, and the message then
    says only "regenerate it the way this project generates it" — so a row promising the
    command sends the agent to invent one."""
    row = _row("| `loci init add-file <src>`", RECOVERY)
    assert re.search(r"\*\*null\*\* when the recipe records none", row)


def test_the_uncoded_refusal_rule_is_stated_where_set_and_add_file_are():
    sect = _section("Refusals with no `error.code`", RECOVERY)
    assert "caller error" in sect and "exit 2" in sect
    assert re.search(r"[Nn]ever re-send it unchanged", sect)


# ── 4. contradiction: the axis a keyword lint cannot see ────────────────────

#: Per section, escape-hatch wordings a campaign used to REVERSE a rule while keeping
#: every phrase the positive assertions look for. Each was verified to pass the whole
#: prose-lint suite before this table existed.
CONTRADICTIONS: tuple[tuple[Path, str, str, str], ...] = (
    (BOOTSTRAP, "`uv` missing",
     r"installs host tools for you|you may install uv",
     "reverses the host-tool policy"),
    (BOOTSTRAP, "`loci` missing",
     r"[Ss]top waiting|carry on without it|do not wait|safe to start beside",
     "licenses skipping the in-flight install wait"),
    # The setup-first route is one sentence away from its own reversal: the installer
    # is still documented right below it as the fallback, so "just run it" reads as a
    # shortcut rather than the regression it is — no `uv` check, no doctor pass, and
    # no report of either when the install fails.
    (BOOTSTRAP, "`loci` missing",
     r"skip `?/loci:setup|no need (?:to run|for) `?/loci:setup"
     r"|installer instead of `?/loci:setup|run the installer first",
     "puts the bare installer back in front of `/loci:setup` for a missing CLI"),
    (SKILL, "Step 2",
     r"do not need to (?:get a yes|ask)|no need to ask|without asking",
     "licenses reconfiguring the user's build tree unasked"),
    (COMPDB, "Consent, before anything runs",
     r"do not need to (?:get a yes|ask)|no need to ask|without asking",
     "reverses the consent rule where the commands actually are"),
    (SKILL, "Headless runs",
     r"pick (?:the|a) (?:first|likeliest|best)|must not stop|ask anyway",
     "licenses guessing a target, or asking, with nobody to answer"),
    (SKILL, "Step 3", r"[Oo]ffer every candidate|as many options as",
     "ignores AskUserQuestion's four-option maximum"),
    # The reorder's own reversal: a readiness line that absorbs the caveat, or one
    # printed whatever `.data.validated` says. Either turns the strongest claim this
    # skill makes into decoration.
    (SKILL, "Step 4", r"fold (?:it|the warning|the caveat) into|inside the readiness"
                      r" line|say it validated either way|whatever `\.data\.validated`",
     "lets the readiness line absorb the caveats, or claims validation it has not"),
    (VOICE, "Outcome first",
     r"drop the (?:notes|warnings)|paraphrase the refusal|shorten `\.error\.detail`"
     r"|the vocabulary is banned",
     "trims the two classes of sentence that never shrink, or deletes the machinery "
     "instead of demoting it"),
    (VOICE, "Step 1 \u2014 say what you recognised",
     r"give (?:them|the user) an ETA|usually takes a|say how long it will take",
     "puts back a duration this step cannot know"),
    (VOICE, "Before you ask to run something",
     r"skip the specifics|no need to say what it changes|tell them afterwards",
     "turns consent into an announcement"),
    (VOICE, "While it runs",
     r"tick a stage you are about to|paste the build (?:log|output)"
     r"|narrate each command|tick every stage up front|drop the offer",
     "restores the narration, ticks a stage that has not happened, or takes the "
     "detail away instead of offering it"),
    (VOICE, "Step 4 \u2014 ready",
     r"ask (?:it )?with `AskUserQuestion`|a second question is fine"
     r"|name a function you think",
     "spends a question this skill does not have, or invents a function"),
    (SKILL, "Step 4", r"[Ss]kip the confirmation|no need to confirm",
     "licenses an unconfirmed recipe reported as confirmed"),
    # T15. Two rules, and inverting either produces a confident wrong answer
    # rather than a visible failure: sending a Go user to `bear` for a file the
    # go tool never writes, and recording a knob that reaches no build while
    # reading as applied in the recipe.
    (GO, "<intro>",
     # Third cut. Round 1 wrote it (1/11, and it fired on the shipped prose);
     # round 1's fix made it instruction-shaped (14/14 on hand-written cases);
     # round 2 generalised the test set and it scored 1/10. Widened here from
     # reversals a person would actually write, in both directions — an
     # instruction to go get a database, and a claim that Go has one.
     #
     # Coverage is partial and deliberately so: this mechanism catches the
     # wordings a campaign used, exactly as its siblings do (`[Ss]kip the
     # confirmation|no need to confirm`).
     #
     # The residual, stated rather than papered over: `_NEGATED` is a
     # 24-character ANCHORED look-back, so it clears "never regenerate a compile
     # database" — where the negation adjoins the hit — and not "do not tell the
     # user to regenerate a compile database", where it is twenty characters
     # away. Write the rule the first way. Widening `_NEGATED` is not the fix:
     # its canary (`test_the_negation_window_still_catches_a_licence`) exists
     # because widening it by five characters made every contradiction test in
     # this file vacuous.
     r"(?<![a-z])(?:[Gg]enerate|[Rr]egenerate|[Pp]roduce|[Cc]reate|[Hh]and)\s+"
     r"(?:\w+['\u2019]?s?\s+){0,2}(?:an?|the|one)?\s*"
     r"(?:`?bear`?|compile[_ ](?:database|commands)|`?compdb\.regen`?)"
     r"|(?:[Rr]un|[Uu]se|[Tt]ry)\s+(?:\w+['\u2019]?s?\s+){0,3}"
     r"(?:`?bear`?|`?compdb\.regen`?)|bear\s+--"
     r"|(?:[Gg]o|module|project)s?\s+(?:do|does|can|will)?\s*ha[sv]e?\s+a\s+compile"
     r"|[Tt]here\s+is\s+a\s+compile[_ ](?:database|commands)"
     r"|(?:still\s+)?(?:be\s+given|needs?|requires?)\s+a\s+compile[_ ](?:database|commands)"
     r"|(?:[Pp]oint|[Ss]end|[Rr]efer)\s+(?:\w+\s+){0,3}(?:at|to)\s+`?compdb\.md"
     r"|-DCMAKE_EXPORT_COMPILE_COMMANDS|(?:[Tt]urn on|[Ee]nable)\s+`?CMAKE_EXPORT"
     r"|`?set`?[^\n]{0,48}(?:is enough|suffices|on its own|is all you need)"
     r"|(?:no need|not necessary|unnecessary)[^\n]{0,48}--refresh"
     r"|(?:[Ss]kip|[Ss]kipping)\s+the\s+`?--refresh"
     r"|`?--refresh`?[^\n]{0,24}(?:is optional|can wait)"
     r"|(?:takes effect|applies|works)\s+(?:straight away|immediately|at once)"
     r"|re-?renders?\s+automatically|picks it up automatically",
     "sends a Go project after a compile database, or leaves a knob that "
     "reaches no build"),
    (RECOVERY, "`arch_mismatch` and `outside_target`",
     r"`--refresh` fixes (?:both|it)|re-run `--refresh` for (?:both|these)",
     "restores the mis-route --refresh cannot fix"),
    (COMPDB, "CMake, rule 2",
     r"(?:so|then) delete |remove the cache and|wipe the build (?:tree|dir)",
     "licenses deleting a configured build tree"),
    (COMPDB, "CMake, rule 1",
     r"default generator is fine|any generator will do",
     "licenses the cold-tree configure that records the host compiler"),
    (SKILL, "Step 1",
     r"[Ss]kipping the regeneration|initializing over the stale database is"
     r"|acceptable to re-derive over",
     "licenses initializing over a stale compile database"),
    (SKILL, "Step 1",
     r"the (?:shell's|current) directory is fine|--project-root is optional",
     "licenses initializing a subtree"),
    (SKILL, "Step 1",
     r"stopping is the kinder|saying so and stopping|report it and stop"
     r"|stop here rather than|spend a run on Step 3",
     "licenses stopping on a tree Step 3 would classify as transient"),
    (SKILL, "Step 5", r"`--refresh` fixes (?:both|all)",
     "restores the arch_mismatch mis-route in the pointer"),
    (RECOVERY, "`init_unsupported`",
     r"say the project is permanently unsupported and stop"
     r"|no need to mention the unbuilt",
     "reverses permanent-vs-unbuilt"),
    (RECOVERY, "`init_failed` shapes",
     r"any order|the order does not matter",
     "removes the precedence the overlapping messages need"),
    (BOOTSTRAP, "Sign-in", r"[Rr]un `loci login` (?:yourself|for them)",
     "licenses running a browser OAuth flow the agent cannot complete"),
    (COMPDB, "Generators, by build system",
     r"--wipe|-G Ninja -DCMAKE_EXPORT",
     "puts a destructive or forbidden form in the matrix itself"),
    (COMPDB, "Synthesizing, where no generator exists",
     r"copy (?:a|the) flag set|approximate the flags",
     "licenses invented flags"),
    # ── round 3: the sections a 42-mutation campaign reversed with 106/106 green ──
    (SKILL, INTRO,
     r"where the answer is obvious|you may pass it|it is safe to assume",
     "licenses a fabricated confirmation — the one absolute rule"),
    (SKILL, INTRO, r"[Cc]ommit (?:it|the recipe)",
     "reverses the machine-local split"),
    (SKILL, "Step 0", r"[Aa]ny(?:thing)? argparse error is a stale CLI"
                      r"|prints nothing on stdout is a failed init",
     "restores the over-broad stale-CLI rule that loops through the installer"),
    # The gate's own previous wording is the reversal now: Step 0 used to forbid
    # routing through `/loci:setup`, and a missing or behind-the-pin CLI ran the
    # installer inline instead — which checks no prerequisite and verifies nothing.
    (SKILL, "Step 0", r"do \*\*not\*\* require `/loci:setup` first"
                      r"|without running `/loci:setup`|the pin is only advisory",
     "sends a missing or behind-the-pin CLI back to the inline installer"),
    (SKILL, "Step 4", r"[Ii]f a recipe exists, say the project is initialized"
                      r"|no need to relay the notes",
     "licenses reporting an unvouched or broken recipe as healthy"),
    (SKILL, "Step 5", r"`--refresh` fixes (?:both|all)",
     "restores the arch_mismatch mis-route in the pointer"),
    (BOOTSTRAP, "`loci` present but too old",
     r"reinstall on any error|treat any failure as a stale CLI",
     "widens the stale-CLI signature"),
    # The reversal here is the naive reading of the gate: "version differs ⇒
    # reinstall". The pin is a FLOOR — the CLI only moves forward — so an equality
    # test sends every dev checkout and every machine ahead of an older plugin's pin
    # to `/loci:setup`, on every invocation, to install nothing. The unknown case is
    # the same trap by another route: no pin line, no plugin dir, or a `--version`
    # that prints nothing parseable is not evidence of a stale CLI.
    (BOOTSTRAP, "`loci` behind",
     r"different from the pin|any difference|does not match the pin"
     r"|reinstall (?:it )?anyway|newer (?:is|counts as) stale"
     r"|unknown (?:means|is) (?:stale|behind)",
     "turns the floor into an equality, so a newer or unreadable version reinstalls"),
    (BOOTSTRAP, "A note on timeouts",
     r"timed-out call (?:means|is) (?:a|the) fail",
     "reads a timeout as a failed install"),
    (COMPDB, "Look before you generate",
     r"walk the tree for build directories|search for build directories",
     "re-grows the evidence search in the file where the temptation lives"),
    (COMPDB, "Check the tool exists first",
     r"install it with the|install the missing tool",
     "reverses the host-tool policy where the tools are named"),
    (COMPDB, "CMake, rule 3", r"a reasonable `-G`|guess the toolchain",
     "licenses guessing the generator or the toolchain file"),
    (COMPDB, "Ninja: never redirect straight onto the destination",
     r"the direct form is fine|redirect straight onto",
     "restores the truncating redirect as an exception"),
    (COMPDB, "`loci init probe --with-make`", r"harmless|read-only",
     "denies that --with-make runs the build"),
    (COMPDB, "Multi-image repos generate per image",
     r"any (?:image|database) will do|either image is fine",
     "restores the wrong-image database"),
    (COMPDB, "Regenerating a database that has gone stale",
     r"[Rr]egeneration is optional|skip the regeneration",
     "restores initializing over a stale database"),
    # The artifact-only recipe's rule is a BOUNDARY: three analyses read the
    # binary, timing and energy do not, and the database that would lift them is
    # the user's build to produce. Both halves are reversible in the same
    # sentence — "time it anyway" erases the boundary, and "generate one
    # yourself" erases whose job it is by running their build or inventing flags,
    # which is the very trap `Synthesizing` is guarded against one section up.
    (COMPDB, "When the build cannot be observed",
     r"tim(?:e|ing) (?:it |them )?(?:anyway|still works)|"
     r"run (?:the|their) build (?:for them|yourself)|generate one yourself",
     "erases the boundary the recipe records, or moves the database onto us"),
    (RECOVERY, "The four variants",
     r"invoke `set` first|mention it afterwards|without asking first",
     "reverses the `set` consent rule the task names as binding"),
    (RECOVERY, "Seeding from LOCI's own records", r"non-null",
     "restores the null test that is true of every project"),
    (RECOVERY, "A free-form target answer",
     r"pick the closest|map it yourself|choose on their behalf",
     "licenses answering the target question for the user"),
    (RECOVERY, "`init_failed` shapes",
     r"any order|the order does not matter|add the flag yourself",
     "removes the precedence, or licenses taking the tier decision"),
    (RECOVERY, "Refusals with no `error.code`",
     r"re-send it|retry the same call",
     "licenses re-sending a caller error unchanged"),
    (RECOVERY, "`arch_mismatch` and `outside_target`",
     r"edit(?:ing)? `?\.loci/build\.yaml`? (?:by hand )?is",
     "licenses hand-editing the recipe"),
)

def test_every_rule_section_has_a_hatch_entry():
    """Completeness, so the coverage cannot rot back.

    A 42-mutation campaign found 14 sections with no `CONTRADICTIONS` entry, and
    reversed a settled fix in most of them with the whole suite green. Coverage measured
    by hand does not stay measured, so this asserts it.

    There is deliberately **no exemption list**. One was added here and a verification
    pass showed every entry in it was already covered by a `CONTRADICTIONS` prefix — so
    it excused nothing, while its docstring ("sections whose content is reference rather
    than a rule") invited a future reader to add a rule-bearing section to it and get a
    real exemption for free. Every section in these four files carries a rule; if one
    ever does not, it probably should not be a section.
    """
    covered = {(p.name, h) for p, h, _pat, _why in CONTRADICTIONS}
    missing = [
        f"{path.name}: {m.group(1).strip()}"
        for path in DOCS
        for m in re.finditer(r"(?m)^ {0,3}#{2,3} (.+)$", _body(path))
        if not any(m.group(1).strip().startswith(h)
                   for p, h in covered if p == path.name)
    ]
    assert not missing, (
        "every section in these files carries a rule, so every one needs a "
        "CONTRADICTIONS entry — these have none:\n  " + "\n  ".join(missing))


#: A prohibition is not a licence. These sections FORBID the very wordings the
#: patterns hunt for ("**never run `loci login` yourself**", "never copy a flag set
#: from another project"), so a match preceded by a negation inside this window is the
#: rule being stated, not reversed.
_NEGATED = re.compile(r"(?:never|not|cannot|do not|don't|forbidden|no need to)\s*$",
                      re.I)


def test_the_negation_window_still_catches_a_licence():
    """`_NEGATED` is an allowlist, so it is a hole the size of its own pattern —
    appending `|\\w+` to it makes every hit read as negated and all 30+ contradiction
    tests pass vacuously, which a reviewer proved with five characters. This is the same
    canary `test_the_kwarg_exemption_is_not_a_laundry` gives the other allowlist."""
    # The window is the 24 chars BEFORE a hit, so the canary has to end the way real
    # prose does — in a word. An earlier version ended in punctuation, which `\w+\s*$`
    # cannot match, so the widened pattern passed the canary and disarmed the table
    # anyway.
    for benign in ("and why, and get a yes", "before running any of it",
                   "so say what you are about to run"):
        assert not _NEGATED.search(benign), (
            f"_NEGATED matched {benign!r}, which contains no negation — it has been "
            f"widened into a blanket exemption and every CONTRADICTIONS test below is "
            f"now vacuous")
    for real in ("you must never ", "which you should not ", "this is forbidden "):
        assert _NEGATED.search(real), f"the negation window stopped seeing {real!r}"
    # And the ANCHOR, which is the whole point: without the trailing `$` the window
    # becomes "does any negation-ish substring appear in the preceding 24 chars", which
    # `Nothing`, `notes`, `note` and `another` all satisfy — so a licence introduced by
    # "Nothing more is owed:" reads as negated. Deleting one character defeated the
    # alternation-only canary while every assertion above still passed.
    for trailing in ("Nothing more is owed: ", "the notes say ", "another way is "):
        assert not _NEGATED.search(trailing), (
            f"_NEGATED matched {trailing!r}, where the negation-ish word is not the "
            f"trailing one — the `$` anchor has been dropped and the window no longer "
            f"means 'this hit is negated'")


@pytest.mark.parametrize(
    "path,heading,pattern,why", CONTRADICTIONS,
    ids=[f"{p.name}:{h[:18]}:{w[:24]}" for p, h, _pat, w in CONTRADICTIONS])
def test_no_escape_hatch_contradicts_the_rule(path: Path, heading: str,
                                              pattern: str, why: str):
    sect = _section(heading, path)
    for hit in re.finditer(pattern, sect):
        before = sect[max(0, hit.start() - 24): hit.start()]
        if _NEGATED.search(before):
            continue
        assert False, (
            f"{path.name} / {heading}: {why} — found {hit.group(0)!r} with no negation "
            f"before it ({before!r}). The positive assertion for this rule still "
            f"passes, which is why this test exists.")


def test_the_committed_vs_machine_local_split_is_not_reversed():
    """The recipe is machine-local and gitignored. "Commit it so the team shares one
    recipe" kept every other phrase intact and no test noticed."""
    intro = _flat()[:_flat().index("## Step 0")]
    assert "machine-local" in intro
    assert "gitignores it" in intro
    assert not re.search(r"[Cc]ommit (?:it|the recipe)", intro)


def test_the_envelope_discipline_paragraph_survives():
    """Deleting it outright passed every test the first time round."""
    intro = _flat()[:_flat().index("## Step 0")]
    assert re.search(r"[Oo]ne JSON envelope on stdout", intro)
    assert "`jq`" not in intro, (
        "the envelope discipline no longer routes through `jq`: the envelope is "
        "printed, and the model reads `ok` out of it")
    assert "`ok`" in intro
    assert re.search(r"never via Python|never through Python", intro)


# ── 5. paths, links and size ───────────────────────────────────────────────

@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.name)
def test_no_out_of_project_write_is_instructed(path: Path):
    """The repo lint globs `SKILL.md` only, and the first version of this one
    parametrized `(SKILL, COMPDB)` — so an out-of-project write instructed from
    `recovery.md` or `bootstrap.md` was green, proven twice ("Write the candidate
    patterns to /tmp/loci-prefer.txt", "Capture the installer's output to
    /tmp/loci-install.log"). Every file, and every mention has to be a prohibition."""
    text = _raw(path)
    for m in re.finditer(r"(?<![A-Za-z0-9_])/(?:tmp|var/tmp)\b", text):
        window = text[max(0, m.start() - 200): m.end() + 200]
        assert re.search(r"\bNEVER\b|\bnever\s+(?:write|use)\b", window), (
            f"{path.name}: /tmp at offset {m.start()} is not bracketed by an "
            f"unconditional prohibition")


@pytest.mark.parametrize("path", (SKILL, COMPDB), ids=lambda p: p.name)
def test_the_tmp_prohibition_is_present_and_unconditional(path: Path):
    """Vacuous on deletion before: the loop body never ran once the only `/tmp`
    mention was removed, so the test that claims to "fail loudly if THIS skill loses
    the rule" passed."""
    text = _raw(path)
    hits = list(re.finditer(r"(?<![A-Za-z0-9_])/(?:tmp|var/tmp)\b", text))
    assert hits, (
        f"{path.name} writes files (or forbids writing them) — the out-of-project "
        f"prohibition must be present, not merely unviolated")
    for m in hits:
        window = text[max(0, m.start() - 200): m.end() + 200]
        assert re.search(r"\bNEVER\b|\bnever\s+(?:write|use)\b", window), (
            f"{path.name}: /tmp at offset {m.start()} is not bracketed by an "
            f"unconditional prohibition")


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.name)
def test_every_relative_link_resolves(path: Path):
    """Four files now; a dead pointer silently drops a whole step's procedure. The
    repo's own anchor lint globs `*/SKILL.md`, so links inside the three reference
    files are checked by nothing else — anchored ones included, which the first
    version of this regex excluded."""
    offenders = []
    for target in re.findall(r"\]\(([^)\s]+)\)", _raw(path)):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        file_part, _, anchor = target.partition("#")
        dest = (path.parent / file_part).resolve() if file_part else path
        if not dest.is_file():
            offenders.append(f"{target} (no such file)")
            continue
        if anchor:
            body = dest.read_text(encoding="utf-8")
            slugs = {re.sub(r"[^a-z0-9]+", "-", h.lower()).strip("-")
                     for h in re.findall(r"(?m)^#{1,6} (.+)$", body)}
            if f'id="{anchor}"' not in body and anchor.lower() not in slugs:
                offenders.append(f"{target} (no such anchor)")
    assert not offenders, f"{path.name}: dead links {offenders}"


def test_every_reference_file_is_actually_referenced():
    """A file nothing points at is a file the model never reads."""
    flat = _flat(SKILL)
    for ref in (BOOTSTRAP, COMPDB, RECOVERY, GO, VOICE):
        assert f"]({ref.name})" in flat, (
            f"{ref.name} exists but SKILL.md never links to it")


def test_the_skill_stays_inside_its_size_budget():
    raw = SKILL.read_bytes().replace(b"\r\n", b"\n")
    crlf = len(raw.replace(b"\n", b"\r\n"))
    assert crlf <= SIZE_BUDGET, (
        f"skills/init/SKILL.md is {crlf} bytes with CRLF endings, over the "
        f"{SIZE_BUDGET}-byte budget. Procedure belongs in bootstrap.md / compdb.md / "
        f"recovery.md, which load only when a step sends the reader there.")


def test_the_reference_files_stay_proportionate():
    """A tripwire, not a criterion. If these grow far past the skill itself the split
    has stopped being "procedure out of line" and become the cascade prose this
    initiative is deleting, wearing four filenames."""
    skill = len(SKILL.read_bytes())
    refs = sum(len(p.read_bytes()) for p in (BOOTSTRAP, COMPDB, RECOVERY))
    assert refs <= 2 * skill, (
        f"reference files total {refs} bytes against SKILL.md's {skill}")
