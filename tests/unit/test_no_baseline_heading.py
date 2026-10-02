"""AAD-7554: a missing Before is a fact about the measurement, never about the code.

The post-edit report headed every function that had no Before `(NEW)`. A one-constant
edit to a function two weeks old, made through a shell so that no snapshot was armed,
was reported as new code. The template hard-coded the word, and the line under it said
"first measurement on this branch" whatever the CLI's reason for having no Before.

`prepare` now says which functions are new (`data.added_functions`), and the reason a
Before is missing arrives on `data.provenance[].withheld`. This file pins that the
report reads those two, and that no shipped prose puts the old claims back.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
SKILLS = PLUGIN_ROOT / "skills"
POST_EDIT = SKILLS / "loci-post-edit" / "SKILL.md"

_FENCE = re.compile(r"^```[a-z]*\n(.*?)^```$", re.S | re.M)


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    rest = text[start + len(heading):]
    # The template's own fence holds `## Post-Edit:` lines, so the section ends
    # at the next `### ` heading OUTSIDE a fence, not at the first `#` line.
    # Blanked to the same length, so an offset in one is an offset in the other.
    outside = _FENCE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), rest)
    nxt = re.search(r"^#{2,3} ", outside, re.M)
    return rest[:nxt.start()] if nxt else rest


def _no_baseline() -> tuple[str, str]:
    """The section's prose, and its template fence."""
    text = POST_EDIT.read_text(encoding="utf-8").replace("\r\n", "\n")
    section = _section(text, "### Template (no baseline)")
    fence = _FENCE.search(section)
    assert fence, "the no-baseline template lost its fence"
    return section[:fence.start()], fence.group(1)


def test_the_no_baseline_heading_does_not_assert_new_code():
    _prose, fence = _no_baseline()
    heading = fence.splitlines()[0]
    assert heading.startswith("## Post-Edit: <FunctionName>"), heading
    assert "no baseline" in heading, (
        "the heading must be able to say the measurement has no Before without "
        f"saying the code is new: {heading!r}")
    assert not re.search(r"\(NEW\)", heading), (
        f"`(NEW)` is hard-coded again — every Before-less function reads as new code: "
        f"{heading!r}")


def test_new_is_granted_only_by_the_field_that_proves_it():
    prose, _fence = _no_baseline()
    paragraph = " ".join(prose.split())
    assert re.search(r"`\(NEW\)` only for a function in `data\.added_functions`",
                     paragraph), paragraph
    assert "`(no baseline)`" in paragraph, paragraph


def test_the_last_line_relays_the_clis_reason_not_a_history():
    prose, fence = _no_baseline()
    last = fence.rstrip().splitlines()[-1]
    paragraph = " ".join(prose.split())
    assert "first measurement" not in last, last
    # A `(NEW)` function beside a Before HAS one: its line may not say otherwise,
    # and the eval grader reads "no pre-edit baseline" as a missing Before.
    assert "no pre-edit baseline" not in last and "not in the pre-edit object" in last, last
    for named in ("`data.provenance[]`", "`withheld.reason`", "once per unit",
                  "`baseline_reason`"):
        assert named in paragraph, (named, paragraph)


def test_the_field_list_names_added_functions():
    text = POST_EDIT.read_text(encoding="utf-8")
    assert "- `data.added_functions` —" in text


@pytest.mark.parametrize("claim", ["first measurement on this branch",
                                   "first-edit measurement"])
def test_no_shipped_prose_states_the_codes_history_for_a_missing_before(claim):
    """Both said something about the code's past that a missing Before cannot
    support. The CLI's `withheld.reason` says what is actually known."""
    hits = [str(p.relative_to(PLUGIN_ROOT)) for p in SKILLS.rglob("*.md")
            if claim in p.read_text(encoding="utf-8")]
    assert not hits, (claim, hits)


def _cli_analyse() -> Path | None:
    for raw in (os.environ.get("LOCI_DEV_CLI_PATH"), os.environ.get("LOCI_CLI_SRC"),
                str(PLUGIN_ROOT.parent / "loci-cli")):
        if not raw:
            continue
        for base in (Path(raw) / "src" / "loci" / "cli", Path(raw)):
            if (base / "analyse.py").is_file():
                return base / "analyse.py"
    return None


@pytest.mark.skipif(_cli_analyse() is None, reason="no loci-cli checkout")
def test_the_fields_the_template_reads_are_ones_prepare_emits():
    """A field name in the skill that the CLI never writes makes every function read
    `(no baseline)` for ever, and nothing else would notice."""
    src = _cli_analyse().read_text(encoding="utf-8")
    assert '"added_functions"' in src
    assert re.search(r'line\["withheld"\] = \{"code": [^}]*"reason"', src, re.S), (
        "the provenance line no longer carries `withheld.reason`")
