"""The contract guard must deny, and must not over-deny.

``.loci/contract.yaml`` states the bounds the agent's own work is judged against,
so the agent may draft changes but never apply them (ADR-0016/ADR-0017). The
guard is a ``PreToolUse`` hook, which means it is harness-enforced and cannot be
lost from the model's context — but it is also **fail-open**: if the script goes
missing, errors, or times out, the tool call proceeds and the guard silently
becomes nothing. These tests are the only thing that notices that.

Two assertions carry most of the weight:

* ``contract draft edit`` is ALLOWED while ``contract edit`` is DENIED. They are
  one token apart, so a lazy prefix match breaks the whole authoring flow and a
  lazy suffix match reopens the guard.
* every C/C++/Rust path the advisory hooks handled before still passes through,
  because the guard must not change the analysis pipeline's behaviour.
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
GUARD = PLUGIN_ROOT / "hooks" / "contract-guard.sh"


def _find_bash() -> str | None:
    if sys.platform == "win32":
        for cand in (
            r"C:\Program Files\Git\usr\bin\bash.exe",
            r"C:\Program Files (x86)\Git\usr\bin\bash.exe",
        ):
            if Path(cand).is_file():
                return cand
    return shutil.which("bash")


# jq is gated as hard as bash: without it the guard degrades to matching the raw
# payload, which is a different code path from the one these tests are written
# against. Running anyway meant the "must deny" cases failed and every "must not
# over-deny" case passed for the wrong reason.
pytestmark = pytest.mark.skipif(
    _find_bash() is None or shutil.which("jq") is None,
    reason="bash and jq required",
)


def _to_bash_path(p: Path) -> str:
    s = Path(p).as_posix()
    m = re.match(r"^([A-Za-z]):/(.*)$", s)
    return f"/{m.group(1).lower()}/{m.group(2)}" if m else s


def _base_path() -> str:
    """A PATH the guard can actually parse a payload on.

    Without `jq` the guard degrades to matching the raw payload — deliberately
    over-broad, and NOT the code path these tests are written against. jq is not in
    /usr/bin on a Windows checkout (chocolatey, scoop and winget all put it
    elsewhere), so hardcoding a jq-less PATH silently sent every case down the
    fallback: the "must deny" assertions failed and, worse, every "must not
    over-deny" assertion passed for the wrong reason. Resolve jq's real directory.
    """
    base = "/usr/bin:/bin:/usr/local/bin"
    jq = shutil.which("jq")
    if jq:
        base = f"{_to_bash_path(Path(jq).parent)}:{base}"
    return base


def _run(payload: dict, project_dir: Path, *, env: dict | None = None) -> dict | None:
    """Run the guard on one hook payload; return its decision, or None if allowed."""
    base = {
        "PATH": _base_path(),
        "HOME": str(Path.home()),
        "CLAUDE_PROJECT_DIR": _to_bash_path(project_dir),
    }
    proc = subprocess.run(
        [_find_bash(), _to_bash_path(GUARD)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
        env={**base, **(env or {})},
    )
    assert proc.returncode == 0, f"guard must always exit 0; got {proc.returncode}"
    out = proc.stdout.strip()
    if not out:
        return None
    return json.loads(out)["hookSpecificOutput"]


def _edit(path: str) -> dict:
    return {"tool_name": "Edit", "tool_input": {"file_path": path, "new_string": "x"}}


def _write(path: str) -> dict:
    return {"tool_name": "Write", "tool_input": {"file_path": path, "content": "x"}}


def _bash(command: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}}


def _denied(decision) -> bool:
    return decision is not None and decision.get("permissionDecision") == "deny"


# ── route 1: direct writes to the file ──────────────────────────────────────

@pytest.mark.parametrize("spelling", [
    "{root}/.loci/contract.yaml",          # absolute
    "./.loci/contract.yaml",               # cwd-relative
    ".loci/contract.yaml",                 # bare relative
    "{root}/src/../.loci/contract.yaml",   # traversal
])
def test_edit_and_write_are_denied_for_every_spelling(tmp_path, spelling):
    path = spelling.format(root=_to_bash_path(tmp_path))
    assert _denied(_run(_edit(path), tmp_path)), f"Edit {path} should be denied"
    assert _denied(_run(_write(path), tmp_path)), f"Write {path} should be denied"


def test_deny_reason_points_at_the_user_executed_command(tmp_path):
    # The reason must not hand the agent a command to run itself — that is how a
    # guard ends up advertising its own bypass.
    decision = _run(_edit(".loci/contract.yaml"), tmp_path)
    reason = decision["permissionDecisionReason"]
    assert "draft add" in reason
    assert "! loci contract accept" in reason


@pytest.mark.parametrize("path", [
    "src/contract.yaml",                   # same basename, wrong place
    "vendor/other/.loci/contract.yml",     # .yml, not the guarded file
    ".loci/state.json",                    # another file under .loci/
    "docs/contract.yaml",
])
def test_unrelated_files_are_allowed(tmp_path, path):
    assert _run(_edit(path), tmp_path) is None, f"{path} must not be guarded"


@pytest.mark.parametrize("path", [
    "src/main.c", "src/app.cpp", "include/api.h", "src/lib.rs",
])
def test_source_files_pass_through_untouched(tmp_path, path):
    # The guard must not disturb the advisory snapshot/scan pipeline.
    assert _run(_edit(path), tmp_path) is None


# ── route 2: Bash commands that write it ────────────────────────────────────

@pytest.mark.parametrize("command", [
    "loci contract accept",
    "loci contract init",
    "loci contract init --force",
    "loci contract edit --index 3",
    "loci contract disable --index 3",
    "loci contract enable --index 3",
    "LOCI_LOG_LEVEL=DEBUG loci contract accept",          # env prefix
    "loci   contract   accept",                            # extra whitespace
    "loci -f json contract accept",                        # global flag between
    "make build && loci contract disable --index 1",        # second in a chain
    "echo hi; loci contract accept",
    "loci contract accept\n",                              # trailing newline
])
def test_contract_writing_verbs_are_denied(tmp_path, command):
    assert _denied(_run(_bash(command), tmp_path)), f"{command!r} should be denied"


@pytest.mark.parametrize("command", [
    "loci contract show",
    "loci contract lint --draft",
    "echo '{}' | loci contract draft add",
    "loci contract draft edit --index 3",
    "loci contract draft disable --index 3",
    "loci contract draft enable --index 3",
    "loci contract draft show",
    "loci contract draft clear",
])
def test_reading_and_drafting_are_allowed(tmp_path, command):
    # If this regresses, the authoring flow is dead — the agent cannot propose
    # anything at all.
    assert _run(_bash(command), tmp_path) is None, f"{command!r} must be allowed"


def test_draft_edit_and_edit_land_on_opposite_sides(tmp_path):
    # One token apart. The single most important pair in this file.
    assert _run(_bash("loci contract draft edit --index 0"), tmp_path) is None
    assert _denied(_run(_bash("loci contract edit --index 0"), tmp_path))


@pytest.mark.parametrize("command", [
    "sed -i 's/2048/8192/' .loci/contract.yaml",
    "cat > .loci/contract.yaml <<'EOF'\nversion: 1\nEOF",
    "printf 'x' >> .loci/contract.yaml",
    "python -c \"open('.loci/contract.yaml','w').write('')\"",
    "cp new.yaml .loci/contract.yaml",
    "git restore .loci/contract.yaml",
])
def test_shell_writes_to_the_file_are_deliberately_not_guarded(tmp_path, command):
    """This test asserts a decision, not an oversight. Do not "fix" it.

    The guard used to match write *shapes* — a redirect at the path, or the path
    named alongside ``sed -i``/``tee``/``cp``/``python``/… It was removed because
    every defect and every false positive the guard ever produced came from that
    list: it denied a ``.c`` edit whose comment mentioned the path, it missed the
    same tools when they led the command, and one variable (``P=.loci/…``) walked
    straight through it. It never caught a real mistake.

    What is left is what an agent actually does by accident — ``Edit``/``Write``
    on the file, and the CLI verbs the tool's own ``--help`` advertises. Shell
    writes are covered by the commit diff, which ADR-0015 makes the real backstop.
    """
    assert _run(_bash(command), tmp_path) is None, f"{command!r} must be allowed"


@pytest.mark.parametrize("command", [
    "cat .loci/contract.yaml",
    "git diff -- .loci/contract.yaml",
    "git diff --stat -- .loci/contract.yaml",
    "grep -n stack_depth .loci/contract.yaml",
])
def test_reads_of_the_contract_are_allowed(tmp_path, command):
    # Reads must pass. The agent has to know the bounds to respect them
    # (ADR-0017 rejected guarding reads), and the skill runs
    # `git diff --stat -- .loci/contract.yaml` to show the user what landed —
    # a guard that blocked it would break its own confirmation step.
    assert _run(_bash(command), tmp_path) is None, f"{command!r} must be allowed"


@pytest.mark.parametrize("command", [
    "make -j4",
    "loci build compile --source src/main.c",
    "git status",
    "loci elf stack --elf build/app.elf --arch aarch64",
])
def test_unrelated_commands_are_allowed(tmp_path, command):
    assert _run(_bash(command), tmp_path) is None


# ── the degraded (no jq) branch ──────────────────────────────────────────────
#
# The guard cannot parse the payload without jq, and route 1 used to be skipped
# entirely in that state: `Edit .loci/contract.yaml` was ALLOWED, while an edit to
# an unrelated source file whose *content* named the path was DENIED. Both
# directions were wrong and the comment claimed otherwise. These tests pin the
# corrected behaviour: `file_path` is extracted with sed, so both routes still
# decide on the field rather than on the payload's raw text.

@pytest.fixture
def no_jq(tmp_path):
    """Env whose PATH holds the guard's tools but no jq.

    HOME is redirected too — the guard appends the usual install dirs to PATH,
    so a real ``~/.local/bin/jq`` would otherwise be found and the branch under
    test would never run.
    """
    if sys.platform == "win32":
        pytest.skip("PATH shadowing is not reliable on Windows")
    binaries = ["bash", "sed", "tr", "git", "realpath", "cat"]
    stub = tmp_path / "no-jq-bin"
    stub.mkdir()
    for name in binaries:
        real = shutil.which(name)
        if real is None:
            pytest.skip(f"{name} not available")
        (stub / name).symlink_to(real)
    assert shutil.which("jq", path=str(stub)) is None
    return {"PATH": str(stub), "HOME": str(tmp_path / "home")}


def test_no_jq_still_denies_the_file(tmp_path, no_jq):
    assert _denied(_run(_edit(".loci/contract.yaml"), tmp_path, env=no_jq))
    assert _denied(_run(_write(".loci/contract.yaml"), tmp_path, env=no_jq))


def test_no_jq_still_denies_the_verbs(tmp_path, no_jq):
    assert _denied(_run(_bash("loci contract accept"), tmp_path, env=no_jq))
    assert _denied(_run(_bash("loci contract disable --index 0"), tmp_path, env=no_jq))


def test_no_jq_does_not_deny_a_source_file_that_merely_names_the_path(tmp_path, no_jq):
    # Route 1 decides on the extracted `file_path`, never on the payload text —
    # matching the whole payload would deny an edit for its own *content*, which
    # is what once made this comment unwritable into a .c file on a jq-less host.
    payload = {
        "tool_name": "Edit",
        "tool_input": {
            "file_path": "src/main.c",
            "new_string": "/* bounds live in .loci/contract.yaml, seeded by python */",
        },
    }
    assert _run(payload, tmp_path, env=no_jq) is None


def test_no_jq_leaves_drafting_alone(tmp_path, no_jq):
    assert _run(_bash("echo '{}' | loci contract draft add"), tmp_path, env=no_jq) is None
    assert _run(_bash("loci contract draft edit --index 0"), tmp_path, env=no_jq) is None


# ── the prefilter ────────────────────────────────────────────────────────────

def test_payload_without_the_subject_exits_before_forking(tmp_path):
    # This hook runs on every Bash call in every repo the plugin is installed
    # for. A payload without the literal `contract` cannot be denied by either
    # route, so it must return without spawning jq, git or realpath.
    assert _run(_bash("npm test -- --watch=false"), tmp_path) is None
    assert _run(_edit("web/src/index.ts"), tmp_path) is None
