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

import errno
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
GUARD = PLUGIN_ROOT / "hooks" / "contract-guard.sh"

#: A literal backslash, built rather than written: the tooling on this
#: machine decodes a `\uXXXX` in file content into the character it names.
B = chr(92)


def _esc(s: str) -> str:
    r"""`s` spelled entirely in `\uXXXX` escapes.

    No serializer produces one of these for an ASCII letter, so every caller
    hands the guard its own JSON text through `raw_json=`. That is the F15
    reachability argument, and it is the reason this helper exists at all.
    """
    return "".join(B + "u%04x" % ord(c) for c in s)


def _raw_edit(target: str, new_string: str = "x") -> str:
    """An `Edit` payload as TEXT, so an escape in it survives to the guard."""
    return ('{"tool_name": "Edit", "tool_input": {"file_path": "' + target
            + '", "new_string": "' + new_string + '"}}')


def _raw_bash(command: str) -> str:
    return '{"tool_name": "Bash", "tool_input": {"command": "' + command + '"}}'


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


def _native_path(p: Path) -> str:
    r"""`C:/…` — the spelling Claude Code puts in `CLAUDE_PROJECT_DIR`.

    The MSYS twin of `_to_bash_path`, and the reason it exists is F16: every
    route-1 fixture in this file went through `_to_bash_path`, so both sides of
    the guard's comparison were always spelled `/c/…` — the ONE spelling where
    the old three-rung `_resolve` agreed with itself. The suite was green
    through four holes because of it. A test that resolves a path and does not
    vary this is measuring the blind spot.
    """
    return str(Path(p)).replace(chr(92), "/")


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


def _run(payload: dict, project_dir: Path, *, env: dict | None = None,
         ascii_json: bool = True, raw_json: str | None = None) -> dict | None:
    """Run the guard on one hook payload; return its decision, or None if allowed.

    `ascii_json=False` sends the payload as raw UTF-8, which is what the
    harness sends — `JSON.stringify` does not escape non-ASCII. The default
    stays `True` so every ASCII payload keeps the shape it was written against.
    Either way stdin and stdout are UTF-8: this console's cp1252 cannot even
    encode the paths the locale test uses.

    `raw_json` sends that text verbatim instead of serialising `payload`, for
    the one thing a serializer cannot express: an ASCII letter written as a
    `\\uXXXX` escape. Neither `json.dumps` nor `JSON.stringify` will produce
    one — which is the reachability argument for F15 as much as it is the
    reason this parameter exists.
    """
    base = {
        "PATH": _base_path(),
        "HOME": str(Path.home()),
        "CLAUDE_PROJECT_DIR": _to_bash_path(project_dir),
    }
    proc = subprocess.run(
        [_find_bash(), _to_bash_path(GUARD)],
        input=(raw_json if raw_json is not None
               else json.dumps(payload, ensure_ascii=ascii_json)),
        capture_output=True,
        text=True,
        encoding="utf-8",
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


def _make_dir_link(link: Path, target: Path) -> bool:
    """A directory link, by whichever mechanism this host has.

    A Windows SYMLINK needs a privilege, so the tests that need one skip there
    and the one behaviour that differs by platform goes untested on the platform
    it was found on. A JUNCTION needs none — `mklink /J`, no admin — and Git
    Bash reports it as a symlink and follows it exactly the same way.
    """
    try:
        link.symlink_to(target, target_is_directory=True)
        if link.is_dir():
            return True
    except (OSError, NotImplementedError):
        pass
    if sys.platform == "win32":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                       capture_output=True, text=True, timeout=30)
        return link.is_dir()
    return False


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


# ── route 1: the build recipe and the flag pin (T10) ────────────────────────
#
# `.loci/build.yaml` records the compiler, flags and target ISA every measurement
# is made with, so an agent that can edit it can move its own goalposts. It is
# gitignored, so unlike the contract it has NO commit diff to fall back on — the
# escrow hash (`recipe_tampered`) is its only after-the-fact backstop, which is
# why the before-the-fact deny matters more here than it does for contract.yaml.
# `flags.json` joins it because an explicit `mode:"replace"` pin OUTRANKS the
# recipe: guarding one without the other leaves a higher-precedence side door.
#
# These ship in the same release as the runtime-contract rewrite that stopped
# instructing the flags.json write. A guard that denies what the prose in the
# model's context tells it to do is worse than no guard.

RECIPE_FILES = (
    ".loci/build.yaml",
    ".loci/build/flags.json",
    # `.loci-build/flags.json`, the pre-move spelling, was here while the CLI
    # still read it; since T14 nothing does — see the test that pins it OPEN.
)


@pytest.mark.parametrize("rel", RECIPE_FILES)
@pytest.mark.parametrize("shape", [
    "{root}/{rel}",             # absolute
    "./{rel}",                  # cwd-relative
    "{rel}",                    # bare relative
    "{root}/src/../{rel}",      # traversal
])
def test_recipe_and_flag_pin_are_denied_for_every_spelling(tmp_path, rel, shape):
    path = shape.format(root=_to_bash_path(tmp_path), rel=rel)
    assert _denied(_run(_edit(path), tmp_path)), f"Edit {path} should be denied"
    assert _denied(_run(_write(path), tmp_path)), f"Write {path} should be denied"


def _win(path: str) -> str:
    """The native spelling a real payload carries: drive letter, backslashes."""
    return path.replace("/", "\\")


@pytest.mark.parametrize("rel", (".loci/contract.yaml",) + RECIPE_FILES)
@pytest.mark.parametrize("case", ["upper", "title", "dir-upper"])
def test_a_case_variant_of_a_guarded_path_is_denied(tmp_path, rel, case):
    """NTFS and APFS are case-insensitive, so `FLAGS.JSON` IS the guarded file.

    It opens it, and the CLI reads it straight back — `build_dir(root)/"flags.json"`
    is a `Path.is_file()` that succeeds. A byte-exact guard therefore let the
    highest-precedence input to every measurement be rewritten with one keystroke
    on the two platforms most developers use.

    `.loci/build/flags.json` is the one that matters most: it is gitignored (no
    commit diff), it has no escrow record (no `recipe_tampered` afterwards) and
    no validator, so nothing notices the write at any later point. The recipe is
    a notch less bad and contract.yaml is committed.
    """
    head, _, base = rel.rpartition("/")
    spelled = {"upper": f"{head}/{base.upper()}",
               "title": f"{head}/{base.title()}",
               "dir-upper": f"{head.upper()}/{base}"}[case]
    assert _denied(_run(_edit(spelled), tmp_path)), f"Edit {spelled} should be denied"
    assert _denied(_run(_write(spelled), tmp_path)), f"Write {spelled} should be denied"


@pytest.mark.parametrize("rel", (".loci/contract.yaml",) + RECIPE_FILES)
@pytest.mark.parametrize("suffix", [".", " ", "..", ". "])
def test_a_trailing_dot_or_space_does_not_evade_the_guard(tmp_path, rel, suffix):
    # Win32 strips trailing dots and spaces from a filename, so `build.yaml.`
    # opens `build.yaml`. Same class as the case bypass: a spelling the guard
    # reads as a different file and the filesystem reads as the guarded one.
    assert _denied(_run(_edit(rel + suffix), tmp_path)), f"{rel + suffix!r}"


@pytest.mark.parametrize("rel", (".loci/contract.yaml",) + RECIPE_FILES)
@pytest.mark.parametrize("shape", ["{root}\\{rel}", "{rel}", "{root}\\src\\..\\{rel}"])
def test_the_native_windows_spelling_is_denied(tmp_path, rel, shape):
    """The spelling a real payload actually carries — and no test used to send it.

    Every other path in this file goes through `_to_bash_path()`, which rewrites
    `C:\\…` to `/c/…`, so the suite never contained a backslash. Real payloads do:
    `hooks/post-edit-hook.sh` and `pre-edit-hook.sh` both say so, and
    `lib/compile-and-read-back.sh` matches `[A-Za-z]:[/\\\\]` for the same reason.

    It denied before this change only through MSYS `realpath`'s implicit
    drive-path conversion — an undocumented side effect of one host's binary. The
    suffix fallback could not help, because Git Bash resolves a backslash in a
    file test but NOT in a parameter expansion. The guard now normalises the
    separator itself, which is what makes this test pass on a host whose
    `realpath` does no such thing.
    """
    win_root = str(tmp_path)
    path = shape.format(root=win_root, rel=_win(rel))
    assert _denied(_run(_edit(path), tmp_path)), f"Edit {path} should be denied"


@pytest.mark.parametrize("rel", (".loci/contract.yaml",) + RECIPE_FILES)
def test_a_spelling_only_the_walk_can_normalise_is_denied(tmp_path, rel):
    """Pins the resolution branch, which nothing pinned before.

    Every other spelling in this file — including `src/../.loci/contract.yaml` —
    still *ends* with the guarded path, so the suffix fallback catches it and the
    comparison above could be deleted with the suite green (measured:
    it was). A `..` inside the guarded directory has no such suffix, so only the
    normalisation catches it.

    Named for `realpath` until F16, which took `realpath` off the verdict path
    entirely — it is `_walk` that answers this now. The property is the same
    one and it is the reason the test survives the rename.

    contract.yaml is parametrized in here deliberately: the three files share one
    implementation now, so this is the assertion that keeps the two new ones from
    silently getting a weaker one than the file that already worked.
    """
    head, _, base = rel.rpartition("/")
    path = f"{_to_bash_path(tmp_path)}/{head}/zz/../{base}"
    assert not path.endswith(f"/{rel}"), "this spelling is caught by the suffix rule"
    assert _denied(_run(_edit(path), tmp_path)), f"Edit {path} should be denied"
    assert _denied(_run(_write(path), tmp_path)), f"Write {path} should be denied"


@pytest.mark.parametrize("rel", (".loci/contract.yaml",) + RECIPE_FILES)
def test_a_symlinked_guarded_directory_is_resolved(tmp_path, rel):
    """The resolution must see through a symlinked `.loci` / `.loci-build`.

    The realistic shape: the dot-directory lives on another volume, or the
    checkout is reached through a link. Both spellings name one file — through
    the link, and at the real location — and the suffix fallback catches only the
    first, so this is the assertion the normalisation exists for.

    Windows needs a privilege to create a symlink, so this skips there. That is
    exactly why it has to be run on Linux before the change is called done.
    """
    head, _, rest = rel.partition("/")          # `.loci` / `.loci-build`
    real = tmp_path / "elsewhere"
    (real / rest).parent.mkdir(parents=True, exist_ok=True)
    try:
        (tmp_path / head).symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this host cannot create symlinks")

    through_link = f"{_to_bash_path(tmp_path)}/{rel}"
    at_real_location = f"{_to_bash_path(real)}/{rest}"
    assert _denied(_run(_edit(through_link), tmp_path)), through_link
    assert _denied(_run(_edit(at_real_location), tmp_path)), (
        f"{at_real_location} is the same file as {rel} and only the resolution "
        f"says so")


@pytest.mark.parametrize("rel", (".loci/contract.yaml",) + RECIPE_FILES)
def test_a_symlink_that_hides_the_name_is_a_known_gap(tmp_path, rel):
    """This asserts a limitation, not an oversight. Do not "fix" it in isolation.

    The prefilter is a substring match on the RAW payload, because this hook runs
    ahead of every Bash, Edit and Write in every repo the plugin is installed for
    and may not fork before deciding. A link whose own name carries none of the
    guarded basenames therefore never reaches route 1 at all — the normalisation
    below it never runs. Closing that would mean a `jq` on every Edit and Write
    everywhere, which is the cost the prefilter exists to avoid.

    It is also the risk the design already priced in: §6.5 says the guard
    *reduces* goalpost-moving rather than closing it, the recipe's escrow hash
    turns such a write into a `recipe_tampered` refusal on the next compile, and
    contract.yaml is committed so its diff shows it.

    What this pins is PARITY. The gap is contract.yaml's, from before the recipe
    existed; the two files added beside it must be no weaker and no stronger. If
    a later change closes it, close it for all three and delete this test.
    """
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x", encoding="utf-8")
    try:
        (tmp_path / "innocent.txt").symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("this host cannot create symlinks")
    path = f"{_to_bash_path(tmp_path)}/innocent.txt"
    assert _run(_edit(path), tmp_path) is None, (
        f"{rel} is now caught through a name-hiding symlink. If that is "
        f"deliberate, make it true for all three guarded files and delete this "
        f"test; a guard that catches one of them and not the others reads as "
        f"protection that is not there.")


def test_recipe_deny_reason_names_the_sanctioned_write_path(tmp_path):
    # Unlike contract.yaml's, this reason DOES hand the agent a command — `loci
    # init set` is the interface the file lost, and §6.5 makes it agent-runnable
    # on purpose. So the reason has to carry the consent that goes with it, or
    # the guard is advertising its own bypass with one extra step.
    reason = _run(_edit(".loci/build.yaml"), tmp_path)["permissionDecisionReason"]
    assert "loci init set" in reason, "the reason offers no sanctioned alternative"
    assert "--refresh" in reason, "no route for the target switch / recovery"
    assert "Ask the user first" in reason, "the reason drops the consent step"
    assert "recipe_tampered" in reason, (
        "the reason must say why going round the guard by shell does not work")


def test_flag_pin_deny_reason_names_the_sanctioned_write_path(tmp_path):
    rel = ".loci/build/flags.json"
    reason = _run(_edit(rel), tmp_path)["permissionDecisionReason"]
    assert "loci init set" in reason, f"{rel} reason offers no alternative"
    assert "OUTRANKS" in reason, (
        f"{rel} reason no longer says why this file is guarded at all")


@pytest.mark.parametrize("shape", ["{root}/.loci-build/flags.json", ".loci-build/flags.json"])
def test_the_legacy_flag_pin_is_not_guarded_because_nothing_reads_it(tmp_path, shape):
    """The pre-move `.loci-build/flags.json` was guarded while the CLI read it:
    a pin there outranked the recipe. Since T14 the CLI reads one build root, so
    a write there changes no measurement — and a guard on a file nothing reads
    is a deny reason that lies about what the file does. Pinned OPEN so the
    fourth path does not creep back in with a comment saying the CLI still reads
    it, which is how it was justified the first time."""
    path = shape.format(root=_to_bash_path(tmp_path))
    assert _run(_edit(path), tmp_path) is None, f"Edit {path} is denied for nothing"
    assert _run(_write(path), tmp_path) is None, f"Write {path} is denied for nothing"


@pytest.mark.parametrize("path", [
    ".loci/build/dumps/stack-analysis.json",   # LOCI's own output, not a pin
    ".loci/build/objects/armv6-m/blink.o.meta.json",   # a sidecar
    "build.yaml",                              # a CI file that is not the recipe
    "ci/build.yaml",
    ".loci/build.yml",                         # .yml is not the guarded file
    "vendor/flags.json",
    ".loci/flags.json",                        # not under build/
])
def test_neighbours_of_the_guarded_files_are_allowed(tmp_path, path):
    # The guard must not turn `.loci/build/` into a read-only directory: the CLI
    # writes objects, sidecars and text dumps there and the agent reads them.
    assert _run(_edit(path), tmp_path) is None, f"{path} must not be guarded"


@pytest.mark.parametrize("command", [
    "loci init --auto",
    "loci init --refresh --target=armv7e-m",
    "loci init set rust.features=max-pure",
    "loci init set build.compdb.select.prefer_output=build/app.dir/**",
    "loci init add-file src/new.c",
    "loci init probe",
    "cat .loci/build.yaml",
    "jq -r .target .loci/build.yaml",
    "cat .loci/build/flags.json",
])
def test_the_sanctioned_recipe_verbs_and_reads_are_allowed(tmp_path, command):
    """No Bash verb is denied for the recipe, and that is the decision.

    `loci init` IS the write path — the consent lives in the skill's question
    before it invokes one — and a recipe nobody can write is a project nobody can
    measure. Reads are unguarded for the same reason they are for contract.yaml.
    """
    assert _run(_bash(command), tmp_path) is None, f"{command!r} must be allowed"


@pytest.mark.parametrize("rel", (".loci/contract.yaml",) + RECIPE_FILES)
def test_an_alternate_data_stream_spelling_is_denied(tmp_path, rel):
    """`path::$DATA` opens the file itself on NTFS.

    Same class as the case bypass and the trailing dot — a spelling the
    filesystem resolves to the guarded file and a byte comparison reads as a
    different one. Verified end-to-end on NTFS: a `Write` to
    `.loci\\build.yaml::$DATA` lands on `.loci\\build.yaml`.
    """
    assert _denied(_run(_edit(rel + "::$DATA"), tmp_path)), f"{rel}::$DATA"
    assert _denied(_run(_write(rel + "::$DATA"), tmp_path)), f"{rel}::$DATA"


@pytest.mark.parametrize("rel", (".loci/contract.yaml",) + RECIPE_FILES)
def test_the_verdict_does_not_depend_on_any_external_binary(tmp_path, rel):
    """No fork may sit between the payload and the verdict.

    The first case-insensitivity fix lowercased both sides through
    `printf | tr`. With `tr` off a minimal PATH both strings came back empty,
    every comparison failed, and all four files became writable — exit 0, no
    output, nothing to notice. That is worse than the `cat` fail-open it shipped
    beside: there the payload was empty, here the guard parses it correctly and
    then declines to act.

    `jq` has its own documented degradation and is excluded; everything else the
    verdict could reach for is taken away at once.
    """
    if sys.platform == "win32":
        pytest.skip("PATH shadowing is not reliable on Windows")
    stub = tmp_path / "minimal-bin"
    stub.mkdir()
    for name in ("bash", "jq"):
        real = shutil.which(name)
        if real is None:
            pytest.skip(f"{name} not available")
        (stub / name).symlink_to(real)
    for gone in ("tr", "sed", "realpath", "git", "cat", "printf"):
        assert shutil.which(gone, path=str(stub)) is None
    env = {"PATH": str(stub), "HOME": str(tmp_path / "home")}
    assert _denied(_run(_edit(rel), tmp_path, env=env)), (
        f"{rel} was writable with only bash and jq on PATH — something on the "
        f"verdict path forks, and a host without it fails open silently")


@pytest.mark.parametrize("mb", [1, 3])
def test_a_large_write_is_still_decided_inside_the_hook_budget(tmp_path, mb):
    """`hooks.json` gives this hook 5 s, and PreToolUse is fail-open.

    Reading the payload with `IFS= read -r -d ''` issues one read(2) per byte on
    a pipe: a 2 MB `Write` took 6.4 s, overran the timeout, and the write went
    through — size alone flipping the verdict on the one guarded file that has no
    commit diff, no escrow and no validator. It also added ~5 s to every ordinary
    large write, guarded or not.

    The bound is the hook's real budget rather than a micro-benchmark — but that
    does not make it load-proof, and this docstring used to say it did. It went
    red at 19 % of a full Windows suite, then passed 3/3 re-run idle immediately
    after (P98). Measured idle here: 0.37 s at 1 MB and 0.53 s at 3 MB against
    the 5 s budget, versus the 6.4 s at 2 MB and 5.9 s at 3 MB that the
    `read(2)`-per-byte version cost.

    So read a red here as a finding, not as a flake. The same arithmetic holds
    off the test bench: `hooks.json` gives the hook 5 s and PreToolUse is
    fail-open, so on a machine loaded by a real build a large `Write` to a
    guarded path can overrun and go through unguarded — which is the defect this
    test exists for. Closing that means measuring CPU time instead of wall clock,
    or widening the budget in `hooks.json`; both are behaviour decisions, and
    neither is made here.
    """
    payload = {"tool_name": "Write",
               "tool_input": {"file_path": ".loci/build/flags.json",
                              "content": "x" * (mb * 1024 * 1024)}}
    start = time.monotonic()
    decision = _run(payload, tmp_path)
    elapsed = time.monotonic() - start
    assert _denied(decision), f"a {mb} MB write to a guarded file was allowed"
    assert elapsed < 5.0, (
        f"the guard took {elapsed:.1f}s on a {mb} MB payload, against the 5 s "
        f"timeout in hooks.json — past it the hook is killed and fails open")


def _walk_body() -> str:
    """`_walk` alone, lifted out of the guard.

    Behavioural tests cannot see this function: the suffix arm above the
    comparison answers every spelling a test naturally writes, which is how the
    old `_lexical` shipped turning every RELATIVE path absolute with the suite
    green, and how all four of F16 stayed invisible for a release. So it is
    exercised directly, the way `_lexical` was.
    """
    src = GUARD.read_text(encoding="utf-8")
    # The bound comes with it: `_walk` reads it, and `set -u` is on.
    bound = re.search(r"^_ROUTE1_MAX_SEGMENTS=\d+$", src, re.M).group(0)
    return (bound + chr(10)
            + src[src.index("_walk() {"):src.index(chr(10) + "_guarded_match() (")])


@pytest.mark.parametrize("given,where,tail", [
    # Nothing below `tmp_path` exists but `real/`, so the walk stops where it
    # runs out of directory and collapses the rest as text — which is what
    # `realpath -m` does for a component that is not there, and what Win32 does
    # for every component (`C:\a\nope\..\b` opens `C:\a\b`).
    ("a/b", ".", "/a/b"),
    ("a/zz/../b", ".", "/a/b"),           # the whole point of the collapse
    ("a/./b", ".", "/a/b"),
    ("a//b", ".", "/a/b"),
    ("a/b/..", ".", "/a"),
    ("a b/c", ".", "/a b/c"),             # a space is not a separator
    ("a*b/c", ".", "/a*b/c"),             # nor is a glob character expanded
    ("./a", ".", "/a"),
    # …and where it DOES exist the walk descends, so there is no tail at all.
    ("real", "real", ""),
    ("real/", "real", ""),
    ("real/./", "real", ""),
    ("real/x", "real", "/x"),
    # THE SUBTLE ONE. `..` brings the tail back to empty and the walk then
    # resumes descending, because at that moment the path really does point at
    # the directory we are standing in. Without it the two sides of route 1's
    # comparison split in different places — `<root>` + `/.loci/contract.yaml`
    # against `<root>/.loci` + `/contract.yaml` — and nothing ever matches.
    ("nope/../real/x", "real", "/x"),
    ("nope/../real", "real", ""),
    ("real/../real/x", "real", "/x"),
    # …and the anchors, which relative cases never reach. `//` is NOT a UNC
    # anchor here: `cd -P //` makes MSYS enumerate the network (2.83 s of a 5 s
    # budget measured on a server that is simply absent, and one that answers
    # slowly can block far longer), so a leading `//` is walked from `/` like
    # any other absolute path — the guard collapses it on both sides before it
    # gets here.
    ("{abs}/real/x", "real", "/x"),
    ("{abs}/real/../real/x", "real", "/x"),
    ("{abs}/nope/../real", "real", ""),
    ("{win}/real/x", "real", "/x"),
])
def test_the_walk_descends_what_exists_and_collapses_what_does_not(
        tmp_path, given, where, tail):
    (tmp_path / "real").mkdir()
    given = given.format(abs=_to_bash_path(tmp_path), win=_native_path(tmp_path))
    script = (_walk_body() + chr(10) +
              '_w_home=$PWD\n'
              '_walk "$1"; _rc=$?\n'
              '[ "$_rc" = 0 ] || { printf "rc%s" "$_rc"; exit 0; }\n'
              'if [ "$PWD" -ef "$2" ]; then _d=same; else _d="$PWD"; fi\n'
              'printf "%s %s" "$_d" "$_w_tail"\n')
    path = tmp_path / "walk.sh"
    path.write_text(script, encoding="utf-8", newline="\n")
    out = subprocess.run(
        [_find_bash(), _to_bash_path(path), given,
         _to_bash_path(tmp_path / where)],
        capture_output=True, text=True, timeout=30, cwd=str(tmp_path))
    assert out.returncode == 0, out.stderr
    assert out.stdout == f"same {tail}", (
        f"_walk({given!r}) = {out.stdout!r}, want 'same {tail}' — where 'same' "
        f"is the walk having landed in {where!r}")


def test_the_walk_refuses_past_the_segment_bound(tmp_path):
    """`_ROUTE1_MAX_SEGMENTS` at the function, either side of the edge.

    rc 2 means "too long to walk inside the budget, deny without comparing" and
    rc 0 means "walked". The behavioural test above pins what the guard does
    with them; this pins where the edge is, because the constant is the whole
    mechanism and a typo in it is silent.
    """
    src = GUARD.read_text(encoding="utf-8")
    bound = int(re.search(r"^_ROUTE1_MAX_SEGMENTS=(\d+)$", src, re.M).group(1))
    script = (_walk_body() + chr(10) +
              '_w_home=$PWD\n'
              '_walk "$1"; printf "rc%s" "$?"\n')
    path = tmp_path / "walkbound.sh"
    path.write_text(script, encoding="utf-8", newline="\n")

    def rc(segments: int) -> str:
        return subprocess.run(
            [_find_bash(), _to_bash_path(path), "a/" * (segments - 1) + "a"],
            capture_output=True, text=True, timeout=60,
            cwd=str(tmp_path)).stdout

    assert rc(bound) == "rc0", f"{bound} segments must still be walked"
    assert rc(bound + 1) == "rc2", (
        f"{bound + 1} segments must be refused — past the bound the collapse "
        f"is quadratic and the hook is killed at 5 s, which fails open")


def test_the_walk_leaves_the_shell_as_it_found_it(tmp_path):
    # It sets `IFS` and `set -f` to split a path safely. Both are global, and the
    # guard keeps using globs and word splitting afterwards. (It also CDs, which
    # is why `_guarded_match` is a subshell — pinned separately below.)
    script = ('before=$(printf %s "$IFS" | od -An -c | tr -d " \\n")\n'
              + _walk_body() + chr(10) +
              '_w_home=$PWD\n'
              '_walk "a/zz/../b"\n'
              'after=$(printf %s "$IFS" | od -An -c | tr -d " \\n")\n'
              '[ "$before" = "$after" ] || { echo "IFS changed"; exit 1; }\n'
              'case "$-" in *f*) echo "noglob left on"; exit 1 ;; esac\n'
              'echo ok\n')
    path = tmp_path / "walkenv.sh"
    path.write_text(script, encoding="utf-8", newline="\n")
    out = subprocess.run([_find_bash(), _to_bash_path(path)],
                         capture_output=True, text=True, timeout=30,
                         cwd=str(tmp_path))
    assert out.stdout.strip() == "ok", f"{out.stdout}{out.stderr}"


def test_the_walk_is_only_ever_run_inside_a_subshell():
    """`_walk` CDs. A `cd` that escaped would move the hook itself.

    `_guarded_match` is declared with `( … )` rather than `{ … }` for exactly
    this, and it is the only caller. Route 2 runs after route 1 in the same
    process and resolves nothing, so a leaked `cd` would not fail loudly — it
    would quietly change what a relative path means.
    """
    src = GUARD.read_text(encoding="utf-8")
    assert "_guarded_match() (" in src, (
        "`_guarded_match` is no longer a subshell — `_walk`'s `cd` now escapes "
        "into the rest of the hook")
    # Every call must be INSIDE that subshell, not merely spelled like one: a
    # top-level `_walk "$fp"` satisfies "starts with `_walk `" and moves the
    # hook's own working directory for everything after it.
    body = src[src.index("_guarded_match() ("):src.index(chr(10) + ")", src.index(
        "_guarded_match() ("))]
    calls = [l.strip() for l in src.splitlines()
             if re.match(r"^\s*_walk\s", l) and not l.lstrip().startswith("#")]
    inside = [l.strip() for l in body.splitlines()
              if re.match(r"^\s*_walk\s", l)]
    assert calls and calls == inside, (
        f"`_walk` is called {len(calls)} times but only {len(inside)} are "
        f"inside `_guarded_match`'s subshell: {sorted(set(calls) - set(inside))}")


def test_route_1_resolves_paths_with_one_rung():
    r"""F16 items 3 and 4, structurally: there is no ladder to choose from.

    `_resolve` used to be three rungs — `realpath -m`, then `realpath`, then a
    lexical collapse plus `cd`. Which one answered depended on the host, and on
    macOS and the BSDs (no `-m`, and `realpath` fails on a path that does not
    exist yet, which is every `Write`) the two sides of one comparison could take
    DIFFERENT rungs on the same call: `realpath` for the guarded file that is
    there, the shell for the unborn `file_path`. Those two rungs disagree about
    how to spell a drive and about a symlink behind a `..`, so the comparison
    could not match and all three files were writable.

    The fix is not a better ladder, it is no ladder: one function, on every
    host, for both sides. This pins the ABSENCE, because a helpful "fast path
    when GNU realpath is available" would pass every other test in this file on
    this machine and reopen items 1, 2 and 4 on the machines it is not run on.
    """
    code = chr(10).join(l for l in GUARD.read_text(encoding="utf-8").splitlines()
                        if not l.lstrip().startswith("#"))
    # `realpath` by name, because that is the one that was there — and the
    # others that would do the same job, because banning one name only moves
    # the ladder to a different binary. Route 1 resolves with `cd`, `[ -d ]` and
    # `-ef`, all builtins; anything here that must be INSTALLED is a rung whose
    # presence decides the verdict.
    for tool in ("realpath", "readlink", "cygpath", "stat ", "find ", "dirname",
                 "basename"):
        assert tool not in code, (
            f"`{tool.strip()}` is on the verdict path. Route 1 resolves with "
            f"builtins on purpose: a host tool is a rung, a rung that is "
            f"sometimes absent is a second rung, and two rungs that disagree "
            f"is F16")


def test_route_1_walks_every_path_it_denies():
    """One list, read twice — and a test that says so.

    `_ROUTE1_GUARDED` is what the walk compares against; the `guard_path` calls
    are what maps a hit to its deny reason, and what
    `test_the_guarded_paths_this_lint_screens_match_the_guard`
    (tests/unit/test_freshness_contract.py) reads to know which paths the prose
    lints must screen. A file in one list and not the other is a guarded file
    that is either never compared or never denied.
    """
    src = GUARD.read_text(encoding="utf-8")
    walked = re.search(r'_ROUTE1_GUARDED="([^"]*)"', src).group(1).split()
    denied = re.findall(r'guard_path "([^"]+)"', src)
    assert walked == denied, (
        f"the walk compares {walked} and the deny reasons cover {denied}")


def test_the_payload_is_not_read_one_byte_at_a_time():
    """A structural pin, because the behavioural one is platform-dependent.

    `IFS= read -r -d ''` issues one read(2) per byte on a pipe. On MSYS that is
    3.98 s for 3 MB against this hook's 5 s timeout — which fails open, so size
    alone flipped the verdict on a guarded file. On Linux the same form is fast
    enough that the timing test below stays green, so the timing test cannot be
    the only thing standing here.

    The builtin read is kept as a FALLBACK for a host with no `cat`, where slow
    beats blind. What must not come back is it being the primary path.
    """
    body = GUARD.read_text(encoding="utf-8")
    # Code only: the comment above the read explains the byte-at-a-time problem
    # and naturally quotes the form it is warning about.
    code = chr(10).join(l for l in body.splitlines()
                        if not l.lstrip().startswith("#"))
    primary = code.split("if command -v cat", 1)[0]
    assert "read -r -d" not in primary, (
        "the payload is read with the builtin as the primary path; it is "
        "byte-at-a-time on a pipe and overruns the hook's 5 s timeout on a large "
        "write, which fails open")
    assert "payload=$(cat)" in body, "the fast path is gone"
    assert "IFS= read -r -d '' payload" in body, (
        "the no-`cat` fallback is gone — a host without it now reads an empty "
        "payload and allows every write, silently")
    # The PATH repair has to precede anything that looks for a binary, which is
    # what made a missing `cat` a total fail-open in the first place.
    assert body.index('PATH="$PATH:') < body.index("command -v cat"), (
        "the PATH repair no longer precedes the payload read")


def test_the_hook_is_registered_for_every_tool_it_claims_to_guard():
    """The guard reads `file_path` and never branches on `tool_name` — but it
    only ever runs for the tools `hooks.json` matches.

    A test parametrized over `MultiEdit`/`NotebookEdit`/`Update` was green while
    asserting a property that is false: `Update` is not in the matcher, and a
    real `NotebookEdit` payload carries `notebook_path`, not `file_path`. Pin the
    matcher instead, so the claim and the wiring cannot drift apart.
    """
    doc = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    groups = doc.get("hooks", doc).get("PreToolUse", [])
    matchers = [g.get("matcher") for g in groups
                if any("contract-guard" in (e.get("command") or "")
                       for e in g.get("hooks", []))]
    assert matchers == ["Edit|Write|Bash"], (
        f"contract-guard is registered for {matchers}. Every tool that can write "
        f"a file by `file_path` must be here — the guard cannot deny what it is "
        f"never invoked for.")


def test_the_guard_does_not_fork_on_a_payload_it_cannot_deny(tmp_path):
    """The prefilter's early exit, asserted rather than described.

    The old version of this said "it must return without spawning jq, git or
    realpath" in its docstring and asserted only the verdict, so deleting the
    early exit (`*) exit 0 ;;` → `*) ;;`) left the suite green on Windows while
    every Bash, Edit and Write in every repo paid three forks. This hook runs
    ahead of all of them with a 5 s budget, so the fork count IS the behaviour.

    Counted with shims ahead of the real binaries on PATH.
    """
    bindir = tmp_path / "countbin"
    bindir.mkdir()
    counter = tmp_path / "forks.txt"
    for name in ("jq", "realpath", "git", "tr", "sed", "cat"):
        real = shutil.which(name)
        if real is None:
            pytest.skip(f"{name} not available")
        shim = bindir / name
        shim.write_text(
            "#!/bin/sh\n"
            f'printf "%s\\n" {name} >> "{_to_bash_path(counter)}"\n'
            f'exec "{_to_bash_path(Path(real))}" "$@"\n',
            encoding="utf-8", newline="\n")
        shim.chmod(0o755)

    def forks(payload) -> int:
        counter.write_text("", encoding="utf-8")
        env = {"PATH": f"{_to_bash_path(bindir)}:{_base_path()}"}
        _run(payload, tmp_path, env=env)
        return len(counter.read_text(encoding="utf-8").split())

    # Nothing in the payload can be denied by either route. One fork is the
    # payload read itself (`cat`, because bash's builtin read is byte-at-a-time
    # on a pipe and overran the hook's timeout on a large write); anything above
    # that is the prefilter having failed to exit.
    assert forks(_bash("npm test -- --watch=false")) <= 1
    assert forks(_edit("web/src/index.ts")) <= 1
    # A Bash call naming the recipe reaches no route that can deny it either:
    # route 1 needs a `file_path`, and route 2 only denies contract verbs.
    # A capitalised `Contract` is ordinary in Solidity, .NET and TypeScript
    # trees. Making the prefilter's contract token case-insensitive without
    # narrowing it to a FILE NAME put 0.5-0.7 s of guard on every tool call in
    # such a repo, measured at 7x on an Edit whose only `Contract` was in a code
    # comment.
    assert forks(_edit("src/ContractService.ts")) <= 1
    assert forks(_bash("grep -rn CONTRACT_ADDRESS .")) <= 1
    assert forks(_edit("src/main.c")) <= 1
    assert forks(_bash("cat .loci/build.yaml")) <= 1, (
        "a Bash payload naming the recipe paid forks for a route that cannot "
        "deny it — the prefilter's file_path condition is gone")


def test_the_prefilter_admits_the_new_files(tmp_path):
    """The prefilter used to require the literal `contract`.

    Left alone, it would have made route 1 unreachable for both new files — the
    guard would deny nothing and no assertion above would have said so, because
    every one of them would have passed the payload straight through.
    """
    for rel in RECIPE_FILES:
        assert "contract" not in rel, "this test is vacuous if the path says contract"
        assert _denied(_run(_edit(rel), tmp_path)), f"{rel} never reached route 1"


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
    # Every row below RUNS the verb and was already denied by the substring
    # match the token matcher replaced. They are the floor: the fix narrows what
    # counts as a match, never which invocations are denied.
    "/usr/local/bin/loci contract accept",                 # absolute path
    "loci.exe contract accept",                            # Windows binary name
    "C:\\Users\\dev\\.local\\bin\\loci.exe contract init",   # native spelling
    # The binary `case` has six alternatives and two of them were reached by no
    # row at all — a mutation deleting either was invisible on both platforms.
    # A Windows path WITHOUT `.exe` needs `*'\'loci`; a POSIX path WITH it
    # needs `*/loci.exe`.
    "C:\\Users\\dev\\bin\\loci contract accept",
    "/usr/local/bin/loci.exe contract accept",
    "(loci contract accept)",                              # subshell
    "$(loci contract enable --index 0)",                   # substitution
    "`loci contract disable --index 0`",                   # the older one
    "loci contract accept &",                              # backgrounded
    "time loci contract accept",                           # wrapper word
    "if true; then loci contract enable --index 0; fi",    # after a reserved word
    "make build\nloci contract accept",                    # newline, not `&&`
    "loci \\\ncontract accept",                            # line continuation
    "loci --format contract contract accept",              # the flag ate the token
    # Wrappers. There is no list of these in the guard and there must not be:
    # round 1 had one, and a 230-spelling review corpus found 109 invocations it
    # let through. `hooks/turn-clean.sh` itself runs `timeout 10 loci …` and
    # `lib/eval-graders.sh` records `uv run loci init` as a residual of the same
    # mistake, so this is how the binary is really spelled around here.
    "uv run loci contract accept",
    "uvx loci contract accept",
    "poetry run loci contract accept",
    "npx loci contract accept",
    "timeout 30 loci contract accept",
    "eval loci contract accept",
    "env -i loci contract accept",                         # a FLAG to a wrapper
    "sudo -u ci loci contract accept",
    "nice -n 10 loci contract accept",
    "xargs -r loci contract accept",
    "stdbuf -o0 loci contract accept",
    "docker run img loci contract accept",
    "ssh host loci contract accept",
    ">log.txt loci contract accept",                       # leading redirection
    "2>/dev/null loci contract accept",
    # A QUOTED ARGUMENT is not a quoted command. Round 2 stopped the scan at any
    # token carrying a quote, which allowed all of these — the binary in plain
    # sight, one character away from denied. A path with a space or a `"$VAR"` in
    # a CI script produces them by accident.
    'loci -f "json" contract accept',
    "loci --note 'why' contract edit --index 0",
    'loci --dir "my dir" contract disable --index 0',
    'loci --config "$HOME/.loci.toml" contract accept',
    'sudo -u "$CI_USER" loci contract accept',
    'env LOCI_TOKEN="$T" loci contract accept',
    'env LOCI_NOTE="a b" loci contract accept',            # region spans two words
    "timeout '30' loci contract accept",
    'docker run -v "$PWD:/w" img loci contract accept',
    'uv run --project "$PWD" loci contract accept',
    '> "$LOG" loci contract accept',
    # An ESCAPED quote is not a quote, and reading one as a quote LOSES the
    # binary: the token's count comes out odd, a region opens that nothing can
    # close, and every bare `loci` after it stops being a command word.
    'MSG="a\\"b" loci contract accept',
    "MSG='it'\\''s' loci contract accept",
    "MSG=$'a\\'b' loci contract accept",
    # …and a separator INSIDE a quoted argument cuts the segment. `(`, `)`,
    # `;`, `|`, `&` and a backtick all do it, and the quoted text is removed
    # before the split now, so there is no fragment left for them to cut.
    'env NOTE="fix(guard)" loci contract accept',
    'env NOTE="a;b" loci contract accept',
    'env NOTE="a|b" loci contract accept',
    'git commit -m "wip" && env NOTE="x&y" loci contract accept',
    # A COMMAND SUBSTITUTION is a command whatever quoting surrounds it. Every
    # row below RAN the verb and was allowed by 0.2.3 (F10 §1), because `$( … )`
    # was cut into its own segment while the `"` before it left the carried
    # quote state open — so the substituted command's own `loci` was read as
    # quoted text. The unquoted spelling three rows further up was denied all
    # along, which is what showed it was the QUOTE hiding it, not the `$(`.
    'echo "$(loci contract accept)"',
    'OUT="$(loci contract accept)"',
    'echo "applied: $(loci contract accept)"',
    'echo "`loci contract accept`"',
    'if [ -n "$(loci contract accept)" ]; then echo ok; fi',
    'X="$(  loci contract accept  )"',
    'echo "$(echo `loci contract accept`)"',            # a backtick inside $( )
    'echo "`echo $(loci contract accept)`"',            # …and the other nesting
    # A `)` that belongs to something else must not be read as the one that
    # closes the substitution. Tracking only `$(` and not a plain `(` meant a
    # subshell, an arithmetic expansion, a function definition or a process
    # substitution popped the frame early, restored `"…"`, and masked the rest
    # of the substitution away. All four RAN the verb.
    'echo "$( (true) && loci contract accept )"',
    'MSG="$( (cd /tmp && pwd) && loci contract accept )"',
    'echo "$(echo $((1+1)); loci contract accept)"',
    'echo "$(f() { :; }; loci contract accept)"',
    'echo "$(read x < <(true); loci contract accept)"',
    'echo "$(echo a\\) ; loci contract accept)"',       # …and an ESCAPED one
    # A backtick region ends at the next backtick whatever the quoting inside
    # it: an unbalanced `'` in there is an error INSIDE the substitution and
    # nothing more, so the `;` after it still separates and the verb still runs.
    # A scan that let that apostrophe open a region masked the invocation away —
    # 113 of 113 bypasses in a 52 000-command fuzz were this one shape.
    "N=`wc -l < user's.txt`; loci contract accept",
    "echo `grep -c don't f.txt`; loci contract accept",
    "echo \"`grep -c don't f.txt`\"; loci contract accept",
    "echo `'`; loci contract accept",                   # the minimal spelling
    # A quote in a COMMENT is not a quote. Reading one as a quote opened a
    # region nothing closed, so every `loci` on every later line stopped being
    # a command word — and an apostrophe in a comment is ordinary English, not
    # an evasion. 64 of 64 bypasses in a 372-candidate multi-line fuzz were this
    # one shape, and `#'` is the whole of it.
    "cd /x && make build   # don't forget\nloci contract accept",
    "git status  # the user's tree\nloci contract accept",
    'echo hi  # say "hi\nloci contract accept',
    "#'\nloci contract accept",
    "make build  # step 1) don't\nmake test\nloci contract accept",
    # …and the word boundary that decides whether a `#` starts one has to be
    # TRACKED, not read off the previous byte. Round 4 broke it in both
    # directions with these. An ESCAPED metacharacter is not a boundary — the
    # shell keeps the word going — so the first five are commands, not comments,
    # and reading them as comments dropped the invocation that followed:
    "echo a\\ #x; loci contract accept",
    "echo a\\\t#x; loci contract accept",
    "echo a\\;#x; loci contract accept",
    "echo a\\&#x; loci contract accept",
    "echo a\\|#x; loci contract accept",
    # …and one row per non-whitespace member of the boundary set, because a
    # mutation dropping `;` and `&` from it was invisible to the whole suite.
    "echo x;#don't\nloci contract accept",
    "echo x&#don't\nloci contract accept",
    "echo x\t#don't\nloci contract accept",
    "echo a |#don't\nloci contract accept",
    "false ||#don't\nloci contract accept",
    # A `)` that closes a word-continuing `(` does NOT open a word — `<(`,
    # `>(` and `=(` keep the word going, so `#` after their `)` is text and
    # the command after it is a command. Saying every `)` opened a word
    # commented these out and they RAN.
    "cat <(echo a)#x; loci contract accept",
    "echo a > >(cat)#x; loci contract accept",
    "x=(a b)#x; loci contract accept",
    "diff <(sort a)#x <(sort b); loci contract accept",
    # A backtick closes its region from ANY depth. A `(` or `$(` opened inside
    # it sits above the frame and is never popped, because a `)` inside `'…'`
    # is literal — so the close was missed and the apostrophe hid the rest.
    "echo `('`; loci contract accept",
    "echo `a(don't`; loci contract accept",
    "echo `(echo don't)`; loci contract accept",
    "echo `echo $(echo don't)`; loci contract accept",
    # …and an ESCAPED backtick closes nothing, so it ends no comment either.
    'echo "`x # a\\`don\'t`" ; loci contract accept',
    "echo `x # a\\`don't` ; loci contract accept",
    # …while after `$(` or a line continuation a word CAN begin, so these two
    # really are comments and the apostrophe in one is not a quote. Reading the
    # `#` as text is round 3's defect, back.
    "echo \"$(#don't\nloci contract accept)\"",
    "echo a \\\n#don't\nloci contract accept",
    # A comment ends at a backtick that closes an open region, because bash
    # finds the closing backquote without honouring comments. Ending only at a
    # newline let the comment eat the command after the substitution.
    "echo `make # x` ; loci contract accept",
    "echo \"`make # x`\" ; loci contract accept",
    "N=`wc -l f # c`; loci contract accept",
    # Inside `'…'` a backslash is LITERAL, so the quote after it CLOSES the
    # region — and everything from there on is a command again. Stripping `\'`
    # without asking which region it was in left `_r2_open` pinned for the rest
    # of the command and every later `loci` stopped being a command word (F10
    # §2). A Windows path in a `grep -F` pattern is how this arrives by accident.
    "echo 'a\\'; loci contract accept",
    "echo 'C:\\'; loci contract accept",
    "grep -F 'C:\\Users\\' f.txt; loci contract accept",
    "printf '%s' 'end\\' && loci contract accept",
    # Only the CURRENT quote character can close a region; the other kind is
    # ordinary text inside it. Counting the kind of the token's FIRST quote made
    # `'it'"'"'s'` — the canonical way to put an apostrophe inside a
    # single-quoted string — read as an open region that never closed (F10 §3).
    """echo 'it'"'"'s done'; loci contract accept""",
    """git commit -m 'don'"'"'t'; loci contract accept""",
    """echo 'a'"b'c"; loci contract accept""",
    '''echo "a"'b"c'; loci contract accept''',
    # A substitution BETWEEN the binary and the verb. Every one of these runs
    # the verb, and every one was ALLOWED from F07 until F11: `(`, `)` and the
    # backtick were segment separators, so the mask cut `loci --root $` /
    # `pwd` / ` contract accept` and denied none of the three. The scan lifts
    # the body out of the word now instead of cutting the word at it, so what
    # the matcher sees is the invocation with a hole where the substitution
    # was. Residual R5, closed.
    "loci --root $(pwd) contract accept",
    "loci -C `pwd` contract accept",
    'loci --project "$(pwd)" contract accept',
    "loci -n $((1+1)) contract accept",
    # …and the same spelling one level down, which is what a flat pair of
    # accumulators cannot do: with the bodies collected in one stream a nested
    # `$(` writes its boundary into the middle of its PARENT's body and cuts
    # the invocation there, so `$( … )` around the first row above would be six
    # characters of bypass. Only the outermost frame is lifted, so a nested
    # body stays glued where the shell puts it.
    "$(loci --root $(pwd) contract accept)",
    "`loci --root $(pwd) contract accept`",
    "loci --root $(echo $(pwd)) contract accept",
    'loci --root "$(echo "$(pwd)")" contract accept',
    # …a region carrying a separator of its own, which must not leak out of it
    "loci --root `cd /x; pwd` contract accept",
    # …and the NESTED body with one byte of glue beside it, which is the shape
    # that says every frame is lifted and not only the outermost. With the
    # inner body left glued to the `x`, the binary arrives as the word `xloci`
    # and matches nothing: all four of these RAN the verb and were allowed for
    # one review round. Any glue byte works except `/` and `\`, which leave the
    # word matching `*/loci`.
    "$(x$(loci contract accept))",
    "$($(loci contract accept)x)",
    "`x$(loci contract accept)`",
    "$(x`loci contract accept`)",
    "echo $(basename /tmp/$(loci contract accept).log)",
    "$(echo $(echo $(echo x$(loci contract accept))))",
    # …and an empty substitution glued to the binary, where the word the shell
    # runs really is `loci`.
    "loci$(true) contract accept",
    "$(true)loci contract accept",
    # An ESCAPED backtick is one the shell keeps inside the word, and it was in
    # the separator set — so this ran the verb and was cut into `loci --root a`
    # and `b contract accept`. The set lost the backtick when the mask stopped
    # writing one.
    "loci --root a\\`b contract accept",
    # NTFS and APFS are case-insensitive, so a case-variant IS the binary and
    # all three of these ran the verb. Route 1 has reasoned about this since
    # T10; route 2 did not until F10. The VERBS stay case-sensitive — see
    # `test_a_command_that_only_documents_the_verb_is_allowed`.
    "LOCI contract accept",
    "Loci contract accept",
    "loci.EXE contract accept",
    "/usr/bin/LOCI contract accept",
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


@pytest.mark.parametrize("command", [
    # The three that were measured on 2026-09-08, while the guard was denying
    # them: a handoff note could not be written from Bash, and the commit
    # message that documents the sanctioned flow could not be written at all.
    "git commit -m \"Document that the user runs loci contract accept\"",
    "echo 'run: ! loci contract accept' >> runs/F06/handoff.md",
    "python - <<'PY'\n"
    "open('notes.md','w').write('hand the user: ! loci contract accept')\n"
    "PY",
    # …and the same sentence for each of the five denied subcommands, because
    # the old matcher had one arm per verb and a fix that missed one would look
    # green here.
    "git commit -m \"the user runs loci contract accept, never the agent\"",
    "git commit -m \"the user runs loci contract init, never the agent\"",
    "git commit -m \"the user runs loci contract edit, never the agent\"",
    "git commit -m \"the user runs loci contract disable, never the agent\"",
    "git commit -m \"the user runs loci contract enable, never the agent\"",
    # Searching the shipped prose for the verb. Allowed before this change too,
    # but only by accident: a `'` happened to sit where the leading space the
    # substring match needed would have been.
    "grep -rn 'contract accept' skills/",
    "grep -rn contract accept skills/",
    # Prose that names a PATH to the binary. Round 2 denied every one of these:
    # the token `"/usr/bin/loci` matched `*/loci` with the opening quote absorbed
    # by the `*`, so a sentence became a command word. A token that opens a
    # quoted region is never the binary now.
    'echo "/usr/bin/loci contract accept now" >> note.md',
    "echo './loci contract accept now' >> note.md",
    'echo "C:\\tools\\loci.exe contract accept now" >> note.md',
    "python -c \"print('/usr/bin/loci contract accept x')\"",
    # An apostrophe inside a double-quoted message. Counting both quote
    # characters together closed the region at `user's` and read the next `loci`
    # as a command word — and apostrophes are ordinary in exactly this prose.
    'git commit -m "the user\'s own loci contract accept line"',
    'git commit -m "don\'t run loci contract accept yourself"',
    # …and the same apostrophe written the way a shell forces you to write it
    # inside a SINGLE-quoted string. `'it'"'"'s'` is five quote characters, so
    # per-token parity called it an open region and denied the sentence; the
    # shell closes it. 0.2.3 denied this one — the mirror image of the bypass in
    # `test_contract_writing_verbs_are_denied`, from the same defect.
    """echo 'it'"'"'s a loci contract accept line' >> note.md""",
    # A backtick inside `'…'` is literal text, not a substitution — which is how
    # a handoff note names the command it is telling the user to run. Escaped
    # inside `"…"` it is literal too. Unescaped inside `"…"` it RUNS, and the
    # deny corpus carries that spelling.
    "echo 'run `loci contract accept` yourself' >> note.md",
    'echo "run \\`loci contract accept\\` yourself" >> note.md',
    "echo '$(loci contract accept)' >> note.md",
    # A substitution that CLOSES before the verb leaves the verb in quoted text.
    # This is what the frame stack buys, and it is why a closing `)` restores
    # the enclosing `"…"` rather than dropping to unquoted: without the restore
    # the following `"` opens instead of closes and every quoted mention for the
    # rest of the command is read as a command.
    'echo "$(date) loci contract accept"',
    'echo "$(echo "hi") loci contract accept"',
    'echo "$(date)"; git commit -m "then run loci contract accept"',
    # The verb is case-SENSITIVE, and that is what keeps prose about the
    # contract out of this. Only the BINARY is matched case-insensitively.
    "grep -rn 'Contract Accept' skills/",
    "loci CONTRACT accept",
    "loci contract ACCEPT",
    # A COMMENT is not command text, so writing the invocation in one is a
    # mention like any other. The scan drops the body — which is what stops a
    # stray apostrophe in a comment from hiding the next line, and this side of
    # it comes free.
    "make build  # then run loci contract accept yourself",
    "git commit -m wip  # the user runs loci contract accept",
    "make build\n# loci contract accept is the user's job\nmake test",
    # …but only where a word can begin. These are not comments and must not be
    # treated as one.
    "echo a#b; grep -rn 'contract accept' skills/",
    "curl 'http://x/y#loci contract accept'",
])
def test_a_command_that_only_documents_the_verb_is_allowed(tmp_path, command):
    """Writing the verb down is not running it, and the guard must tell them apart.

    Route 2 substring-matched the whitespace-normalised command text, so a
    commit message, an ``echo`` appending a handoff note and a heredoc writing
    documentation were all denied — the guard could not see where a command
    began. That is the same defect that got the write-*shape* list deleted from
    this route (``test_shell_writes_to_the_file_are_deliberately_not_guarded``:
    "it denied a ``.c`` edit whose comment mentioned the path"), reached through
    commands instead of paths.
    """
    assert _run(_bash(command), tmp_path) is None, f"{command!r} must be allowed"


@pytest.mark.parametrize("command", [
    'loci "contract" accept',
    "loci 'contract' accept",
    'loci contract "accept"',
    "V=accept; loci contract $V",
    'loci "contract" init',
])
def test_the_evasions_are_recorded_not_closed(tmp_path, command):
    """This test asserts a decision, not an oversight. Do not "fix" it.

    Every command here RUNS a contract-writing verb and every one of them is
    allowed, because the guard does not unquote and does not expand — it splits
    the command into segments at the shell's own separators and reads words. A
    quoted token is the same class of thing as ``cat > .loci/contract.yaml``,
    which the test above allows deliberately, and it is out of scope for the
    same recorded reason: shape and text matching produced "every defect and
    every false positive the guard ever produced" and "never caught a real
    mistake".

    Route 2 is a speed bump over the verbs the tool's own ``--help`` advertises,
    not a sandbox. ADR-0015's commit diff is the backstop for what walks through
    it. The point of writing these down is that the next reader finds them here
    instead of rediscovering them and believing the guard is airtight.
    """
    assert _run(_bash(command), tmp_path) is None, f"{command!r} must be allowed"


@pytest.mark.parametrize("command", [
    "$LOCI contract accept",
    '"$HOME/.local/bin/loci" contract accept',
    "'/usr/local/bin/loci' contract accept",
    "sh -c 'loci contract accept'",
    "bash -lc 'loci contract accept'",
    "python -m loci_cli contract accept",
])
def test_a_binary_the_guard_cannot_see_is_the_price_of_reading_tokens(tmp_path, command):
    """These RAN the verb and the substring match denied them. Now they pass.

    They are the cost of the rule, stated rather than hidden: the guard reads a
    segment's unquoted head, so a binary spelled through a variable, wrapped in
    quotes, or handed to a nested shell as a quoted string is not a `loci` token
    it can see. `python -m loci_cli` is in the list for symmetry only — there is
    no `src/loci_cli/__main__.py`, so it runs nothing today.

    Do NOT close these by unquoting or by expanding. That is precisely what
    ``test_shell_writes_to_the_file_are_deliberately_not_guarded`` records as the
    mistake — shape and text matching "never caught a real mistake" — and
    unquoting the binary means unquoting the verb, which would take
    ``loci "contract" accept`` with it and make this guard a parser. The trade is
    deliberate: it buys back the 109 wrapper spellings (``uv run``, ``timeout``,
    ``sudo``, ``env -i``, a leading redirection) that a first-word rule lost, and
    those are how this repo actually spells the binary.
    """
    assert _run(_bash(command), tmp_path) is None, f"{command!r} must be allowed"


def _oversize(command_tail: str, kb: int = 200) -> str:
    body = "\n".join(f'  emit("line {i}", "x");' for i in range(kb * 1024 // 24))
    return f"cat <<'EOF' > Vault.sol\n{body}\nEOF\n{command_tail}"


def test_a_command_too_large_to_tokenise_falls_back_rather_than_timing_out(tmp_path):
    """`hooks.json` gives this hook 5 s, and past it PreToolUse fails open.

    Tokenising costs more per byte than the substring test it replaced —
    `${var//…}` in bash is not linear at this scale — so above `_R2_MAX_TOKENISE`
    the guard uses the old matcher instead. Measured on this machine at 1.1 MB
    on the jq-less rung: 49.8 s tokenising, against 6.4 s for the version being
    replaced, against a 5 s budget. A guard that gets slower than its own timeout
    is a guard the caller can switch off by choosing a size.

    So the oversize path is coarse ON PURPOSE, and the second assertion pins
    that: a mention inside a 200 KB heredoc is denied, exactly as `bb4a547`
    denied every mention at every size. Read a red on the timing as a finding —
    it means the fallback has stopped being reachable.
    """
    invocation = _oversize("loci contract accept")
    assert len(invocation) > 190_000, len(invocation)
    start = time.monotonic()
    assert _denied(_run(_bash(invocation), tmp_path)), (
        "a large payload walked the verb past the guard")
    elapsed = time.monotonic() - start
    assert elapsed < 5.0, (
        f"the guard took {elapsed:.1f}s on a {len(invocation) // 1024} KB command, "
        f"against the 5 s timeout in hooks.json")
    # The cost of the fallback, stated rather than discovered: over the cap, a
    # mention is a match again.
    assert _denied(_run(_bash(_oversize("echo 'see loci contract accept'")), tmp_path))
    # …and under it, it is not.
    assert _run(_bash("echo 'see loci contract accept'"), tmp_path) is None
    # Runs of whitespace do not walk through it. The first version of the
    # fallback dropped the collapse loop and kept a fixed-space glob, so
    # `loci   contract   accept` plus a pad ran the verb — and the pad is easy to
    # add, which made the SIZE the caller's way of switching the guard off.
    # `bb4a547` denied this in 0.28 s at the cap; the claim that nothing finished
    # in time at these sizes was true only for an indented heredoc at ≥512 KB.
    for spelling in ("loci   contract   accept",
                     "loci\tcontract\taccept",
                     "loci contract\naccept"):
        assert _denied(_run(_bash(_oversize(spelling)), tmp_path)), spelling


def test_a_command_that_is_all_separators_is_decided_inside_the_budget(tmp_path):
    """The byte cap does not bound the WORK, and 64 KB of punctuation proved it.

    Every segment costs a function call and a `set --`. A command of nothing but
    separators is under the byte cap and yet ~32 000 segments: 3.0 s of the 5 s
    budget on an idle machine, against 0.35 s for `bb4a547`, which had no
    per-segment cost at all. Past the budget the hook is killed and fails open —
    so a byte cap alone left a way to switch the guard off by choosing
    punctuation rather than size. `$#` after the split is the exact count, and
    over `_R2_MAX_SEGMENTS` the substring test answers instead.
    """
    noise = ";".join("a" for _ in range(30_000))
    command = f"{noise};loci contract accept"
    assert len(command) < 65_536, len(command)
    start = time.monotonic()
    assert _denied(_run(_bash(command), tmp_path)), "the fallback did not answer"
    elapsed = time.monotonic() - start
    assert elapsed < 5.0, (
        f"the guard took {elapsed:.1f}s on a command of {command.count(';')} "
        f"segments, against the 5 s timeout in hooks.json")


@pytest.mark.parametrize("mb", [1, 3])
def test_a_megabyte_command_is_still_decided_inside_the_hook_budget(tmp_path, mb):
    """Route 1 has had this test since T10. Route 2 never did, and needed it.

    Traced at 1.1 MB, `bb4a547` took 6.3 s on the jq-less rung and 13.0 s with
    jq, both past the 5 s in `hooks.json` — so a Bash command big enough killed
    this hook and PreToolUse failed open, and the size was the caller's to
    choose. Two things were paying: the old matcher's whitespace-collapse loop,
    which rewrites and re-compares the whole string once per iteration, and
    three `jq` invocations that each parsed the entire payload when a payload
    carries either a `file_path` or a `command`, never both.

    Read a red here as a finding, and the same caveat applies as to route 1's
    version of this test: it has gone red under a loaded machine and green idle,
    because the budget is wall clock. That does not make the arithmetic wrong —
    off the bench, a real build loading the machine is exactly when a large
    write arrives.
    """
    body = "\n".join(f'  emit("line {i}", "x");' for i in range(mb * 1024 * 1024 // 24))
    command = f"cat <<'EOF' > Vault.sol\n{body}\nEOF\nloci contract accept"
    start = time.monotonic()
    decision = _run(_bash(command), tmp_path)
    elapsed = time.monotonic() - start
    assert _denied(decision), f"a {mb} MB command walked the verb past the guard"
    assert elapsed < 5.0, (
        f"the guard took {elapsed:.1f}s on a {mb} MB command, against the 5 s "
        f"timeout in hooks.json — past it the hook is killed and fails open")


def test_only_the_key_that_is_there_is_parsed(tmp_path):
    """No `jq` at all, for any field.

    This used to count `jq` runs and assert exactly one, down from three: every
    one of them piped and parsed the ENTIRE payload, and a `Bash` payload has no
    `file_path` while an `Edit` payload has no `command`, so two of the three were
    always parsing a megabyte to return `""`. `lib/loci_json.sh` reads the field
    in parameter expansion, so the count is now zero on both routes and the
    ladder that made the answer depend on which host tool was installed is gone.
    Counted, because the cost is invisible in a verdict.
    """
    bindir = tmp_path / "countbin"
    bindir.mkdir()
    counter = tmp_path / "forks.txt"
    for name in ("jq", "cat", "git", "realpath", "sed"):
        real = shutil.which(name)
        if real is None:
            pytest.skip(f"{name} not available")
        shim = bindir / name
        shim.write_text(
            "#!/bin/sh\n"
            f'printf "%s\\n" {name} >> "{_to_bash_path(counter)}"\n'
            f'exec "{_to_bash_path(Path(real))}" "$@"\n',
            encoding="utf-8", newline="\n")
        shim.chmod(0o755)

    def forks(payload) -> list[str]:
        counter.write_text("", encoding="utf-8")
        env = {"PATH": f"{_to_bash_path(bindir)}:{_base_path()}"}
        _run(payload, tmp_path, env=env)
        return counter.read_text(encoding="utf-8").split()

    # Route 2 is the hot path: one `cat` for the payload, and nothing else.
    assert forks(_bash("loci contract show")).count("jq") == 0
    # Route 1 reads `file_path` the same forkless way. `cwd` is not read from the
    # payload by any binary either — it was the third unconditional parse.
    assert forks(_edit(".loci/contract.yaml")).count("jq") == 0

@pytest.mark.parametrize("command", [
    # R4 — the substitution the scan closes EARLY. Bash finds the `)` that ends
    # `$( … )` by parsing the command inside it; a `case` arm puts one there
    # with no `(` to match, so the scan restores the enclosing `"…"` at the arm
    # and masks the rest away. A `#` comment or a heredoc body line does it too.
    'echo "$(case $x in *) loci contract accept;; esac)"',
    'echo "$(case $x in a|b) loci contract init;; esac)"',
    # R3 — a substitution that PRODUCES the binary. This half used to be
    # filed under R5 because ONE cut severed both, and F11 separated them: the
    # body is a command of its own and `which loci` is not an invocation, so
    # what is left of the outer word is `contract accept` with no binary in it.
    # Closing it means EXPANDING, which takes `loci "contract" accept` with it.
    "$(which loci) contract accept",
    "`which loci` contract accept",
    '"$(command -v loci)" contract accept',
    'eval "$(which loci) contract accept"',
    # R8 — an ESCAPED separator inside a word. This is R5's shape reached
    # through an escape instead of a substitution: `\\;` is an ordinary
    # character to the shell and keeps the word going, the mask emits it, and
    # the split cuts a word bash does not cut. All five RUN, all five are
    # allowed at `43ed7a9` too, and F11 closed only the sixth member — the
    # escaped BACKTICK — because that one could leave `_R2_SEPARATORS`
    # altogether once the mask stopped writing a backtick for any other
    # reason. `(` and `)` still stand for a real subshell and `;`, `&`, `|` are
    # not scan characters, so the other five need the split to move INTO the
    # scan. Filed as F25.
    "loci --root a\\(b contract accept",
    "loci --root a\\)b contract accept",
    "loci --root a\\;b contract accept",
    "loci --root a\\&b contract accept",
    "loci --root a\\|b contract accept",
    # R9 — an expansion the SHELL ERASES, glued to a token the guard has to
    # recognise. The mask writes the `$` and lets the name through as ordinary
    # text, so the word arrives as `${X}loci` and matches no binary pattern,
    # while bash removes the empty expansion and runs `loci`. The first five
    # need no substitution at all and are allowed at `43ed7a9`; the last two are
    # the three spellings F11 adds, where the lift joins the erased `$X` to the
    # binary across the substitution that used to cut between them. They add no
    # shorter route: the family's cheapest member is `$@loci contract accept` at
    # 22 bytes and needs no substitution at all. Filed as F26 — closing it means
    # the mask consuming an expansion instead of writing its text, on the arm
    # every command with a `$` goes through.
    "${X}loci contract accept",
    "${X:-}loci contract accept",
    "${X#a}loci contract accept",
    "$@loci contract accept",
    "$1loci contract accept",
    "$X$(true)loci contract accept",
    "${BIN}`true`loci contract accept",
    # R10 — a REDIRECTION OPERATOR glued to the binary. Both matcher loops split
    # a segment on SPACES, and bash also breaks a word at `<` and `>`, so
    # `loci>x` arrives as one word and matches no binary pattern while the shell
    # runs `loci` and creates `x`. `<`/`>` are deliberately not separators —
    # they separate words, not commands — so nothing re-splits. **The cheapest
    # residual in the file**: 22 bytes, no quoting, no `$`, no substitution.
    # Inherited; `43ed7a9` allows all four. Filed as F27.
    "loci>x contract accept",
    "loci>>x contract accept",
    "loci<&0 contract accept",
    "loci<$(pwd) contract accept",
    # R11 — a TRAILING BACKSLASH. At end of input the `\\` arm emits a literal
    # backslash, so the verb arrives as `accept\\`; the shell that runs the
    # command supplies the newline the scan never saw, which makes it a line
    # continuation, and the verb runs. Inherited, filed as F27 with R10 because
    # both are the same sentence: the matcher's idea of a word is not bash's.
    "loci contract accept\\",
])
def test_a_substitution_can_hide_an_invocation(tmp_path, command):
    """This test asserts a decision, not an oversight. Do not "fix" it.

    Every command here RUNS a contract-writing verb and every one is allowed.
    Two review rounds found them, and the second round found the first round's
    attempt to close R4 — "the parens must balance or do not trust the mask" —
    defeated by putting one extra `(` anywhere in the command, including in a
    comment, inside `'…'`, or in a neighbouring payload field. Counting cannot
    find the end of a `$( … )`; only parsing can.

    That attempt also cost more than the hole. Over the balance check the
    verdict fell to ``_R2_OVERSIZE_RE``, which matches the PHRASE with no binary
    token, so one odd paren beside one substitution denied 22 ordinary commands
    that ran nothing — led by
    ``echo "$(date +%F): 1) run loci contract accept" >> HANDOFF.md``, which is
    the note F07 exists to permit.

    **R5 is not here any more.** A substitution between the binary and the verb
    used to be allowed by the same cut, and F11 closed it — the four spellings
    moved to ``test_contract_writing_verbs_are_denied``. What stays is R3, the
    hidden binary, which the cut merely reached by a second route: closing it
    means expanding, and the file has refused that since F07 because expanding
    the binary means expanding the verb.

    **R8 is not a substitution at all**, and it is here rather than in a test of
    its own because it is the same DEFECT: a cut the shell does not make. An
    escaped `(`, `)`, `;`, `&` or `|` is an ordinary character that keeps a word
    going, the mask emits it, and the split reads it as a command boundary. F11
    closed the sixth member, the escaped backtick, and could only close that one
    — with the markers gone the backtick could leave ``_R2_SEPARATORS``
    altogether, while `(` and `)` still stand for a real subshell and `;`, `&`
    and `|` are never seen by the scan. Closing the rest means the split moving
    into the scan. Filed as F25; do not close it here.
    """
    assert _run(_bash(command), tmp_path) is None, f"{command!r} must be allowed"


def test_an_odd_quote_in_a_heredoc_body_hides_what_follows(tmp_path):
    """This test asserts a decision, not an oversight. Do not "fix" it.

    Residual R6. A heredoc body is not command text, but the guard is handed one
    flat string and cannot know where the body ends without matching `<<WORD`
    against a later line equal to WORD — a heredoc parser, which this file has
    refused since F07 for reasons its own history keeps confirming. So a stray
    apostrophe in a body opens a region the scan never closes, and every command
    after the heredoc stops being visible.

    The `#` COMMENT half of the same defect IS closed, because a comment ends at
    a newline and needs nothing parsed; the deny corpus carries five spellings
    of it. That asymmetry is the whole reason this is written down: the two look
    identical from outside and only one of them can be fixed cheaply.

    It also makes the heredoc residual in the route 2 header CONDITIONAL — an
    unquoted body line mentioning the verb is denied only when the apostrophes
    before it happen to be even. The second row is that, and it is allowed.
    """
    head = "cat <<EOF > n.md\n"
    assert _run(_bash(f"{head}don't\nEOF\nloci contract accept"), tmp_path) is None
    assert _run(_bash(f"{head}don't run loci contract accept\nEOF"), tmp_path) is None
    # …and with the apostrophes even, the same note lands on the documented side.
    assert _denied(_run(_bash(f"{head}don't don't run loci contract accept\nEOF"),
                        tmp_path))


def _locale_that_folds_non_ascii(tmp_path) -> str | None:
    """A locale this host has where bash's `nocasematch` folds beyond ASCII.

    Without one there is nothing for the test below to assert: `LC_ALL=C` cannot
    have NARROWED what the shell was not doing anyway. Git Bash here has
    `en_US.UTF-8` and not `C.UTF-8`; WSL has the reverse. Probed rather than
    guessed, because `locale -a` and what `setlocale` accepts disagree.
    """
    script = tmp_path / "fold.sh"
    script.write_text(
        'shopt -s nocasematch\n'
        '[[ $(printf "\\xd0\\x9f") == $(printf "\\xd0\\xbf") ]] && echo folds\n',
        encoding="utf-8", newline="\n")
    for name in ("en_US.UTF-8", "C.UTF-8", "C.utf8", "en_US.utf8"):
        out = subprocess.run([_find_bash(), _to_bash_path(script)],
                             capture_output=True, text=True, timeout=30,
                             env={"PATH": _base_path(), "LC_ALL": name})
        if out.stdout.strip() == "folds":
            return name
    return None


def test_route_1_still_folds_case_the_way_the_filesystem_does(tmp_path):
    """`LC_ALL=C` is set for route 2's byte accounting and must not reach here.

    Route 1 compares PATHS with `nocasematch`, and under C that folding is
    ASCII-only. With the locale forced globally, a project at `…/Проект` stopped
    matching an edit spelled `…/ПРОЕКТ/…`, so the guarded file became writable
    on exactly the case-insensitive filesystems route 1's own preamble exists
    for. `José` → `JOSÉ` does it too, and a Windows home directory is a normal
    place to find one. Both `bb4a547` and `addcd22` denied these.

    The four spellings are the ones that miss `guard_path`'s ASCII suffix arm
    and rest entirely on the `realpath` comparison — which is what that
    comparison was there for.
    """
    locale = _locale_that_folds_non_ascii(tmp_path)
    if locale is None:
        pytest.skip("no locale on this host folds case beyond ASCII")
    for name, upper in (("Проект", "ПРОЕКТ"), ("José", "JOSÉ")):
        root = tmp_path / name
        (root / ".loci").mkdir(parents=True)
        for spelling in (".loci//contract.yaml", ".loci/./contract.yaml",
                         ".loci/x/../contract.yaml", ".loci//build.yaml"):
            target = f"{_to_bash_path(tmp_path)}/{upper}/{spelling}"
            decision = _run(_edit(target), root, ascii_json=False,
                            env={"LC_ALL": locale,
                                 "CLAUDE_PROJECT_DIR": _to_bash_path(root)})
            assert _denied(decision), (
                f"{target!r} was allowed under {locale} — the guard has "
                f"narrowed case folding to ASCII")


def test_a_non_ascii_path_escaped_by_json_dumps_is_denied(tmp_path):
    r"""`\uXXXX` reaches route 1 decoded. This was F12's hole.

    `lib/loci_json.sh` reads the field in bash string operations, and for one
    release it left every `\\uXXXX` as the six characters that were written,
    where the jq it replaced decoded them. A producer that escapes non-ASCII —
    Python's `json.dumps` does so by DEFAULT — therefore handed route 1 a path
    matching nothing, and the guarded file was writable. No locale is
    involved: the spelling below is the project's own, so the `nocasematch`
    fold above is not what was failing.

    Both spellings of the same path, and both must be denied. Claude Code
    sends raw UTF-8, so `ascii_json=False` is the live one; `ascii_json=True`
    is every test, replay harness and third-party producer, and it is the one
    that used to walk through.

    ⚠ NAMED FOR WHAT IT TESTS, after a review round caught the first name
    ("…_is_denied_the_same_as_a_raw_one") claiming coverage that does not
    exist. It is NOT true in general that a JSON-escaped path is denied: the
    prefilter at the top of this hook decides on the raw PAYLOAD TEXT before
    any field is read, so a `file_path` that also escapes the ASCII letters of
    `contract` matches no prefilter arm and is allowed without route 1 ever
    running. `3a10684` allows it too, so it is not this change's regression,
    and no serializer Claude Code uses escapes ASCII letters — `json.dumps`
    leaves `contract.yaml` literal, which is why THIS test denies. That gap
    was F15's door 1 and it is closed for the VALUE:
    `test_the_prefilter_sees_what_the_field_read_sees` below admits and denies
    every escaped spelling of a guarded path.

    ⚠ IT IS STILL NOT TRUE IN GENERAL, and the first draft of this correction
    said it was. An escaped KEY — `"\u0066ile_path"` — is read by `json.loads`
    and `JSON.parse` as `file_path` and, until F17, by `lib/loci_json.sh` as
    absent: route 1 never ran and the guarded file was writable. The prefilter
    arm admitted that payload; the field read one layer down could not see it.
    Closed by F17 and pinned in `test_an_escaped_key_is_read_as_the_name_it_spells`
    below. What is STILL not general is a DUPLICATE name — two keys spelling
    `file_path`, of which both parsers take the last and this library takes the
    first — pinned as a known ALLOW in
    `test_a_duplicate_name_answers_with_the_first_and_the_harness_takes_the_last`.
    """
    root = tmp_path / "Проект"
    (root / ".loci").mkdir(parents=True)
    target = f"{_to_bash_path(tmp_path)}/Проект/.loci//contract.yaml"
    for ascii_json in (True, False):
        assert _denied(_run(_edit(target), root, ascii_json=ascii_json)), (
            f"the {'escaped' if ascii_json else 'raw UTF-8'} spelling of a "
            f"guarded path walked past route 1")


#: Every spelling of a guarded file that the four literal prefilter arms miss.
#: The first two escape LETTERS, which is what F15 was filed for; the rest
#: escape the DOT or a NUL, and they are why the arm cannot be narrowed to the
#: `\u004X`-`\u007X` band an escaped letter always sits in. Every row was
#: ALLOWED at `dccf9d4`.
#:
#: The first seven are resolved to a guarded path by `json.loads`, by
#: `JSON.parse` and by the filesystem. THE NUL ROW IS NOT, and the first
#: version of this comment said it was: `json.loads` gives
#: `.loci/build<NUL>.yaml`, Python and Node both refuse to open a path with a
#: NUL in it, and POSIX truncates at one — so what that row pins is the
#: GUARD'S OWN decode, which drops `\u0000` rather than truncating, and which
#: therefore denies. It is a deliberate over-deny (the reasoning is at
#: `_loci_json_unicode`), not a spelling anything else reads as the recipe.
#: The "narrower does not hold" argument rests on the two dot rows, which are
#: real.
ESCAPED_GUARDED_SPELLINGS = [
    ("{root}/.loci/" + _esc("contract") + ".yaml", "all eight letters escaped"),
    ("{root}/.loci/c" + _esc("o") + "ntract.yaml", "one letter escaped"),
    ("{root}/.loci/" + _esc("build") + ".yaml", "build.yaml, letters escaped"),
    ("{root}/.loci/build/" + _esc("flags") + ".json", "flags.json, letters escaped"),
    ("{root}/.loci/CONTRACT" + _esc(".") + "YAML", "uppercase name, dot escaped"),
    ("{root}/.loci/build" + _esc(".") + "yaml", "build.yaml, only the dot escaped"),
    ("{root}/.loci/build/flags" + _esc(".") + "json", "flags.json, only the dot"),
    ("{root}/.loci/build" + B + "u0000.yaml", "a NUL the decode drops"),
]


@pytest.mark.parametrize("spelling,what", ESCAPED_GUARDED_SPELLINGS)
def test_the_prefilter_sees_what_the_field_read_sees(tmp_path, spelling, what):
    r"""The prefilter decided on raw payload TEXT, above the field read.

    It exits ALLOW unless the payload carries a guarded token, and all four of
    its literal arms read the text as written — so a `file_path` spelling any
    part of a guarded name in `\uXXXX` matched none of them and the decode that
    would resolve it back never ran. Every row above is a write to one of the
    three guarded files, and every row was allowed at `dccf9d4`. This was F15's
    door 1, and the file states the rule it broke twenty lines below it:
    **decide on the FIELD, never on the payload text.**

    THE ARM IS `\u00`, and both halves of that are measured rather than chosen.
    Narrower does not work: an escaped letter is always `\u004X`-`\u007X`, but
    the last four rows escape the DOT or a NUL instead, and they beat all four
    literal arms too — arms 2-4 each demand a LITERAL `.` between the name and
    the extension, and arm 1 is lowercase-sensitive, so `CONTRACT\u002eYAML`
    and `build\u002eyaml` need no escaped letter at all. Wider does not work
    either, and that is the cost side: `\u` alone matches every Windows path in
    JSON, because `C:\\users` carries those two characters, and it would put
    the full field read on every tool call in every Windows repo.

    What the arm does NOT cover, deliberately: an escape naming a NON-ASCII
    character (`\u0100` and up). It cannot spell a guarded name, because all
    three are ASCII throughout, and covering it costs the `\u` arm above. Nor
    `\U0063` — JSON has no uppercase-U escape, so `json.loads` rejects that
    document outright and it names no file at all.
    """
    (tmp_path / ".loci" / "build").mkdir(parents=True)
    target = spelling.format(root=_to_bash_path(tmp_path))
    assert _denied(_run({}, tmp_path, raw_json=_raw_edit(target))), (
        f"a write to a guarded file with {what} was allowed — the prefilter "
        f"is deciding on payload text again (F15 door 1)")


def test_the_escaped_spelling_is_denied_by_the_arm_not_by_a_decoy(tmp_path):
    r"""The isolation that says WHICH layer is working, kept for the next break.

    The same escaped path, with the literal word `contract` added somewhere the
    payload's own fields do not reach. That token satisfies the prefilter by
    itself, so the second assertion exercises the DECODE alone and the first
    exercises the decode plus the new arm. A future regression is then legible
    rather than ambiguous: both red means the decoder in `lib/loci_json.sh`
    broke, and only the first red means the `\u00` arm was lost.
    """
    (tmp_path / ".loci").mkdir(parents=True)
    target = f"{_to_bash_path(tmp_path)}/.loci/{_esc('contract')}.yaml"
    assert _denied(_run({}, tmp_path, raw_json=_raw_edit(target))), (
        "the arm is gone — F15 door 1 is back")
    decoy = '{"tool_name": "Edit", "decoy": "contract", "tool_input": ' \
            '{"file_path": "' + target + '", "new_string": "x"}}'
    assert _denied(_run({}, tmp_path, raw_json=decoy)), (
        "the decode itself regressed: this payload reaches route 1 on the "
        "literal token alone, so the arm is not what failed")


#: Every way of spelling the NAME `file_path` that the literal key search
#: cannot see. Every row was ALLOWED at `dea825d`, for both guarded files.
#:
#: ⚠ AN `upper=True` ROW IS ONLY A ROW WHERE THE HEX PAIR HOLDS A LETTER. `f`
#: is 0x66, so an uppercase spelling of it is the same six characters and the
#: row tests what the one above it tests — which is what the first draft of
#: this list did, twice. `l` (0x6c) and `_` (0x5f) are the only two characters
#: of `file_path` that have a second spelling at all, and
#: `test_an_uppercase_row_is_a_different_payload` is what says so.
ESCAPED_KEY_SPELLINGS = [
    ((0,), False, "the first character"),
    ((2,), False, "a character whose hex pair holds a letter"),
    ((2,), True, "the same character, uppercase hex"),
    ((4,), False, "the underscore"),
    ((4,), True, "the underscore, uppercase hex"),
    ((8,), False, "the last character"),
    (tuple(range(9)), False, "every character"),
]


def _spell(name: str, idx, *, upper: bool = False) -> str:
    """`name` with the characters at `idx` written as an escape."""
    fmt = "u%04X" if upper else "u%04x"
    return "".join(B + fmt % ord(c) if i in idx else c
                   for i, c in enumerate(name))


@pytest.mark.parametrize("rel", ["contract.yaml", "build.yaml"])
@pytest.mark.parametrize("idx,upper,what", ESCAPED_KEY_SPELLINGS)
def test_an_escaped_key_is_read_as_the_name_it_spells(tmp_path, rel, idx,
                                                      upper, what):
    r"""F15 closed the prefilter and the decode; this is the layer below them.

    The decode resolves a `\uXXXX` in a VALUE, and `_loci_json_seek` looked for
    the literal text `"file_path"` — so an escaped KEY was invisible to it.
    `json.loads` and `JSON.parse` both read `"\u0066ile_path"` as `file_path`;
    the field read answered absent, `fp` was empty, and route 1 never ran at
    all, so the guarded file was written. Every row here was ALLOWED at
    `dea825d`. Filed as F17 off F15's review round, and closed in
    `lib/loci_json.sh` with a second search pass.

    The prefilter is NOT what failed — the payload carries `\u00` and is
    admitted — which is why this sits beside the arm rather than inside it: the
    arm did its job and the layer below it did not. The library-level cases,
    the cost of the second pass and the duplicate-name decision are in
    `test_loci_json.py`; what this file adds is the HARM, which is a write to a
    guarded file that the harness really performs.
    """
    (tmp_path / ".loci").mkdir(parents=True)
    root = _to_bash_path(tmp_path)
    target = f"{root}/.loci/{rel}"
    doc = ('{"tool_name": "Edit", "tool_input": {"'
           + _spell("file_path", idx, upper=upper) + '": "' + target
           + '", "new_string": "x"}}')
    assert json.loads(doc)["tool_input"]["file_path"] == target, (
        "the row is not a write to the guarded file")
    assert _denied(_run({}, tmp_path, raw_json=doc)), (
        f"a write to {rel} with {what} of the KEY escaped was allowed — the "
        f"field read is looking for the literal name again (F17)")


#: The same, for `command` — its own list, because the labels name WHICH
#: character is escaped and the two names do not line up. Three of its
#: characters have a hex pair holding a letter: `o` (0x6f), `m` (0x6d) and `n`
#: (0x6e); `c` (0x63) does not, which is why the uppercase row below is not the
#: first character.
ESCAPED_COMMAND_SPELLINGS = [
    ((0,), False, "the first character"),
    ((2,), False, "a character whose hex pair holds a letter"),
    ((2,), True, "the same character, uppercase hex"),
    ((5,), True, "a second such character, uppercase hex"),
    ((6,), False, "the last character"),
    (tuple(range(7)), False, "every character"),
]


@pytest.mark.parametrize("name,rows", [
    ("file_path", ESCAPED_KEY_SPELLINGS),
    ("command", ESCAPED_COMMAND_SPELLINGS),
])
def test_an_uppercase_row_is_a_different_payload(name, rows):
    """The guard on the two lists above, because the failure is SILENT.

    `_spell(name, idx, upper=True)` differs from the lowercase spelling only
    when one of the escaped characters has a hex digit that is a letter. Ask a
    row to escape `f` (0x66) with `upper=True` and it produces the same six
    characters as the row above it: a duplicate that reads as coverage of the
    uppercase arm and is not. Both lists shipped that way in this change's
    first draft.
    """
    for idx, upper, what in rows:
        if not upper:
            continue
        assert _spell(name, idx, upper=True) != _spell(name, idx), (
            f"the {what!r} row of {name} escapes no character whose hex pair "
            f"holds a letter, so it is the lowercase row again")


@pytest.mark.parametrize("idx,upper,what", ESCAPED_COMMAND_SPELLINGS)
def test_an_escaped_command_key_is_denied_on_route_2(tmp_path, idx, upper, what):
    r"""The same defect by the other route, and it was never written down.

    F17 was filed against `file_path`, but `command` is read through the same
    function: an invocation whose KEY is escaped reached route 2 as an empty
    command, which matches no invocation, which is ALLOW — while the harness
    reads the key as `command` and RUNS the verb. `loci contract accept` was
    allowed this way at `dea825d`, measured.

    The prefilter admits it on the literal `contract` in the value, so this is
    the field read alone, with nothing else to attribute a pass to.
    """
    command = "loci contract accept"
    doc = ('{"tool_name": "Bash", "tool_input": {"'
           + _spell("command", idx, upper=upper) + '": "' + command + '"}}')
    assert json.loads(doc)["tool_input"]["command"] == command
    assert _denied(_run({}, tmp_path, raw_json=doc)), (
        f"an invocation with {what} of the `command` key escaped ran the verb "
        f"and was allowed")


def test_an_escaped_key_does_not_turn_a_mention_into_a_deny(tmp_path):
    """The over-deny direction, which is the one F07 was filed for.

    Reading an escaped key is only safe if what is read through it is still
    judged the same way, so the case that matters is a quoted MENTION under an
    escaped `command` key. It decodes to a mention and stays ALLOWED: the
    second pass changes which text route 2 sees, never what counts as an
    invocation.
    """
    doc = ('{"tool_name": "Bash", "tool_input": {"'
           + _spell("command", (0,)) + "\": \"echo 'loci contract accept'\"}}")
    assert _run({}, tmp_path, raw_json=doc) is None, (
        "an escaped-key quoted mention was denied — the second pass widened "
        "route 2's matcher")


#: The three guarded files, repo-relative. A duplicate name is tried against
#: all three: the guard decides WHICH file a `file_path` is before it decides
#: anything else, and each reaches that decision by its own arm.
_GUARDED_RELATIVE = [".loci/contract.yaml", ".loci/build.yaml",
                     ".loci/build/flags.json"]


@pytest.mark.parametrize("rel", _GUARDED_RELATIVE)
@pytest.mark.parametrize("guarded_last", [True, False])
def test_a_duplicate_name_is_refused_rather_than_decided(
        tmp_path, rel, guarded_last):
    r"""F19: the guard read the FIRST `file_path`, Claude Code performs the LAST.

    RFC 8259 leaves a duplicate name to the parser, so neither reading is
    wrong — but two parsers reading one document have to agree, and only one of
    them writes the file. At `4fbbc2a` this guard read `<root>/ok.c`, matched no
    guarded file and ALLOWED, while `json.loads` and `JSON.parse` both resolve
    the same payload to the guarded file; measured on all three. The other
    order was the mirror image, a DENY of a write to `ok.c`.

    **Both orders are refused now, and neither is decided.** Taking the last
    key would look like the fix and is not — `lib/loci_json.sh` reads a name at
    any DEPTH, so "last" agrees with a parser for two keys in one object and
    contradicts it for a nested one, and it breaks the envelope invariant six
    other hooks rest on (`test_the_envelope_nesting_is_why_the_first_key_wins`
    in `test_loci_json.py`). So the guard answers the question it can answer:
    this payload is ambiguous, and a verdict about the wrong write is not a
    verdict.

    Nothing to do with escapes: both names here are plain literal text.
    Reaching it needs a producer that emits a duplicate name — `JSON.stringify`
    serialises an object whose keys are unique by construction — which is why
    this writes its own JSON, and why it is a gap of the same class as F12, F15
    and F17 rather than something the product sends today. It is the cheapest
    of the four to reach: the others need an escaped ASCII letter, this needs
    only the same name twice.
    """
    (tmp_path / ".loci" / "build").mkdir(parents=True)
    root = _to_bash_path(tmp_path)
    guarded = f"{root}/{rel}"
    harmless = f"{root}/ok.c"
    order = [harmless, guarded] if guarded_last else [guarded, harmless]
    doc = ('{"tool_name": "Edit", "tool_input": {'
           + ", ".join('"file_path": "%s"' % p for p in order)
           + ', "new_string": "x"}}')
    assert json.loads(doc)["tool_input"]["file_path"] == order[-1], (
        "the row is not what it claims")

    decision = _run({}, tmp_path, raw_json=doc)
    assert _denied(decision), (
        f"a payload naming `file_path` twice ({rel} "
        f"{'last' if guarded_last else 'first'}) was not refused — the guard "
        f"is deciding on a name it cannot decide (F19)")
    assert "twice" in decision.get("permissionDecisionReason", ""), (
        "the deny reason does not say WHY, so the model cannot fix it")


@pytest.mark.parametrize("invocation_last", [True, False])
def test_a_duplicate_command_is_refused_on_route_2(tmp_path, invocation_last):
    """Route 2 had it too, and it was not measured until F19 was worked.

    `command` is read through the same function as `file_path`, so a Bash
    payload naming it twice with the invocation SECOND ran `loci contract
    accept` and was ALLOWED at `4fbbc2a` — the guard decided about `echo hi`.
    The mirror order denied a payload whose command is `echo hi`, which is
    F07's over-deny reached by a different route. One refusal covers both,
    which is the second reason the fix is here and not in a `file_path`-shaped
    detector.
    """
    verb = "loci contract accept"
    order = ["echo hi", verb] if invocation_last else [verb, "echo hi"]
    doc = ('{"tool_name": "Bash", "tool_input": {'
           + ", ".join('"command": "%s"' % c for c in order) + '}}')
    assert json.loads(doc)["tool_input"]["command"] == order[-1]
    assert _denied(_run({}, tmp_path, raw_json=doc)), (
        "a payload naming `command` twice was not refused (F19)")


@pytest.mark.parametrize("idx,upper,what", ESCAPED_KEY_SPELLINGS)
def test_a_duplicate_whose_second_spelling_is_escaped_is_refused_too(
        tmp_path, idx, upper, what):
    r"""F17 and F19 composed, and the reason the detector is not the tally.

    `loci_json_count file_path` reads the LITERAL name only — deliberately, and
    its own comment says so — so a duplicate whose second key is spelled
    `\uXXXX` would be no duplicate to it, and the guard would go on reading the
    first key while the harness performs the second. Measured ALLOW at
    `4fbbc2a`. `loci_json_dup` runs the tally over the copy the escaped search
    reads, so both spellings are one name.
    """
    (tmp_path / ".loci").mkdir(parents=True)
    root = _to_bash_path(tmp_path)
    guarded = f"{root}/.loci/contract.yaml"
    doc = ('{"tool_name": "Edit", "tool_input": {"file_path": "' + root
           + '/ok.c", "' + _spell("file_path", idx, upper=upper) + '": "'
           + guarded + '", "new_string": "x"}}')
    assert json.loads(doc)["tool_input"]["file_path"] == guarded
    assert _denied(_run({}, tmp_path, raw_json=doc)), (
        f"a duplicate whose second key has {what} escaped was allowed, and the "
        f"harness writes the Contract Envelope")


@pytest.mark.parametrize("tool_input,what", [
    ({"command": "forge build", "cwd": "contracts"}, "an MCP shell call"),
    ({"file_path": "contracts/Token.sol", "cwd": ".", "patch": "x"},
     "an MCP editor call"),
])
def test_a_tool_input_may_carry_its_own_cwd(tmp_path, tool_input, what):
    """`cwd` is NOT screened, and this is the measurement that decided it.

    The harness writes `cwd` at the TOP LEVEL of every payload, so a tool whose
    own input names `cwd` — ordinary for an MCP shell or editor server — makes
    a nested duplicate of it out of nothing the model did. Screening `cwd`
    refused both rows below in any repo with the word `contract` in it, which
    the prefilter's own comment says is `ContractService.ts`, `CONTRACT_ADDRESS`
    and a `// Contract:` code comment as well as a `contracts/` directory.

    The other side of the trade is empty: every duplicate-`cwd` shape built for
    this task is DENIED at `4fbbc2a` anyway, because route 1's suffix arms
    decide a path carrying a guarded basename without consulting the project
    root at all. A screen with a measured over-deny in front of it and no
    measured defect behind it is the wrong trade for a hook that runs ahead of
    every tool call.

    `file_path` and `command` do not have this shape: the harness writes
    neither outside `tool_input`.
    """
    (tmp_path / ".loci").mkdir(parents=True)
    payload = {"cwd": _to_bash_path(tmp_path),
               "tool_name": "mcp__x__y", "tool_input": tool_input}
    assert _run(payload, tmp_path) is None, (
        f"{what} carrying its own `cwd` beside the harness's was refused — "
        f"`cwd` is back in the duplicate screen (F19)")


def test_a_nested_name_is_refused_too_and_that_is_deliberate(tmp_path):
    r"""The over-deny this refusal buys, pinned rather than discovered later.

    `{"file_path":"ok.c","meta":{"file_path":"<guarded>"}}` resolves to `ok.c`
    for `json.loads` and for Claude Code, and is refused here. That is the
    direction this guard errs in everywhere else, and the payload is one no
    product sends — `JSON.stringify` serialises an object whose keys are
    unique, and the harness puts `file_path` only under `tool_input`. It is
    also not avoidable: a depth-blind reader cannot tell a nested key from a
    sibling one, which is the same fact that stops the library taking the last
    key. The reason names the field, so the model can fix it in one edit.
    """
    (tmp_path / ".loci").mkdir(parents=True)
    root = _to_bash_path(tmp_path)
    doc = ('{"tool_name": "Edit", "tool_input": {"file_path": "' + root
           + '/ok.c", "meta": {"file_path": "' + root
           + '/.loci/contract.yaml"}, "new_string": "x"}}')
    assert json.loads(doc)["tool_input"]["file_path"] == f"{root}/ok.c"
    decision = _run({}, tmp_path, raw_json=doc)
    assert _denied(decision), (
        "a nested second `file_path` is not refused — the screen counts keys "
        "at one depth only, and the library does not")
    assert "file_path" in decision.get("permissionDecisionReason", "")


@pytest.mark.parametrize("tool_input,what", [
    ({"file_path": "src/main.c", "new_string": "x"}, "one file_path"),
    ({"command": "echo contract"}, "one command"),
    ({"file_path": "src/a.c", "new_string": '{"file_path": "contract.yaml"}'},
     "a second `file_path` inside the edit CONTENT"),
    ({"file_path": "src/a.c",
      "new_string": '["file_path", "file_path", "contract"]'},
     "the name as array elements in the content"),
    ({"file_path": "src/a.c",
      "new_string": '{\n  "command": "loci contract accept",\n'
                    '  "command": "x"\n}'},
     "a genuinely duplicated name inside the CONTENT"),
])
def test_one_name_is_not_a_duplicate(tmp_path, tool_input, what):
    """The over-deny line, and the last three rows are the ones that bite.

    Every `"` inside a JSON string arrives ESCAPED, so a `"file_path":` written
    in an edit's content is `\"file_path\":` on the wire and is not a second
    key — the same property that makes the whole field read safe. A screen that
    matched payload TEXT rather than keys would refuse an ordinary edit to any
    file that mentions the name, which in this repo is most of them.

    ⚠ EVERY ROW CARRIES THE HARNESS'S OWN TOP-LEVEL `cwd`, because that is what
    a real payload has and leaving it out is how the `cwd` over-deny went
    unseen for a round — see `test_a_tool_input_may_carry_its_own_cwd`.
    """
    payload = {"cwd": _to_bash_path(tmp_path), "tool_name": "Edit",
               "tool_input": tool_input}
    assert _run(payload, tmp_path) is None, (
        f"an ordinary payload with {what} was refused")


def test_a_duplicate_screen_decides_inside_the_hook_budget(tmp_path):
    r"""THE COST TEST for the screen, which runs on every payload that gets past
    the prefilter.

    Two `loci_json_dup` calls, and each can pay a rewrite when the document
    carries a `\u00` — so this is F17's worst payload: 64 KB of escaped
    backslashes with one `\u00` behind a pair, which makes the claim run.
    Measured **1.24 s** against 0.79 s at `4fbbc2a`, with 5 s the point at
    which `hooks.json` kills the hook and PreToolUse fails OPEN.
    """
    B2 = B + B
    command = B2 + B + "u0066" + B2 * 30_000
    start = time.monotonic()
    decision = _run({}, tmp_path, raw_json=_raw_bash(command))
    elapsed = time.monotonic() - start
    assert decision is None, "the row is a mention, not an invocation"
    assert elapsed < 5.0, (
        f"the duplicate screen over a 64 KB payload that trips the claim took "
        f"{elapsed:.1f}s; past 5 s the hook is killed and PreToolUse fails open")


@pytest.mark.parametrize("name", ["file_path", "command"])
def test_one_whitespace_byte_does_not_put_the_screen_over_the_budget(
        tmp_path, name):
    r"""The shape that decided how `loci_json_dup` is built, kept as its detector.

    The screen's first implementation asked `loci_json_count`, whose arithmetic
    fast path is abandoned for a QUADRATIC WALK the moment a quoted name is
    followed by whitespace anywhere in the document — its own
    `case "$doc" in *"$key"[ws]*`. Its header says that walk is affordable only
    because "reaching it takes a producer none of these hooks read from"; a
    guard payload is exactly such a producer, and `json.dumps(indent=2)` puts a
    newline after the last element of an array.

    Measured on a 55 KB payload naming `cwd` 9 200 times as array elements:
    **0.109 s with no whitespace after any of them, 3.846 s with one newline**,
    against 0.082 s at `4fbbc2a` — which DENIES the same payload. `hooks.json`
    kills this hook at 5 s and PreToolUse then fails OPEN, so that was a worse
    hole than the one the screen closes, reachable by a pretty-printer. It asks
    `_loci_json_seek_win` twice instead, which is the LINEAR search F14 bought:
    0.18 s and 0.29 s on the rows below.

    The row is still DENIED — on its guarded `file_path`, which is the point:
    the cost must not change what the verdict is, only how long it takes.
    """
    (tmp_path / ".loci").mkdir(parents=True)
    root = _to_bash_path(tmp_path)
    guarded = f"{root}/.loci/contract.yaml"
    filler = '"%s",' % name * 4_000
    doc = ('{"cwd":"%s","tool_name":"Edit","tool_input":{"file_path":"%s",'
           '"new_string":"x"},"z":[%s"%s"\n]}' % (root, guarded, filler, name))
    assert json.loads(doc)["tool_input"]["file_path"] == guarded

    start = time.monotonic()
    decision = _run({}, tmp_path, raw_json=doc)
    elapsed = time.monotonic() - start
    assert _denied(decision), "the guarded file_path must still be refused"
    assert elapsed < 2.5, (
        f"a payload repeating `{name}` as an array element with ONE newline "
        f"after one of them took {elapsed:.1f}s; the screen is back on "
        f"`loci_json_count`'s quadratic walk and the hook is killed at 5 s")


@pytest.mark.parametrize("command,what", [
    ("loci " + _esc("contract") + " accept", "the verb, escaped"),
    ("loci " + B + "u0063ontract accept", "the verb's first letter"),
    ("loci " + _esc("contract") + " " + _esc("accept"), "verb and subverb"),
    (_esc("loci") + " " + _esc("contract") + " " + _esc("accept"), "all three words"),
    ("loci" + _esc(" ") + _esc("contract") + _esc(" ") + _esc("accept"),
     "the separating spaces too"),
])
def test_an_escaped_verb_is_denied_on_route_2(tmp_path, command, what):
    r"""Route 2 had door 1 as well, and it was not measured until F15.

    The prefilter's plain `*contract*` arm is the only one a Bash payload can
    match — the other three require a `file_path` — so a command spelling the
    verb in escapes exited ALLOW above the field read, exactly as route 1 did.
    Route 2's own scan was never the problem: with the prefilter satisfied by a
    token it cannot see, all five spellings here were already DENIED at
    `dccf9d4`, because `loci_json_get command` hands the scan the decoded text.
    So one arm closes both routes, and this test is what says so.
    """
    assert _denied(_run({}, tmp_path, raw_json=_raw_bash(command))), (
        f"an invocation with {what} ran the verb and was allowed")


def test_an_escaped_mention_is_still_not_an_invocation(tmp_path):
    r"""The arm must not turn a MENTION into a deny.

    Admitting more payloads to route 2 is only safe if route 2 keeps reading
    tokens rather than text, so the case that matters is the one F07 exists
    for: a quoted mention of the verb, spelled in escapes. It decodes to a
    quoted mention and stays ALLOWED — the arm changes which payloads are
    looked at, never what counts as an invocation.
    """
    quoted = "echo '" + _esc("loci contract accept") + "'"
    assert _run({}, tmp_path, raw_json=_raw_bash(quoted)) is None, (
        "an escaped quoted mention was denied — the arm widened route 2's "
        "matcher, which is the over-deny direction F07 was filed for")


def test_an_ordinary_payload_still_exits_at_the_prefilter(tmp_path):
    r"""THE COST TEST for the arm, and the reason it is `\u00` and not `\u`.

    The prefilter's job is keeping an ordinary tool call at zero work, so the
    arm is only affordable if the payloads it newly admits are ones nobody
    sends. A Windows path in JSON carries `\\` before every component, i.e.
    the two characters `\u` wherever a component starts with `u` — so a `\u`
    arm would send every Windows repo's every tool call through the full field
    read. `\u00` needs a component spelled `u00…`, which is not a path anyone
    has.

    Counted in FORKS rather than seconds, the way the fork test above is: route
    1 forks `realpath` and `git`, so one fork (the payload read) means the
    prefilter exited and nothing was parsed. Measured before the arm landed,
    64 KB of Windows paths cost 0.048 s end to end, and 1 MB cost 0.097 s; the
    same payloads after it are unchanged, because they match no arm.
    """
    bindir = tmp_path / "countbin"
    bindir.mkdir()
    counter = tmp_path / "forks.txt"
    for name in ("jq", "realpath", "git", "tr", "sed", "cat"):
        real = shutil.which(name)
        if real is None:
            pytest.skip(f"{name} not available")
        shim = bindir / name
        shim.write_text(
            "#!/bin/sh\n"
            f'printf "%s\\n" {name} >> "{_to_bash_path(counter)}"\n'
            f'exec "{_to_bash_path(Path(real))}" "$@"\n',
            encoding="utf-8", newline="\n")
        shim.chmod(0o755)

    def forks(raw: str) -> int:
        counter.write_text("", encoding="utf-8")
        env = {"PATH": f"{_to_bash_path(bindir)}:{_base_path()}"}
        _run({}, tmp_path, env=env, raw_json=raw)
        return len(counter.read_text(encoding="utf-8").split())

    win = r"C:\\Users\\Vladimir\\src\\proj\\lib\\mod.ts"
    # ⚠ THE LOWERCASE-`u` ROWS ARE THE ONLY ONES THAT DISCRIMINATE, and the
    # first version of this test had none: `C:\\Users` capitalises the U, so
    # that fixture carries no `\u` at all and passes against the `\u` arm this
    # docstring says it rules out. Review round 1 measured it — a `\u` mutant
    # forks TWICE on the two rows below and once on every other row here.
    lower = r"C:\\users\\vladimir\\src\\proj\\lib\\mod.ts"
    for label, raw in [
        ("a Windows path, native spelling", _raw_edit(win)),
        ("64 KB of Windows paths", _raw_edit("src/mod.ts", (win + " ") * 1500)),
        ("1 MB of Windows paths", _raw_edit("src/mod.ts", (win + " ") * 24000)),
        ("a lowercase `users` component", _raw_edit(lower)),
        ("64 KB of lowercase `users` paths",
         _raw_edit("src/mod.ts", (lower + " ") * 1500)),
        ("64 KB of C source", _raw_edit("src/main.c",
                                        "static int f(int x) { return x + 1; }"
                                        * 1600)),
    ]:
        assert forks(raw) <= 1, (
            f"{label} paid for a route that cannot deny it — the prefilter arm "
            f"is too wide, and every tool call in such a repo pays it")


def test_an_escape_dense_payload_is_decided_inside_the_hook_budget(tmp_path):
    r"""What the arm's newly admitted payloads cost, since they now pay a read.

    A payload the arm admits pays the field read, and the field read decodes.
    Both are bounded — `LOCI_JSON_MAX` caps what is parsed at the guard's own
    64 KB and `_LOCI_JSON_UMAX` caps the decode's work — and `hooks.json` kills
    this hook at 5 s, after which PreToolUse fails OPEN, so the ceiling is
    worth an assertion. The value here is a `file_path` that is nothing but
    escapes, which is the worst shape for the decoder.

    ⚠ IT IS THE FULL WIDTH AGAIN, and that is F16's doing. For one release this
    read `* 1000`, because past `_LOCI_JSON_UMAX` the decode stops and hands the
    remaining escapes back as text — backslashes and all — and the guard masks
    every backslash to `/` before resolving, so the path arrived at `realpath`
    with one component per escape. 2 000 escapes was **45 s** where the
    prefilter used to exit in 0.05 s, and 10 900 (the most that fits under
    `LOCI_JSON_MAX`) was not measurable in any useful time at all. That cost was
    `realpath`'s, not this arm's — the same defect
    `test_a_padded_path_is_decided_inside_the_hook_budget` reaches without any
    escape — and F16 took `realpath` off the verdict path. The walk that
    replaced it is linear in components.

    It was never a hole, which is why it was pinned rather than fixed here: a
    value the decode gave up on keeps its escapes as text, so it spells no
    guarded path, and the write that proceeds when the hook is killed goes to a
    file named `{AAAA…}`. Reaching it needs a payload written by hand —
    `JSON.stringify` does not escape an ASCII letter — which is the same
    argument that makes F15's door 1 a gap rather than a hole.
    """
    (tmp_path / ".loci").mkdir(parents=True)
    dense = (B + "u0041") * 10900
    start = time.monotonic()
    decision = _run({}, tmp_path, raw_json=_raw_edit(dense))
    elapsed = time.monotonic() - start
    assert decision is None, "a path of 10 900 escaped A's is not a guarded file"
    assert elapsed < 5.0, (
        f"the arm's worst fully-decoded payload took {elapsed:.1f}s against "
        f"the 5 s timeout in hooks.json — past it the hook is killed and fails "
        f"open")


def _spellings(p: Path) -> dict:
    r"""The three ways one directory reaches this hook.

    `msys` is what every tool inside Git Bash produces, `native` is what Claude
    Code puts in `CLAUDE_PROJECT_DIR`, and `win` is what a real `file_path`
    carries (`post-edit-hook.sh` says so twice). On a POSIX host all three are
    the same string, and the matrix below is then three runs of one case — which
    is fine: it is Windows the spellings differ on.
    """
    return {"msys": _to_bash_path(p),
            "native": _native_path(p),
            "win": str(Path(p))}


@pytest.mark.parametrize("rel", (".loci/contract.yaml",) + RECIPE_FILES)
@pytest.mark.parametrize("root_is,fp_is", [
    ("native", "msys"),   # THE LIVE ONE: what Claude Code and Git Bash produce
    ("msys", "native"),
    ("win", "msys"),
    ("msys", "win"),
    ("native", "native"),
    ("msys", "msys"),     # the pair the whole suite used to be written in
])
def test_one_file_is_one_verdict_however_the_two_sides_are_spelled(
        tmp_path, rel, root_is, fp_is):
    r"""F16 item 1, which was LIVE on `main` and needed nothing but a `./`.

    Route 1 used to resolve the `file_path` and the guarded path SEPARATELY and
    compare the two strings, over a `realpath` that preserves the spelling it is
    given: `C:/proj/…` answers `C:/…` and `/c/proj/…` answers `/c/…`, for one
    file. Claude Code sets `CLAUDE_PROJECT_DIR` to the native spelling and every
    tool inside Git Bash writes the MSYS one, so the two halves of the
    comparison routinely disagreed — and the only thing saving the guard was the
    SUFFIX arm, which matches the raw text before anything is resolved.

    Which is why the pad is here. `.loci/./././…/contract.yaml` is a legal thing
    to write, the filesystem opens it as the Contract Envelope, and it does not
    end in `.loci/contract.yaml`, so the suffix arm cannot answer it and the
    comparison is all that is left. At `dccf9d4` all three guarded files were
    writable this way, measured, on both drive spellings.

    The fix is not a canonical string — there is no canonical string on MSYS
    (`realpath` keeps what it is given, `cd -P`+`pwd -P` re-spells through the
    mount table, `pwd -W` is MSYS-only). It is that both sides are walked by one
    function inside one subshell, and the directory half of the comparison is
    `-ef`, which is the filesystem's own identity test and is blind to all of
    this.
    """
    (tmp_path / ".loci" / "build").mkdir(parents=True)
    spelling = _spellings(tmp_path)
    head, _, tail = rel.rpartition("/")
    padded = f"{spelling[fp_is]}/{head}/{'./' * 60}{tail}"
    # The control the pin this replaced carried, and the reason it did: without
    # it all 18 cases pass through the suffix arm the moment anyone widens it,
    # and the comparison they exist for is never reached.
    assert not padded.endswith(f"/{rel}"), (
        f"{padded} still ends with {rel} — the suffix arm answers it and this "
        f"case is measuring that arm, not the comparison below it")
    assert _denied(_run(_edit(padded), tmp_path,
                        env={"CLAUDE_PROJECT_DIR": spelling[root_is]})), (
        f"{rel} spelled {fp_is} under a {root_is} root was allowed — the two "
        f"sides of route 1's comparison are being produced independently again")


@pytest.mark.parametrize("rel", (".loci/contract.yaml",) + RECIPE_FILES)
def test_a_padded_path_is_decided_inside_the_hook_budget(tmp_path, rel):
    r"""F16 item 2: the cost class, which was the fail-open.

    THE ONE MEMBER REACHABLE FROM THE PRODUCT WITH NOTHING BUT A PROMPT.
    `file_path` is the model's own string and `JSON.stringify` carries it
    verbatim, so pad the path with `ab/` and take it back with `../`:
    `realpath -m`, `json.loads` and Win32 all read the result as the guarded
    file, and the guard denies it — which is the first assertion, and what says
    the path really is the guarded one.

    It was ALLOWED in production anyway, because MSYS's `realpath` is
    super-linear in the number of path COMPONENTS and `hooks.json` kills this
    hook at 5 s, after which PreToolUse permits the write. Measured end to end
    on Git for Windows (coreutils 8.32): 100 components 0.54 s, 600 3.66 s,
    1 000 **12.47 s**, 1 500 **28.18 s**. GNU coreutils 9.4 answers the 1 500
    case in 0.002 s, which is why this used to be a Windows-only test asserting
    the guard took LONGER than its own budget.

    `realpath` is off the verdict path now and the walk that replaced it is
    linear in components on every host — 2 000 components, end to end through
    the hook, 0.09 s — so the assertion reads forwards and the skipif is gone.

    This spelling is over 6 000 characters and therefore past POSIX `PATH_MAX`
    too, but it does NOT have F20's defect, and the difference is which arm
    answers. This path carries the guarded BASENAME, so `_route1_hit` reaches
    it through the `case` and `_guarded_match`; it is ~2 006 segments, which is
    over `_ROUTE1_MAX_SEGMENTS`, so `_walk` returns 2 before descending a
    single component and the deny is the BOUND's. Either way nothing hands the
    whole name to a syscall. F20's link is called `notes.txt`, so `-ef` over
    the whole name is the only thing that can see it — and that is the one arm
    `PATH_MAX` can silence. Same for the `'ab/' * 8000` fixtures below.

    ⚠ THAT THE BOUND ANSWERS IS ALSO WHY `elapsed` BELOW TIMES A SHORT-CIRCUIT
    rather than the walk this docstring names, and why the two causes the deny
    message offers are both wrong today. F18 quartered the bound from 4 096 to
    1 024; at 4 096 this fixture's 2 006 segments were under it and really were
    walked. Filed as **F23** — re-sizing the fixture so the clock times the
    walk again is its own task, with its own non-vacuity criterion, and it is
    not F20's to do while passing through. The sibling three hundred lines
    below, `test_both_readings_are_decided_inside_the_hook_budget`, already
    carries the same correction for itself.
    """
    (tmp_path / ".loci" / "build").mkdir(parents=True)
    head, _, tail = rel.rpartition("/")
    target = (f"{_to_bash_path(tmp_path)}/{head}/"
              f"{'ab/' * 1000}{'../' * 1000}{tail}")
    start = time.monotonic()
    decision = _run(_edit(target), tmp_path)
    elapsed = time.monotonic() - start
    assert _denied(decision), (
        f"a 2 000-component spelling of {rel} was allowed — either the walk "
        f"stopped collapsing `..` past a component that does not exist, or the "
        f"hook was killed at 5 s and PreToolUse failed open")
    assert elapsed < 5.0, (
        f"the guard took {elapsed:.1f}s on a 2 000-component path, against the "
        f"5 s timeout in hooks.json — past it the hook is killed and the write "
        f"goes through")


def test_the_guarded_file_is_denied_at_whatever_it_points_at(tmp_path):
    r"""The LAST component, which the walk deliberately does not follow.

    `_walk` descends into every component that exists, so it resolves a link
    anywhere in the middle of a path — but the guarded file itself is the last
    component and `cd` cannot enter a file. Reading its target needs `readlink`,
    which is a fork and which BSD ships without `-f`.

    So `_guarded_match` asks the filesystem instead, with `-ef` on the two paths
    as they were given, before anything has `cd`-ed. That is one test over both
    sides — not two resolutions — and it follows the last component on every
    host. It answers false for a path that is not there yet, which is the
    ordinary `Write`, and that case is the walk's.

    Denying here is a widening: `realpath` would have caught a SYMLINK in this
    position and this catches a hard link too, because both name one inode and
    one inode is one Contract Envelope.
    """
    (tmp_path / ".loci").mkdir()
    real = tmp_path / "elsewhere" / "contract.yaml"
    real.parent.mkdir()
    real.write_text("x", encoding="utf-8")
    link = tmp_path / ".loci" / "contract.yaml"
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError):
        try:
            os.link(real, link)
        except OSError:
            pytest.skip("this host cannot link a file")
    assert _denied(_run(_edit(_to_bash_path(real)), tmp_path)), (
        f"{real} IS .loci/contract.yaml — one inode, and the guard writes the "
        f"bounds nobody but the user may change")
    # The control: an ordinary neighbour of the link target is not the file.
    other = tmp_path / "elsewhere" / "notes.yaml"
    other.write_text("x", encoding="utf-8")
    assert _run(_edit(_to_bash_path(other)), tmp_path) is None, other


@pytest.mark.parametrize("rel", (".loci/contract.yaml",) + RECIPE_FILES)
def test_a_link_whose_own_name_hides_the_guarded_file_is_denied(tmp_path, rel):
    r"""A REGRESSION this change introduced and review round 1 caught.

    The basename gate above the walk reads the RAW `file_path`, where the
    version it replaced read the RESOLVED one. So a link called `notes.txt`
    pointing at `.loci/contract.yaml` carries no guarded basename, never reaches
    the walk, and was ALLOWED — while `main` denies it, because `realpath`
    follows the last component. All three files, measured.

    It is reachable: the payload-level prefilter's first arm is an ungated
    `*contract*` over the WHOLE payload, so any `new_string` that says
    "contract" admits any `file_path` at all. Hence the fixture here says it.

    The fix is that `-ef` runs BESIDE the suffix arm rather than inside the
    function the gate guards — one filesystem identity test over both paths as
    given, forkless, before anything is resolved. It is the only thing that
    follows a link in the last component, so it cannot live behind a gate that
    is keyed on that component's name.

    `test_a_symlink_that_hides_the_name_is_a_known_gap` is still the gap it
    always was, and does not overlap: its fixture says `contract` nowhere, so it
    exits at the payload prefilter without a fork, which is the cost that gap
    exists to avoid.

    ⚠ HOW LONG THE PAD MAY BE IS THE HOST'S ANSWER, NOT THE GUARD'S (F20).
    Windows long paths are far longer than POSIX ones and `-ef` reaches every
    spelling this test asks it for — 5 027 characters at `padded(2500)`,
    measured — so there the 4 KB length gate is exactly what the pad pins.
    POSIX `PATH_MAX` counts the NUL — 4 096 on Linux, 1 024 on macOS — and past
    it no syscall takes the name at all: `-ef` answers false because it cannot
    stat the path, not because anything is bounded, and route 1 allows. That
    ALLOW is CORRECT, and the `r+` below is what says so rather than leaving
    the next reader to rediscover it as a hole — nothing can write through the
    name either, same errno, and the two flip on the same byte. So on POSIX the
    pad is the longest spelling the host still reaches, computed because `head`
    is a `tmp_path` whose length is not fixed. The verdict on the over-length
    spelling is deliberately NOT pinned: denying it would be a harmless
    over-deny, and pinning today's ALLOW would make that look like a
    regression.

    Until this was fixed all three parametrisations were red on Linux, on
    `main` and on every branch, for a spelling of `len(tmp_path) + 4 100`
    characters — about 4 165 on the host it was filed from, and NOT a constant,
    which is the same mistake one paragraph down from the line that says the
    count has to be computed.

    What POSIX cannot pin behaviourally, `test_the_ef_arm_carries_no_length_
    test` pins structurally: a 4 096-byte gate is invisible to a host that
    cannot spell 4 097 bytes, and this file's answer to a property a fixture
    cannot exercise is to read the guard's own source. Measured: a gate at
    2 048 reddens the loop below on Linux, a gate at 4 096 reddens it only on
    Windows.
    """
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x", encoding="utf-8")
    link = tmp_path / "notes.txt"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        try:
            os.link(target, link)
        except OSError:
            pytest.skip("this host cannot link a file")
    def edit(path: str) -> dict:
        return {"tool_name": "Edit",
                "tool_input": {"file_path": path,
                               "new_string": "this is a contract change"}}

    assert _denied(_run(edit(_to_bash_path(link)), tmp_path)), (
        f"notes.txt IS {rel} and was allowed — the `-ef` test is behind the "
        f"basename gate again, where the name it is meant to see through hides "
        f"it")

    # ⚠ AND PADDED, which is the shape review round 2 found. `-ef` is the only
    # thing route 1 has for a path whose last component is not a guarded
    # basename, so ANY bound on it — a length gate was tried, at 4 096 — is a
    # bound that ALLOWS past itself. Measured at the time: 4 095 characters
    # denied, 4 097 allowed, and the write that followed landed in the Contract
    # Envelope. The pad is free and it is the same `./` this whole task exists
    # for, so the unpadded case above cannot stand alone.
    head, _, base = _to_bash_path(link).rpartition("/")

    def padded(pads: int) -> str:
        return f"{head}/{'./' * pads}{base}"

    if sys.platform == "win32":
        spellings = [padded(2045), padded(2500)]
        for spelling in spellings:
            assert len(spelling) > 4096, "this case is not past a 4 KB gate"
    else:
        try:
            ceiling = os.pathconf(head, "PC_PATH_MAX")
        except (OSError, ValueError):    # not every host answers the question
            ceiling = -1
        # ⚠ AND NOT EVERY HOST RAISES WHEN IT DECLINES: POSIX says `pathconf`
        # reports "indeterminate" by RETURNING -1 with errno untouched, and
        # CPython hands that back rather than raising. Without this the
        # arithmetic below goes negative and the test dies about a PATH_MAX of
        # -1 instead of falling back.
        if ceiling < 0:
            ceiling = 4096
        # Each pad is two characters, so one more takes the name past the
        # ceiling — which the assertion below states rather than assumes.
        fits = (ceiling - 2 - len(head) - len(base)) // 2
        # A floor, because at `fits == 0` the pad is the empty string and both
        # spellings silently become the unpadded path asserted above — green,
        # having tested no pad at all. Needs a ~1 013-character `head` on macOS
        # or ~4 085 on Linux, which is a deep `TMPDIR` or `--basetemp`.
        assert fits > 100, (
            f"only {fits} pads fit under a PATH_MAX of {ceiling} with a "
            f"{len(head)}-character tmp_path — this fixture would pass "
            f"without padding anything")
        spellings = [padded(fits // 2), padded(fits)]
        assert len(padded(fits)) <= ceiling - 1 < len(padded(fits + 1)), (
            f"the pad arithmetic is off: {len(padded(fits))} characters "
            f"against a PATH_MAX of {ceiling}")
        # BOTH SIDES OF THE EDGE. The negative one alone is not enough: if the
        # host's real ceiling is below what `pathconf` reports, `padded(fits)`
        # is unreachable for exactly F20's reason, route 1 allows, and the
        # failure lands in the loop below blaming the guard for a length bound
        # it does not have — which is the misdiagnosis this whole task exists
        # to stop. So the reachable spelling is shown to be reachable first.
        with open(padded(fits), "r+"):
            pass
        # Opened `r+` rather than `w`: a write-capable mode that truncates
        # nothing, so if some host ever DOES take the name this assertion can
        # fail without emptying the guarded file it is about.
        with pytest.raises(OSError) as refused:
            with open(padded(fits + 1), "r+"):
                pass
        assert refused.value.errno == errno.ENAMETOOLONG, (
            f"a spelling past PATH_MAX was opened ({refused.value!r}) — then "
            f"it reaches the guarded file after all, and the ALLOW route 1 "
            f"gives it is a hole rather than the host refusing first")

    for spelling in spellings:
        assert _denied(_run(edit(spelling), tmp_path)), (
            f"a {len(spelling)}-character spelling of {rel} was allowed while "
            f"the {len(_to_bash_path(link))}-character one was denied — "
            f"something on the `-ef` path is bounded by length, and a bound "
            f"that skips the only test that sees a link is a bound that allows")


def test_the_ef_arm_carries_no_length_test():
    r"""The half of the test above that POSIX cannot reach (F20).

    `-ef` is the ONLY thing route 1 has for a link whose last component is not
    a guarded basename, so any bound on it is a bound that ALLOWS past itself —
    F16 review round 2 measured a 4 096-character gate letting the write land
    in the Contract Envelope. The padded loop above is the behavioural pin for
    that, and on POSIX it can no longer reach it: `PATH_MAX` is 4 096 counting
    the NUL, so no fixture on that host can spell the 4 097 characters a gate
    at 4 096 needs to be caught. Measured — a gate at 2 048 reddens the loop on
    Linux, a gate at 4 096 reddens it only on Windows.

    So the property is held here instead, by reading the guard, which is what
    this file does whenever a fixture cannot exercise something:
    `test_both_readings_are_decided_inside_the_hook_budget` says it plainly —
    a wall clock cannot hold a bound and the structural assertion beside it is
    what does. Same shape, one platform further.

    `${#` is the whole search, and that is not a shortcut: this hook forks
    nothing on the verdict path, so a length test it could actually ship is a
    parameter expansion. A forked one (`expr length`, `wc -c`) would break the
    rule `test_the_verdict_does_not_depend_on_any_external_binary` and
    `test_neither_route_forks_its_way_to_a_verdict` pin, and would be caught
    there instead.
    """
    src = GUARD.read_text(encoding="utf-8")
    start = src.index("_route1_hit() {")
    body = src[start:src.index(chr(10) + "}" + chr(10), start)]
    # Comments go first. This hook carries its reasoning inline and both
    # searches below are for text that its own prose also says — the `-ef`
    # arm is described in a comment four lines under itself.
    body = chr(10).join(ln.split(" #")[0] for ln in body.splitlines()
                        if not ln.strip().startswith("#"))

    arms = [ln.strip() for ln in body.splitlines() if "-ef" in ln]
    assert len(arms) == 1, (
        f"`_route1_hit` has {len(arms)} `-ef` tests, not one: {arms}. The "
        f"identity test is the only arm that sees through a link's own name, "
        f"and this pin is written against there being exactly one of it")
    assert "${#" not in body, (
        f"`_route1_hit` has grown a length test:{chr(10)}"
        + chr(10).join("    " + ln for ln in body.splitlines() if "${#" in ln)
        + f"{chr(10)}`-ef` is the only arm that follows a link in the last "
        f"component, so a bound on it is a bound that ALLOWS past itself — "
        f"measured at 4 096 in F16 review round 2, with the write landing in "
        f"the Contract Envelope. If this is deliberate, the padded loop in "
        f"`test_a_link_whose_own_name_hides_the_guarded_file_is_denied` is "
        f"where the new edge has to be measured, on Windows — Linux cannot "
        f"spell past PATH_MAX and will stay green through it")


@pytest.mark.skipif(sys.platform != "win32", reason="MSYS network lookup")
def test_a_unc_spelling_is_decided_inside_the_hook_budget(tmp_path):
    r"""A leading `//` is a network lookup, and route 1 does two things with a
    path that take it: one `-ef` and one `cd -P`.

    Measured here against a server that is simply not there: 2.83 s of the 5 s
    `hooks.json` allows, for an ordinary `file_path`. A server that answers
    slowly rather than not at all can block for far longer, and past 5 s the
    hook is killed and PreToolUse permits the write — the fail-open this whole
    task is about, reached without any padding or escape.

    So the guard collapses a leading `//` on BOTH sides before it resolves
    anything. The verdict is unchanged (the suffix arm answers this spelling
    either way, and it is the guard's documented over-deny that ANY
    `.loci/contract.yaml` is denied); what changes is that it is answered
    without asking the network.

    ⚠ THE SERVER NAME IS RANDOM PER RUN, and it has to be. Windows caches a
    failed name lookup, so a fixed name is 2.53 s the first time this runs on a
    machine and 0.13 s every time after — which is a timing test that passes
    for a reason that has nothing to do with the code. Measured: with the
    collapse removed and a cold name, 2.53 s; with it, 0.22 s.

    The structural pin below is the one that cannot go stale this way; this one
    is what says the number is real.
    """
    (tmp_path / ".loci").mkdir()
    cold = f"//srv-{uuid.uuid4().hex[:12]}/shr/proj"
    start = time.monotonic()
    decision = _run(_edit(f"{cold}/.loci/contract.yaml"), tmp_path)
    elapsed = time.monotonic() - start
    assert _denied(decision), "the guard's over-deny of any .loci/contract.yaml"
    assert elapsed < 2.0, (
        f"a UNC-spelled path took {elapsed:.1f}s — route 1 is asking the "
        f"network for a server before it decides, and a slower one takes the "
        f"whole 5 s budget with it")


def test_a_leading_double_slash_is_collapsed_on_both_sides():
    """The structural half of the UNC pin, because the timing half can go quiet.

    A machine with no network stack answers a dead server instantly and the
    test above then passes whatever the code does. What must hold regardless is
    that NEITHER path route 1 stats still begins with `//` — both sides, or the
    comparison is being made between two differently-spelled strings, which is
    the whole subject of F16.
    """
    code = chr(10).join(l for l in GUARD.read_text(encoding="utf-8").splitlines()
                        if not l.lstrip().startswith("#"))
    for var in ("fp", "root"):
        assert re.search(r"case \$%s in //\*\) %s=" % (var, var), code), (
            f"`{var}` is no longer collapsed before route 1 resolves it: a "
            f"leading `//` makes MSYS ask the network for a server, which is "
            f"2.53 s of a 5 s budget for one that is absent and unbounded for "
            f"one that is slow")


@pytest.mark.parametrize("rel", (".loci/contract.yaml",) + RECIPE_FILES)
def test_a_project_root_with_a_trailing_slash_is_the_same_root(tmp_path, rel):
    """`CLAUDE_PROJECT_DIR` is not guaranteed to be spelled without one.

    Unstripped it makes `$root/$g` read `<root>//.loci/…`. In practice nothing
    observable changes for an ordinary root — the walk skips empty segments and
    `-ef` collapses them — and a review round proved it: removing the call-site
    strip leaves this test and the whole suite green. It is kept for the one
    root where it is not cosmetic, `/`, where `$root/$g` would otherwise be
    `//.loci/…` and route 1 would ask the network for a server called `.loci`.

    So this test is not that strip's pin, and does not claim to be. What it
    covers is the SPELLING reaching route 1 at all: `CLAUDE_PROJECT_DIR` is not
    guaranteed to arrive without a trailing slash, and every other route-1
    fixture assumes it does.
    """
    (tmp_path / ".loci" / "build").mkdir(parents=True)
    head, _, tail = rel.rpartition("/")
    padded = f"{_to_bash_path(tmp_path)}/{head}/{'./' * 60}{tail}"
    for root in (_to_bash_path(tmp_path) + "/", _native_path(tmp_path) + "/"):
        assert _denied(_run(_edit(padded), tmp_path,
                            env={"CLAUDE_PROJECT_DIR": root})), (
            f"{rel} was allowed under a root spelled {root!r}")


def test_a_path_too_long_to_walk_is_denied_rather_than_allowed(tmp_path):
    r"""The work bound, and the direction that makes it safe.

    The collapse is quadratic in the tail it accumulates, and at the 64 KB the
    field read caps at that is 21 780 components and 3.74 s against a 5 s kill
    that fails OPEN. `_ROUTE1_MAX_SEGMENTS` stops it, and what matters is that
    it stops it by DENYING.

    F16 item 4 is the record of the other kind: a bound that allowed past
    itself, chose a different resolution for the long side than for the short
    one, and made all three guarded files writable. This one decides nothing
    about the comparison — it reads the `file_path` alone, short-circuits before
    any comparison happens, and errs the way the rest of this guard errs.

    ⚠ THE MIDDLE ASSERTION IS THE ONE THAT PINS THE BOUND, and the first
    version of this test did not have it. A path that really IS the guarded
    file is denied with the bound and denied without it — the walk gets there on
    its own, slowly — so raising `_ROUTE1_MAX_SEGMENTS` to a billion left the
    whole suite green while the hook went to 3.69 s alone and 5.88 s with four
    running at once, which is past the 5 s in `hooks.json` and therefore an
    allowed write. Review round 2 measured exactly that.

    The signature of the bound is its OVER-deny: a path that carries a guarded
    basename, is long, and is NOT the guarded file. With the bound that is
    DENIED; without it the walk resolves it correctly and ALLOWS it. That is a
    verdict that moves, and it is the only kind of assertion a work bound can
    have.

    The last assertion is the other edge: the bound must not have become a
    blanket deny for any long path, only for one that got past the basename
    gate.
    """
    (tmp_path / ".loci" / "build").mkdir(parents=True)
    root = _to_bash_path(tmp_path)
    over = f"{root}/.loci/{'ab/' * 8000}{'../' * 8000}contract.yaml"
    start = time.monotonic()
    assert _denied(_run(_edit(over), tmp_path)), (
        "a path over the segment bound was ALLOWED — the bound must deny, or "
        "it is F16 item 4 with a different constant")
    assert time.monotonic() - start < 5.0, "the bound did not short-circuit"
    # Carries `contract.yaml`, so the gate admits it; resolves to
    # `<root>/src/contract.yaml`, which is not guarded. Only the bound denies it.
    not_the_file = f"{root}/src/{'ab/' * 8000}contract.yaml"
    assert _denied(_run(_edit(not_the_file), tmp_path)), (
        "the bound is gone: this path is NOT the Contract Envelope and the "
        "walk says so correctly — which means nothing is stopping the walk "
        "from running at full length, and at the 64 KB the field read caps at "
        "that is 3.69 s alone and 5.88 s under load, past the 5 s kill")
    long_but_innocent = f"{root}/src/{'ab/' * 8000}{'../' * 8000}main.c"
    assert _run(_edit(long_but_innocent), tmp_path) is None, (
        "the bound has become a blanket deny for any long path")


def test_the_segment_bound_denies_on_the_guarded_side_too(tmp_path):
    """The bound has two sides, and only one of them used to answer.

    `_guarded_match` walks the `file_path` and then the guarded path. The second
    walk read `|| continue`, which turns "too long to walk" into "this is not
    that file" — an ALLOW, on the side the file's own prose says always denies.
    It needs `$root` itself to be enormous, which takes an unset
    `CLAUDE_PROJECT_DIR` and a payload `cwd` of thousands of segments: low
    reachability, but the claim the bound rests on has to be true on both sides
    or it is not the claim.
    """
    (tmp_path / ".loci").mkdir()
    # The  is SHORT — otherwise its own walk trips the bound first
    # and the second one never runs, which is how the first version of this
    # test passed with the defect in place. The `./` is what gets it past the
    # suffix arm and into the comparison at all.
    huge = "/" + "ab/" * 6000 + "proj"
    payload = {"tool_name": "Edit", "cwd": huge,
               "tool_input": {
                   "file_path": f"{_to_bash_path(tmp_path)}/.loci/./contract.yaml",
                   "new_string": "x"}}
    assert _denied(_run(payload, tmp_path, env={"CLAUDE_PROJECT_DIR": ""})), (
        "the guarded side of the comparison swallowed the bound as 'not that "
        "file' and allowed the write")


def test_a_symlink_is_resolved_before_the_dot_dot_is_applied(tmp_path):
    r"""F16 item 3, which was LIVE on every host without GNU `realpath`.

    The old shell rung ran a purely LEXICAL collapse first and only then
    `cd`+`pwd -P`, so a symlink followed by `..` was erased before it could be
    resolved and the rung answered a different file than `realpath` does:

        L -> <root>/.loci/build
        <root>/L/../contract.yaml    really <root>/.loci/contract.yaml
                                     lexically <root>/contract.yaml

    On macOS and the BSDs that rung is not a fallback — there is no
    `realpath -m` and `realpath` fails on a file that does not exist yet, which
    is every `Write` — so it is the only rung, and this was a writable Contract
    Envelope there. Measured by taking `realpath` off PATH on Linux: `main`
    ALLOWS this spelling, the walk denies it.

    `_walk` descends with `cd -P`, one component at a time, so the link is
    resolved BEFORE the `..` that follows it — which is what `realpath` does.
    The second assertion is the control: one more `..` leaves the guarded
    directory for real, and that one must still be allowed, or the test is
    passing because the guard denied everything.
    """
    (tmp_path / ".loci" / "build").mkdir(parents=True)
    if not _make_dir_link(tmp_path / "L", tmp_path / ".loci" / "build"):
        pytest.skip("this host cannot create a directory link")
    root = _to_bash_path(tmp_path)
    for rel in (".loci/contract.yaml",) + RECIPE_FILES:
        base = rel[len(".loci/"):]
        inside = f"{root}/L/../{base}"
        assert not inside.endswith(f"/{rel}"), "the suffix arm answers this one"
        assert _denied(_run(_edit(inside), tmp_path)), (
            f"{inside} IS {rel} — the `..` was applied before the link was "
            f"resolved")
        outside = f"{root}/L/../../{base}"
        assert _run(_edit(outside), tmp_path) is None, (
            f"{outside} is <root>/{base}, which is not guarded")


# ── F18: Win32 decides a path component by component ────────────────────────

_PAD = "./" * 60
"""Past the suffix arm, which answers any path ENDING in a guarded name.

Every F18 fixture that means to reach the walk carries this. Without it the arm
answers first and the test is measuring the arm — which is how all four of F16
stayed invisible for a release, and how the two holes F18 closes stayed
invisible through F16's own suite.
"""


def _writes_the_guarded_file(root: Path, spelling: str, guarded: str) -> bool:
    """Does a write through `spelling` actually land in `guarded`?

    The assertion every F18 fixture needs beside its verdict. A spelling nothing
    can open is not a hole, and a test that only pins the verdict cannot tell
    the two apart: `<root>/.loci ./contract.yaml` (a SPACE) and
    `<root>/.loci../contract.yaml` (two dots) look exactly like the one-dot
    spelling and Win32 opens neither. `open()` is used rather than `Path`,
    because pathlib normalises a path before it opens it and the whole question
    here is what the unnormalised one reaches.
    """
    p = os.path.join(str(root), spelling.replace("/", os.sep))
    try:
        with open(p, "w", encoding="utf-8") as f:
            f.write("PWNED\n")
    except OSError:
        return False
    g = os.path.join(str(root), guarded.replace("/", os.sep))
    with open(g, encoding="utf-8") as f:
        landed = f.read().startswith("PWNED")
    # Put it back: the guard is run against this tree afterwards.
    with open(g, "w", encoding="utf-8") as f:
        f.write("x\n")
    return landed


def _guarded_tree(root: Path) -> None:
    (root / ".loci" / "build").mkdir(parents=True, exist_ok=True)
    for rel in (".loci/contract.yaml",) + RECIPE_FILES:
        (root / rel).write_text("x\n", encoding="utf-8")


@pytest.mark.parametrize("rel", (".loci/contract.yaml",) + RECIPE_FILES)
@pytest.mark.parametrize("spelling", ["bash", "native"])
def test_a_dotted_component_is_read_the_way_win32_reads_it(tmp_path, rel, spelling):
    r"""F18 item 1: Win32 strips a trailing dot from EVERY component.

    `<root>/.loci./contract.yaml` opens the Contract Envelope — measured through
    Win32 itself, with the write performed and the guarded file read back, which
    is what `_writes_the_guarded_file` asserts here. The guard stripped a
    trailing dot only from the END of the whole `file_path`, and MSYS's `stat`
    strips none at all (it hands NT the name verbatim, where a trailing dot is
    literal), so `[ -d ".loci." ]` was false, `-ef` was false, and the walk put
    `.loci.` in the tail and compared it against a different file. All three
    guarded files were writable on `main` and after F16.

    ⚠ IT IS AN OVER-DENY ON POSIX, deliberately, and that is what the second
    half asserts. A component ending in a dot is an ordinary name there, so
    `<root>/.loci./contract.yaml` really is a different file — created here, and
    denied anyway. The guard reads every path both ways and denies on either,
    which is the only way to be right on the platform whose writer is Win32 and
    on the one whose writer is not. It is the same trade the backslash mask
    makes (a POSIX file named `a\.loci\build.yaml`) and the same one the
    end-of-string strip has always made.

    Both spellings, because F16: every route-1 fixture in this file used to go
    through `_to_bash_path`, which is the ONE spelling where the old resolver
    agreed with itself.
    """
    _guarded_tree(tmp_path)
    head, _, base = rel.rpartition("/")
    dotted = f"{head}./{base}"
    root = {"bash": _to_bash_path(tmp_path), "native": _native_path(tmp_path)}[spelling]

    if sys.platform == "win32":
        assert _writes_the_guarded_file(tmp_path, dotted, rel), (
            f"{dotted} no longer opens {rel} on this host — the hole this test "
            f"is about is gone and so is the reason for the deny below")
    else:
        (tmp_path / f"{head}.").mkdir(exist_ok=True)
        (tmp_path / dotted).write_text("a different file\n", encoding="utf-8")

    for path in (f"{root}/{dotted}", f"{root}/{head}./{_PAD}{base}"):
        assert _denied(_run(_edit(path), tmp_path)), f"Edit {path}"
        assert _denied(_run(_write(path), tmp_path)), f"Write {path}"


@pytest.mark.parametrize("rel", (".loci/contract.yaml",) + RECIPE_FILES)
@pytest.mark.parametrize("suffix,why", [
    (" ", "a trailing SPACE on an interior component is not stripped"),
    ("..", "a RUN of trailing dots on an interior component is not stripped"),
])
def test_a_spelling_win32_does_not_open_is_still_allowed(tmp_path, rel, suffix, why):
    """Where the deny stops, and it is measured rather than assumed.

    The strip above widens what route 1 matches, in a hook that runs ahead of
    every Edit and Write in every repo the plugin is installed for, so the edge
    has to be a measurement. Win32 takes exactly ONE dot off an interior
    component and takes no spaces off one at all: `<root>/.loci../contract.yaml`
    and `<root>/.loci /contract.yaml` are both ENOENT, verified here by writing
    through them before the verdict is asked for.

    The END of the whole path is a different rule — the whole run, dots and
    spaces both — and `test_a_trailing_dot_or_space_does_not_evade_the_guard`
    is the one that pins it.
    """
    _guarded_tree(tmp_path)
    head, _, base = rel.rpartition("/")
    spelling = f"{head}{suffix}/{_PAD}{base}"
    assert not _writes_the_guarded_file(tmp_path, spelling, rel), (
        f"{spelling} now opens {rel} on this host — {why} no longer holds, and "
        f"this spelling belongs with the denied ones instead")
    assert _run(_edit(f"{_native_path(tmp_path)}/{spelling}"), tmp_path) is None, (
        f"{spelling} is not {rel} on any host and was denied — the dot strip "
        f"has widened past the rule it implements")


@pytest.mark.parametrize("path", [
    "src./main.c",                      # an ordinary dotted directory
    ".loci./notes.txt",                 # …inside the guarded one, unguarded name
    ".loci./build./notes.txt",
    # A component that is all dots is a NAME, not an instruction, and the strip
    # may not make it one. Round 1 of review found `.../` reading as `../`, so
    # this path was denied while the write it describes goes to
    # `<root>/x/.../.loci/contract.yaml` — a real file on POSIX and no file at
    # all on Windows, but never the Contract Envelope.
    # …and PADDED, or the suffix arm answers instead of the walk and the case
    # is measuring an arm that over-denies any path ending in a guarded name.
    "x/.../.loci/" + _PAD + "contract.yaml",
    "x/..../.loci/" + _PAD + "contract.yaml",
    "x/y/....../.loci/" + _PAD + "contract.yaml",
])
def test_an_ordinary_dotted_path_is_allowed(tmp_path, path):
    # The control for the strip: it is not a blanket deny for a dot before a
    # slash. None of these carries a guarded basename and none of them IS a
    # guarded file once the dot is gone.
    _guarded_tree(tmp_path)
    assert _run(_edit(f"{_native_path(tmp_path)}/{path}"), tmp_path) is None, path
    assert _run(_write(f"{_to_bash_path(tmp_path)}/{path}"), tmp_path) is None, path


@pytest.mark.skipif(sys.platform == "win32",
                    reason="Win32 cannot create a name that ends in a dot")
def test_the_raw_spelling_is_decided_too_where_dotted_names_are_real(tmp_path):
    """The other half of item 1, and the reason the raw reading is KEPT.

    `R. -> <root>`, so `<root>/R./.loci/…contract.yaml` really IS the Contract
    Envelope on this host and the dot-stripped spelling `<root>/R/.loci/…`
    reaches nothing at all. A change that REPLACED the raw reading with Win32's
    rather than adding it beside it loses this deny — one `main` has — and it
    stays green on Windows, where the fixture cannot even be built: Win32 strips
    the dot when the link is created, so there is no `R.` to walk through.

    Written because the mutation round said so.
    `runs/F18-2026-09-12/mutation_round1.txt` records exactly that mutant
    UNCAUGHT on Windows, which is the whole of why this test is here and is
    skipped there.
    """
    _guarded_tree(tmp_path)
    try:
        (tmp_path / "R.").symlink_to(tmp_path, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this host cannot create symlinks")

    spelling = f"R./.loci/{_PAD}contract.yaml"
    assert _writes_the_guarded_file(tmp_path, spelling, ".loci/contract.yaml"), (
        f"{spelling} no longer reaches the Contract Envelope on this host — "
        f"without that this test is about nothing")
    assert _denied(_run(_edit(f"{_to_bash_path(tmp_path)}/{spelling}"), tmp_path)), (
        f"{spelling} was allowed. The dot-stripped spelling is not the only "
        f"reading: on a host where a name may end in a dot, the RAW one is the "
        f"file, and route 1 has to decide both")


def test_the_dotted_reading_is_applied_to_the_root_too(tmp_path):
    """Both sides of the comparison, spelled by the same rules — or it is F16.

    The second reading takes a trailing dot off every component of the
    `file_path`. If it does not do the same to the project ROOT, the guarded
    path it is compared against is still the POSIX spelling, and a root with a
    dotted component then matches nothing: one reading on one side and the other
    on the other is not a comparison, it is two different questions.

    Reachable, and not only in theory: `root` falls back to the payload's own
    `cwd` when there is no `CLAUDE_PROJECT_DIR` and no git checkout, and the
    model writes that field. It is the same door
    `test_the_segment_bound_denies_on_the_guarded_side_too` uses.
    """
    _guarded_tree(tmp_path)
    root = _to_bash_path(tmp_path)
    payload = {"tool_name": "Edit", "cwd": f"{root}.",
               "tool_input": {
                   "file_path": f"{root}/.loci/{_PAD}contract.yaml",
                   "new_string": "x"}}
    assert _denied(_run(payload, tmp_path, env={"CLAUDE_PROJECT_DIR": ""})), (
        "a project root spelled with a trailing dot stopped matching its own "
        "guarded files — the dot strip is being applied to the file_path and "
        "not to the root it is compared against")


@pytest.mark.skipif(sys.platform != "win32", reason="the NTFS 8.3 short name")
def test_a_dotted_component_does_not_hide_the_short_name(tmp_path):
    r"""The `-ef` half of item 1, and the reason the strip is not only in the walk.

    `<root>/.loci/CONTRA~1.YAM` opens the Contract Envelope, needs no link and
    no third-party producer, and the only thing in route 1 that catches it is
    `-ef` — it carries no guarded basename, so the gate never admits it to the
    walk. F16 closed it. A trailing dot on the directory re-opened it, because
    MSYS's `stat` cannot see through the dot and `-ef` then answers false about
    a file Win32 opens.

    So the dot strip is applied to the `-ef` operand too, as a second spelling
    of the whole path rather than inside the walk. Measured with the write
    performed and the guarded file read back.

    ⚠ THE PAYLOAD HAS TO SAY `contract` SOMEWHERE ELSE, and that is a fact
    about this spelling rather than a convenience for the test. `CONTRA~1.YAM`
    matches none of the prefilter's arms, so a payload carrying nothing but
    this path exits ALLOW above route 1 and the guard never resolves anything —
    which is the same gap `test_a_symlink_that_hides_the_name_is_a_known_gap`
    records from the other side. What makes it reachable rather than academic
    is that the content of an edit to a contract-shaped file ordinarily does
    say the word; F16's own harness had to do this too.
    """
    _guarded_tree(tmp_path)
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "(New-Object -ComObject Scripting.FileSystemObject).GetFile("
         f"'{tmp_path / '.loci' / 'contract.yaml'}').ShortName"],
        capture_output=True, text=True, timeout=60)
    name = out.stdout.strip()
    if not name or name.lower() == "contract.yaml":
        pytest.skip("8.3 short names are disabled on this volume")
    spelling = f".loci./{name}"
    assert _writes_the_guarded_file(tmp_path, spelling, ".loci/contract.yaml"), (
        f"{spelling} no longer opens the Contract Envelope on this host")
    for root in (_to_bash_path(tmp_path), _native_path(tmp_path)):
        payload = {"tool_name": "Edit",
                   "tool_input": {"file_path": f"{root}/{spelling}",
                                  "new_string": "a contract change"}}
        assert _denied(_run(payload, tmp_path)), (
            f"{spelling} was allowed: `-ef` is deciding on the raw spelling "
            f"only, and it is the ONLY thing route 1 has for a name the "
            f"basename gate never admits")
    # …and the control for the paragraph above: without the token in the
    # payload this never reaches route 1 at all. If that ever changes, the
    # prefilter has widened and this test is no longer the narrow claim it says.
    assert _run(_edit(f"{_native_path(tmp_path)}/{spelling}"), tmp_path) is None, (
        "the prefilter now admits a payload that names no guarded token — "
        "read the ⚠ above: this test's reachability argument rests on it")


@pytest.mark.parametrize("rel", (".loci/contract.yaml",) + RECIPE_FILES)
def test_a_dot_dot_across_a_link_is_denied_on_either_reading(tmp_path, rel):
    r"""F18 item 2: Win32 applies `..` to the TEXT, before any I/O.

        L -> <root>/deep/sub
        <root>/L/../.loci/contract.yaml
            POSIX (cd -P, and realpath):  <root>/deep/.loci/contract.yaml
            Win32 (what actually opens):  <root>/.loci/contract.yaml

    F16 taught the walk to resolve the link BEFORE the `..`, which is right on
    macOS and Linux and is what closed F16 item 3 — and is the wrong reading on
    the platform whose writer is textual. `main` is wrong on both, so this is
    not a regression; it is the half F16 did not reach.

    The answer is to deny on EITHER reading, because the two genuinely name
    different files and nothing in a hook can know which one the tool that
    performs the write will open. On Windows that is asserted the only way it
    can be — the write is performed through the spelling and the guarded file is
    read back. On POSIX the same spelling reaches a different file and the deny
    is an over-deny, deliberate and the same one item 1 makes.

    ⚠ THE CONTROLS ARE PADDED AND THE HOLE IS NOT. `<root>/L/../.loci/
    contract.yaml` ends with a guarded name, so the suffix arm answers it on
    `main` too; only the padded spelling reaches the comparison, and that one
    `main` allows.
    """
    _guarded_tree(tmp_path)
    (tmp_path / "deep" / "sub").mkdir(parents=True, exist_ok=True)
    if not _make_dir_link(tmp_path / "L", tmp_path / "deep" / "sub"):
        pytest.skip("this host cannot create a directory link")
    head, _, base = rel.rpartition("/")

    hole = f"L/../{head}/{_PAD}{base}"
    if sys.platform == "win32":
        assert _writes_the_guarded_file(tmp_path, hole, rel), (
            f"{hole} no longer opens {rel} on this host")
    for root in (_to_bash_path(tmp_path), _native_path(tmp_path)):
        assert _denied(_run(_edit(f"{root}/{hole}"), tmp_path)), (
            f"{root}/{hole} is {rel} to Win32 and was allowed")

    # The control, and it has to be padded for the same reason the hole is:
    # neither reading reaches the guarded directory. POSIX resolves it to
    # <root>/deep/nope/.loci and Win32 to <root>/nope/.loci.
    safe = f"L/../nope/{head}/{_PAD}{base}"
    assert _run(_edit(f"{_native_path(tmp_path)}/{safe}"), tmp_path) is None, (
        f"{safe} is not {rel} on either reading — denying on either has become "
        f"denying on neither in particular")


def test_the_walk_carries_the_win32_reading_beside_the_posix_one(tmp_path):
    """`_w_desc` and `_w_phys` at the function, which no behavioural test reaches.

    The suffix arm answers every spelling a test naturally writes and the walk
    is a subshell inside a subshell, so this is the only place the two readings
    can be read side by side. `_w_desc$_w_tail` must be the path exactly as
    Win32 collapses it — `..` applied to the NAME — while the shell stands where
    POSIX says the path points.

    `_w_phys` is the gate on resolving that second reading at all, and it is
    CONSERVATIVE on purpose: set whenever a `..` was applied by CDing, whether
    or not it crossed a link. Narrowing it to "crossed a link" needs a `[ -L ]`
    per descended component — a stat on the verdict path, in the one function
    that is meant to have none — and it would put the correctness of the second
    reading on whether one host's `stat` reports a junction as a link. The cost
    of being conservative is one extra walk on a path with a `..`; the cost of
    being wrong is a guarded file.

    What it must NOT do is fire on a path whose two readings cannot differ: a
    `..` that lands in `_w_tail` is already collapsed textually, which IS
    Win32's rule, and a path with no `..` at all has one reading.
    """
    (tmp_path / "deep" / "sub").mkdir(parents=True)
    if not _make_dir_link(tmp_path / "L", tmp_path / "deep" / "sub"):
        pytest.skip("this host cannot create a directory link")
    script = (_walk_body() + chr(10) +
              '_w_home=$PWD\n'
              '_walk "$1"\n'
              'printf "%s|%s|%s" "$_w_desc$_w_tail" "$_w_tail" "${_w_phys:-.}"\n')
    path = tmp_path / "walkdesc.sh"
    path.write_text(script, encoding="utf-8", newline="\n")
    root = _to_bash_path(tmp_path)

    def walk(given: str) -> tuple[str, str, str]:
        out = subprocess.run([_find_bash(), _to_bash_path(path), given],
                             capture_output=True, text=True, timeout=30,
                             cwd=str(tmp_path))
        assert out.returncode == 0, out.stderr
        return tuple(out.stdout.split("|"))

    # Across the link: the two readings are different files, and that is the
    # whole of item 2.
    lex, tail, phys = walk(f"{root}/L/../x")
    assert lex == f"{root}/x", (
        f"the Win32 reading of L/../x is {lex!r}, want {root}/x — `_w_desc` "
        f"popped the directory the link points at instead of the link's name")
    assert phys == "1", "a `..` was applied by CDing and `_w_phys` says it was not"

    # A `..` that is never CD-ed is not one: the gate stays shut where every
    # `..` is collapsed in the tail, and where there is no `..` at all.
    for given in (f"{root}/nope/../x", f"{root}/deep/sub/x",
                  f"{root}/deep/./sub/x"):
        lex, tail, phys = walk(given)
        assert phys == ".", (
            f"_walk({given!r}) set `_w_phys`, so the second reading is resolved "
            f"for a path that has only one — the gate has stopped being a gate")

    # …and it IS set for a `..` that CDs and does not cross a link, because the
    # gate is conservative: `deep/sub/..` reads the same both ways and still
    # pays for the second walk. That is the trade in the docstring, and a
    # narrower gate is the thing not to write without reading it.
    for given, want in ((f"{root}/deep/sub/../x", f"{root}/deep/x"),
                        # …and one the tail hands BACK to the descent: the
                        # first `..` empties `_w_tail`, so the second is CD-ed
                        # like any other.
                        (f"{root}/nope/../../x", f"{root.rpartition('/')[0]}/x")):
        lex, tail, phys = walk(given)
        assert (lex, phys) == (want, "1"), (
            f"_walk({given!r}) = ({lex!r}, {phys!r}), want ({want!r}, '1')")


def test_win32_dots_takes_one_dot_off_a_component_and_nothing_else(tmp_path):
    """`_win32_dots` alone, against the rule it implements.

    Lifted out the way `_walk` is, because route 1 decides the ordinary
    spelling first and the arms answer before this one is reached. The table is
    Win32's, measured — `runs/F18-2026-09-12/probe_win32.py` in the follow-ups
    repo — and the three that are NOT about dots are the ones that broke the
    first draft: a relative path must not come back absolute, `.` and `..` are
    instructions rather than names, and `/../../` hands a single pass every
    other one.
    """
    src = GUARD.read_text(encoding="utf-8")
    body = src[src.index('_w_dots=""'):src.index(chr(10) + "# WHICH guarded file")]
    script = (body + chr(10) + '_win32_dots "$1"; printf "%s" "$_w_dots"\n')
    path = tmp_path / "dots.sh"
    path.write_text(script, encoding="utf-8", newline="\n")

    def dots(given: str) -> str:
        out = subprocess.run([_find_bash(), _to_bash_path(path), given],
                             capture_output=True, text=True, timeout=30)
        assert out.returncode == 0, out.stderr
        return out.stdout

    for given, want in [
        ("/r/.loci./contract.yaml", "/r/.loci/contract.yaml"),
        ("/r/.loci/build./flags.json", "/r/.loci/build/flags.json"),
        ("C:/r/a./b./c", "C:/r/a/b/c"),
        (".loci./contract.yaml", ".loci/contract.yaml"),      # relative, stays so
        ("./x", "./x"),                                       # NOT "/x"
        ("./.loci./x", "./.loci/x"),
        ("../x", "../x"),
        ("/r/../x", "/r/../x"),
        ("/r/../../x", "/r/../../x"),                         # the overlap
        ("/r/../../../x", "/r/../../../x"),
        ("/r/./././x", "/r/./././x"),
        ("/r/a../x", "/r/a./x"),          # ONE dot, and `a.` is then nothing
        # `...` is left alone, because the strip would turn a NAME into an
        # INSTRUCTION: for one round `<root>/x/.../.loci/contract.yaml` read as
        # `<root>/.loci/contract.yaml` and was denied, on a host where `...` is
        # an ordinary directory and the write goes elsewhere entirely. Four and
        # up need no escape — one dot off a run of dots is still a run of dots,
        # which is a name on both platforms and an invalid one on Windows.
        ("/r/.../x", "/r/.../x"),
        ("/r/..../x", "/r/.../x"),
        ("/r/....../x", "/r/...../x"),
        ("/r/a b./c", "/r/a b/c"),        # a space is not a separator
        ("/r/a /c", "/r/a /c"),           # …and is not stripped either
        ("/r/x/contract.yaml", "/r/x/contract.yaml"),         # untouched
        # The LAST component is interior too — a `$root` is joined to `/$g`
        # before it is used, and it is the one operand the end-of-string strip
        # at the call site never sees.
        ("/r/proj.", "/r/proj"),
        ("/r/proj./", "/r/proj/"),
        ("/r/..", "/r/.."),
        ("/r/.", "/r/."),
        ("/", "/"),
        ("C:/", "C:/"),
    ]:
        assert dots(given) == want, f"_win32_dots({given!r}) = {dots(given)!r}"

    # ⚠ AND NO BYTE IN THE PATH SWITCHES IT OFF. The escape is a DOT — `..`
    # becomes `...` and `.` becomes `..`, and the strip, which takes exactly one
    # dot off a component, is its own inverse — so there is no sentinel to
    # collide with. For one round there was: two control bytes stood in for `.`
    # and `..` and a path already carrying one was returned UNCHANGED, which was
    # a hole rather than a limitation. The behavioural half is
    # `test_win32_dots_is_not_switched_off_by_a_byte_in_the_path`.
    for byte in ("\x01", "\x02", "\x7f"):
        assert dots(f"/r/x{byte}/../.loci./c") == f"/r/x{byte}/../.loci/c", (
            f"a {byte!r} in the path changed what _win32_dots does to the rest "
            f"of it — a sentinel is back, and so is the hole it opens")


@pytest.mark.parametrize("rel", (".loci/contract.yaml",) + RECIPE_FILES)
@pytest.mark.parametrize("escape,what", [
    (r"", "a byte that was a mask sentinel for one round"),
    (r"", "the other one"),
])
def test_win32_dots_is_not_switched_off_by_a_byte_in_the_path(
        tmp_path, rel, escape, what):
    r"""Review round 1's CRITICAL, and the argument that let it in.

    `_win32_dots` used to hold `.` and `..` aside in two control bytes while it
    stripped, and to return a path that ALREADY carried one unchanged — which
    switched the whole Win32 reading off for that path. The justification was
    "Win32 forbids both bytes in a name, so on that platform the write cannot
    land", and it is false for the very reason F18 item 2 exists: **Win32
    collapses `..` textually, BEFORE it validates any component name.** The
    component carrying the byte is popped away and never reaches the
    filesystem, so everything after it opens normally:

        <root>/x/../.loci./contract.yaml   writes the Contract Envelope

    Measured on all three guarded files, ALLOWED by the first draft of this
    change. Reachable by the same route F15 established: no serializer escapes
    an ASCII letter, but `\uXXXX` for a CONTROL byte is what `JSON.stringify`
    produces for one, and the payload prefilter's fifth arm exists to admit it.

    `raw_json` is how the byte gets in at all — `json.dumps` would escape the
    backslash, and the point is the escape the DECODER sees.
    """
    _guarded_tree(tmp_path)
    head, _, base = rel.rpartition("/")
    root = _to_bash_path(tmp_path)
    payload = (r'{"tool_name":"Edit","tool_input":{"file_path":"'
               + f"{root}/x{escape}/../{head}./{base}"
               + r'","new_string":"a contract change"}}')
    assert _denied(_run({}, tmp_path, raw_json=payload)), (
        f"{what} switched the Win32 reading off for the whole path, so "
        f"{head}./{base} was compared as written and allowed")


@pytest.mark.parametrize("rel", (".loci/contract.yaml",) + RECIPE_FILES)
def test_an_alternate_data_stream_suffix_does_not_amputate_the_path(tmp_path, rel):
    r"""Review round 1's second CRITICAL, and it is older than this change.

    `fp="${fp%%::*}"` cut the `file_path` at the FIRST `::` and threw away
    everything after it — the guarded name included — so a `::` in an INTERIOR
    component blinded route 1 completely. Win32 does not need that component to
    be openable: it pops it textually with the `..` that follows, exactly as in
    item 2, and opens what is left.

        <root>/a::b/../.loci/contract.yaml   writes the Contract Envelope

    No escape, no dotted component, no symlink, no long path. ALLOWED on
    `main ca3c9a9` and by this change's first draft. The suffix is now cut from
    the LAST component only, which is the only place an ADS suffix can be and
    the only place the strip was ever for —
    `test_an_alternate_data_stream_spelling_is_denied` is that half.
    """
    _guarded_tree(tmp_path)
    root = _to_bash_path(tmp_path)
    for spelling in (f"{root}/a::b/../{rel}",
                     f"{root}/a::b/../{rel.rpartition('/')[0]}./"
                     f"{rel.rpartition('/')[2]}"):
        assert _denied(_run(_edit(spelling), tmp_path)), (
            f"{spelling} is {rel} to Win32 and was allowed — the ADS strip is "
            f"cutting the path rather than the last component's suffix")
    # The control: a single colon is not the ADS syntax and nothing is cut, so
    # this one has always been denied and must stay that way.
    assert _denied(_run(_edit(f"{root}/a:b/../{rel}"), tmp_path))


def test_both_readings_are_decided_inside_the_hook_budget(tmp_path):
    """The shape that asks for every pass F18 added, against the 5 s kill.

    One `..` applied by CDing (two readings) and a dot on every component (two
    spellings) is four walks of one `file_path` where F16 had one. That is what
    `_ROUTE1_MAX_SEGMENTS` was quartered for: at 4 096 this shape is 2.71 s
    against a 5 s `hooks.json` timeout that fails OPEN, and at 1 024 it is
    0.92 s — `main` is 0.85 s on the same payload.

    ⚠ IT HAS TO BE SIZED TO THE BOUND, NOT TO THE BYTE CAP, and the first
    version of this test was not: a 64 KB path is over the segment bound at
    4 096 and at 1 024 alike, so it is denied unwalked either way and the test
    was timing a short-circuit at both. The shape that costs is the LONGEST one
    the bound still walks, which is why the count is read from the hook.

    The structural assertion beside the clock is the one that actually holds
    the bound. A wall clock cannot: `test_a_path_too_long_to_walk_is_denied_
    rather_than_allowed` records a round where the bound was raised to a
    billion and the whole suite stayed green while the hook went past its
    timeout. The numbers live beside the constant and in
    `runs/F18-2026-09-12/worst_shapes_{fix,main}.txt`.
    """
    src = GUARD.read_text(encoding="utf-8")
    bound = int(re.search(r"^_ROUTE1_MAX_SEGMENTS=(\d+)$", src, re.M).group(1))
    assert bound <= 1024, (
        f"_ROUTE1_MAX_SEGMENTS is {bound}. F18 quartered it from 4 096 to pay "
        f"for a second reading of `..` and a second spelling of a trailing "
        f"dot: at 4 096 the worst shape is 2.71 s against the 5 s in "
        f"hooks.json, which fails OPEN, and at 1 024 it is 1.00 s — which is "
        f"what `main` costs on the worst shape IT allows. Raising it hands "
        f"that margin back")

    (tmp_path / ".loci").mkdir()
    (tmp_path / "deep").mkdir()
    root = _to_bash_path(tmp_path)
    unit = "abcdefghijkln./"
    # Three short of the bound: `deep`, `..` and `.loci` spend three, and one
    # segment past it is a short-circuit rather than the walk this is timing.
    n = bound - len([c for c in root.split("/") if c]) - 4
    assert n > 100, "tmp_path is too deep for this shape to reach the bound"
    worst = f"{root}/deep/../.loci/{unit * n}contract.yaml"
    assert len(worst) < 65536, "the field read would truncate this instead"
    start = time.monotonic()
    assert _run(_edit(worst), tmp_path) is None, (
        "this path is NOT a guarded file and both readings say so — a deny "
        "here means the walk short-circuited and the clock below is timing "
        "nothing")
    elapsed = time.monotonic() - start
    assert elapsed < 3.0, (
        f"the two readings took {elapsed:.1f}s on a {len(worst)}-byte, "
        f"{n}-component path, against the 5 s in hooks.json — past it the "
        f"hook is killed and PreToolUse fails open, which allows the write")


def test_the_decode_cap_is_a_recorded_allow(tmp_path):
    r"""F15 door 2, pinned rather than closed. Do not "fix" this on its own.

    `_loci_json_unicode` stops decoding once its work meter passes
    `_LOCI_JSON_UMAX` and hands the remaining escapes back as the six
    characters that were written, which route 1 reads as "matches no guarded
    file" — ALLOW. The meter is escapes x REMAINING length, so the escape count
    needed falls as the value grows, and a `file_path` padded with escaped `./`
    — which every resolver collapses back to the guarded file — trips it at
    about 1 600 escapes.

    LEFT AS IT IS, with F14 in hand, which is the decision F15 was told to
    make. F14 made `_loci_json_seek` linear but did not touch the unescaper,
    and that is still one slice of the remainder per escape, so raising the cap
    far enough to close this shape buys back the OTHER fail-open: past 5 s the
    hook is killed and PreToolUse allows the write. The two are not equally
    reachable — an ordinary paste reaches the wall clock (F13), while this
    needs a producer that escapes `.` and `/`, and neither `JSON.stringify` nor
    `json.dumps` does. Read the ⚠ at `_LOCI_JSON_UMAX` before moving the
    number.

    The pad that DENIES is the control: it says the decode still resolves this
    shape, so the ALLOW below is the cap and nothing else.

    ⚠ THE TIME THIS SHAPE COSTS IS NOT THE CAP. F15's own table recorded
    2.5 s at 800 pads and 25 s at 1 000 and attributed both to
    `_LOCI_JSON_UMAX`. The VERDICTS are the cap's — they are what this test
    pins — but the time is `realpath` resolving one path component per
    backslash in the escapes the cap hands back, which is F16. Timed per stage,
    the decode is flat across all three rows at 0.15 s ± 0.04. Attributing a
    cost to the wrong line is how a cap gets moved for no gain.
    """
    (tmp_path / ".loci").mkdir(parents=True)
    root = _to_bash_path(tmp_path)
    pad = _esc("./")
    assert _denied(_run({}, tmp_path,
                        raw_json=_raw_edit(f"{root}/.loci/{pad * 700}contract.yaml"))), (
        "700 escaped ./ no longer decode — this is the cap's control, and "
        "without it the ALLOW below proves nothing")
    assert _run({}, tmp_path,
                raw_json=_raw_edit(f"{root}/.loci/{pad * 800}contract.yaml")) is None, (
        "the cap no longer opens here — if `_LOCI_JSON_UMAX` was raised or the "
        "unescaper was made linear, F15 door 2 is closed: say so and delete "
        "this test")


@pytest.mark.parametrize("command", [
    # R7 — a `#` inside a COMPOUND TOKEN. Bash reads `(( … ))`, `$(( … ))`,
    # `[[ … ]]` and an extglob as ONE token and has no comments inside any of
    # them; the `#` arm decides on the byte before it and cannot see that, so
    # it invents a comment and drops the invocation after it. Six characters of
    # prefix in the cheapest spelling.
    "((#)) ; loci contract accept",
    "((count++ # bump)) ; loci contract accept",
    "for ((i=0 #z; i<0; i++)); do :; done ; loci contract accept",
    "diff $((a&#c)) | loci contract accept",
    "[[ $f =~ ^(//|#) ]] ; loci contract accept",
    "shopt -s extglob\nls @(a|#z) ; loci contract accept",
    # …and the mirror image: after a FUNCTION DEFINITION's `()` bash does start
    # a comment, and the arm reads it as text, so the apostrophe in it hides
    # what follows. `<(`, `>(`, `=(` and `@(` are all correct; only `f()` is not.
    "f()#don't\n{ :; }\nloci contract accept",
])
def test_a_compound_token_is_not_a_comment(tmp_path, command):
    """This test asserts a decision, not an oversight. Do not "fix" it.

    Every command here RUNS a contract-writing verb, every one is allowed, and
    `bb4a547` denied them all by matching text. They are residual R7.

    Recorded rather than chased for the reason the header gives: no choice of
    BYTES fixes it. The word-boundary set was measured with a sweep over all
    100 printable bytes and the sweep was right — at top level. The exceptions
    are contextual, and closing them means teaching the scan `((`, `$((`, `[[`
    and extglob. That arm produced a critical in each of the last three review
    rounds, and the round that found this family is the one that also found the
    quadratic in its terminator; adding four more constructs to it without a
    review round left is the move this file's history says not to make.

    The `#` arm still earns its place: without it an apostrophe in ANY comment
    hides every later line, which is ordinary English rather than a contrived
    token. R7 is the price of that, and it is written down.
    """
    assert _run(_bash(command), tmp_path) is None, f"{command!r} must be allowed"



def test_an_unquoted_mention_is_the_stated_residual(tmp_path):
    """The honest stopping point for a forkless lexical guard, pinned.

    The guard is handed one flat command string and cannot know where a heredoc
    body begins — ``cat <<'EOF'`` quotes the DELIMITER, not the lines after it —
    so an UNQUOTED line of prose that contains the invocation is read as the
    invocation and denied. That is wider than "a line that starts with it": the
    ``loci`` token may sit anywhere in the line before ``contract <verb>``,
    because the binary is looked for along the segment rather than demanded
    first, which is what keeps every wrapper spelling denied. The second row is
    the most natural spelling of the very handoff note this route exists to
    permit, and it is refused; the header used to claim a narrower residual than
    that, and this is where the claim is pinned instead of asserted.

    Quote it and it is allowed, which is what every documentation case above
    does, and what the note that provoked this task did
    (``echo 'run: ! loci contract accept' >> …``). Writing the file with the
    Write tool is allowed too: route 1 guards three paths, not every file.

    Do not chase this with a heredoc parser. The guard's own history says
    parsing is where its defects come from.
    """
    head = "cat <<'EOF' > handoff.md\n"
    unquoted = f"{head}To apply, run: loci contract accept\nEOF"
    mid_line = f"{head}then run loci contract accept yourself\nEOF"
    quoted = f"{head}To apply, run: 'loci contract accept'\nEOF"
    assert _denied(_run(_bash(unquoted), tmp_path)), "the residual is gone; restate it"
    assert _denied(_run(_bash(mid_line), tmp_path)), (
        "the residual is narrower than the header says — restate the header")
    assert _run(_bash(quoted), tmp_path) is None
    # An EMPTY quoted string is not quoting: the shell drops it and the word is
    # `loci`, so this is the same unquoted mention and lands the same way. 0.2.3
    # allowed it, by the accident of counting quotes per token rather than
    # reading them; it is recorded here because that is a behaviour change.
    assert _denied(_run(_bash("echo ''loci contract accept"), tmp_path))
    # …and a SUBSTITUTION between the mention and the verb is the same residual
    # reached a third way, which is the one behaviour change F11 makes in the
    # over-deny direction. Until F11 the `$(` cut this line into `echo loci $`
    # and ` contract accept`, so the mention escaped the rule by accident; the
    # lift joins the word back together and the documented rule applies to it.
    # Nothing here runs — `loci` is an argument of `echo` — and that is exactly
    # what the residual says: this guard cannot tell an argument from a binary
    # without becoming the parser its own history forbids. Quote it and it is
    # allowed, the same as every row above.
    assert _denied(_run(_bash("echo loci $(date) contract accept"), tmp_path))
    assert _run(_bash("echo 'loci $(date) contract accept'"), tmp_path) is None


# ── route 2's matcher, driven directly ───────────────────────────────────────
#
# The payload-level tests above are the behaviour. These two drive the matcher
# itself, for the reason
# `test_the_walk_descends_what_exists_and_collapses_what_does_not` drives
# `_walk`: the guard is fail-open, so a matcher that silently answers "no" to
# everything looks exactly like a guard with nothing to deny.

def _matcher_script(tmp_path: Path, extra: str) -> str:
    """The matcher, extracted into a runnable script FILE.

    A file, not ``bash -c``: the two parse a backslash in a ``case`` pattern
    differently, so a matcher driven through ``-c`` can answer differently from
    the same source run the way the hook is run. That cost an hour on
    2026-09-09 — the harness was wrong and the guard was right.
    """
    src = GUARD.read_text(encoding="utf-8")
    # From the first constant route 2 owns, so the extract carries every one of
    # them: `_R2_MAX_SEGMENTS` was defined further up for one round, and with it
    # unset every case in the table below answered "allow" — a harness that
    # cannot fail, hiding behind a suite that looked green.
    start = src.index("_R2_MAX_SEGMENTS=")
    end = src.index('\nif [ -n "$cmd" ]; then')
    path = tmp_path / "matcher.sh"
    path.write_text(f"{src[start:end]}\n{extra}", encoding="utf-8", newline="\n")
    return _to_bash_path(path)


MATCHER_CASES = [
    # (command, is an invocation)
    ("loci contract accept", True),
    ("loci contract init --force", True),
    ("LOCI_A=1 LOCI_B=2 loci contract accept", True),      # two assignments
    ("loci -f json contract accept", True),                 # global flag between
    ("make build && loci contract init", True),
    ("(loci contract edit --index 0)", True),
    ("time loci contract disable --index 0", True),
    ("! loci contract accept", True),
    ("if true; then loci contract enable; fi", True),
    ("/opt/loci/bin/loci contract accept", True),
    ("C:\\bin\\loci.exe contract accept", True),
    ("a\nloci contract accept", True),                      # newline separates
    ("loci contract draft edit --index 0", False),          # the important pair
    ("loci contract show", False),
    ("loci contract", False),                               # no subcommand
    ("loci contract acceptance-test", False),               # not the token
    ("git commit -m \"run loci contract accept\"", False),
    ("echo 'loci contract accept' > note.md", False),
    ("notloci contract accept", False),                     # not the binary
    ("./loci-wrapper contract accept", False),              # nor is this
    ("$LOCI contract accept", False),                       # nor is a variable
    ("loci \"contract\" accept", False),                    # recorded evasion
    ("V=accept; loci contract $V", False),                  # recorded evasion
    ("uv run loci contract accept", True),                  # the wrapper family
    ("timeout 30 loci contract accept", True),
    ("env -i loci contract accept", True),
    (">log.txt loci contract accept", True),
    ("loci --format contract contract accept", True),       # keep scanning
    ("a\tloci contract accept", True),                      # a tab is not a separator
    ('loci -f "json" contract accept', True),               # a quoted ARGUMENT
    ('sudo -u "$CI_USER" loci contract accept', True),
    ('echo "/usr/bin/loci contract accept now"', False),    # …vs a quoted SENTENCE
    # One argv element, and nothing in it runs. `bb4a547` denied it and 0.2.3
    # denied it; removing the quoted text before the split is what makes it
    # allowed, and the masking is right — this is a mention.
    ("loci --help \"x contract accept y\"", False),
    # The scan, at the level the payload tests cannot reach: a substitution is
    # command context inside `"…"`, literal inside `'…'`, and the state comes
    # back to the right place when it closes.
    ('echo "$(loci contract accept)"', True),
    ("echo '$(loci contract accept)'", False),
    ('echo "$(echo "hi") loci contract accept"', False),    # closes back INTO "…"
    ('echo "$(echo "hi") $(loci contract accept)"', True),
    ("echo 'a\\'; loci contract accept", True),             # `\` is literal in '…'
    ('echo "a\\"; loci contract accept"', False),           # …but escapes in "…"
    ("MSG=$'a\\'b' loci contract accept", True),            # $'…' DOES escape
    ("echo $'a\\'b loci contract accept'", False),
    # A HALF-quoted verb runs and is allowed, the same recorded evasion as
    # `loci contract "accept"`: the quoted characters are removed, so the word
    # arrives as `acc`. Removal keeps it ONE word — blanking to spaces would
    # hand the split two — but nothing here unquotes, by decision.
    ("loci contract acc\"ept\"", False),
    ("C:\\Users\\dev\\loci.exe contract accept", True),     # backslashes survive
    ("LOCI contract accept", True),                         # the binary is caseless
    ("loci contract ACCEPT", False),                        # the verb is not
]


def test_the_matcher_reads_tokens_not_text(tmp_path):
    """The table IS the specification of what counts as an invocation."""
    bash = _find_bash()
    script = _matcher_script(
        tmp_path,
        '_command_invokes_verb "$1" && printf "deny\\n" || printf "allow\\n"')
    got = []
    for command, _want in MATCHER_CASES:
        out = subprocess.run([bash, script, command],
                             capture_output=True, text=True, timeout=30)
        assert out.returncode == 0, f"{command!r}: {out.stderr}"
        got.append(out.stdout.strip())
    wrong = [(c, w, g) for (c, w), g in zip(MATCHER_CASES, got)
             if g != ("deny" if w else "allow")]
    assert not wrong, "\n".join(
        f"{c!r}: wanted {'deny' if w else 'allow'}, got {g}" for c, w, g in wrong)


def _shell_quote_state(token: str):
    """Where POSIX/bash quoting stands after consuming `token`.

    The reference the scan is measured against. ``None`` means the token cannot
    stand alone — it ends in a backslash, which would escape the delimiter that
    ends the word rather than anything inside it.
    """
    i, n, state = 0, len(token), ""
    while i < n:
        c = token[i]
        if state == "":
            if c == "\\":
                if i + 1 >= n:
                    return None
                i += 2
                continue
            if c in "'\"":
                state = c
            i += 1
        elif state == "'":
            # The whole of defects 2 and 3: nothing is special in here but the
            # closing quote — a backslash is literal and a `"` is text.
            if c == "'":
                state = ""
            i += 1
        else:
            if c == "\\":
                if i + 1 >= n:
                    return None
                i += 2 if token[i + 1] in '$`"\\\n' else 1
                continue
            if c == '"':
                state = ""
            i += 1
    return state


def test_the_quote_scan_agrees_with_the_shell(tmp_path):
    """Every token of length ≤ 6 over ``{' " \\ a}``, against the real rules.

    This is the test the three F10 defects would have failed, and it is here
    because they were not one-off spellings: the per-token quote counter they
    came from disagreed with the shell on 302 of these tokens — 151 of them by
    calling a region OPEN that the shell had closed, which is the direction that
    hides a `loci`. Seven hand-written rows in the deny corpus above cannot say
    that; 4 695 generated ones can.

    Its alphabet is deliberately narrow, so read what it does NOT cover:
    ``test_the_scan_masks_what_the_rules_say`` is the one that carries the
    backtick, ``$``, ``(`` and ``)``, and the review round that found this
    test's blind spot found two criticals inside it.

    Disagreeing toward "unquoted" is allowed and only over-denies (prose read as
    a command). Disagreeing the other way is a bypass, and there must be none.
    """
    bash = _find_bash()
    alphabet = ["'", '"', "\\", "a"]
    tokens = ["".join(p) for length in range(1, 7)
              for p in itertools.product(alphabet, repeat=length)]
    listing = tmp_path / "tokens.txt"
    listing.write_text("\n".join(tokens) + "\n", encoding="utf-8", newline="\n")
    script = _matcher_script(
        tmp_path,
        'while IFS= read -r tok; do\n'
        '    _r2_mask "$tok" || { printf "CAP\\n"; continue; }\n'
        '    printf "%s\\n" "${_r2_st:-.}"\n'
        'done < "$1"\n')
    out = subprocess.run([bash, script, _to_bash_path(listing)],
                         capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr
    got = out.stdout.splitlines()
    assert len(got) == len(tokens), f"{len(got)} answers for {len(tokens)} tokens"

    bypass, overdeny, checked = [], [], 0
    for token, answer in zip(tokens, got):
        want = _shell_quote_state(token)
        if want is None:
            continue
        checked += 1
        scanned = "" if answer == "." else answer
        if scanned == want:
            continue
        (overdeny if scanned == "" else bypass).append((token, scanned, want))

    assert checked > 4_000, f"the generator produced only {checked} usable tokens"
    assert not bypass, "the scan holds a region OPEN that the shell has closed — " \
        "every `loci` after one of these stops being a command word:\n" + "\n".join(
            f"  {t!r}: scan={s!r} shell={w!r}" for t, s, w in bypass[:20])
    # Not required to be empty — over-denying is the safe direction — but it is
    # empty today, and a red here is worth reading before it is relaxed.
    assert not overdeny, "\n".join(
        f"  {t!r}: scan={s!r} shell={w!r}" for t, s, w in overdeny[:20])


_MASK_ESC_OUT = set("'\"\\`$()")
_MASK_ESC_DQ = set('"\\`$\n')
#: What, passed over, leaves a place where a WORD can begin. Read by the `#`
#: arm and by nothing else. `|`, `<` and `>` are deliberately absent — see the
#: hook.
_MASK_WORD_BOUNDARY = set(" \t\n;&|")


def _reference_mask(command: str) -> str:
    """What `_r2_mask` should produce, re-derived from the rules it documents.

    Returns the two streams JOINED the way `_command_invokes_verb` joins them —
    the outer command, a newline, then the substitution bodies — because that
    string is what the verdict is read out of and comparing only half of it
    would leave the half F11 added unpinned.

    **This is a re-derivation of the RULES, not a model of bash**, and the
    difference is the whole of residual R4: where an unmatched `)` ends a
    `$( … )` early, this model ends it early too, so it agrees with the hook and
    both are wrong about the shell. A review round proved that by handing it
    ``echo "(" ; echo "$(case $x in *) loci contract accept;; esac)"`` and
    getting the hook's own answer back. Do not read a green here as "the scan
    matches the shell".

    What it does catch is the hook not doing what the hook SAYS it does, which
    is how four of the eight criticals across rounds 1-4 would have been caught:
    a plain `(` pushing no frame, a backtick failing to close from inside `'…'`,
    and both halves of the `#` word-boundary defect. Whether the rules are right about bash is what the
    corpus differ and the argv oracle in `runs/F10-2026-09-09/` answer, and they
    are not a unit test.
    """
    out, state, stack = [], "", []
    # The substitution bodies, and where in `out` each OPEN frame began — one
    # entry per frame of `stack` and in the same order, because the backtick
    # close pops several frames at once and needs the first of them. A plain `(`
    # records one it never uses, to keep the two aligned.
    #
    # EVERY frame lifts. A nested `$( … )` is a command inside a word of its
    # parent, which is the same sentence one level down, and its lift removes
    # its body from the parent so the parent's word closes over the hole. The
    # first version of this model lifted only the outermost, matching a hook
    # that did the same, and both were wrong the same way — `$(x$(I))` glued the
    # inner body to the `x` and `xloci` is not the binary.
    subs: list[str] = []
    offs: list[int] = []

    def joined(chunks):
        return "".join(chunks)

    def push_off():
        offs.append(len(joined(out)))

    def lift(at):
        nonlocal out
        whole = joined(out)
        subs.append(whole[at:])
        out = [whole[:at]]

    word_can_begin = True
    i, n = 0, len(command)
    while i < n:
        j = i
        while j < n and command[j] not in "\\'\"`$()#":
            j += 1
        head = command[i:j]
        if head:
            if state == "":
                out.append(head)
            word_can_begin = head[-1] in _MASK_WORD_BOUNDARY
        if j >= n:
            break
        c, i = command[j], j + 1
        nxt = command[i] if i < n else ""

        if c == "`" and any(f in "bB" for f in stack):
            while stack[-1] not in "bB":
                stack.pop(); offs.pop()
            state = "" if stack.pop() == "b" else '"'
            word_can_begin = False
            # Whatever those frames held is ONE backtick region, so one body
            # comes out here and not one per frame — lifted from the offset of
            # the backtick's OWN frame, which is the outermost of the ones being
            # discarded.
            lift(offs.pop())
        elif state == "'":
            if c == "'":
                state, word_can_begin = "", False
        elif state == "A":
            if c == "'":
                state, word_can_begin = "", False
            elif c == "\\":
                i += 1
        elif state == '"':
            if c == '"':
                state, word_can_begin = "", False
            elif c == "`":
                push_off()
                stack.append("B"); state = ""; word_can_begin = True
            elif c == "$" and nxt == "(":
                push_off()
                stack.append("P"); state = ""; word_can_begin = True
                i += 1
            elif c == "\\" and nxt in _MASK_ESC_DQ:
                i += 1
        elif c == "'":
            state, word_can_begin = "'", False
        elif c == '"':
            state, word_can_begin = '"', False
        elif c == "#":
            if word_can_begin:
                ends = "\n" + ("`" if any(f in "bB" for f in stack) else "")
                while i < n:
                    start = i
                    while i < n and command[i] not in ends:
                        i += 1
                    if i < n and command[i] == "`":
                        # Only the last 64 bytes are classified, because the
                        # hook's `${x##*[!\\]}` is quadratic in the run and is
                        # bounded for it. A run of 64 or more is taken as
                        # UNESCAPED — the comment ends, which emits more text.
                        tail = command[start:i][-64:]
                        run = len(tail) - len(tail.rstrip("\\"))
                        if run < 64 and run % 2 == 1:
                            i += 1
                            continue
                    break
                word_can_begin = True
            else:
                out.append("#"); word_can_begin = False
        elif c == "`":
            push_off()
            stack.append("b"); state = ""; word_can_begin = True
        elif c == "(":
            # A `(` that BEGINS a word is a subshell — a command boundary, so it
            # is written and cut at. One that CONTINUES a word is `<(`, `>(` or
            # a `=(` array assignment: a word's contents, exactly like
            # `$( … )`, so it is lifted and writes nothing.
            push_off()
            stack.append("s" if word_can_begin else "S")
            word_can_begin = True
            if stack[-1] == "s":
                out.append("(")
        elif c == ")":
            # A `)` that ends a SUBSTITUTION writes nothing and lifts the body
            # out; one that ends a subshell, or that matches nothing, is
            # ordinary text. A subshell IS a command boundary; a substitution is
            # a word's contents.
            if stack and stack[-1] == "s":
                stack.pop(); offs.pop(); word_can_begin = True; out.append(")")
            elif stack and stack[-1] == "S":
                stack.pop(); word_can_begin = False; lift(offs.pop())
            elif stack and stack[-1] == "p":
                stack.pop(); state = ""; word_can_begin = False
                lift(offs.pop())
            elif stack and stack[-1] == "P":
                stack.pop(); state = '"'; word_can_begin = False
                lift(offs.pop())
            else:
                word_can_begin = True; out.append(")")
        elif c == "$":
            if nxt == "(":
                push_off()
                stack.append("p"); word_can_begin = True; i += 1
            elif nxt == "'":
                state, word_can_begin = "A", False; i += 1
            elif nxt == '"':
                state, word_can_begin = '"', False; i += 1
            else:
                word_can_begin = False; out.append("$")
        elif c == "\\":
            if nxt == "\n":
                # A line continuation joins the lines and leaves the word
                # boundary exactly as it found it.
                i += 1
            elif nxt in _MASK_ESC_OUT:
                out.append(nxt); word_can_begin = False; i += 1
            elif nxt == "":
                out.append("\\"); word_can_begin = False
            else:
                out.append("\\" + nxt); word_can_begin = False; i += 1
    return joined(out) + "\n" + "".join("\n" + b for b in subs)


def test_the_scan_masks_what_the_rules_say(tmp_path):
    """Every token of length ≤ 5 over ``{' " \\ ` $ ( ) a}``, masks compared.

    The narrow test above compares one bit — the quote state at the end of a
    token — over an alphabet with no substitution characters in it. Round 1
    found two criticals hiding in exactly that gap, and both left the final
    quote state correct: a plain `(` pushed no frame, so a subshell's `)` popped
    the `$(` frame and hid the rest of the substitution; and a backtick did not
    close its region from inside `'…'`, so one apostrophe in a backticked
    command hid everything after it. This one compares the whole masked copy
    over the alphabet that contains them.

    `\\003` is in the alphabet because the value boundary is a state reset the
    narrow test cannot reach either, and round 2 found the model had no arm for
    it at all. `#`, a SPACE and a NEWLINE joined it after round 4, whose two
    criticals were both in the `#` arm and both invisible here: without a `#`
    there is nothing to comment, and without whitespace or a newline there is
    no word boundary to get wrong and no place for a comment to end. Eight of
    nine mutations of that arm survived the suite before they were added.

    Driven NUL-delimited in both directions, because a token and a mask can now
    both contain a newline.

    A SECOND alphabet joined it after F11's review round 1, and the reason is
    the sharpest coverage lesson this test has had. The round's critical was a
    nested substitution whose body was glued to its parent's text, and this
    comparison could not see it **because no token in the corpus could spell
    one**: the shortest nested close is six bytes (`$($())`), the generator
    stops at four, and of 16 104 tokens exactly two reach a nested close at all
    — both unterminated. A differential test over a corpus that cannot express
    the property it is meant to protect reports green forever. So `{$ ( ) ` a}`
    is swept to length 6 as well, which is where `$($())` and `` `$()` `` live.

    Both STREAMS are compared, joined the way `_command_invokes_verb` joins
    them. F11 split the mask in two — the outer command and the substitution
    bodies it lifted out of it — and a comparison of `_r2_masked` alone would
    pass with every body silently dropped, which is a guard that denies nothing
    inside a substitution.

    Read `_reference_mask`'s docstring for what this does and does not prove.
    """
    bash = _find_bash()
    alphabet = ["'", '"', "\\", "`", "$", "(", ")", "#", " ", "\n", "a"]
    tokens = ["".join(p) for length in range(1, 5)
              for p in itertools.product(alphabet, repeat=length)]
    # …and the substitution alphabet alone, far enough to CLOSE a nested frame
    # (six bytes) and then to put GLUE beside one (seven), which is the exact
    # shape round 1's critical was: `$(x$(I))`. The backtick drops out at seven
    # only to keep the count down — a backtick region and a `$( … )` are the
    # same frame kind to this code, and both are swept at five and six.
    tokens += ["".join(p) for length in (5, 6)
               for p in itertools.product("`$()a", repeat=length)]
    tokens += ["".join(p) for p in itertools.product("$()a", repeat=7)]
    # Non-vacuity, because the whole point of the second alphabet is that the
    # first one cannot reach these and nothing said so for a review round.
    for spelling in ("$($())", "`$()`", "$(`a`)", "$(a$())", "$($(a))"):
        assert spelling in tokens, f"the corpus cannot spell {spelling!r}"
    listing = tmp_path / "mask_tokens.bin"
    listing.write_bytes(b"".join(tok.encode("utf-8") + b"\0" for tok in tokens))
    script = _matcher_script(
        tmp_path,
        'while IFS= read -r -d "" tok; do\n'
        '    if _r2_mask "$tok"; then\n'
        '        printf "M%s\\0" "$_r2_masked"$'"'"'\\n'"'"'"$_r2_subs"\n'
        '    else printf "B\\0"; fi\n'
        'done < "$1"\n')
    out = subprocess.run([bash, script, _to_bash_path(listing)],
                         capture_output=True, timeout=300)
    assert out.returncode == 0, out.stderr[:2000]
    # STDERR, not only the exit status, and the reason is `_r2_offs`. The offset
    # stack is read with `${_r2_offs: -6}` and `$(( 10#… ))`, so a stack that
    # came un-aligned from `_r2_stack` — one frame pushing no offset, one pop
    # discarding none — reads an empty slice and bash says
    # "10#: invalid arithmetic base" on stderr and CARRIES ON with 0. The mask
    # would then be wrong and this test would have passed. Nothing here may
    # write to stderr at all.
    assert not out.stderr, (
        "the scan wrote to stderr, which is how an un-aligned offset stack "
        f"shows up: {out.stderr[:2000]!r}")
    got = out.stdout.split(b"\0")[:-1]
    assert len(got) == len(tokens), (
        f"{len(got)} answers for {len(tokens)} tokens — the NUL framing slipped")

    wrong = []
    for token, raw in zip(tokens, got):
        answer = raw.decode("utf-8")
        want = _reference_mask(token)
        # No token this short can reach the scan's work cap, so every one of
        # them must come back with a mask rather than a "give up".
        if answer == "B":
            wrong.append((token, "gave up", want))
        elif answer[1:] != want:
            wrong.append((token, answer[1:], want))

    assert len(tokens) > 15_000, f"the generator produced only {len(tokens)} tokens"
    assert not wrong, "the scan and the rules it documents disagree:\n" + "\n".join(
        f"  {t!r}: scan={a!r} rules={w!r}" for t, a, w in wrong[:20])


# (command, the outer stream, the substitution bodies)
#
# Read the third column as "each body on its own line, newline first". The
# empty string means the command has no substitution in it at all.
LIFT_CASES = [
    # The four R5 spellings, as the mask sees them: the outer word closes over
    # the hole the substitution left, and the body is a command of its own.
    ("loci --root $(pwd) contract accept",
     "loci --root  contract accept", "\npwd"),
    ("loci -C `pwd` contract accept",
     "loci -C  contract accept", "\npwd"),
    ('loci --project "$(pwd)" contract accept',
     "loci --project  contract accept", "\npwd"),
    # `$(( … ))` is a `$(` frame with a plain `(` inside it, so the arithmetic
    # comes out as a body and the parens come with it.
    ("loci -n $((1+1)) contract accept",
     "loci -n  contract accept", "\n(1+1)"),
    # NESTING, and it is the same sentence one level down: a nested `$( … )` is
    # a command inside a word of its PARENT, so it is lifted out of the parent
    # the way the parent is lifted out of the outer command, and the parent's
    # word closes over the hole. `a$(b$(c)d)e` is the shape in miniature — `c`
    # is a command of its own, `bd` is the word its parent was left holding, and
    # `ae` is the outer word.
    #
    # The first version of this lifted only the OUTERMOST frame, and these three
    # rows are what it got wrong: the inner body was glued to whatever touched
    # it inside the parent, so `$(x$(loci contract accept))` masked to the body
    # `xloci contract accept` — and `xloci` is not the binary, so the verb ran
    # and was allowed. Six characters of prefix, found by review round 1. Do not
    # go back to it; the argument that lifting each frame "cuts the parent body"
    # is true of writing a MARKER into the parent and is the opposite of what a
    # lift does.
    ("$(loci --root $(pwd) contract accept)",
     "", "\npwd\nloci --root  contract accept"),
    ("loci --root $(echo $(pwd)) contract accept",
     "loci --root  contract accept", "\npwd\necho "),
    ("a$(b$(c)d)e", "ae", "\nc\nbd"),
    # …and the glue itself, which is what the outermost-only rule destroyed.
    ("$(x$(loci contract accept))", "", "\nloci contract accept\nx"),
    ("$($(loci contract accept)x)", "", "\nloci contract accept\nx"),
    ("`x$(loci contract accept)`", "", "\nloci contract accept\nx"),
    ("$(x`loci contract accept`)", "", "\nloci contract accept\nx"),
    # A backtick region closes from any depth, and whatever those frames held is
    # ONE region: one body comes out, not one per frame. The `'` inside it does
    # open a region, so the `t` after it is quoted text and is dropped like any
    # other — the region ends at the backtick whatever state it is in, which is
    # F10's round-1 critical and the reason this row is here rather than a
    # tidier one.
    ("echo `a(don't`; loci contract accept",
     "echo ; loci contract accept", "\nadon"),
    # A plain `(` is a SUBSHELL — a real command boundary — so it is not lifted
    # and both parens stay in the outer stream where the split can read them.
    ("(loci contract accept)", "(loci contract accept)", ""),
    ("cat <(echo a)#x; loci contract accept",
     "cat <#x; loci contract accept", "\necho a"),
    # …including one INSIDE a substitution, where it is part of that body.
    ('echo "$( (true) && loci contract accept )"',
     "echo ", "\n (true) && loci contract accept "),
    # A `)` matching nothing is ordinary text, as it always was.
    ("echo a) b", "echo a) b", ""),
    # Quoted text is still removed, and a substitution inside `"…"` is still a
    # command: the body is lifted, the prose around it is dropped.
    ('echo "$(date) loci contract accept"', "echo ", "\ndate"),
    ('echo "$(loci contract accept)"', "echo ", "\nloci contract accept"),
    # An empty body still gets a line of its own, or two bodies would join into
    # an invocation neither of them is.
    ("$(loci contract)$( accept)", "", "\nloci contract\n accept"),
    # An ESCAPED backtick is text the shell keeps inside the word. It reaches
    # the outer stream, which is why the separator set may not contain one.
    ("loci --root a\\`b contract accept",
     "loci --root a`b contract accept", ""),
    # An unterminated region is never lifted: its body stays in the outer stream
    # joined to the word it opened in. Nothing runs — bash calls it a syntax
    # error — and leaving it joined is the deny direction. It is also the second
    # of F11's two over-deny families, and it is here rather than only in the
    # evidence file because it is the one a reader will hit:
    # `loci --root $(pwd contract accept` is ALLOWED at `43ed7a9`, DENIED here,
    # and runs nothing on either. `bb4a547` denied it too, so this is a return
    # to the older answer rather than a new invention.
    ("echo $(pwd; loci contract accept",
     "echo pwd; loci contract accept", ""),
    ("loci --root $(pwd contract accept",
     "loci --root pwd contract accept", ""),
    # A command with no substitution in it must come back untouched, second
    # stream empty. Without this row every case above passes with the lift
    # applied to everything.
    ("loci contract accept", "loci contract accept", ""),
    ("make build && loci contract accept", "make build && loci contract accept", ""),
    # A PROCESS SUBSTITUTION is a substitution in a word too, so it is lifted
    # and writes no paren — while a real SUBSHELL is a command boundary and goes
    # on writing both. That one distinction is the rest of R5: until F11's
    # review round 2, `loci --root <(pwd) contract accept` masked to
    # `loci --root <` / `pwd` / ` contract accept`, none an invocation, and it
    # RAN. The `s`/`S` frames already knew which was which, for the `#` arm's
    # sake; nothing used it for the lift.
    ("loci --root <(pwd) contract accept",
     "loci --root < contract accept", "\npwd"),
    ("cat > >(tee log)", "cat > >", "\ntee log"),
    ("x=(a b)", "x=", "\na b"),
    # …and the subshell control, which must NOT lift. (The row above at the top
    # of this table has it too; this one is here because the `S` arm is the
    # nearest neighbour and the two are one edit apart.)
    ("(cd /x && make)", "(cd /x && make)", ""),
]


def test_a_substitution_is_lifted_out_of_the_word_it_sits_in(tmp_path):
    """Residual R5, closed: the table IS the specification of the two streams.

    `_r2_mask` produces a copy of the command with quoted text removed, and
    since F11 it produces TWO: `_r2_masked`, the outer command, and `_r2_subs`,
    the substitution bodies it lifted out of it. The payload tests above say
    what the verdict is; this one says what the mask is, for the reason
    `test_the_matcher_reads_tokens_not_text` exists — a verdict can be right for
    the wrong reason, and every defect this scan has had was a mask that looked
    plausible.

    The property, in one sentence: a `$( … )` or a backtick region is a command
    inside a WORD of another command, so its body must be segmented and the word
    around it must not be split. Writing a `$(` into the mask and calling it a
    separator got the first half right and the second half wrong —
    ``loci --root $(pwd) contract accept`` became three segments of which none
    was an invocation, and it RAN.

    Two rows are worth more than the rest. The nested ones fail the design this
    task was filed with — a flat pair of accumulators, every body appended to
    one stream as it opens and closes — because a nested `$(` then writes its
    boundary into the middle of its PARENT's body and cuts the invocation there.
    And the last two fail anything that lifts unconditionally.
    """
    bash = _find_bash()
    listing = tmp_path / "lift_cases.bin"
    listing.write_bytes(b"".join(c.encode("utf-8") + b"\0" for c, _m, _s in LIFT_CASES))
    script = _matcher_script(
        tmp_path,
        'while IFS= read -r -d "" c; do\n'
        '    _r2_mask "$c" || { printf "B\\0B\\0"; continue; }\n'
        '    printf "M%s\\0S%s\\0" "$_r2_masked" "$_r2_subs"\n'
        'done < "$1"\n')
    out = subprocess.run([bash, script, _to_bash_path(listing)],
                         capture_output=True, timeout=120)
    assert out.returncode == 0, out.stderr[:2000]
    got = out.stdout.split(b"\0")[:-1]
    assert len(got) == 2 * len(LIFT_CASES), (
        f"{len(got)} answers for {2 * len(LIFT_CASES)} — the NUL framing slipped")
    wrong = []
    for i, (command, masked, subs) in enumerate(LIFT_CASES):
        gm = got[2 * i].decode("utf-8")[1:]
        gs = got[2 * i + 1].decode("utf-8")[1:]
        if (gm, gs) != (masked, subs):
            wrong.append((command, (gm, gs), (masked, subs)))
    assert not wrong, "the scan does not split the streams the way it says:\n" + "\n".join(
        f"  {c!r}\n    got  masked={g[0]!r} subs={g[1]!r}\n    want masked={w[0]!r} subs={w[1]!r}"
        for c, g, w in wrong)


def test_the_lift_charges_itself_against_the_work_cap(tmp_path):
    """The lift's cost is invisible to the per-jump charge, so it bills its own.

    `_r2_lift` copies the mask twice per substitution, each O(the mask so far),
    while `_R2_MAX_SCAN` is charged the REMAINING length per jump — so a
    substitution near the END of a long command is charged almost nothing and
    costs a full copy of everything before it. 3 464 empty backtick pairs after
    58 KB of text, all inside the byte cap and inside the work cap as it was,
    took the hook to 2.63 s against the 5 s in `hooks.json`, past which it is
    KILLED and PreToolUse FAILS OPEN. Found by F11's review round 1.

    Asserted as a RETURN CODE and not as a clock, for the reason
    `test_a_padded_path_is_decided_inside_the_hook_budget` is a cautionary tale:
    a budget test that times a short-circuit measures nothing. rc 2 is
    `_command_invokes_verb` saying "over a cap, ask the coarse answer", which is
    the whole point — over the cap the answer is blunter but it arrives. With
    the charge deleted the scan finishes and answers rc 0, and **nothing else in
    this suite notices**: the guard file was byte-identical, 499 passed, under a
    mutation that removed it. This test is the only thing holding it.

    The second row is the control, and it says the charge is not too LARGE: the
    same 58 KB with a hundred substitutions after it — far more than any real
    command carries — must still get route 2's precise answer. Over the cap the
    verdict falls to `_R2_OVERSIZE_RE`, which matches the phrase with no binary
    token at all, so a charge that sends ordinary commands there costs the
    mask's own catches and denies prose. Putting the substitutions at the FRONT
    instead is NOT a control: 3 464 of them there is ~415 M of per-jump charge
    before the lift is counted at all, so that shape was over the cap at
    `43ed7a9` too and proves nothing about this line.
    """
    bash = _find_bash()
    # Read the command from a FILE, not from argv: these shapes are ~64 KB and
    # Windows refuses a command line that long with WinError 206.
    script = _matcher_script(
        tmp_path,
        'IFS= read -r -d "" c < "$1"\n'
        '_command_invokes_verb "$c"; printf "%s\\n" "$?"')

    def rc(command: str) -> str:
        blob = tmp_path / "charge.bin"
        blob.write_bytes(command.encode("utf-8") + b"\0")
        out = subprocess.run([bash, script, _to_bash_path(blob)],
                             capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, out.stderr[:2000]
        return out.stdout.strip()

    # 1 000 pairs, and the COUNT is load-bearing — the first version of this
    # test used 3 464, the number round 1 measured the 2.63 s at, and it was
    # VACUOUS: at that count the per-jump charge alone reaches the cap, so the
    # answer is rc 2 with or without the lift's own charge. Two scan characters
    # per pair, each billed the remaining length, is ~4 x pairs² of per-jump
    # charge, against pairs x 58 500 for the lift — so only a count between
    # ~410 (where the lift's charge first reaches 24 M) and ~2 450 (where the
    # per-jump charge does) can tell the two guards apart. Measured across
    # 50…3 464: they diverge from 800 to 3 000 and agree at both ends.
    verb = "loci contract accept"
    dense = "echo " + "a" * 58_500 + " " + "``" * 1_000 + f"; {verb}"
    ordinary = "echo " + "a" * 58_500 + " " + "``" * 100 + f"; {verb}"
    assert len(dense) < 65_536, "the shape has to fit under the byte cap"

    assert rc(dense) == "2", (
        "the substitution-dense shape finished the scan, so the lift is not "
        "charging itself — it costs a copy of the whole mask per close and the "
        "per-jump charge cannot see it. Restore the `_r2_work` line in "
        "`_r2_lift`.")
    assert rc(ordinary) == "0", (
        "a hundred substitutions after 58 KB went over the cap, so the charge "
        "is too large: that is well inside what a real command carries, and "
        "over the cap the verdict falls to a regex that matches the phrase "
        "with no binary token")


def test_the_separator_set_carries_no_backtick():
    """A structural pin, because the behaviour it protects is one spelling wide.

    The backtick left `_R2_SEPARATORS` when the mask stopped writing one: the
    only backtick that can still reach a stream is an ESCAPED one, which the
    shell keeps inside the word, so splitting at it can only ever cut a word
    bash does not cut. That is a HOLE and not an over-deny —
    ``loci --root a\\`b contract accept`` runs the verb and was cut into
    ``loci --root a`` and ``b contract accept``, neither an invocation — and the
    deny corpus carries the spelling.

    It is pinned structurally as well because putting the backtick back is a
    one-character edit that reads like tidying, and the single corpus row that
    would catch it needs a backslash, a backtick and a verb in the same command
    to fail. `(` and `)` must STAY: the mask still writes both for a subshell,
    which is a real command boundary.
    """
    line = [ln for ln in GUARD.read_text(encoding="utf-8").splitlines()
            if ln.startswith("_R2_SEPARATORS=")]
    assert len(line) == 1, f"expected one definition, found {line}"
    assert "`" not in line[0], (
        f"the backtick is back in the separator set: {line[0]}\n"
        "An escaped backtick is the only one the mask emits, and the shell "
        "keeps it inside the word — splitting there re-opens R5's last member.")
    for paren in "()":
        assert paren in line[0], (
            f"{paren!r} left the separator set: {line[0]}\n"
            "A plain paren is a subshell, and the mask still writes it.")


def _locale_with_multibyte_lengths(tmp_path) -> str | None:
    """A locale this host has where bash's `${#var}` is not a byte count.

    That is the property `LC_ALL=C` exists to defeat: the scan charges itself
    per `${#}` unit while bash pays per byte, so a multibyte fill buys more
    work than it is charged for. Without such a locale the shapes below cannot
    tell the two apart and there is nothing to measure.

    The test is "fewer than the four bytes of the probe character", not
    "exactly one": MSYS bash counts UTF-16 units, so an astral character is 2
    there and 1 on Linux. Either way the scan is undercharged, which is what
    matters — asking for 1 skipped the whole test on Windows, which is the
    platform the defect was found on.
    """
    script = tmp_path / "mb.sh"
    script.write_text('v=$(printf "\\xf0\\x9f\\x98\\x80")\n'
                      '[ "${#v}" -lt 4 ] && echo multibyte\n',
                      encoding="utf-8", newline="\n")
    for name in ("en_US.UTF-8", "C.UTF-8", "C.utf8", "en_US.utf8"):
        out = subprocess.run([_find_bash(), _to_bash_path(script)],
                             capture_output=True, text=True, timeout=30,
                             env={"PATH": _base_path(), "LC_ALL": name})
        if out.stdout.strip() == "multibyte":
            return name
    return None


BACKTICK = chr(96)

_DENSE_SHAPES = {
    # (label, command). All three are under the byte cap and under any
    # plausible COUNT cap, and each pays in a different way.
    "quotes at the front":
        "echo " + '""' * 3_000 + " " + "a" * 50_000 + "; loci contract accept",
    "dollars at the front":
        "echo " + "$" * 3_000 + " " + "a" * 50_000 + "; loci contract accept",
    "parens at the end":
        "a" * 53_800 + " " + "(" * 5_600 + " ; loci contract accept",
    # The multibyte one, and it is the reason `LC_ALL=C` is at the top of the
    # hook. 4-byte fill, so a scan that charges CHARACTERS while bash walks
    # BYTES under-charges itself fourfold. Measured on this shape: 0.65 s as
    # shipped, 6.57 s with that line removed — past the 5 s in `hooks.json`,
    # where the hook is killed and PreToolUse fails OPEN and the write goes
    # through. The three ASCII rows above cannot see that, which is how the
    # first version of this test stayed green through the defect.
    "4-byte fill":
        "echo '" + "\U0001F600(" * 4_500 + "' ; loci contract accept",
    # A trailing backslash RUN inside a comment inside a backtick region. The
    # comment terminator has to know whether that backtick is escaped, and the
    # obvious way to ask — `${x##*[!\\]}` — is quadratic in the run: 25 ms at
    # 1 k, 1.07 s at 16 k, 5.2 s at 32 k measured standalone. Each backslash
    # costs one byte while the work cap is charged the REMAINING length, so
    # 32 600 of them sat under every cap and took the hook past its 5 s
    # timeout: rc 124, empty stdout, PreToolUse fails OPEN. Only the last 64
    # bytes are classified now — 3.96 s to 0.13 s, flat.
    "backslash run in a comment":
        "echo " + BACKTICK + "x #" + "\\" * 32_600 + BACKTICK
        + " ; loci contract accept",
}


@pytest.mark.parametrize("shape", sorted(_DENSE_SHAPES))
def test_a_command_dense_with_scan_characters_is_decided_inside_the_budget(
        tmp_path, shape):
    """Neither bytes nor the COUNT of scan characters bounds the work — the
    product does, and it is a product of BYTES.

    Each jump costs one pass over what is left of the command, so the time is
    scan characters × remaining length. Crowding them at either end of a 56 KB
    command is under every other cap and is what pays the most.

    These run in a MULTIBYTE locale, which no other timing test here does, and
    that is the point twice over: every harness in this repo hands the hook a
    minimal env, so every measurement in this file was taken in the C locale,
    where bash's parameter expansion is about twice as fast as in the UTF-8 a
    real session has — and a multibyte command costs four times what a
    character count charges for it.

    The locale is PROBED, not named. Git Bash here has `en_US.UTF-8` and not
    `C.UTF-8`; WSL has the reverse, so naming one pinned `LC_ALL=C` on a single
    platform and the mutation matrix said so.

    Over the cap the coarse answer takes over, and it still denies these: each
    one really does end in the invocation.
    """
    locale = _locale_with_multibyte_lengths(tmp_path)
    if locale is None:
        pytest.skip("no multibyte locale on this host")
    command = _DENSE_SHAPES[shape]
    assert len(command.encode("utf-8")) < 65_536, len(command.encode("utf-8"))
    start = time.monotonic()
    decision = _run(_bash(command), tmp_path,
                    env={"LANG": locale, "LC_ALL": locale})
    elapsed = time.monotonic() - start
    assert _denied(decision), "the fallback did not answer"
    assert elapsed < 5.0, (
        f"the guard took {elapsed:.1f}s on {shape}, against the 5 s timeout in "
        f"hooks.json — past it the hook is killed and PreToolUse fails open")


def test_each_cap_is_where_it_says_it_is(tmp_path):
    """Every cap must CHANGE the answer, or its number is decoration.

    Round 2 mutated `_R2_MAX_SCAN` from 16 M to 48 M and to 100 000,
    `_R2_MAX_SEGMENTS` from 512 to 100 000, and `_R2_MAX_TOKENISE` from 65 536
    to 100 000 000, and the whole suite stayed green through all of it. A cap
    nothing pins is a number the next reader is free to get wrong in either
    direction — too high and the hook is killed and fails open, too low and
    ordinary prose goes to a regex that matches the phrase with no binary.

    The pair is the assertion. The same quoted MENTION is allowed under each cap
    (the mask reads it and sees no command word) and denied over it (the coarse
    regex reads the text), so each row proves the cap sits between its two
    sizes — the direction a mutation in either direction breaks.
    """
    quoted = "echo 'see loci contract accept' >> n.md"
    for label, under, over in [
        # bytes: _R2_MAX_TOKENISE = 65536
        ("byte cap",
         f"{quoted} # {'x' * 60_000}",
         f"{quoted} # {'x' * 70_000}"),
        # segments: _R2_MAX_SEGMENTS = 512
        ("segment cap",
         quoted + "; echo y" * 400,
         quoted + "; echo y" * 600),
        # scan work: _R2_MAX_SCAN = 24000000, charged as the remaining length
        # per jump. The "over" row is deliberately just over — ~1.2x, not the
        # ~7x it was — because a 7x overshoot still trips a cap raised to 48 M,
        # which is the value round 2 removed for blowing the budget.
        ("scan cap",
         "echo " + '""' * 200 + " " + "a" * 40_000 + f"; {quoted}",
         "echo " + '""' * 380 + " " + "a" * 40_000 + f"; {quoted}"),
    ]:
        assert len(over) < 200_000, label
        assert _run(_bash(under), tmp_path) is None, (
            f"{label}: a quoted mention UNDER the cap is denied — the cap is "
            f"too low, and ordinary prose is reaching the coarse regex")
        assert _denied(_run(_bash(over), tmp_path)), (
            f"{label}: a command OVER the cap still went to the tokeniser — "
            f"the cap is too high, or it has stopped being read")


@pytest.mark.parametrize("verb", ["accept", "init", "edit", "disable", "enable"])
def test_the_coarse_path_denies_every_verb(tmp_path, verb):
    """`_R2_OVERSIZE_RE` carries its own copy of the verb list.

    Only `accept` was ever exercised over a cap, so dropping `init` from that
    regex changed nothing any test could see — and the two lists are edited in
    different places, which is exactly how they drift apart.
    """
    command = f"cat <<'EOF' > V.sol\n{'x' * 70_000}\nEOF\nloci contract {verb}"
    assert _denied(_run(_bash(command), tmp_path)), (
        f"the coarse path does not know about `contract {verb}`")


def test_the_coarse_path_reads_a_line_continuation(tmp_path):
    """Reaching the coarse answer must not also be a way to spell the verb.

    Every cap on this route hands the command to `_R2_OVERSIZE_RE`, and each one
    is reachable by choosing the input: bytes, segments, and now the scan's work
    budget — 9.8 KB of punctuation is enough for the last. So the coarse test has
    to read every spelling the tokenising path reads, and it did not read one:
    `loci contract \\<newline>accept` is a line continuation that `_r2_mask`
    folds away and the regex had no arm for, so the pair "trip a cap, then use a
    continuation" ran the verb and was allowed. `bb4a547` allowed it too, and it
    is reachable there and on 0.2.3 through the SEGMENT cap.
    """
    tail = "loci contract \\\naccept"
    # One row per cap, and each was checked to reach the cap it names — the
    # first spelling of the middle one was `";" * 600`, which reaches none:
    # runs of `;` become runs of newlines and IFS-whitespace splitting drops
    # the empties, so it is two segments, not 601. A row that cannot fail is
    # worse than no row.
    for label, noise in (("scan cap", "$" * 9_800),
                         ("segment cap", "; echo y " * 600),
                         ("byte cap", "a" * 70_000)):
        command = f"echo x {noise} ; {tail}"
        assert _denied(_run(_bash(command), tmp_path)), (
            f"the verb walked through the coarse path behind the {label}")


def test_the_matcher_leaves_the_shell_as_it_found_it(tmp_path):
    # It sets `IFS` and `set -f` to split into segments and words. Both are
    # global, and route 2 is the last thing to run — but route 1's path walk had
    # this same test written for it after the same mistake, and the two
    # functions sit in the same script.
    bash = _find_bash()
    script = _matcher_script(
        tmp_path,
        'before_ifs=$(printf %s "$IFS" | od -An -c | tr -d " \\n")\n'
        'before_f=$-\n'
        '_command_invokes_verb "make build && loci contract accept" || true\n'
        'after_ifs=$(printf %s "$IFS" | od -An -c | tr -d " \\n")\n'
        '[ "$before_ifs" = "$after_ifs" ] || { echo "IFS changed"; exit 1; }\n'
        'case "$-" in *f*) echo "noglob left on"; exit 1 ;; esac\n'
        'case "$before_f" in *f*) echo "noglob was on before"; exit 1 ;; esac\n'
        'echo ok\n')
    out = subprocess.run([bash, script], capture_output=True, text=True, timeout=30)
    assert out.stdout.strip() == "ok", f"{out.stdout}{out.stderr}"


def test_the_matcher_does_not_glob_a_word_it_is_splitting(tmp_path):
    """`set -f` around the splits, asserted where its absence would show.

    Without it, a command carrying `*` is expanded against the CWD while the
    guard is deciding: the word `loci` can be produced out of nowhere by a
    filename, and a real invocation can be destroyed by one. Both directions are
    the same bug and neither is visible in an empty directory.
    """
    work = tmp_path / "cwd"
    work.mkdir()
    (work / "loci").write_text("", encoding="utf-8")
    bash = _find_bash()
    script = _matcher_script(
        tmp_path,
        '_command_invokes_verb "$1" && printf "deny\\n" || printf "allow\\n"')
    # `echo * contract accept` in a directory whose only file is named `loci`:
    # with pathname expansion on, the `*` BECOMES the binary token and the
    # command is denied for a word no one wrote.
    for command, want in [("echo * contract accept", "allow"),
                          ("loci contract accept *", "deny")]:
        out = subprocess.run([bash, script, command],
                             capture_output=True, text=True, timeout=30,
                             cwd=str(work))
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == want, f"{command!r} -> {out.stdout!r}"


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
    # Route 2 reads the `command` FIELD here too, so the spellings that need a
    # segment boundary have to survive the extraction: an env prefix, a chain, a
    # newline (JSON-escaped in the payload text, so nothing in the raw bytes
    # separates the two commands), and a `\"` before the verb (which a naive cut
    # at the first quote would truncate the command at).
    assert _denied(_run(_bash("LOCI_LOG_LEVEL=DEBUG loci contract accept"),
                        tmp_path, env=no_jq))
    assert _denied(_run(_bash("make build && loci contract init"), tmp_path, env=no_jq))
    assert _denied(_run(_bash("make build\nloci contract edit --index 0"),
                        tmp_path, env=no_jq))
    assert _denied(_run(_bash('cd "/my dir" && loci contract accept'),
                        tmp_path, env=no_jq))
    assert _denied(_run(_bash("/usr/local/bin/loci contract enable --index 0"),
                        tmp_path, env=no_jq))


def test_no_jq_unescapes_before_it_reads_quotes(tmp_path, no_jq):
    """The order of the unescapes is the whole of this, and it was wrong once.

    Without jq the guard undoes JSON's escapes itself. Done naively — `\\n` and
    `\\t` before `\\\\` — a Windows path is destroyed: `C:\\\\tools\\\\loci.exe`
    came back as `C:\\ ools\\\\loci.exe`, the first token stopped being a `loci`
    binary, and every developer whose CLI lives under a directory starting with
    `t` was unguarded on every jq-less host. `\\r` was not unescaped at all, so
    the CR handling the matcher advertises did nothing here and a CRLF-authored
    two-line command walked through.
    """
    assert _denied(_run(_bash("C:\\tools\\loci.exe contract accept"),
                        tmp_path, env=no_jq)), "an escaped backslash ate the path"
    assert _denied(_run(_bash("C:\\next\\loci.exe contract init"),
                        tmp_path, env=no_jq))
    assert _denied(_run(_bash("loci contract accept\r\nmake test"),
                        tmp_path, env=no_jq)), "the CR was never unescaped"
    # …and a TAB has to become whitespace, or the whole invocation arrives as
    # one token and matches no binary. Reached by no test until round 5, so a
    # mutation deleting the pass was invisible — and it is a rung disagreement
    # as well as a bypass, because jq hands back a real tab either way.
    assert _denied(_run(_bash("loci\tcontract accept"), tmp_path, env=no_jq)), (
        "a tab in the command was never unescaped")
    assert _denied(_run(_bash("loci\tcontract\taccept"), tmp_path, env=no_jq))



def test_no_jq_decides_a_large_command_inside_the_hook_budget(tmp_path, no_jq):
    """`hooks.json` gives this hook 5 s, and PreToolUse is fail-open.

    The first version of this change extracted the `command` field by walking it
    to its closing quote, one `${rest#"$head"\\"}` per escaped quote. That is
    quadratic: 0.22 s at 16 KB, 3.5 s at 64 KB, 54 s at 256 KB, and end to end a
    94 KB command took 8.2 s — past the timeout, so the hook was killed and the
    deny was LOST. The version it replaced took 0.08 s on the same payload, so
    this was a regression that a large `cat <<EOF` could trigger on purpose.

    The bound is the hook's real budget. Read a red here as a finding: it means
    the payload handling has stopped being linear.
    """
    body = "\n".join(f'  emit("line {i}", \\"x\\");' for i in range(4000))
    command = f"cat <<'EOF' > Vault.sol\n{body}\nEOF\nloci contract accept"
    assert len(command) > 90_000, len(command)
    start = time.monotonic()
    decision = _run(_bash(command), tmp_path, env=no_jq)
    elapsed = time.monotonic() - start
    assert _denied(decision), "a large payload walked the verb past the guard"
    assert elapsed < 5.0, (
        f"the guard took {elapsed:.1f}s on a {len(command) // 1024} KB command, "
        f"against the 5 s timeout in hooks.json — past it the hook is killed and "
        f"fails open")


def test_no_jq_leaves_an_edit_or_write_to_route_1(tmp_path, no_jq):
    """Route 2 must not read a payload that has no command in it.

    Without jq the guard cannot isolate a field, so it unescapes the payload and
    lets JSON's own quotes end a segment. Run on an `Edit`/`Write`, that turns
    the `\\n` escapes in the file's CONTENT into command separators — and a
    HANDOFF.md whose text names the verb was denied here while being allowed on
    every host with jq. The `"command"` gate is what keeps route 2 off those
    payloads; `bb4a547` had it, and it was dropped by accident when the field
    extractor was replaced.

    This closes the class, not one case: JSON escapes a quote inside a string
    value, so a file's CONTENT can never forge the literal `"command"` the gate
    matches on — a `.mcp.json`, a fenced JSON block and a Python dict literal
    were all tried. What it does not close, and never did, is a `Bash` payload's
    own sibling fields; see `test_no_jq_reads_a_whole_bash_payload_like_the_base`.
    """
    for payload in (
        {"tool_name": "Write",
         "tool_input": {"file_path": "HANDOFF.md",
                        "content": "run loci contract accept\n"}},
        {"tool_name": "Edit",
         "tool_input": {"file_path": "src/main.c",
                        "new_string": "/* the user runs loci contract accept */\n"}},
    ):
        assert _run(payload, tmp_path, env=no_jq) is None, payload["tool_name"]
        assert _run(payload, tmp_path) is None, f"{payload['tool_name']} with jq"


def test_no_jq_reads_a_whole_bash_payload_like_the_base(tmp_path, no_jq):
    """The rung difference is closed, not merely recorded.

    Without jq the guard used to be unable to isolate one field, so route 2 saw
    the whole transformed payload — and a `Bash` payload carries `description`,
    `cwd` and `transcript_path` beside the command. A model-authored
    `description` naming the verb therefore DENIED a command that does not, on
    that rung only, and this test asserted that over-breadth as an inherited
    trade.

    The fix that was rejected then was quadratic: walking the JSON value to its
    closing quote cost 3.5 s at 64 KB and 54 s at 256 KB against a 5 s budget.
    `lib/loci_json.sh` does it in a bounded, linear pass instead, so both rungs
    now read the same field and return the same verdict. The assertions are kept
    pointed at the same payload, in the opposite direction.
    """
    payload = {"tool_name": "Bash",
               "tool_input": {"command": "loci contract draft edit --index 0",
                              "description": "then run loci contract accept"}}
    assert _run(payload, tmp_path) is None, "the command is a draft verb, not a write"
    assert _run(payload, tmp_path, env=no_jq) is None, (
        "with jq absent the guard must still read the COMMAND field — denying "
        "this means the degraded rung is back to matching payload text")

    # …and it must still decide. The same host, the same route, a real verb.
    real = {"tool_name": "Bash",
            "tool_input": {"command": "loci contract accept",
                           "description": "apply the drafted bound"}}
    assert _denied(_run(real, tmp_path, env=no_jq)), (
        "the forkless rung has stopped deciding altogether")

def test_no_jq_allows_a_command_that_only_documents_the_verb(tmp_path, no_jq):
    """The degraded branch decides on the field, not on the payload text.

    Route 2 used to be handed the WHOLE payload on a jq-less host, which is the
    same "decide on the text, not on the field" mistake route 1 was fixed for
    one round earlier. With a token matcher it is not merely over-broad, it is
    the wrong input: the payload's own JSON punctuation is not a command, so the
    guard would either have stopped denying anything at all here — a silent
    fail-open — or denied every mention of the verb. Both directions are tested,
    here and in `test_no_jq_still_denies_the_verbs`.
    """
    assert _run(_bash('git commit -m "the user runs loci contract accept"'),
                tmp_path, env=no_jq) is None
    assert _run(_bash("echo 'run: ! loci contract accept' >> handoff.md"),
                tmp_path, env=no_jq) is None


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


@pytest.mark.parametrize("rel", RECIPE_FILES)
def test_no_jq_still_denies_the_recipe_and_the_pin(tmp_path, rel, no_jq):
    # Same degradation as contract.yaml's: `file_path` is extracted with sed, so
    # route 1 still decides on the field. Without this the two new files would be
    # writable on every jq-less host — the one state where nobody is watching.
    assert _denied(_run(_edit(rel), tmp_path, env=no_jq)), rel
    assert _denied(_run(_write(rel), tmp_path, env=no_jq)), rel


def test_no_jq_does_not_deny_a_source_file_that_merely_names_the_recipe(tmp_path, no_jq):
    # The prefilter now matches `build.yaml` anywhere in the payload, so a source
    # file whose *content* names the recipe reaches route 1 on a jq-less host for
    # the first time. It must still be decided on the extracted `file_path`.
    payload = {
        "tool_name": "Edit",
        "tool_input": {
            "file_path": "src/main.c",
            "new_string": "/* flags come from .loci/build.yaml, not flags.json */",
        },
    }
    assert _run(payload, tmp_path, env=no_jq) is None


@pytest.mark.parametrize("rel", (".loci/contract.yaml",) + RECIPE_FILES)
def test_a_minimal_path_without_cat_does_not_fail_open(tmp_path, rel):
    """The guard must read its payload with a builtin, not with `cat`.

    Its own comment says "Hook PATH is often minimal" — and the PATH repair that
    exists for that sits forty lines below where the payload is read. With `cat`
    off PATH the guard read an EMPTY payload, the prefilter exited 0, and every
    write to every guarded file was allowed. Silent and total: a fail-open hook
    prints nothing, so the only thing that notices is a test like this one.
    """
    if sys.platform == "win32":
        pytest.skip("PATH shadowing is not reliable on Windows")
    stub = tmp_path / "no-cat-bin"
    stub.mkdir()
    for name in ["bash", "sed", "tr", "git", "jq", "realpath"]:
        real = shutil.which(name)
        if real is not None:
            (stub / name).symlink_to(real)
    assert shutil.which("cat", path=str(stub)) is None
    env = {"PATH": str(stub), "HOME": str(tmp_path / "home")}
    assert _denied(_run(_edit(rel), tmp_path, env=env)), (
        f"{rel} was writable on a PATH with no `cat` — the guard fails open")


MINIMAL_TOOLS = ("tr", "sed", "realpath", "git", "cat", "printf", "grep", "awk")


def _stub_path(tmp_path, keep):
    """A PATH holding only `keep`, with the guard's own repair dirs verified empty.

    The guard appends `/usr/local/bin`, `/opt/homebrew/bin` and `$HOME/.local/bin`
    to whatever PATH it is given, so a stub directory is not on its own proof that
    a binary is unreachable — on a host where `/usr/local/bin` carries coreutils
    this test would silently stop testing its own claim.
    """
    stub = tmp_path / ("only-" + "-".join(keep))
    stub.mkdir()
    for name in keep:
        real = shutil.which(name)
        if real is None:
            pytest.skip(f"{name} not available")
        (stub / name).symlink_to(real)
    home = tmp_path / "home"
    effective = [str(stub), "/usr/local/bin", "/opt/homebrew/bin",
                 str(home / ".local" / "bin")]
    for gone in MINIMAL_TOOLS:
        if gone in keep:
            continue
        found = shutil.which(gone, path=os.pathsep.join(effective))
        if found:
            pytest.skip(f"{gone} is reachable at {found} after the PATH repair")
    return {"PATH": str(stub), "HOME": str(home)}


@pytest.mark.parametrize("keep", [("bash", "jq"), ("bash",)])
def test_neither_route_forks_its_way_to_a_verdict(tmp_path, keep):
    """No external binary may sit between the payload and either verdict.

    Route 1's version of this was fixed once already, by taking `tr` out of the
    path comparison. Route 2 kept `printf | tr | sed` to normalise the command,
    so with either binary missing `norm` collapsed to a single space and every
    contract-writing verb was allowed — the same silent fail-open, in the other
    half of the same guard, shipped in the same commit that fixed the first.

    `("bash",)` alone is not a corner case: Git for Windows puts `Git\\cmd` on
    PATH and `Git\\usr\\bin` only optionally, so `jq`, `sed`, `tr` and `cat` are
    all absent together on a stock install — and the guard's PATH repair adds
    POSIX-shaped directories that cannot supply them.
    """
    if sys.platform == "win32":
        pytest.skip("PATH shadowing is not reliable on Windows")
    env = _stub_path(tmp_path, keep)
    for rel in (".loci/contract.yaml",) + RECIPE_FILES:
        assert _denied(_run(_edit(rel), tmp_path, env=env)), (
            f"route 1 allowed {rel} with PATH={keep}")
    assert _denied(_run(_bash("loci contract accept"), tmp_path, env=env)), (
        f"route 2 allowed a contract-writing verb with PATH={keep}")
    # …and it must still not over-deny when it is running blind.
    assert _run(_edit("src/main.c"), tmp_path, env=env) is None
    assert _run(_bash("loci contract show"), tmp_path, env=env) is None


@pytest.mark.parametrize("rel", (".loci/contract.yaml",) + RECIPE_FILES)
def test_the_normalisation_needs_no_host_tool_at_all(tmp_path, rel):
    """A `..` the suffix rule cannot see, decided with nothing but bash.

    Written when the normalisation was rung 3 of a ladder — reachable only when
    `realpath` could not answer, which is every `Write` on stock macOS. The
    suite tested no-realpath (with spellings the suffix rule catches) and `..`
    normalisation (with realpath present) but never their intersection, so
    replacing the rung with `_lex="$1"` left the whole suite green while these
    four spellings went from DENY to ALLOW.

    F16 deleted the ladder: `_walk` is the only rung and it forks nothing, so
    this state is no longer special — which is the point. What it now pins is
    that the verdict does not depend on a binary being installed, the same
    property `test_the_guard_is_not_writable_with_a_minimal_path` pins for the
    plain spellings, for a `..` that only the walk can answer.
    """
    if sys.platform == "win32":
        pytest.skip("PATH shadowing is not reliable on Windows")
    env = _stub_path(tmp_path, ("bash", "jq"))
    head, _, base = rel.rpartition("/")
    path = f"{_to_bash_path(tmp_path)}/{head}/zz/../{base}"
    assert not path.endswith(f"/{rel}"), "the suffix rule would catch this"
    assert _denied(_run(_edit(path), tmp_path, env=env)), (
        f"{path} was allowed with no realpath — `_lexical` is not wired in")


@pytest.fixture
def no_realpath(tmp_path):
    """Env whose PATH has the guard's tools but no `realpath`.

    This is not exotic: it is stock macOS before 12.3, and effectively stock
    after it too — that `realpath` is the FreeBSD one, which has no `-m` and
    fails on a path that does not exist, so every `Write` fell through the same
    way. Homebrew installs `grealpath`, and the guard appends `/opt/homebrew/bin`
    AFTER `$PATH`, so the system binary wins anyway.

    It was the state where the realpath branch did nothing and the shell rung
    answered alone — which is where F16 item 3 lived, and why three of that
    task's four holes were live on every Mac. The branch is gone and the shell
    is what answers everywhere now, so this fixture pins that taking the binary
    away changes NOTHING, rather than which rung is reached.
    """
    if sys.platform == "win32":
        pytest.skip("PATH shadowing is not reliable on Windows")
    stub = tmp_path / "no-realpath-bin"
    stub.mkdir()
    for name in ["bash", "sed", "tr", "git", "cat", "jq", "printf", "pwd"]:
        real = shutil.which(name)
        if real is not None:
            (stub / name).symlink_to(real)
    assert shutil.which("realpath", path=str(stub)) is None
    return {"PATH": str(stub), "HOME": str(tmp_path / "home")}


@pytest.mark.parametrize("rel", (".loci/contract.yaml",) + RECIPE_FILES)
def test_without_realpath_the_guard_still_denies(tmp_path, rel, no_realpath):
    # The suffix fallback carries the plain spellings, and `_walk` — `cd -P` per
    # component, the same mechanism post-edit-hook.sh uses — carries the ones
    # that need normalising. Since F16 there is no other rung to fall back to,
    # so this asserts the ordinary path rather than a degraded one.
    assert _denied(_run(_edit(rel), tmp_path, env=no_realpath)), rel
    assert _denied(_run(_write(rel), tmp_path, env=no_realpath)), rel
    abs_path = f"{_to_bash_path(tmp_path)}/{rel}"
    assert _denied(_run(_edit(abs_path), tmp_path, env=no_realpath)), abs_path


def test_no_jq_leaves_drafting_alone(tmp_path, no_jq):
    assert _run(_bash("echo '{}' | loci contract draft add"), tmp_path, env=no_jq) is None
    assert _run(_bash("loci contract draft edit --index 0"), tmp_path, env=no_jq) is None


# ── the prefilter ────────────────────────────────────────────────────────────

def test_payload_without_the_subject_exits_before_forking(tmp_path):
    # This hook runs on every Bash call in every repo the plugin is installed
    # for. A payload without the literal `contract` cannot be denied by either
    # route, so it must return without spawning jq or git — and without the
    # subshell route 1's walk runs in.
    assert _run(_bash("npm test -- --watch=false"), tmp_path) is None
    assert _run(_edit("web/src/index.ts"), tmp_path) is None
