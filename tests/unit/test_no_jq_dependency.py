"""Nothing this plugin ships may invoke `jq` — hook, library or skill body.

`jq` is a HOST dependency the plugin bundles nothing for, so every `jq` on a path
a user's session takes is a way for LOCI to be silently absent on a fresh machine
— the hooks that used it gated themselves off with `command -v jq || exit 0`,
which loses the measurement and says nothing.

Todo 044 removed them in two phases. The payload reads went into
`loci hook edit-scan` and the other `loci hook` verbs, and the small forkless
reads into `lib/loci_json.sh`; then the SKILL BODIES stopped telling the model to
pipe envelopes through one. That second half is the same defect one layer up: a
`jq` in a fenced recipe is a host dependency the plugin does not ship, and on a
machine without one the model either skips the read or invents a parser.

The file lists below ARE the assertion. A new hook, library or skill is covered
the moment it exists, which is the property a grep-for-a-string lint cannot give.

Three exemptions, all deliberate:

* `lib/eval-metrics.sh` and `lib/eval-graders.sh` are the developer's eval
  harness. Nothing a user runs reaches them, so they do not carry the "works on a
  fresh machine" motivation this lint exists for.
* `setup/setup.sh` may still USE jq where it finds it. It runs before the CLI
  exists, so it has nothing else to parse with — but it must not require it, and
  `test_setup_can_run_without_jq` is what pins that.

Naming jq is not using it. Three documents say, in prose, that it is NOT a
prerequisite — that is the fact this lint protects, and
`test_no_skill_probes_for_jq` is what keeps the prose from turning back into a
probe.

AAD-7590 is the third failure in the family, and neither lint above sees it: a
skill can NAME an envelope field, invoke no jq and probe for none, and still get
one — because naming `.data.checks[]` without saying where it is read from leaves
the model to supply the read itself.
`test_a_skill_naming_a_field_says_the_field_is_read_off_the_print` is that half.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent

#: The eval harness, and why it is not in scope. See the module docstring.
EXEMPT = {"eval-metrics.sh", "eval-graders.sh"}

#: A `jq` invocation, not the word: `find_jq`, `"$JQ"` and prose about jq are all
#: legitimate in the files that still route around it.
_INVOCATION = re.compile(r"(?<![\w.$/-])jq\s+(?:-|'|\"|\.)", re.M)

#: An envelope field named in prose, in either spelling: `data.healthy` and the
#: jq-filter `.data.checks[]` are the same instruction to the model.
_ENVELOPE_FIELD = re.compile(r"`\.?data\.[a-z_]")

#: The half that says what to DO with the field — stated by the skill, or reached
#: through the shared contract that states it.
_READ_IT_OFF_THE_PRINT = re.compile(r"let it print", re.I)


def _shipped() -> list[Path]:
    files = sorted(PLUGIN_ROOT.glob("hooks/*.sh")) + sorted(PLUGIN_ROOT.glob("lib/*.sh"))
    return [f for f in files if f.name not in EXEMPT]


def _model_facing() -> list[Path]:
    """Every markdown a skill hands the model — `SKILL.md` and its references."""
    return sorted(PLUGIN_ROOT.glob("skills/**/*.md"))


def _skill_bodies() -> list[Path]:
    """One per skill: the document the model is handed FIRST. A reference file
    (`init/compdb.md`, `_shared/verdicts.md`) is only ever reached through one of
    these, so the rule its SKILL.md carries is already in context by then."""
    return sorted(PLUGIN_ROOT.glob("skills/*/SKILL.md"))


def _rel(path: Path) -> str:
    return path.relative_to(PLUGIN_ROOT).as_posix()


def test_there_is_something_to_check():
    """Non-vacuous: a glob that stops matching passes this file silently."""
    names = {f.name for f in _shipped()}
    assert {"post-edit-hook.sh", "pre-edit-hook.sh", "contract-guard.sh",
            "session-init.sh", "setup-steps.sh"} <= names, sorted(names)


@pytest.mark.parametrize("path", _shipped(), ids=lambda p: p.name)
def test_no_shipped_hook_or_library_invokes_jq(path):
    lines = [f"{i}: {ln.strip()}"
             for i, ln in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
             if not ln.lstrip().startswith("#") and _INVOCATION.search(ln)]
    assert not lines, (
        f"{path.name} invokes jq, which is a host dependency this plugin does not "
        f"ship. Payload and envelope reads belong in a `loci hook` verb or in "
        f"lib/loci_json.sh:\n  " + "\n  ".join(lines))


def test_there_are_skill_bodies_to_check():
    """Non-vacuous, on the same reasoning as the hook glob above: the six documents
    phase 2 cleared are the ones a drifting glob would stop matching."""
    names = {_rel(p) for p in _model_facing()}
    assert {"skills/_shared/loci-runtime-contract.md",
            "skills/loci-post-edit/SKILL.md",
            "skills/loci-preflight/SKILL.md",
            "skills/exec-trace/SKILL.md",
            "skills/bug-report/SKILL.md",
            "skills/setup/SKILL.md"} <= names, sorted(names)


@pytest.mark.parametrize("path", _model_facing(), ids=_rel)
def test_no_skill_tells_the_model_to_pipe_an_envelope_through_jq(path):
    """A `loci` envelope is printed where the model can read it. A recipe that pipes
    it through `jq` instead needs a host tool the plugin does not ship, and buries
    the answer in a shell variable the next Bash call cannot reach."""
    lines = [f"{i}: {ln.strip()}"
             for i, ln in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
             if _INVOCATION.search(ln)]
    assert not lines, (
        f"{_rel(path)} instructs a jq invocation. Read the field out of the printed "
        f"envelope, or ask the CLI for what the skill wants — `loci elf diff` "
        f"answers with `data.functions` and `loci elf stack --comparing-elf` with "
        f"`data.frame_deltas` for exactly this reason:\n  " + "\n  ".join(lines))


@pytest.mark.parametrize("path", _model_facing(), ids=_rel)
def test_no_skill_probes_for_jq(path):
    """`jq` is not a prerequisite any more, so no skill may gate on finding one.

    Separate from the invocation lint because it is the opposite failure: a skill
    with no `jq` in a single fence can still stop a whole session at Step 0 for want
    of it."""
    text = path.read_text(encoding="utf-8")
    assert "command -v jq" not in text, (
        f"{_rel(path)} probes for `jq`. Nothing the plugin ships runs one, so a host "
        f"without it is a working host and must not be told otherwise")


def test_setup_can_run_without_jq():
    """`setup/setup.sh` is the one place jq may still be used, because it runs
    before the CLI it installs. It may not REQUIRE it: the hooks.json check says
    it was skipped rather than failing the setup."""
    text = (PLUGIN_ROOT / "setup" / "setup.sh").read_text(encoding="utf-8")
    assert "no jq or python" in text, (
        "setup.sh no longer names the no-jq path, so a host without jq cannot be "
        "told what was skipped")


def test_there_are_skill_bodies_naming_a_field():
    """Non-vacuous twice over: a glob that stops matching, and a screen that stops
    screening, both pass the lint below in silence."""
    named = {_rel(p) for p in _skill_bodies()
             if _ENVELOPE_FIELD.search(p.read_text(encoding="utf-8"))}
    assert {"skills/setup/SKILL.md", "skills/help/SKILL.md",
            "skills/init/SKILL.md", "skills/exec-trace/SKILL.md"} <= named, sorted(named)


@pytest.mark.parametrize("path", _skill_bodies(), ids=_rel)
def test_a_skill_naming_a_field_says_the_field_is_read_off_the_print(path):
    """AAD-7590: the same defect one layer past the two lints above. `jq` was gone
    from every fence and it came back anyway, because Step 3 of `/loci:setup` named
    `.data.checks[]` — jq's own filter spelling — and never said how to get at it.
    The model supplied the missing half itself, with `2>&1` in front of it, and
    `loci doctor`'s stderr trace made the PARSE fail (exit 5) where the check had
    passed.

    So naming a field obliges the skill to say the field is read off what the
    command printed. Ten of the twelve bodies already did, by stating the rule or by
    loading the shared contract that states it. `setup` is the one the ticket came
    from; `help` is the other, found by writing this lint, and it runs three envelope
    commands in a single Bash on the fast path."""
    text = path.read_text(encoding="utf-8")
    if not _ENVELOPE_FIELD.search(text):
        return
    assert _READ_IT_OFF_THE_PRINT.search(text) or "loci-runtime-contract" in text, (
        f"{_rel(path)} names an envelope field but never says it is read off the "
        f"printed output. A field with no rule is an invitation to parse: say "
        f"\"let it print\" as `contract`, `init` and `trends` do, or load "
        f"skills/_shared/loci-runtime-contract.md, which says it for the other eight")
