"""`/bug-report`'s turn-baseline recipe, run as the file ships it.

The turn tree is where a post-edit "Before" comes from, and its directory name is a
one-way digest of the turn id — so `turn.json` is the only place the id survives.
Phase 03 confirmed by grep that nothing read it; phase 10 gave it a consumer,
because the three states it distinguishes are exactly the ones a *"there was no
Before"* report cannot be answered without:

* an empty `orig/` — the pre-edit hook never captured;
* a populated `orig/` with an empty `obj/` — the capture worked, the
  reconstruction did not;
* a turn id matching no tree at all — the compile was asked to verify a turn that
  was never stamped.

**The fence is extracted from the SKILL.md and executed**, not retyped here. Running
a retyped copy proves nothing about what ships: the shell around the command —
quoting, `$( )`, the line continuations, whether the loop variable already ends in a
slash — is most of what a model copies, and it is where five of the six defects that
turned `lib/compile-and-read-back.sh` from a fenced recipe into a script lived.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
SKILL = PLUGIN_ROOT / "skills" / "bug-report" / "SKILL.md"


def _find_bash() -> str | None:
    if sys.platform == "win32":
        for cand in (
            r"C:\Program Files\Git\usr\bin\bash.exe",
            r"C:\Program Files (x86)\Git\usr\bin\bash.exe",
        ):
            if Path(cand).is_file():
                return cand
    return shutil.which("bash")


pytestmark = pytest.mark.skipif(
    _find_bash() is None or shutil.which("jq") is None,
    reason="bash and jq required",
)


def _to_bash_path(p: Path) -> str:
    s = Path(p).as_posix()
    m = re.match(r"^([A-Za-z]):/(.*)$", s)
    return f"/{m.group(1).lower()}/{m.group(2)}" if m else s


def _recipe() -> str:
    """The one bash fence in the skill that walks the turn trees.

    Selected on `turns/*/`, which is the thing it is FOR, rather than on its
    position or on a phrase from the prose around it — a selector that can match
    zero fences and still let the test pass is how a lint quietly stops linting, so
    the count is asserted."""
    blocks = re.findall(r"```bash\n(.*?)```", SKILL.read_text(encoding="utf-8"), re.S)
    hits = [b for b in blocks if "turns/*/" in b]
    assert len(hits) == 1, (
        f"expected exactly one turn-tree recipe in {SKILL.name}, found {len(hits)}"
    )
    program = hits[0]
    # The recipe must depend on nothing but its own substitution. `$PROJECT_ROOT`
    # was the first version's root variable and the skill assigns it nowhere, so the
    # recipe silently reported "no turn baselines" for every project; only the test
    # harness, which exported it, made it work.
    assert _PLACEHOLDER in program, (
        f"the recipe no longer carries the {_PLACEHOLDER} the prose tells the model "
        f"to substitute:\n{program}"
    )
    for name in re.findall(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)", program):
        assert name in {"ROOT", "t"} or f"{name}=" in program, (
            f"the recipe reads ${name}, which nothing in it or in the skill sets"
        )
    return program


def _tree(root: Path, key: str, turn: str, *, sources: int, objects: int) -> None:
    t = root / ".loci-build" / "turns" / key
    (t / "orig" / "src").mkdir(parents=True, exist_ok=True)
    (t / "obj" / "9-abcd").mkdir(parents=True, exist_ok=True)
    (t / "turn.json").write_text(
        json.dumps({"turn": turn, "created": "2026-08-14T00:00:00+00:00"}) + "\n",
        encoding="utf-8")
    for i in range(sources):
        # `.locisrc` is the real suffix: a capture is stored under its own name plus
        # that, so a Makefile glob in the user's project cannot pick it up as a
        # second copy of their source.
        (t / "orig" / "src" / f"app{i}.c.locisrc").write_text("x", encoding="utf-8")
    for i in range(objects):
        (t / "obj" / "9-abcd" / f"app{i}.o").write_bytes(b"\x7fELF")


_PLACEHOLDER = "<project-root>"


def _run(project_root: Path) -> list[list[str]]:
    """Run the recipe with the substitution the skill tells the model to make.

    The FIRST version of this helper passed `PROJECT_ROOT` in the environment — and
    the recipe read `$PROJECT_ROOT`, which the skill sets nowhere at all. The harness
    was supplying the one thing the shipped text lacked, so every test here passed
    while the real recipe reported "no turn baselines" for every project. That is
    the whole reason this file executes the fence instead of asserting about it, and
    it defeated it exactly once.

    So: nothing is exported, the placeholder is substituted the way the prose says,
    and `_recipe()` asserts the placeholder is still there to substitute."""
    program = _recipe().replace(_PLACEHOLDER, _to_bash_path(project_root))
    proc = subprocess.run(
        [_find_bash(), "-c", program],
        capture_output=True, text=True, timeout=60,
        env={"PATH": f"{_to_bash_path(Path(shutil.which('jq')).parent)}:/usr/bin:/bin"},
    )
    assert proc.returncode == 0, f"stderr={proc.stderr!r}"
    rows = [ln.split("\t")
            for ln in proc.stdout.replace("\r\n", "\n").splitlines() if ln]
    # `mtime, turn id, captured sources, reconstructed objects, path`. Returned as
    # dicts and read by NAME below: the mtime and the path are real values a fixture
    # cannot predict, and freezing the row shape into every assertion is what made
    # four of these fail on a column being added.
    return [{"mtime": r[0], "turn": r[1], "sources": r[2], "objects": r[3],
             "path": r[4]} for r in rows]


def test_the_recipe_reports_the_turn_id_and_both_counts(tmp_path):
    _tree(tmp_path, "aaaa1111", "b52ae369-e1ba-4823-9c6e-3d51b9e0166e",
          sources=3, objects=2)
    rows = _run(tmp_path)
    assert len(rows) == 1, rows
    assert rows[0]["turn"] == "b52ae369-e1ba-4823-9c6e-3d51b9e0166e", rows
    assert (rows[0]["sources"], rows[0]["objects"]) == ("3", "2"), rows
    # The path is what makes a row actionable — the directory name is a one-way
    # digest, so without it a reader cannot go and look at the tree.
    assert rows[0]["path"].endswith("turns/aaaa1111/"), rows
    assert rows[0]["mtime"].startswith("20"), rows


def test_a_captured_turn_with_no_reconstruction_is_reported_plainly(tmp_path):
    """The counts are reported per turn. What they MEAN is prose, not shell: an empty
    `obj/` is the normal state for an ordinary `.c` edit and for every cargo project,
    so the skill must not tell a reader it is a reconstruction failure — pinned in
    `test_the_skill_scopes_the_empty_obj_reading` below."""
    _tree(tmp_path, "aaaa1111", "turn-A", sources=4, objects=0)
    _tree(tmp_path, "bbbb2222", "turn-B", sources=0, objects=0)
    rows = {r["turn"]: (r["sources"], r["objects"]) for r in _run(tmp_path)}
    assert rows["turn-A"] == ("4", "0"), rows
    assert rows["turn-B"] == ("0", "0"), rows


def test_the_rows_are_ordered_by_time_and_not_by_name(tmp_path):
    """"Newest first" was false as first written: a turn directory is a 16-hex digest
    of the turn id, so lexical order is uncorrelated with time and `tail` returned
    the lexically-last trees. With a `--keep-turns` cap the ones that matter are the
    recent ones, so the wrong ten is the wrong answer."""
    import os
    import time
    _tree(tmp_path, "ffff0000", "older", sources=1, objects=0)
    _tree(tmp_path, "0000ffff", "newer", sources=1, objects=0)
    turns = tmp_path / ".loci-build" / "turns"
    old = time.time() - 7200
    os.utime(turns / "ffff0000", (old, old))
    rows = _run(tmp_path)
    assert [r["turn"] for r in rows] == ["older", "newer"], rows


def test_a_project_root_containing_a_space_still_works(tmp_path):
    """Windows is the norm here, and an unquoted `$ROOT` or `$t` splits on it —
    producing either an empty report or one about a directory that does not exist,
    both of which read as "this project has no turn trees"."""
    root = tmp_path / "My Projects" / "fw"
    root.mkdir(parents=True)
    _tree(root, "aaaa1111", "turn-A", sources=1, objects=1)
    rows = _run(root)
    assert len(rows) == 1 and rows[0]["turn"] == "turn-A", rows
    assert (rows[0]["sources"], rows[0]["objects"]) == ("1", "1"), rows


def test_a_project_with_no_turns_directory_reports_nothing(tmp_path):
    """An unmatched glob stays literal in bash, so without the `[ -d ]` guard the
    loop body runs once for the pattern itself and reports a turn id of `?` for a
    tree that does not exist."""
    (tmp_path / ".loci-build").mkdir()
    assert _run(tmp_path) == []


def test_a_tree_with_no_turn_record_does_not_abort_the_walk(tmp_path):
    """`turn.json` is written best-effort and an interrupted capture can leave a
    tree without one. A recipe that died there would hide every LATER tree,
    including the one the report is about."""
    _tree(tmp_path, "aaaa1111", "turn-A", sources=1, objects=1)
    _tree(tmp_path, "bbbb2222", "turn-B", sources=2, objects=1)
    (tmp_path / ".loci-build" / "turns" / "aaaa1111" / "turn.json").unlink()
    rows = _run(tmp_path)
    assert len(rows) == 2, rows
    by_turn = {r["turn"]: (r["sources"], r["objects"]) for r in rows}
    assert by_turn["turn-B"] == ("2", "1"), rows
    # The tree whose record is gone still appears, with its id unknown — losing the
    # ROW would hide a tree the report may be about.
    assert by_turn["?"] == ("1", "1"), rows


def test_the_skill_tells_the_reader_what_the_counts_mean(tmp_path):
    """The numbers are only a diagnostic if the prose says which bug each shape is.
    A recipe that runs and a reader who cannot act on it is the same outcome as no
    recipe — and the prose is the half no execution test can check."""
    text = SKILL.read_text(encoding="utf-8")
    for phrase in ("uncaptured.jsonl", "one-way digest", "never stamped"):
        assert phrase in text, f"the turn-baseline section no longer explains {phrase!r}"


def test_the_skill_scopes_the_empty_obj_reading(tmp_path):
    """The first version of this prose said a populated `orig/` with an empty `obj/`
    meant the reconstruction failed. That is wrong on the two commonest turn shapes
    there are: `obj/` is written only when a HEADER edit is measured through its
    dependents, so an ordinary `.c` edit puts nothing there, and `--baseline` refuses
    Rust outright so a cargo project always shows 0. A model following it would
    report a reconstruction failure on almost every bug report."""
    text = SKILL.read_text(encoding="utf-8")
    assert "empty is NORMAL" in text, (
        "the skill no longer says an empty obj/ is the ordinary case")
    assert "header" in text and "Rust" in text, (
        "the skill no longer names the two shapes that make obj/ legitimately empty")


def test_the_skill_fails_closed_on_a_missing_role(tmp_path):
    """`role` postdates the pinned CLI, so on an un-upgraded install the field is
    absent on every artifact. A criterion phrased as "no candidate whose role is
    measured reports stale" is then satisfied VACUOUSLY — a demonstrably stale object
    recorded as PASS, on exactly the installs most likely to have the problem."""
    text = SKILL.read_text(encoding="utf-8")
    assert "MISSING `role` is a FAIL" in text, (
        "check 8's missing-role rule is gone, so it fails open on the pinned CLI")
