"""What arms LOCI in a directory — the cheap gate, and the four session blocks.

Two false positives are the reason this file exists. A documentation repo armed
as an ``armv7e-m`` C++ project, and the plugin's own repo armed as ``armv6-m``:
both came from *anchors the old gate accepted as evidence* — LOCI's own
``.loci-build/`` cache, deep source sweeps that counted fixture ``.c`` files, an
architecture read off a stray ELF, and a target inferred from a cross-compiler
on ``PATH``. None of those is a statement about the directory.

Since T08 (report §6.3) the gate accepts only a build the project **declares** —
a root build file, or a vendor project file within two levels — and it owns the
arming decision by itself. The scan that used to run below it — compilers on
PATH, ELFs, a guessed target, left in the keyed file as cascade hints for a CLI
too old to read a recipe — is gone since T14: the CLI's cascade is gone, so the
hints have no reader, and `detect-project.sh` IS the gate.

Above the gate sits the recipe. A project ``loci init`` has recorded is described
by its keyed context (the recipe's mirror) and never scanned again; a recorded
``unsupported``/``needs_user`` disarms without a scan; ``failed`` is transient
and re-arms. Those four branches are the four context blocks asserted here.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent

# Every voice-emitting skill must carry its own "## LOCI voice remark" section:
# that is the stated precondition for dropping the 1 385-byte LOCI_VOICE block
# from the session context, and this list is what keeps the precondition true.
# bug-report is excluded on purpose — it says "Do NOT emit a LOCI voice remark".
VOICE_SKILLS = ("loci-preflight", "loci-post-edit", "exec-trace", "control-flow",
                "stack-depth", "memory-report", "trends")


def _find_bash() -> str | None:
    if sys.platform == "win32":
        for cand in (
            r"C:\Program Files\Git\usr\bin\bash.exe",
            r"C:\Program Files (x86)\Git\usr\bin\bash.exe",
        ):
            if Path(cand).is_file():
                return cand
    return shutil.which("bash")


def _has_bash() -> bool:
    return _find_bash() is not None


def _has_jq() -> bool:
    return shutil.which("jq") is not None


def _to_bash_path(p: Path) -> str:
    s = Path(p).as_posix()
    m = re.match(r"^([A-Za-z]):/(.*)$", s)
    if m:
        return f"/{m.group(1).lower()}/{m.group(2)}"
    return s


def _alt_spelling(p: Path) -> str | None:
    """The SAME directory under a different absolute name, or None.

    Git Bash mounts `%TEMP%` at `/tmp`, so everything pytest puts under the
    temp directory has two names — and until this helper existed every fixture
    handed the hook a `HOME` and a cwd spelled the same way, so a `$HOME` guard
    that only works when they agree passed the whole suite. It did not work: an
    optimisation comparing the two paths' last components shipped for one
    commit and re-armed `~/Downloads` off a dotfiles repo.
    """
    if sys.platform != "win32":
        return None
    import tempfile
    real = Path(tempfile.gettempdir()).resolve()
    try:
        rel = Path(p).resolve().relative_to(real)
    except ValueError:
        return None
    return "/tmp/" + rel.as_posix()


def _env(home: Path, **extra: str) -> dict:
    return {
        **os.environ,
        "HOME": _to_bash_path(home),
        "LOCI_STATE_DIR": _to_bash_path(home / ".loci" / "state"),
        **extra,
    }


def _detect(target: Path, home: Path) -> dict:
    args = [_find_bash(), _to_bash_path(PLUGIN_ROOT / "lib" / "detect-project.sh"),
            _to_bash_path(target)]
    res = subprocess.run(args, env=_env(home), capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=60)
    assert res.returncode == 0, f"detect-project.sh exited {res.returncode}\n{res.stderr}"
    return json.loads(res.stdout)


class Session:
    """One session-init run: its hook payload, its context file, its log."""

    def __init__(self, payload: dict, state: Path):
        self.payload = payload
        self.state = state

    @property
    def ctx(self) -> str:
        return self.payload["hookSpecificOutput"]["additionalContext"]

    @property
    def context_file(self) -> Path:
        files = sorted(self.state.glob("project-context-*.json"))
        assert len(files) == 1, f"expected one keyed context, found {files}"
        return files[0]

    @property
    def context(self) -> dict:
        return json.loads(self.context_file.read_text(encoding="utf-8"))

    @property
    def log(self) -> str:
        path = self.state / "loci.log"
        return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""

    #: The build facts only `loci init` writes since T14. On a project init has
    #: never touched, any of them in the keyed file means a scan is back.
    SCAN_FACTS = frozenset({"compiler", "compiler_path", "build_system",
                            "artifact", "loci_artifacts", "loci_target"})

    @property
    def scanned(self) -> bool:
        """Did anything probe the tree — compilers, ELFs, cross-toolchains?

        The scan T14 deleted logged `start: detect_compiler` and wrote eight
        build facts into the keyed file. Both are checked: the log line is what
        the old probe left, and the keys are what any replacement would have to
        write to be of use. Meaningful only where `loci init` has not written —
        on an initialized project the same keys are the recipe's mirror.
        """
        return ("start: detect_compiler" in self.log
                or bool(self.SCAN_FACTS & set(self.context)))

    @property
    def detector_ran(self) -> bool:
        """Was `lib/detect-project.sh` invoked at all?

        The stronger claim, and the one an initialized project makes: the recipe
        answers, so the detector is never even started — no gate, no walk, no
        subprocess.
        """
        return "start: detect-project" in self.log

    @property
    def armed(self) -> bool:
        return "LOCI auto-run rules" in self.ctx


def _run_session_init(cwd: Path, home: Path, **extra: str) -> Session:
    state = home / ".loci" / "state"
    # A fresh log per run, so `scanned` answers about THIS run and not about a
    # previous one against the same fixture.
    if (state / "loci.log").is_file():
        (state / "loci.log").unlink()
    res = subprocess.run(
        [_find_bash(), _to_bash_path(PLUGIN_ROOT / "hooks" / "session-init.sh")],
        env=_env(home, LOCI_ENV="dev", **extra),
        cwd=cwd, capture_output=True, text=True,
        # EXPLICIT utf-8. `text=True` alone decodes with the locale encoding —
        # cp1252 here — so every em-dash and `…` the hook emits came back as
        # mojibake: assertions on that text were comparing against a corrupted
        # copy, and the byte budget measured a block ~50 B larger than the one
        # the hook actually writes.
        encoding="utf-8", errors="replace",
        timeout=60, stdin=subprocess.DEVNULL,
    )
    assert res.returncode == 0, f"session-init.sh exited {res.returncode}\n{res.stderr}"
    return Session(json.loads(res.stdout), state)


def _amend_context(session: Session, **fields) -> None:
    """Write into the keyed context the way ``loci init`` does — merging."""
    path = session.context_file
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(fields)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _cli_shim(dirpath: Path, version: str) -> Path:
    """A ``loci`` on PATH that answers a chosen ``--version`` and nothing else.

    The stale-CLI advisory is decided by three facts the hook reads off the
    machine: whether ``loci`` is on PATH, what ``loci --version`` prints, and
    whether the uv shim directory holds a second copy. On a developer's box those
    are whatever they happen to be — which is how the block-budget test came to
    pass here and fail in WSL (0.1.97 against a 0.1.126 pin), i.e. to decide on an
    environment fact rather than on the code (P68). Pinning all three makes both
    states testable on every host.
    """
    dirpath.mkdir(parents=True, exist_ok=True)
    shim = dirpath / "loci"
    shim.write_text(
        "#!/bin/bash\n"
        'case "$1" in\n'
        '  --version) echo "loci %s" ;;\n'
        "  *) exit 1 ;;\n"
        "esac\n" % version,
        encoding="utf-8", newline="\n")
    shim.chmod(0o755)
    return dirpath


def _cli_version_env(tmp_path: Path, version: str, *, dev: bool = False) -> dict:
    """Environment that makes the CLI-version half of the block deterministic."""
    shim = _cli_shim(tmp_path / f"clishim-{version}", version)
    # An EMPTY uv shim directory, so `_SHADOW` is never set: a second `loci`
    # there adds ~90 B of "shadowing binary" prose to the advisory, and whether
    # one exists is a fact about the developer's machine.
    uv_bin = tmp_path / "uvbin"
    uv_bin.mkdir(exist_ok=True)
    return {
        "PATH": f"{_to_bash_path(shim)}:{os.environ.get('PATH', '')}",
        "UV_TOOL_BIN_DIR": _to_bash_path(uv_bin),
        # THE THIRD INPUT, and it is inherited from the developer's shell unless
        # it is cleared here. `tasks/README.md` tells every plugin task to export
        # `LOCI_DEV_CLI_PATH`, and with it set `_loci_resolve_install_spec`
        # returns early leaving `_loci_cli_pinned` empty — no advisory, whatever
        # the version. So the "deterministic" budget tests still decided on the
        # environment, in the environment the protocol puts the implementer in.
        "LOCI_DEV_CLI_PATH": _to_bash_path(tmp_path / "no-dev-checkout")
        if not dev else _to_bash_path(_dev_checkout(tmp_path)),
    }


def _dev_checkout(tmp_path: Path) -> Path:
    """The shape `_loci_resolve_install_spec` accepts as a floating dev CLI."""
    root = tmp_path / "dev-cli"
    (root / "src" / "loci").mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "loci-tools"\nversion = "0.0.0"\n', encoding="utf-8")
    return root


def _initialized_project(tmp_path: Path, home: Path, *,
                         env_extra: dict | None = None,
                         **fields) -> tuple[Path, Session]:
    """A project with a recipe on disk and ``loci init``'s record in the context.

    Two session-init runs: the first creates the keyed file (on a real machine
    the CLI would have), the second is the one under test. ``env_extra`` reaches
    both runs, so a caller can pin the CLI-version facts the block reports.
    """
    env_extra = env_extra or {}
    proj = tmp_path / "fw"
    (proj / ".loci").mkdir(parents=True)
    (proj / ".loci" / "build.yaml").write_text("schema_version: 1\ntarget: armv7e-m\n")
    (proj / "build").mkdir()
    (proj / "build" / "app.elf").write_bytes(b"\x7fELF")
    first = _run_session_init(proj, home, **env_extra)
    _amend_context(first, **{
        "init_status": "ok",
        "init_recipe": _to_bash_path(proj / ".loci" / "build.yaml"),
        "loci_target": "armv7e-m",
        "compiler": "arm-none-eabi-gcc",
        "build_system": "cmake",
        "validated": "compile-check",
        "confirmed_by_user": True,
        "artifact": _to_bash_path(proj / "build" / "app.elf"),
        **fields,
    })
    return proj, _run_session_init(proj, home, **env_extra)


needs_tools = pytest.mark.skipif(
    not (_has_bash() and _has_jq()), reason="bash and jq required"
)


# ── the gate: what counts as evidence ───────────────────────────────────────

@needs_tools
def test_empty_dir_is_no_project(tmp_path):
    """An empty dir must not inherit a project identity from the host's
    compiler and architecture."""
    target = tmp_path / "empty"
    target.mkdir()
    info = _detect(target, tmp_path / "home")
    assert info["detection_status"] == "no_project"
    assert "loci_target" not in info


@needs_tools
def test_root_build_file_is_the_evidence(tmp_path):
    """Each of the eleven root build files declares a build on its own."""
    # Numbered, not named after the file: `proj-Makefile` and `proj-makefile`
    # are ONE directory on a case-insensitive filesystem, which is every
    # Windows one — and `Makefile`/`makefile` are two distinct entries in the
    # list under test.
    for i, name in enumerate(("Makefile", "makefile", "GNUmakefile",
                              "CMakeLists.txt", "Cargo.toml", "meson.build",
                              "BUILD", "WORKSPACE", "conanfile.txt",
                              "conanfile.py", "vcpkg.json")):
        target = tmp_path / f"proj-{i}"
        target.mkdir()
        (target / name).write_text("")
        info = _detect(target, tmp_path / "home")
        assert info["detection_status"] == "ok", f"{name} did not count as evidence"


@needs_tools
def test_vendor_project_file_two_levels_down_arms(tmp_path):
    """``firmware/app.uvprojx`` repos have no root build file and must still
    arm — the one depth-2 inference the gate kept."""
    target = tmp_path / "repo"
    (target / "firmware").mkdir(parents=True)
    (target / "firmware" / "app.uvprojx").write_text("<Project/>")
    info = _detect(target, tmp_path / "home")
    assert info["detection_status"] == "ok"


@needs_tools
def test_loci_build_cache_is_not_evidence(tmp_path):
    """THE DOCS-REPO FALSE POSITIVE. ``.loci-build/`` only exists because LOCI
    ran here once; treating that as proof the directory is a project is how a
    Markdown repo came to be armed as an armv7e-m C++ project. Inverted from
    the pre-T08 assertion on purpose."""
    target = tmp_path / "docs"
    (target / ".loci-build" / "armv6-m").mkdir(parents=True)
    (target / ".loci-build" / "armv6-m" / "mod.o").write_bytes(b"\x7fELF")
    info = _detect(target, tmp_path / "home")
    assert info["detection_status"] == "no_project"
    assert "loci_target" not in info


@needs_tools
def test_the_new_build_root_is_not_evidence_either(tmp_path):
    """The same false positive under the directory's new name.

    ``.loci-build/`` was dropped as an arming anchor because it only exists where
    LOCI already ran, and the layout move gives that cache a second spelling. A
    gate that answered ``no_project`` for one and ``ok`` for the other would
    re-arm the docs repo the moment its CLI upgraded — and the compat stub means
    a post-move project has BOTH, so the pair is the shape to test."""
    target = tmp_path / "docs"
    (target / ".loci" / "build" / "objects" / "armv6-m").mkdir(parents=True)
    (target / ".loci" / "build" / "objects" / "armv6-m" / "mod.o").write_bytes(b"\x7fELF")
    (target / ".loci-build").mkdir()
    (target / ".loci-build" / ".gitignore").write_text("*\n")

    info = _detect(target, tmp_path / "home")

    assert info["detection_status"] == "no_project"
    assert "loci_target" not in info


@needs_tools
def test_fixture_sources_and_elfs_are_not_evidence(tmp_path):
    """THE PLUGIN-REPO FALSE POSITIVE. A repo whose C files and ELFs are test
    fixtures declares no build, and the deep sweep that counted them is gone."""
    target = tmp_path / "tooling"
    (target / ".git").mkdir(parents=True)
    (target / "tests" / "fixtures").mkdir(parents=True)
    (target / "tests" / "fixtures" / "blink.c").write_text("int main(){}")
    (target / "tests" / "fixtures" / "kernel.elf").write_bytes(b"\x7fELF")
    (target / "README.md").write_text("# docs")
    info = _detect(target, tmp_path / "home")
    assert info["detection_status"] == "no_project"


@needs_tools
def test_loose_sources_without_a_build_file_do_not_arm(tmp_path):
    """Sources in CWD used to be enough. They are not: nothing says how they
    build, which is the question ``/loci:init`` exists to answer."""
    target = tmp_path / "loose"
    target.mkdir()
    (target / "main.c").write_text("int main(){}")
    info = _detect(target, tmp_path / "home")
    assert info["detection_status"] == "no_project"


@needs_tools
def test_git_repo_with_deep_sources_does_not_arm_without_a_build_file(tmp_path):
    """The maxdepth-6 sweep inside a repo is gone with the rest of them."""
    target = tmp_path / "repo"
    (target / ".git").mkdir(parents=True)
    (target / "src" / "app").mkdir(parents=True)
    (target / "src" / "app" / "main.c").write_text("int main(){}")
    info = _detect(target, tmp_path / "home")
    assert info["detection_status"] == "no_project"


@needs_tools
def test_repo_with_submodules_and_a_root_build_file_is_ok(tmp_path):
    """A repo whose sources live in submodules is still ONE project — and now
    it is the root build file that says so."""
    target = tmp_path / "repo"
    (target / ".git").mkdir(parents=True)
    (target / "CMakeLists.txt").write_text("project(x)")
    (target / "sdk").mkdir()
    (target / "sdk" / ".git").write_text("gitdir: ../.git/modules/sdk")
    (target / "sdk" / "drv.c").write_text("int f(){}")
    info = _detect(target, tmp_path / "home")
    assert info["detection_status"] == "ok"


@needs_tools
def test_a_build_file_one_level_down_arms_its_repo(tmp_path):
    """The commonest firmware-monorepo layout there is: `docs/` beside
    `firmware/CMakeLists.txt`. Checking only the root turned it inactive under
    a sentence — "no build file declares a build in this directory" — that was
    simply false."""
    target = tmp_path / "repo"
    (target / ".git").mkdir(parents=True)
    (target / "docs").mkdir()
    (target / "firmware" / "src").mkdir(parents=True)
    (target / "firmware" / "CMakeLists.txt").write_text("project(fw)")
    assert _detect(target, tmp_path / "home")["detection_status"] == "ok"


@needs_tools
def test_a_session_inside_a_repo_finds_the_root_build_file(tmp_path):
    """`cd repo/src/drivers && claude` is an ordinary way to start work, and
    the repo's `Makefile` two levels up is still what builds the file about to
    be edited. The gate climbs to the checkout root — the same climb, with the
    same stops, that the recipe walk makes, so the two cannot disagree about
    which directory is the project."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "Makefile").write_text("all:\n\ttrue\n")
    sub_dir = repo / "src" / "drivers"
    sub_dir.mkdir(parents=True)
    assert _detect(sub_dir, tmp_path / "home")["detection_status"] == "ok"


@needs_tools
def test_a_repo_with_no_build_file_does_not_arm_its_subdirectories(tmp_path):
    """The counterpoint to the checkout-root climb, and the mutation that
    proved it was unguarded: dropping the `_has_root_build_file "$_root"` test
    from that arm makes EVERY subdirectory of EVERY git checkout arm — being
    inside a repo is not evidence, the repo declaring a build is."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "README.md").write_text("# notes")
    deep = repo / "src" / "drivers"
    deep.mkdir(parents=True)
    for d in (repo, repo / "src", deep):
        assert _detect(d, tmp_path / "home")["detection_status"] == "no_project", (
            f"{d} armed without any build file in the checkout")


@needs_tools
def test_a_docs_makefile_is_not_this_projects_build(tmp_path):
    """`sphinx-quickstart` writes `docs/Makefile` into every Python repo it
    touches. Reading a depth-2 build file without asking WHOSE build it is
    re-opens the false-positive class one directory over."""
    target = tmp_path / "pyproj"
    (target / ".git").mkdir(parents=True)
    (target / "docs").mkdir()
    (target / "docs" / "Makefile").write_text("html:\n\t@sphinx-build .\n")
    (target / "pyproject.toml").write_text('[project]\nname="x"\n')
    assert _detect(target, tmp_path / "home")["detection_status"] == "no_project"


@needs_tools
@pytest.mark.parametrize("subdir", [
    "docs", "doc", "Documentation", "test", "tests", "example", "examples",
    "sample", "samples", "bench", "benchmarks", "contrib", "scripts", "www",
    "web", "site",
])
def test_a_build_file_in_a_non_project_subdirectory_is_not_evidence(tmp_path, subdir):
    """Every name in the prune list, not just the one that motivated it: a
    mutation that deleted fifteen of the sixteen left the whole suite green,
    and `test/CMakeLists.txt` or `examples/Makefile` then armed a repo with no
    compiled code of its own."""
    target = tmp_path / f"proj-{subdir}"
    (target / ".git").mkdir(parents=True)
    (target / subdir).mkdir()
    (target / subdir / "Makefile").write_text("all:\n\ttrue\n")
    assert _detect(target, tmp_path / "home")["detection_status"] == "no_project", (
        f"a build file in {subdir}/ counted as this project's own build")


@needs_tools
def test_the_prune_does_not_fire_on_the_projects_own_top_folder(tmp_path):
    """`find`'s prune applies to the START directory too, so a checkout whose
    own basename happened to be `tests` or `docs` or `web` had its entire
    depth-2 walk pruned at the root — the same repo arming or not depending on
    what its top folder was called, under a sentence that was flatly false."""
    for name in ("tests", "docs", "web"):
        target = tmp_path / name
        (target / ".git").mkdir(parents=True)
        (target / "firmware").mkdir()
        (target / "firmware" / "CMakeLists.txt").write_text("project(fw)")
        assert _detect(target, tmp_path / "home")["detection_status"] == "ok", (
            f"a repo whose top folder is called {name!r} pruned itself away")


@needs_tools
def test_a_dependencys_build_file_is_not_evidence(tmp_path):
    """A vendored `Cargo.toml`/`CMakeLists.txt` declares a DEPENDENCY's build.
    The prune list is what says so, and stripping it back to `.git` left the
    whole suite green."""
    for vendor in ("node_modules", "vendor", "third_party", ".venv", "target"):
        target = tmp_path / f"proj-{vendor}"
        (target / ".git").mkdir(parents=True)
        (target / vendor).mkdir()
        (target / vendor / "Cargo.toml").write_text("[package]")
        assert _detect(target, tmp_path / "home")["detection_status"] == "no_project", (
            f"a build file inside {vendor}/ counted as this project's")


@needs_tools
def test_a_dotfiles_repo_does_not_arm_the_home_tree(tmp_path):
    """`~/.git` plus `~/Makefile` is an ordinary way to keep dotfiles. The
    checkout-root climb tested `.git` BEFORE the $HOME stop, so every directory
    under home — `~/Downloads`, an empty one — came back armed, with the
    MANDATORY auto-run rules, while the recipe walk correctly refused the same
    tree. Two walks, one directory, opposite answers."""
    home = tmp_path / "home"
    (home / ".git").mkdir(parents=True)
    (home / "Makefile").write_text("all:\n\ttrue\n")
    for name in ("Downloads", "Documents"):
        (home / name).mkdir()
        assert _detect(home / name, home)["detection_status"] == "no_project", (
            f"~/{name} armed off the dotfiles repo in $HOME")
    # And $HOME itself, which declares the build, still does.
    assert _detect(home, home)["detection_status"] == "ok"


@needs_tools
@pytest.mark.parametrize("marker", ["platformio.ini", "west.yml", "west.yaml", "build.ninja"])
@pytest.mark.parametrize("depth", ["root", "one-down"])
def test_the_other_compdb_routes_count_as_evidence(tmp_path, marker, depth):
    """At the root, and one level down — the nested walk is the same question
    asked of `firmware/`, and it once knew `west.yml` but not `west.yaml`."""
    target = tmp_path / "proj"
    where = target if depth == "root" else target / "fw"
    where.mkdir(parents=True)
    if depth == "one-down":
        (target / ".git").mkdir()
    (where / marker).write_text("# build\n")
    (where / "main.c").write_text("int main(){}")
    assert _detect(target, tmp_path / "home")["detection_status"] == "ok"


@needs_tools
def test_a_container_is_not_claimed_by_one_childs_vendor_file(tmp_path):
    """A directory holding three unrelated repos is not one project because one
    of them keeps a `.uvprojx`. The depth-2 inference is only allowed where
    nothing else owns the file."""
    target = tmp_path / "projects"
    for name in ("fw", "docs", "other"):
        (target / name / ".git").mkdir(parents=True)
    (target / "fw" / "app.uvprojx").write_text("<Project/>")
    assert _detect(target, tmp_path / "home")["detection_status"] == "multi_project"


@needs_tools
def test_container_of_git_repos_is_multi_project(tmp_path):
    target = tmp_path / "projects"
    for name in ("repo-a", "repo-b"):
        (target / name / ".git").mkdir(parents=True)
        (target / name / "main.c").write_text("int main(){}")
    info = _detect(target, tmp_path / "home")
    assert info["detection_status"] == "multi_project"
    assert len(info["subproject_roots"]) == 2


@needs_tools
def test_container_of_build_file_projects_is_multi_project(tmp_path):
    target = tmp_path / "projects"
    (target / "proj-a").mkdir(parents=True)
    (target / "proj-a" / "Cargo.toml").write_text("[package]")
    (target / "proj-b").mkdir(parents=True)
    (target / "proj-b" / "CMakeLists.txt").write_text("")
    info = _detect(target, tmp_path / "home")
    assert info["detection_status"] == "multi_project"


@needs_tools
def test_a_container_with_its_own_build_file_is_a_project(tmp_path):
    """A workspace root — a Cargo workspace, a CMake super-project — has member
    directories that look like subprojects and IS one project. Declared
    evidence wins over the container heuristic."""
    target = tmp_path / "workspace"
    target.mkdir()
    (target / "Cargo.toml").write_text("[workspace]\nmembers=['a','b']")
    for name in ("a", "b"):
        (target / name).mkdir()
        (target / name / "Cargo.toml").write_text("[package]")
    info = _detect(target, tmp_path / "home")
    assert info["detection_status"] == "ok"


@needs_tools
def test_single_foreign_subrepo_is_no_project(tmp_path):
    target = tmp_path / "wrapper"
    (target / "repo" / ".git").mkdir(parents=True)
    (target / "repo" / "Makefile").write_text("all:\n\ttrue\n")
    info = _detect(target, tmp_path / "home")
    assert info["detection_status"] == "no_project"


@needs_tools
def test_repo_subdir_with_submodules_is_not_multi_project(tmp_path):
    """A repo SUBDIR whose children are submodules is one project's interior,
    not a container of independent projects — the worktree walk must find the
    ``.git`` in the ancestor. What it must never be is ``multi_project``."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    target = repo / "firmware"
    for name in ("sdk", "rtos"):
        (target / name).mkdir(parents=True)
        (target / name / ".git").write_text(f"gitdir: ../../.git/modules/{name}")
        (target / name / "Makefile").write_text("all:\n\ttrue\n")
    # `ok`, not `multi_project`, and that is the whole claim: submodules are one
    # project's parts. They declare builds, and this directory is inside the
    # checkout that owns them, so the depth-2 rule reads those as its own.
    assert _detect(target, tmp_path / "home")["detection_status"] == "ok"


@needs_tools
def test_home_dir_is_not_claimed_by_a_stray_vendor_file(tmp_path):
    """$HOME keeps downloads two levels down, which is exactly the depth the
    vendor-file check looks at — so one ``~/Downloads/demo.uvprojx`` would claim
    the entire home tree. Only $HOME itself is excluded from that inference."""
    home = tmp_path / "home"
    (home / "Downloads").mkdir(parents=True)
    (home / "Downloads" / "STM32_demo.uvprojx").write_text("<Project/>")
    info = _detect(home, home)
    assert info["detection_status"] == "no_project"


@needs_tools
@pytest.mark.parametrize("spell_home,spell_cwd", [(False, True), (True, False)])
def test_the_home_guards_hold_when_the_two_paths_are_spelled_differently(
        tmp_path, spell_home, spell_cwd):
    """One directory, two absolute names — the case the inode compare exists
    for, and the case no fixture used to construct.

    Both guards are exercised: the gate's `$HOME` exclusion (a stray
    `~/Downloads/demo.uvprojx` must not claim the home tree) and the
    checkout-root climb (`~/.git` + `~/Makefile`, a dotfiles repo, must not arm
    every directory under it).
    """
    home = tmp_path / "home"
    (home / "Downloads").mkdir(parents=True)
    (home / "Downloads" / "demo.uvprojx").write_text("<Project/>")

    alt_home, alt_probe = _alt_spelling(home), _alt_spelling(home)
    if alt_home is None:
        pytest.skip("no second spelling for the temp directory on this platform")

    env_home = alt_home if spell_home else _to_bash_path(home)
    probe = alt_probe if spell_cwd else _to_bash_path(home)
    res = subprocess.run(
        [_find_bash(), _to_bash_path(PLUGIN_ROOT / "lib" / "detect-project.sh"), probe],
        env={**os.environ, "HOME": env_home,
             "LOCI_STATE_DIR": _to_bash_path(home / ".loci" / "state")},
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["detection_status"] == "no_project", (
        f"the $HOME guard missed a second spelling (HOME={env_home}, cwd={probe})")

    # …and the dotfiles-repo climb, same two spellings.
    (home / ".git").mkdir()
    (home / "Makefile").write_text("all:\n\ttrue\n")
    (home / "empty").mkdir()
    probe_sub = (alt_probe + "/empty") if spell_cwd else _to_bash_path(home / "empty")
    res = subprocess.run(
        [_find_bash(), _to_bash_path(PLUGIN_ROOT / "lib" / "detect-project.sh"), probe_sub],
        env={**os.environ, "HOME": env_home,
             "LOCI_STATE_DIR": _to_bash_path(home / ".loci" / "state")},
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["detection_status"] == "no_project", (
        f"a dotfiles repo armed a subdirectory of $HOME under a second spelling "
        f"(HOME={env_home}, cwd={probe_sub})")


@needs_tools
def test_home_dir_with_own_build_file_is_still_ok(tmp_path):
    """The $HOME exclusion only blocks the inference path — hard evidence in
    ~ itself still counts."""
    home = tmp_path / "home"
    home.mkdir()
    (home / "Makefile").write_text("all:\n\ttrue\n")
    (home / "main.c").write_text("int main(){}")
    info = _detect(home, home)
    assert info["detection_status"] == "ok"


@needs_tools
def test_dir_under_home_with_a_vendor_file_still_arms(tmp_path):
    """Only $HOME itself is excluded; an ordinary directory below it keeps the
    depth-2 vendor inference."""
    home = tmp_path / "home"
    target = home / "work" / "fw"
    (target / "firmware").mkdir(parents=True)
    (target / "firmware" / "app.uvprojx").write_text("<Project/>")
    info = _detect(target, home)
    assert info["detection_status"] == "ok"


@needs_tools
def test_git_repo_without_c_sources_is_no_project(tmp_path):
    target = tmp_path / "repo"
    (target / ".git").mkdir(parents=True)
    (target / "index.js").write_text("console.log(1)")
    info = _detect(target, tmp_path / "home")
    assert info["detection_status"] == "no_project"


# ── the emit ────────────────────────────────────────────────────────────────

@needs_tools
def test_the_emit_carries_exactly_the_surviving_fields(tmp_path):
    """Two fields since T14: the gate's verdict and, for a container, the
    sub-project roots. The eight the scan used to fill beside them — and the ten
    report §6.3's audit dropped before that — are gone and stay gone. Both
    directions matter: a field that comes back is the scan returning under
    another name, and a surviving field that disappears is `session-init.sh`
    losing the verdict it arms on."""
    target = tmp_path / "proj"
    target.mkdir()
    (target / "Makefile").write_text("all:\n\ttrue\n")
    (target / "main.c").write_text("int main(){}")
    info = _detect(target, tmp_path / "home")

    assert set(info) == {"subproject_roots", "detection_status"}, (
        f"detect-project.sh's emit drifted: {sorted(info)}")
    assert info["detection_status"] == "ok"
    for dead in ("compiler", "compiler_path", "build_system", "architecture",
                 "elf_files", "loci_artifacts", "build_dirs", "loci_target",
                 "build_compiler", "language_stack", "project_type",
                 "source_files", "binaries", "asm_files", "cross_compilers",
                 "loci_compatible", "detected_at", "scan_depth"):
        assert dead not in info


@needs_tools
def test_the_arguments_are_one_directory_and_nothing_else(tmp_path):
    """One directory, checked. `--force-scan` — the flag the eval harness used to
    bypass the gate for the artifact hints — went with the scan (T14), and a
    caller still passing it is refused loudly rather than quietly handed a
    verdict it did not ask for: the harness stages a recipe now, and a stale copy
    of it must learn that from an exit 2, not from an eval that measures
    nothing."""
    target = tmp_path / "fixture"
    target.mkdir()
    (target / "kernel.elf").write_bytes(b"\x7fELF")
    (target / "Makefile").write_text("all:\n\ttrue\n")
    script = _to_bash_path(PLUGIN_ROOT / "lib" / "detect-project.sh")

    def run(*args):
        return subprocess.run([_find_bash(), script, *args], env=_env(tmp_path / "home"),
                              capture_output=True, text=True, timeout=60)

    ok = run(_to_bash_path(target))
    assert ok.returncode == 0, ok.stderr
    assert json.loads(ok.stdout)["detection_status"] == "ok"

    for bad in (("--force-scan", _to_bash_path(target)),
                (_to_bash_path(target), "--force-scan"),
                ("--nonsense", _to_bash_path(target)),
                (_to_bash_path(target), _to_bash_path(target)),
                (_to_bash_path(target / "nope"),)):
        res = run(*bad)
        assert res.returncode == 2, f"{bad} was accepted: {res.stdout[:200]}"
        assert not res.stdout.strip(), "a refused invocation still emitted a verdict"


@needs_tools
def test_nothing_bypasses_the_gate(tmp_path):
    """An ELF and a source with no declared build is `no_project`, and there is
    no flag that says otherwise. The harness that needed artifact hints out of
    such a tree stages a recipe instead (`run_evals.sh`), because since T14 the
    hints it wanted do not exist: nothing reads them."""
    target = tmp_path / "fixture"
    target.mkdir()
    (target / "kernel.elf").write_bytes(b"\x7fELF")
    (target / "blink.c").write_text("int main(){}")
    info = _detect(target, tmp_path / "home")
    assert info["detection_status"] == "no_project"
    assert "elf_files" not in info and "loci_artifacts" not in info


# ── the session blocks ──────────────────────────────────────────────────────

@needs_tools
def test_docs_repo_is_inactive_and_unscanned(tmp_path):
    """Acceptance criterion 1, as a test: the shape of the LOCI docs repository
    — a Markdown tree with a leftover ``.loci-build/`` and a fixture source —
    must produce the inactive block, arm nothing, and never reach the scan."""
    target = tmp_path / "docs-repo"
    (target / ".git").mkdir(parents=True)
    (target / ".loci-build" / "armv7e-m").mkdir(parents=True)
    (target / ".loci-build" / "armv7e-m" / "old.o").write_bytes(b"\x7fELF")
    (target / "docs").mkdir()
    (target / "docs" / "architecture.md").write_text("# docs")
    (target / "samples").mkdir()
    (target / "samples" / "example.c").write_text("int main(){}")
    home = tmp_path / "home"
    home.mkdir()

    s = _run_session_init(target, home)
    assert "LOCI: inactive (detection: no_project)" in s.ctx
    assert not s.armed
    # NOT a bare "MUST invoke" search: the disarm sentence quotes that wording in
    # order to override it. What must be absent is the RULE.
    assert "you MUST invoke the loci:loci-preflight" not in s.ctx.lower()
    assert "LOCI target:" not in s.ctx and "Target:" not in s.ctx
    # Phrase by phrase, because the sentence's whole job is to beat two things
    # that outrank it: the skills' own description-level MANDATORY, and the
    # PostToolUse hook's "You MUST invoke … NOW" later in the same context. A
    # substring check on "do NOT apply in this session" passed happily when the
    # rest of the sentence was rewritten into "…by default; use your judgement
    # and go ahead".
    for phrase in ("do NOT apply in this session",
                   "do not invoke any LOCI skill automatically",
                   "OVERRIDES",
                   "post-edit reminder",
                   "not a reason to run one"):
        assert phrase in s.ctx, f"the disarm sentence lost {phrase!r}"
    assert "use your judgement" not in s.ctx
    # And the recovery stays bounded to an explicit request, which is the clause
    # the first cut dropped.
    assert "If the user explicitly asks" in s.ctx
    assert not s.scanned, (
        "the docs repo was probed — the gate ran, which is correct, but it "
        "must stop before any compiler/ELF/PATH probing")
    # Version/plugin-dir lines survive so the upgrade plumbing keeps working.
    assert "loci version:" in s.ctx and "plugin dir:" in s.ctx


@needs_tools
def test_container_is_inactive_with_its_own_reason(tmp_path):
    target = tmp_path / "projects"
    for name in ("repo-a", "repo-b"):
        (target / name / ".git").mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()

    s = _run_session_init(target, home)
    assert "LOCI: inactive (detection: multi_project)" in s.ctx
    assert "2 independent projects" in s.ctx
    assert not s.armed
    assert not s.scanned, "a container of repos was probed"


@needs_tools
def test_uninitialized_project_is_armed_with_the_opt_in_init_rule(tmp_path):
    """Acceptance criterion 3's first half: the gate passes, so the session is
    armed AND carries the ``not_initialized`` rule. No target is asserted —
    there is no recipe to assert one from.

    The rule pinned here **used to be auto-init** and is now opt-in: `loci init`
    writes four files into someone's tree, so which projects LOCI runs on is the
    user's choice, not a consequence of an edit landing on a `.c` file. The
    session is still ARMED, and that is not a contradiction — the analyses run
    and answer `not_initialized`, which is what makes the one-line "run
    /loci:init" reply possible. Arming and adopting are different things, and
    only the second is the user's to decide."""
    target = tmp_path / "proj"
    target.mkdir()
    (target / "Makefile").write_text("all:\n\ttrue\n")
    (target / "main.c").write_text("int main(){}")
    home = tmp_path / "home"
    home.mkdir()

    s = _run_session_init(target, home)
    assert s.armed
    assert "LOCI: inactive" not in s.ctx
    assert "not initialized" in s.ctx
    assert "`not_initialized`" in s.ctx and "/loci:init" in s.ctx
    # The rule must forbid running it, not merely omit the instruction: the old
    # wording ("invoke the loci:init skill ONCE this session") is what a model
    # would otherwise carry over from the six skills that used to repeat it.
    assert "Do NOT invoke the loci:init skill" in s.ctx
    assert "loci:init skill ONCE" not in s.ctx, "the auto-init instruction is gone"
    # BOTH spellings. The pre-T08 block printed the target twice, as `Target:`
    # and as `LOCI target:`, and asserting only the second let the first be
    # restored with all 38 tests green — on the one branch where a fabricated
    # target is the whole failure this task exists to end.
    assert "LOCI target:" not in s.ctx and "Target:" not in s.ctx, (
        "an uninitialized project has no recorded target; the scan's "
        "PATH-derived guess must not be quoted to the model")
    # The gate ran — that is what armed the session — and nothing else did: no
    # probe, and no build fact in the file. Since T14 the only writer of
    # `compiler`, `build_system` and `artifact` is `loci init`; a value here on
    # a project it has never seen is a guess, and `pre-edit-hook.sh` reads
    # `loci_target` by name and ships it to `loci build snapshot` (a host-x86
    # project with a cross-gcc on PATH used to get `armv7e-m` that way).
    assert s.detector_ran
    assert not s.scanned
    assert s.context["init_status"] == "uninitialized"
    assert s.context["detection_status"] == "ok"
    for key in ("loci_target", "compiler", "compiler_path",
                "build_system", "artifact"):
        assert key not in s.context, (
            f"{key} reached the keyed file of a project nothing has initialized: "
            f"{s.context.get(key)!r}")


@needs_tools
def test_initialized_project_reads_the_recipe_mirror_and_never_scans(tmp_path):
    """Acceptance criterion 2. Every fact in the block comes from the keyed
    context ``loci init`` wrote; the scan does not run at all."""
    home = tmp_path / "home"
    home.mkdir()
    proj, s = _initialized_project(tmp_path, home)

    assert not s.detector_ran, (
        "an initialized project must not even start the detector")
    assert s.armed
    assert "LOCI target: armv7e-m" in s.ctx
    assert "Compiler: arm-none-eabi-gcc, Build: cmake" in s.ctx
    assert "recipe: " in s.ctx
    assert "artifact: " in s.ctx and "app.elf" in s.ctx
    assert "LOCI: inactive" not in s.ctx
    # Printed ONCE: the old block spelled the target twice, in `Target:` and in
    # `LOCI target:` — and it is the second one every skill reads.
    assert s.ctx.count("armv7e-m") == 1


#: The pin, read off the file that holds it rather than copied. T16 bumps that
#: number; a copy here would silently stop describing the branch under test.
def _pinned_cli_version() -> str:
    text = (PLUGIN_ROOT / "lib" / "setup-steps.sh").read_text(encoding="utf-8")
    m = re.search(r'^LOCI_CLI_VERSION="([0-9.]+)"', text, re.M)
    assert m, "lib/setup-steps.sh no longer declares LOCI_CLI_VERSION"
    return m.group(1)


def _modelled_size(ctx: str) -> int:
    """The block's size at PRODUCTION path lengths.

    Each path-bearing line is charged its label PLUS a fixed production-length
    allowance, rather than being collapsed to the label. Collapsing exempted
    everything after the colon: 400 bytes appended to the `artifact:` line grew
    the real block by 400 bytes and the test still passed. Under pytest's temp
    directories the paths alone are twice their production length, which is why
    the budget is asserted on the part this task controls.
    """
    labels = {"recipe: ": 34, "artifact: ": 40,
              "plugin dir: ": 52, "project context: ": 59}
    size = 0
    for line in ctx.splitlines():
        label = next((lab for lab in labels if line.startswith(lab)), None)
        if label:
            size += len(label.encode()) + labels[label] + 1
            continue
        # The stale-CLI advisory carries an ABSOLUTE path too — the install log
        # under the state dir — and it is not at the start of the line, so the
        # label table above cannot see it. Measured: 402 B with a pytest temp
        # state dir, 301 B in production, so the un-exempted 101 B of `tmp_path`
        # was being charged to the advisory's ceiling and any host with a longer
        # TMPDIR, a longer username or a `--basetemp` went red. Charge the path
        # its production length instead, the same way the four labels are.
        text = line
        m = re.search(r"`(\S*loci-cli-install\.log)`", text)
        if m:
            text = text[:m.start(1)] + "X" * 46 + text[m.end(1):]
        size += len(text.encode()) + 1
    return size


@needs_tools
def test_the_initialized_block_fits_its_budget(tmp_path):
    """~2.0 KB, measured on the prose rather than on the paths, with a CLI that
    matches the pin.

    **Why the CLI version is pinned here (P68).** This used to run against
    whatever `loci` the developer had: when it is behind `LOCI_CLI_VERSION` the
    hook appends a ~332 B version-skew advisory, so the same code measured
    2 048 B on this machine and 2 380 B in WSL. A test that decides on an
    environment fact is not measuring the code — and the answer is not a bigger
    number, it is two tests, one per state. This is the steady state; the one
    below is the advisory's.
    """
    home = tmp_path / "home"
    home.mkdir()
    proj, s = _initialized_project(
        tmp_path, home, env_extra=_cli_version_env(tmp_path, _pinned_cli_version()))

    assert "this plugin pins" not in s.ctx, (
        "the CLI shim answers the pinned version, so no skew advisory should be "
        "in this block — if one is, the shim is not the `loci` the hook resolved")
    size = _modelled_size(s.ctx)
    # 1 850 B of prose. The four paths add ~190 B on a real machine (a plugin
    # cache dir, the keyed state file, a repo-relative recipe, an artifact), so
    # the injected block lands just inside the ~2.0 KB the design asks for —
    # measured at 1 865 B on a real CMake/arm-none-eabi fixture.
    assert size <= _STEADY_STATE_BUDGET, (
        f"the initialized block models {size} B at production path lengths "
        f"(budget {_STEADY_STATE_BUDGET} B = the ~2.0 KB of acceptance "
        f"criterion 2, plus todo 042's prefix); the block "
        f"as emitted here was {len(s.ctx.encode())} B, inflated by pytest's "
        f"temp paths")
    # The 1 385-byte voice block is what paid for that budget — its twelve
    # worked examples and its Aurora Labs framing. What stayed is the four hard
    # rules, 153 bytes, because after the first cut they existed NOWHERE in the
    # plugin: the per-skill sections say "max 15 words, ground it in a number,
    # attribute improvements" and say nothing about emoji or personas.
    assert "Proof, Not Promises" not in s.ctx
    for example in ("Clean work.", "smart move", "This is tight code",
                    "Battery-friendly", "First measurement recorded"):
        assert example not in s.ctx, f"a worked example survived the drop: {example!r}"
    for rule in ("cite the number", "no emoji", "never vague", "not a persona"):
        assert rule in s.ctx, f"the voice drop took {rule!r} with it"


#: The version-skew advisory's own allowance, on top of the 2 048 B steady-state
#: budget. **The advisory is deliberately outside the budget**, which is the
#: question P68 left open, and the reason is that the two things are not the same
#: kind of text: 2 048 B is what a healthy session pays on EVERY start, forever,
#: while the advisory is a transient failure notice that ends when the upgrade
#: lands. Trimming a diagnostic to fit a steady-state budget makes the diagnostic
#: worse and the budget no smaller in the case it was written for. It gets a
#: ceiling of its own instead, so it cannot grow unwatched either.
_SKEW_ADVISORY_ALLOWANCE = 420

#: The unknown-version marker's allowance, on top of the same 2 048 B budget.
#: Tighter than the advisory's, because it is NOT transient: an unpinned install
#: pays it on every session start for as long as it stays unpinned, which for a
#: dev checkout is forever. Measured at 2 086 B modelled — 67 B for the marker,
#: against a healthy block of 2 019 B.
_UNKNOWN_MARKER_ALLOWANCE = 80

#: The artifact-only rule's allowance, on top of the same steady-state budget.
#: It is a SUBSTITUTION, not an addition: `_AUTORUN_ARTIFACT_ONLY` replaces the
#: 425 B `_AUTORUN_RULES` rather than following it, so this is the difference
#: between the two and not the rule's own size. Both must not print — a block
#: carrying "you MUST invoke loci-post-edit" and "do not" argues with itself, and
#: a session-start line loses that argument to a skill description saying
#: MANDATORY. Not transient, like the unknown-version marker: a project with no
#: compile database pays it every session until one exists. It buys three things
#: nothing else in the block says — which analyses do work here, that the two
#: auto-runs do not apply, and that neither `/loci:init` nor a rebuild is the way
#: out (AAD-7607, where the rebuild suggestion cost a 160 B → 1,032 B stack
#: regression its baseline).
_ARTIFACT_ONLY_ALLOWANCE = 520

#: The steady-state budget the two ceilings below are measured against. It was a
#: flat 2 048 B until todo 042 spelled every skill as `/loci:<name>`: the seven
#: names in the `Available:` line each cost 5 more bytes, and one spelling for a
#: command the user types is worth 35 B of a 2 KB block.
_STEADY_STATE_BUDGET = 2048 + 35


@needs_tools
def test_the_initialized_block_carries_the_skew_advisory_within_its_own_allowance(tmp_path):
    """The state P68 was reported from, made testable on every host.

    A CLI behind the pin is not an edge case — it is what every machine looks
    like between a plugin release and the CLI upgrade landing, and it is what WSL
    looks like here. The advisory has to reach the model (it is the only thing
    that tells it stale behaviour has a cause), and it has to stay bounded.
    """
    home = tmp_path / "home"
    home.mkdir()
    behind = "0.1.1"
    assert behind != _pinned_cli_version()
    proj, s = _initialized_project(
        tmp_path, home, env_extra=_cli_version_env(tmp_path, behind))

    # Non-vacuous: the branch under test really did run.
    assert f"CLI is {behind} but this plugin pins {_pinned_cli_version()}" in s.ctx, (
        "the skew advisory is absent, so this test is measuring the steady-state "
        "block a second time")
    assert "shadowing binary" not in s.ctx, (
        "UV_TOOL_BIN_DIR points at an empty directory, so the shadow arm must "
        "not fire — if it does, the advisory's size depends on the host again")

    size = _modelled_size(s.ctx)
    ceiling = _STEADY_STATE_BUDGET + _SKEW_ADVISORY_ALLOWANCE
    assert size <= ceiling, (
        f"the initialized block models {size} B with the version-skew advisory, "
        f"over its {ceiling} B ceiling ({_STEADY_STATE_BUDGET} B steady "
        f"state + {_SKEW_ADVISORY_ALLOWANCE} B for the advisory)")
    # …and the advisory is what the extra bytes buy: it must name both versions
    # and route the user somewhere. A shorter advisory that says less is not a
    # saving this ceiling is asking for.
    assert "loci:setup" in s.ctx, (
        "the skew advisory no longer routes anywhere — a version mismatch the "
        "model cannot act on is the pre-T08 state, where a stale CLI reached "
        "nobody")
    # …and the advisory is the ONE place a CLI number appears, which is what the
    # four version gates are told to read (P67/P86 — see the test below).
    assert f"CLI is {behind}" in s.ctx


#: The documents whose rules change at a CLI version. Each used to say "check
#: `loci command:` in the session context" — a line that has never carried a
#: version (P67), so all four gates were unexecutable from the day they were
#: written, and the contract's own worked example showed `on PATH, v0.1.104`, a
#: shape `session-init.sh` never emitted.
_VERSION_GATE_SOURCES = (
    "skills/_shared/loci-runtime-contract.md",
    "skills/loci-post-edit/SKILL.md",
)

#: An instruction to LOOK UP a version on the `loci command:` line. Both shapes
#: are quoted from the prose T13 removed: *"check `loci command:` in the session
#: context"* and *"the session context's `loci command:` version"*.
_VERSION_LOOKUP = re.compile(
    r"(?:check|read|compare|consult|inspect|look\s+at)\s+"
    r"(?:the\s+)?(?:session\s+context'?s?\s+)?`loci command:`"
    r"|`loci command:`\s+version",
    re.I)


@needs_tools
def test_no_version_gate_reads_a_number_off_a_line_that_carries_none(tmp_path):
    """P67/P86, resolved the way `test_version_announcement` requires.

    The obvious repair — put the CLI version back on `loci command:` — is the
    format that was RETIRED: two numbers in the context made "what version is
    LOCI?" a two-number answer with an editorial about a by-design mismatch, and
    `test_version_announcement.py` pins its absence. T13 re-added it, that test
    said no, and the gates were rewritten instead.

    So both halves are asserted together, because either alone is a lie: the
    context must not carry a CLI version on that line, and no rule may claim to
    read one from it. What the gates read instead is the stale-CLI advisory —
    present only when the CLI is behind the pin, which is exactly when their
    older-CLI branches apply.
    """
    # BOTH CLI states, because the hook builds that line in two places: a default
    # near the top, and a rebuild inside the stale-CLI branch. A single-state test
    # here let a mutation that re-added the version to the DEFAULT survive — the
    # skew branch overwrites it, so the one state the test ran in could not see
    # the change.
    for i, version in enumerate((_pinned_cli_version(), "0.1.99")):
        home = tmp_path / f"home{i}"
        home.mkdir()
        proj, s = _initialized_project(
            tmp_path / f"p{i}", home,
            env_extra=_cli_version_env(tmp_path, version))
        assert "loci command: loci (on PATH)" in s.ctx, (
            f"the CLI-presence line is gone or reshaped (CLI {version})")
        assert "on PATH, v" not in s.ctx, (
            f"the retired two-number format is back on the `loci command:` line "
            f"(CLI {version})")

    # No rule may send the model to that line for a version.
    #
    # Keyed on the LOOKUP — a verb aimed at the line, or the phrase "`loci
    # command:` version" — not on "the word version appears within 160
    # characters". That first draft flagged the two sentences that say the line
    # carries no version, which is the polarity failure `_ladder_offence` in
    # test_freshness_contract.py records at length: proximity cannot decide what
    # a `not` applies to, and a screen that rejects the correct fix for the debt
    # it guards is one PR from deletion. A retired instruction has to name the
    # line as a place to look; correct prose names it as a place with nothing to
    # find.
    offenders = []
    for rel in _VERSION_GATE_SOURCES:
        text = re.sub(r"\s+", " ", (PLUGIN_ROOT / rel).read_text(encoding="utf-8"))
        for m in _VERSION_LOOKUP.finditer(text):
            offenders.append(f"{rel}: …{text[max(0, m.start() - 60):m.end() + 60]}…")
    assert not offenders, (
        "a rule still reads a CLI version off the `loci command:` line, which "
        "carries none:\n  " + "\n  ".join(offenders)
        + "\n(route it through the contract's `#cli-version-gate` section)")

    # …and the section they were routed to exists, is linked, and says the thing
    # that makes the gate decidable.
    contract = (PLUGIN_ROOT / "skills" / "_shared"
                / "loci-runtime-contract.md").read_text(encoding="utf-8")
    assert contract.count('id="cli-version-gate"') == 1, (
        "the anchor the version gates link to is missing or duplicated")
    # …and WHERE it sits. Uniqueness is not enough: review moved this anchor 544
    # lines down to `## Rust / Cargo projects` — still exactly one — and all four
    # gated links landed on the Rust section instead of the gate. That is the
    # defect round 1 fixed for `#incremental-preamble`, reproduced in the anchor
    # that fix's sibling created, so it gets the same assertion.
    anchored = re.search(
        r'<a id="cli-version-gate"></a>\s*\n+\s*(\*\*[^\n]+|#{1,6} [^\n]+)',
        contract)
    assert anchored, (
        "the `#cli-version-gate` anchor is not immediately above the paragraph "
        "it names")
    assert anchored.group(1).startswith(
        "**Reading the CLI's version when a rule depends on it.**"), (
        f"the anchor now sits above {anchored.group(1)[:60]!r} — the four gated "
        f"rules link to it and would land there instead of on the gate")
    # Section-scoped with a UNIQUE end marker, the way this repo's prose lints
    # are: a whole-file `in` check would pass on the phrases appearing anywhere.
    flat = re.sub(r"\s+", " ", contract)
    start = "**Reading the CLI's version when a rule depends on it.**"
    end = "## Prerequisites: `uv`"
    assert start in flat, "the version-gate paragraph is gone"
    tail = flat.split(start, 1)[1]
    assert tail.count(end) == 1, (
        f"the section bound {end!r} occurs {tail.count(end)}x — a bound that is "
        f"not unique lets a renamed heading widen this slice silently")
    gate = tail.split(end, 1)[0]
    assert "no advisory" in gate.lower() and "at or above" in gate.lower(), (
        "the gate no longer says what the ABSENCE of the advisory means, which "
        "is the half that decides every case on a healthy install")
    linkers = sum((PLUGIN_ROOT / rel).read_text(encoding="utf-8")
                  .count("#cli-version-gate") for rel in _VERSION_GATE_SOURCES)
    assert linkers >= 4, (
        f"only {linkers} version-dependent rules link the gate; four were "
        f"rewritten to use it (Rust trait methods, frame sizes below 0.1.107, "
        f"comma-splitting below 0.1.126, and post-edit's demangling note)")


@needs_tools
def test_the_version_gate_says_unknown_when_the_hook_cannot_assert_one(tmp_path):
    """"No advisory ⇒ at or above the pin" was FALSE, and the state that breaks
    it is the one this initiative develops in.

    `_loci_resolve_install_spec` returns early for a `LOCI_DEV_CLI_PATH`
    checkout, leaving `_loci_cli_pinned` empty — so the stale-CLI advisory cannot
    fire however old the editable CLI is, and the context is byte-identical to a
    healthy pinned install. A model following the gate would report frame sizes
    as authoritative on a pre-0.1.107 CLI, which is the recorded 528 B → 4 B
    defect the rule exists for. `tasks/README.md` mandates that variable for
    every plugin task, and T16 hands the branch to QA the same way.

    Same hole with an unparseable `loci --version`. Both now say so, in a marker
    that carries no number — so `test_version_announcement`'s ban still holds.
    """
    home = tmp_path / "home"
    home.mkdir()
    proj, s = _initialized_project(
        tmp_path, home,
        env_extra=_cli_version_env(tmp_path, "0.1.1", dev=True))
    assert "loci CLI version: could not be determined" in s.ctx, (
        "a floating dev CLI reads as a healthy pinned install, so the four "
        "version gates take the newer branch on a CLI that may not implement it")
    assert "this plugin pins" not in s.ctx, (
        "the advisory fired, so this is not the state under test")
    assert "on PATH, v" not in s.ctx, (
        "the marker must carry no number — that format is retired")
    # …and it has a ceiling, which it did not when it was first added. This is a
    # THIRD block state, measured by neither of the two budget tests — and
    # `_cli_version_env`'s own `LOCI_DEV_CLI_PATH` clearing is what guaranteed
    # they never saw it. It is not transient the way the stale-CLI advisory is:
    # an unpinned install pays it on every session start for as long as it stays
    # unpinned, so it gets the tightest allowance of the three.
    size = _modelled_size(s.ctx)
    ceiling = _STEADY_STATE_BUDGET + _UNKNOWN_MARKER_ALLOWANCE
    assert size <= ceiling, (
        f"the initialized block models {size} B with the unknown-version "
        f"marker, over its {ceiling} B ceiling ({_STEADY_STATE_BUDGET} B "
        f"steady state + "
        f"{_UNKNOWN_MARKER_ALLOWANCE} B). The marker is the price of the gate "
        f"being decidable at all, so shorten it rather than raising this — "
        f"`#cli-version-gate` already carries the explanation.")


@needs_tools
def test_an_unreadable_cli_version_is_also_reported_as_unknown(tmp_path):
    """The second absence-producing state: `loci --version` prints nothing the
    hook can parse, so `_CLI_VER` is empty and the advisory cannot fire."""
    home = tmp_path / "home"
    home.mkdir()
    shim = tmp_path / "mute"
    shim.mkdir()
    (shim / "loci").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8",
                               newline="\n")
    (shim / "loci").chmod(0o755)
    uv_bin = tmp_path / "uvbin2"
    uv_bin.mkdir()
    proj, s = _initialized_project(
        tmp_path, home, env_extra={
            "PATH": f"{_to_bash_path(shim)}:{os.environ.get('PATH', '')}",
            "UV_TOOL_BIN_DIR": _to_bash_path(uv_bin),
            "LOCI_DEV_CLI_PATH": _to_bash_path(tmp_path / "nope"),
        })
    assert "loci CLI version: could not be determined" in s.ctx, s.ctx


def test_no_gated_rule_names_a_version_above_the_pin():
    """The gate's healthy-case row rests on "the pin is at or above every version
    the rules below name". That holds today (0.1.107 and 0.1.126 against a
    0.1.126 pin) and nothing was checking it.

    A rule naming 0.1.130 silently INVERTS the gate: "no advisory" would then
    mean "somewhere between the pin and 0.1.130", and the model would promise
    behaviour no shipped CLI has.
    """
    pin = tuple(int(x) for x in _pinned_cli_version().split("."))
    offenders = []
    # EVERY document that links the gate, not a two-entry tuple. Review put a
    # gated rule naming 0.1.130 into `skills/exec-trace/SKILL.md` and the lint
    # never looked — and nothing stops the next one landing in `stack-depth` or
    # `contract`. The regex also missed `v0.1.130` (`\b` between `v` and `0` is
    # not a boundary) and anything whose major version is not 0.
    linked = sorted({p for p in (PLUGIN_ROOT / "skills").rglob("*.md")
                     if "#cli-version-gate" in p.read_text(encoding="utf-8")}
                    | {PLUGIN_ROOT / rel for rel in _VERSION_GATE_SOURCES})
    for path in linked:
        rel = path.relative_to(PLUGIN_ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        # The trailing lookahead rejects a following DIGIT, not a following dot:
        # `(?![\w.])` made a sentence-final `0.1.130.` invisible, which is how
        # most people write one — round 2's own defeat re-landed by adding a full
        # stop. `1.2.3.4` still matches on its first three components, which is
        # the conservative direction.
        for m in re.finditer(r"(?<![\w.])v?(\d+)\.(\d+)\.(\d+)(?!\d)", text):
            v = tuple(int(g) for g in m.groups())
            if v > pin:
                line = text[:m.start()].count("\n") + 1
                offenders.append(f"{rel}:{line}: {m.group(0)} > pin "
                                 f"{_pinned_cli_version()}")
    assert not offenders, (
        "a rule gates on a CLI version the plugin does not pin, which inverts "
        "the `#cli-version-gate` reasoning:\n  " + "\n  ".join(offenders)
        + "\n(raise LOCI_CLI_VERSION in the same change, or drop the rule)")


def test_every_voice_skill_carries_its_own_voice_section():
    """The precondition for dropping LOCI_VOICE from the session context. If a
    skill loses its section the voice guidance is nowhere at all — so this
    fails rather than the session block quietly having to take it back."""
    for name in VOICE_SKILLS:
        text = (PLUGIN_ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        assert re.search(r"(?m)^#{2,3} LOCI voice remark\s*$", text), (
            f"skills/{name}/SKILL.md has no '## LOCI voice remark' section; the "
            f"session context no longer carries the voice block, so this skill "
            f"would render measurements with no voice guidance at all")


@needs_tools
def test_a_recipe_with_no_recorded_state_is_degraded_but_armed(tmp_path):
    """The state directory can be wiped independently of the repository. The
    recipe still governs, so the session arms and says what will heal it — and
    still does not scan, because a scan would invent a target."""
    home = tmp_path / "home"
    home.mkdir()
    proj = tmp_path / "fw"
    (proj / ".loci").mkdir(parents=True)
    (proj / ".loci" / "build.yaml").write_text("schema_version: 1\n")

    s = _run_session_init(proj, home)
    assert s.armed
    assert "has a recipe but no recorded state" in s.ctx
    assert "LOCI target:" not in s.ctx
    assert not s.detector_ran


@needs_tools
def test_a_recorded_recipe_that_vanished_is_degraded_not_rescanned(tmp_path):
    """``init_status: ok`` with no ``.loci/build.yaml`` found from here. The
    CLI's own discovery is the authority — it is the one that refuses to
    compile — so nothing is cleared and no scan runs; the recipe-derived target
    survives for the pre-edit hook, and the first analysis settles it."""
    home = tmp_path / "home"
    home.mkdir()
    proj, _ = _initialized_project(tmp_path, home)
    shutil.rmtree(proj / ".loci")

    s = _run_session_init(proj, home)
    assert not s.detector_ran
    assert "no `.loci/build.yaml` was found" in s.ctx
    assert s.context["loci_target"] == "armv7e-m", (
        "the recipe-derived target was clobbered by a guess")


@needs_tools
@pytest.mark.parametrize("status,marker", [
    ("unsupported", "LOCI: inactive (init: unsupported)"),
    ("needs_user", "LOCI: inactive (init: needs_user)"),
])
def test_a_recorded_refusal_disarms_without_scanning(tmp_path, status, marker):
    """``unsupported`` is permanent until ``/loci:init``; a headless
    ``needs_user`` waits for a question nobody has asked. Neither arms, neither
    scans, and neither may be reset by this hook."""
    target = tmp_path / "proj"
    target.mkdir()
    (target / "Makefile").write_text("all:\n\ttrue\n")
    home = tmp_path / "home"
    home.mkdir()

    first = _run_session_init(target, home)
    _amend_context(first, init_status=status)
    s = _run_session_init(target, home)

    assert marker in s.ctx
    assert not s.armed
    assert "do NOT apply in this session" in s.ctx
    assert not s.detector_ran, (
        "a recorded refusal must not cost a detector run either")
    assert s.context["init_status"] == status, "session-init reset a sticky status"
    # …and the scan's leftovers go with it. This project WAS armed a moment ago,
    # so its file held `loci_target: armv7e-m` from the machine — which
    # `pre-edit-hook.sh` would have gone on sending to `loci build snapshot`
    # long after the CLI declared the project unsupportable.
    assert "loci_target" not in s.context, (
        "a scan-derived target outlived the refusal that disarmed the project")
    assert "detection_status" not in s.context


@needs_tools
def test_a_failed_init_is_re_armed_at_the_next_session(tmp_path):
    """``failed`` is transient (§6.2): a build broken by the triggering edit
    must not disarm the project for ever — one attempt per session, never a
    permanent disarm."""
    target = tmp_path / "proj"
    target.mkdir()
    (target / "Makefile").write_text("all:\n\ttrue\n")
    home = tmp_path / "home"
    home.mkdir()

    first = _run_session_init(target, home)
    _amend_context(first, init_status="failed", init_reason="compile failed")
    s = _run_session_init(target, home)

    assert s.armed
    assert "`not_initialized`" in s.ctx
    assert s.context["init_status"] == "failed", (
        "the transient status was overwritten, losing why the last attempt failed")


@needs_tools
def test_an_empty_recorded_field_does_not_shift_the_others(tmp_path):
    """`IFS=$'\\t' read -r a b c …` COLLAPSES runs of tabs — tab is IFS
    whitespace — so one empty middle field shifted every later field left by
    one. An initialized project with no recorded `init_recipe` was told its ELF
    was its recipe, and a `0` count arrived where a path belonged.

    The same read then had to survive CRLF: the `jq` a Windows install puts on
    PATH writes `\\r\\n`, and `read` (unlike the command substitution it
    replaced) keeps the `\\r` — so an empty field came back as the
    one-character string `"\\r"`, which is not empty, and `loci_target` reached
    the model as `armv7e-m\\r`."""
    home = tmp_path / "home"
    home.mkdir()
    proj, _ = _initialized_project(tmp_path, home)
    first = _run_session_init(proj, home)
    # The exact shape: an empty middle field, and empty counts after it.
    _amend_context(first, init_recipe=None, artifact=None)

    s = _run_session_init(proj, home)
    assert "LOCI target: armv7e-m" in s.ctx, (
        "a field after the empty one was lost or corrupted")
    assert "artifact:" not in s.ctx, "there is no artifact to name"
    # The recipe line falls back to the walk's own answer, and names the recipe
    # — report §6.3's degraded branch is specified to do exactly that.
    recipe_lines = [ln for ln in s.ctx.splitlines() if ln.startswith("recipe: ")]
    assert len(recipe_lines) == 1 and recipe_lines[0].endswith("build.yaml"), (
        f"the recipe line was lost or is not a recipe: {recipe_lines}")
    # No trailing carriage return reached the model.
    assert "\r" not in s.ctx
    # And the empty-artifact log line is REACHABLE: `artifact` is compared
    # against the empty string, which `"\r"` never equalled.
    assert "recorded no artifact (recipe:" in s.log, (
        "the empty-artifact line cannot fire — the value is not what it is "
        "compared against")


@needs_tools
def test_a_recorded_recipe_or_artifact_that_is_gone_is_not_asserted(tmp_path):
    """A recipe recorded on another machine, or an artifact since deleted, was
    printed as fact — in the degraded block that says the recipe was not
    found."""
    home = tmp_path / "home"
    home.mkdir()
    proj, _ = _initialized_project(tmp_path, home)
    first = _run_session_init(proj, home)
    _amend_context(first, init_recipe="/nowhere/gone/.loci/build.yaml",
                   artifact="/nowhere/gone/app.elf")
    (proj / ".loci" / "build.yaml").unlink()

    s = _run_session_init(proj, home)
    assert "recipe:" not in s.ctx, "a recipe that does not exist was named"
    assert "artifact:" not in s.ctx, "an artifact that does not exist was named"
    assert "/nowhere/gone" not in s.ctx


@needs_tools
def test_the_mirror_carries_the_provenance_the_skills_read(tmp_path):
    """`validated` and `confirmed_by_user` are what the skills' provenance line
    renders when it has no envelope to read them from, and `init_recipe` is how
    a subdirectory session names the recipe at all. Stripping six keys out of
    the copied set left all 57 tests green."""
    home = tmp_path / "home"
    home.mkdir()
    proj, _ = _initialized_project(tmp_path, home)
    (proj / ".git").mkdir()
    first = _run_session_init(proj, home)
    _amend_context(first, init_at="2026-08-28T00:00:00Z", compiler_path="/opt/gcc",
                   init_candidates=[], validated="replay-compare",
                   confirmed_by_user=True)
    sub_dir = proj / "src"
    sub_dir.mkdir()

    s = _run_session_init(sub_dir, home)
    # Two keyed files now (the root's and this one), so select by project_root
    # rather than by `Session.context`, which insists on exactly one.
    own = next(json.loads(f.read_text(encoding="utf-8"))
               for f in sorted(s.state.glob("project-context-*.json"))
               if json.loads(f.read_text(encoding="utf-8"))["project_root"].endswith("src"))
    for key, value in (("validated", "replay-compare"), ("confirmed_by_user", True),
                       ("init_at", "2026-08-28T00:00:00Z"),
                       ("compiler_path", "/opt/gcc"), ("init_candidates", [])):
        assert own.get(key) == value, (
            f"the mirror did not carry {key!r} to the subdirectory's file")
    assert own["init_recipe"].endswith("build.yaml")


@needs_tools
def test_a_subdirectory_session_reads_the_recipe_roots_mirror(tmp_path):
    """`loci init` keys the context file by the RECIPE ROOT; this hook keys it
    by the SESSION CWD. For `cd repo/src && claude` those are two files, and
    without copying the CLI's facts across the second one never receives a
    single fact: the block said "a recipe but no recorded state" for ever, the
    pre-edit hook (which keys the same way) sent no `--loci-target`, and running
    `loci init` from the subdirectory healed the ROOT file — so nothing could
    fix it."""
    home = tmp_path / "home"
    home.mkdir()
    proj, _ = _initialized_project(tmp_path, home)
    (proj / ".git").mkdir()
    sub_dir = proj / "src" / "drivers"
    sub_dir.mkdir(parents=True)

    s = _run_session_init(sub_dir, home)
    state = home / ".loci" / "state"
    files = sorted(state.glob("project-context-*.json"))
    assert len(files) == 2, f"expected the root's file and the subdir's: {files}"

    assert "LOCI target: armv7e-m" in s.ctx
    assert "Compiler: arm-none-eabi-gcc, Build: cmake" in s.ctx
    assert "no recorded state" not in s.ctx
    assert not s.detector_ran

    # And in the FILE, because that is what `pre-edit-hook.sh` reads by name.
    loaded = [json.loads(f.read_text(encoding="utf-8")) for f in files]
    own = next(c for c in loaded if c["project_root"].endswith("drivers"))
    assert own["loci_target"] == "armv7e-m"
    assert own["init_status"] == "ok"
    # The identity keys stay this session's own — they name its measurement
    # files, and copying the root's would merge two histories silently.
    assert own["project_root"].endswith("drivers")
    assert own["cwd_hash"] != next(
        c for c in loaded if not c["project_root"].endswith("drivers"))["cwd_hash"]


@needs_tools
def test_an_init_that_finishes_during_the_scan_is_not_reverted(tmp_path):
    """The read→gate→write window. `detect_and_write_context` reads the context,
    then runs the detector, then writes — and an in-memory copy taken before the
    detector ran reverted a `loci init` that completed inside it: `init_status`
    back to `uninitialized` and every recorded fact gone. The window was seconds
    when the detector scanned; it is a file walk now, and still a window.

    The detector is replaced by a stub that writes into the keyed file exactly
    the way the CLI would, which is the race made deterministic. The stub emits
    the pre-T14 ten-key shape on purpose: the build facts in it must not reach
    the file either, whatever a stale detector says."""
    plugin = tmp_path / "plugin"
    (plugin / "hooks").mkdir(parents=True)
    (plugin / ".claude-plugin").mkdir(parents=True)
    shutil.copytree(PLUGIN_ROOT / "lib", plugin / "lib",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(PLUGIN_ROOT / "hooks" / "session-init.sh", plugin / "hooks")
    (plugin / ".claude-plugin" / "plugin.json").write_text('{"name":"loci","version":"9.9.9"}')

    target = tmp_path / "proj"
    target.mkdir()
    (target / "Makefile").write_text("all:\n\ttrue\n")
    home = tmp_path / "home"
    (home / ".loci" / "state").mkdir(parents=True)

    # First session: creates the keyed file, so the stub can find it by glob.
    subprocess.run([_find_bash(), _to_bash_path(plugin / "hooks" / "session-init.sh")],
                   env=_env(home), cwd=target, capture_output=True, text=True,
                   timeout=60, stdin=subprocess.DEVNULL)

    (plugin / "lib" / "detect-project.sh").write_text(
        "#!/usr/bin/env bash\n"
        "for f in \"$LOCI_STATE_DIR\"/project-context-*.json; do\n"
        "  [ -f \"$f\" ] || continue\n"
        "  jq '. + {init_status:\"ok\", loci_target:\"armv6-m\","
        " init_recipe:\"/p/.loci/build.yaml\", validated:\"compile-check\"}'"
        " \"$f\" > \"$f.new\" && mv -f \"$f.new\" \"$f\"\n"
        "done\n"
        "echo '{\"detection_status\":\"ok\",\"compiler\":\"gcc\","
        "\"build_system\":\"make\",\"loci_target\":\"armv7e-m\","
        "\"elf_files\":[],\"build_dirs\":[],\"loci_artifacts\":[],"
        "\"subproject_roots\":[],\"architecture\":\"x86_64\","
        "\"compiler_path\":null}'\n",
        encoding="utf-8", newline="\n")

    res = subprocess.run(
        [_find_bash(), _to_bash_path(plugin / "hooks" / "session-init.sh")],
        env=_env(home), cwd=target, capture_output=True, text=True,
        timeout=60, stdin=subprocess.DEVNULL)
    assert res.returncode == 0, res.stderr

    ctx = json.loads(next((home / ".loci" / "state").glob("project-context-*.json"))
                     .read_text(encoding="utf-8"))
    assert ctx["init_status"] == "ok", (
        "an init that landed during the scan was reverted to "
        f"{ctx['init_status']!r}")
    assert ctx["validated"] == "compile-check"
    assert ctx["init_recipe"] == "/p/.loci/build.yaml"
    for key in ("compiler", "build_system", "elf_files", "loci_artifacts",
                "architecture"):
        assert key not in ctx, f"the detector's {key} reached the file"


@needs_tools
def test_a_failed_init_beside_a_recipe_is_armed_and_says_why(tmp_path):
    """`init.py` clears `rec` for `recipe_stale`/`recipe_invalid`/
    `recipe_tampered`/`compiler_missing`, so `failed` is recorded with a
    `.loci/build.yaml` still on disk. Reporting that as "a recipe but no
    recorded state" was false twice over, and it withheld the arming §6.2's
    transient semantics require."""
    home = tmp_path / "home"
    home.mkdir()
    proj, _ = _initialized_project(tmp_path, home)
    first = _run_session_init(proj, home)
    _amend_context(first, init_status="failed",
                   init_reason="the recorded compiler is not on this machine")

    s = _run_session_init(proj, home)
    assert s.armed
    assert "has a build recipe, but the last initialization of it FAILED" in s.ctx
    assert "no recorded state" not in s.ctx
    assert "recipe_stale" in s.ctx and "compiler_missing" in s.ctx
    assert s.context["init_status"] == "failed"
    # And NOT the rule for the other state. Emitting both let the block say
    # "has a build recipe" and "this project has no recipe yet" at once, and
    # send the model to wait for a `not_initialized` the CLI cannot answer.
    assert "this project has no recipe yet" not in s.ctx
    assert "LOCI init rule:" not in s.ctx


@needs_tools
def test_an_unrecognised_status_beside_a_recipe_does_not_claim_there_is_none(tmp_path):
    """A newer CLI's status value. The recipe decides the branch; the status
    only decides the wording — ordered the other way round, an unknown value
    reached the "no recipe yet" block with a `.loci/build.yaml` on disk, and the
    only written recovery was to wait for a `not_initialized` that will never
    come."""
    home = tmp_path / "home"
    home.mkdir()
    proj, _ = _initialized_project(tmp_path, home)
    first = _run_session_init(proj, home)
    _amend_context(first, init_status="pending_review")

    s = _run_session_init(proj, home)
    assert s.armed
    assert "this project is not initialized" not in s.ctx
    assert "this project has no recipe yet" not in s.ctx
    assert "has a build recipe" in s.ctx
    # …and it is not called a failure. "The last initialization FAILED" is a
    # claim nothing supports about a value this version merely does not know.
    assert "does not recognise" in s.ctx
    assert "FAILED" not in s.ctx
    assert s.context["init_status"] == "pending_review", "a sticky status was reset"


@needs_tools
def test_the_degraded_state_block_asserts_no_fact_the_cli_did_not_record(tmp_path):
    """A project armed once, then handed a recipe (a `git pull`, or init run on
    another machine). Its file still holds the scan's PATH-derived `compiler`,
    and the degraded branch read it back as fact — `Compiler: clang++` directly
    above a line saying there is no recorded state, for a project with no
    clang++ anywhere in it."""
    target = tmp_path / "proj"
    (target / ".git").mkdir(parents=True)
    (target / "Makefile").write_text("all:\n\ttrue\n")
    home = tmp_path / "home"
    home.mkdir()

    first = _run_session_init(target, home)
    # The scan that wrote this is gone (T14); a file it left behind is not.
    _amend_context(first, compiler="clang++", build_system="make")
    (target / ".loci").mkdir()
    (target / ".loci" / "build.yaml").write_text("schema_version: 1\n")

    s = _run_session_init(target, home)
    assert "has a recipe but no recorded state" in s.ctx
    assert "Compiler:" not in s.ctx, "a scan-derived compiler was asserted as fact"
    assert "LOCI target:" not in s.ctx
    # The recipe itself IS named — that is the one thing this branch knows.
    assert "recipe: " in s.ctx


@needs_tools
def test_a_recipe_the_walk_cannot_reach_is_not_named_even_though_it_exists(tmp_path):
    """The mutation that survived the last round. Both existing tests DELETE the
    recipe, so `_loci_read_mirror_facts`' own `-f` check already clears it and
    the branch's own clear is never exercised. Here the recorded recipe is a
    real file somewhere else on disk — which is what a checkout moved, copied,
    or shared through a synced state directory looks like."""
    home = tmp_path / "home"
    home.mkdir()
    elsewhere = tmp_path / "elsewhere" / ".loci"
    elsewhere.mkdir(parents=True)
    (elsewhere / "build.yaml").write_text("schema_version: 1\n")
    (tmp_path / "elsewhere" / "app.elf").write_bytes(b"\x7fELF")

    proj, _ = _initialized_project(tmp_path, home)
    first = _run_session_init(proj, home)
    _amend_context(first,
                   init_recipe=_to_bash_path(elsewhere / "build.yaml"),
                   artifact=_to_bash_path(tmp_path / "elsewhere" / "app.elf"))
    shutil.rmtree(proj / ".loci")

    s = _run_session_init(proj, home)
    assert "no `.loci/build.yaml` was found" in s.ctx
    assert "recipe: " not in s.ctx, (
        "the block named a recipe — one that exists, but not one that governs "
        "this directory — in the same breath as saying none was found")
    assert "artifact: " not in s.ctx
    assert "elsewhere" not in s.ctx


@needs_tools
def test_the_block_that_says_no_recipe_was_found_does_not_name_one(tmp_path):
    """`init_recipe` records where the CLI last wrote a recipe. Printing that
    path in the very block that says none was found from here is a
    contradiction the model has to resolve, and the file may not exist on this
    machine at all."""
    home = tmp_path / "home"
    home.mkdir()
    proj, _ = _initialized_project(tmp_path, home)
    shutil.rmtree(proj / ".loci")

    s = _run_session_init(proj, home)
    assert "no `.loci/build.yaml` was found" in s.ctx
    assert "recipe: " not in s.ctx, "the block named a recipe it just said was missing"
    assert "artifact: " not in s.ctx
    # The recorded target still stands — the CLI wrote it, and it is what the
    # pre-edit hook needs until the first analysis settles this.
    assert s.context["loci_target"] == "armv7e-m"


@needs_tools
def test_a_detector_that_could_not_run_says_so(tmp_path):
    """A crashed detector is not evidence that this is not a project — but it
    used to print the `no_project` sentence, naming build files, for a
    directory that may well hold one."""
    plugin = tmp_path / "plugin"
    (plugin / "hooks").mkdir(parents=True)
    (plugin / ".claude-plugin").mkdir(parents=True)
    shutil.copytree(PLUGIN_ROOT / "lib", plugin / "lib",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(PLUGIN_ROOT / "hooks" / "session-init.sh", plugin / "hooks")
    (plugin / ".claude-plugin" / "plugin.json").write_text('{"name":"loci","version":"9.9.9"}')
    (plugin / "lib" / "detect-project.sh").write_text(
        "#!/usr/bin/env bash\nexit 1\n", encoding="utf-8", newline="\n")

    target = tmp_path / "proj"
    target.mkdir()
    (target / "Makefile").write_text("all:\n\ttrue\n")
    home = tmp_path / "home"
    (home / ".loci" / "state").mkdir(parents=True)

    res = subprocess.run(
        [_find_bash(), _to_bash_path(plugin / "hooks" / "session-init.sh")],
        env=_env(home), cwd=target, capture_output=True, text=True,
        timeout=60, stdin=subprocess.DEVNULL)
    assert res.returncode == 0, res.stderr
    ctx = json.loads(res.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "LOCI: inactive (detection: failed)" in ctx
    assert "not a claim that it is not a project" in ctx
    assert "no build file (Makefile" not in ctx, (
        "a detector crash was reported as an absence of build files")
    assert "LOCI auto-run rules" not in ctx


# ── the writer: merge, don't overwrite; clear, don't accumulate ─────────────

@needs_tools
def test_cli_written_keys_survive_a_session_start(tmp_path):
    """PARKED FINDING P3. ``detect_and_write_context`` used to overwrite this
    file wholesale ("Always overwrites — no stale state left"), so everything
    ``loci init`` recorded — ``init_status`` above all — was destroyed at the
    next SessionStart, and §6.2's "carried forward by session-init" was
    unimplementable. Asserted on the ARMED branch, the only one that writes
    scan output at all."""
    target = tmp_path / "proj"
    target.mkdir()
    (target / "Makefile").write_text("all:\n\ttrue\n")
    home = tmp_path / "home"
    home.mkdir()

    first = _run_session_init(target, home)
    _amend_context(first,
                   init_status="failed", init_at="2026-08-28T00:00:00Z",
                   init_reason="the tree did not build", init_candidates=[],
                   validated="unvalidated", confirmed_by_user=False,
                   init_recipe="/nowhere/.loci/build.yaml")

    after = _run_session_init(target, home).context
    assert after["init_status"] == "failed"
    assert after["init_at"] == "2026-08-28T00:00:00Z"
    assert after["init_reason"] == "the tree did not build"
    assert after["validated"] == "unvalidated"
    assert after["confirmed_by_user"] is False
    assert after["init_recipe"] == "/nowhere/.loci/build.yaml"


@needs_tools
def test_a_field_the_scan_no_longer_emits_is_deleted(tmp_path):
    """The other half of the merge rule. A blanket merge would keep the eleven
    dropped fields — and a stale ``build_system`` a scanning session wrote — for
    ever on a machine upgrading into this version."""
    target = tmp_path / "proj"
    target.mkdir()
    (target / "Makefile").write_text("all:\n\ttrue\n")
    home = tmp_path / "home"
    home.mkdir()

    first = _run_session_init(target, home)
    # All ELEVEN — the ten report §6.3's audit dropped, and `loci_artifacts`,
    # which T14 dropped with the scan — because the owned-key list could lose
    # six of them and the suite stayed green. The promise in the comment is
    # the list, so the test is the list.
    dead = {"asm_files": [], "binaries": [], "build_compiler": "gcc",
            "cross_compilers": ["arm-none-eabi-gcc"], "detected_at": "2026-01-01T00:00:00Z",
            "language_stack": ["cpp"], "loci_compatible": True,
            "project_type": "cpp", "scan_depth": 8, "source_files": ["main.c"],
            "loci_artifacts": [{"path": "/x/a.o", "mtime": 1}]}
    _amend_context(first, build_system="cargo", **dead)

    after = _run_session_init(target, home).context
    for key in dead:
        assert key not in after, f"{key} survived a session start"
    assert "build_system" not in after, (
        "a build fact a scanning session left behind was kept as if a recipe "
        "had recorded it — no recipe governs this project")


@needs_tools
def test_a_machine_derived_target_already_in_the_file_is_deleted(tmp_path):
    """Two halves, and the tests only pinned one. Stripping the machine keys
    out of the SCAN's fresh output does nothing about a value a PREVIOUS plugin
    version left in the file — and `pre-edit-hook.sh` reads `loci_target` by
    name and ships it to `loci build snapshot`."""
    target = tmp_path / "proj"
    target.mkdir()
    (target / "Makefile").write_text("all:\n\ttrue\n")
    home = tmp_path / "home"
    home.mkdir()

    first = _run_session_init(target, home)
    # What a pre-T08 plugin left behind on a host-x86 project with a cross-gcc
    # on PATH.
    _amend_context(first, loci_target="armv7e-m", architecture="armv7e-m")

    after = _run_session_init(target, home).context
    assert "loci_target" not in after, (
        f"a stale machine-derived target survived: {after.get('loci_target')!r}")
    assert "architecture" not in after


@needs_tools
def test_a_refusal_clears_every_scan_key_not_just_the_target(tmp_path):
    """A refusal is exactly the state in which no recipe governs, so every
    build fact in the file is a leftover — a pre-T14 scan's, or an init's since
    refused — and `pre-edit-hook.sh` reads `loci_target` by name. Gutting the
    key list down to two names once left the suite green while those keys
    outlived the refusal that disarmed the project."""
    target = tmp_path / "proj"
    target.mkdir()
    (target / "Makefile").write_text("all:\n\ttrue\n")
    (target / "main.c").write_text("int main(){}")
    home = tmp_path / "home"
    home.mkdir()

    first = _run_session_init(target, home)
    # What a pre-T14 plugin's scan left in the file, beside the refusal.
    _amend_context(first, init_status="unsupported",
                   compiler="arm-none-eabi-gcc", compiler_path="/usr/bin/arm-none-eabi-gcc",
                   build_system="make", architecture="armv7e-m", loci_target="armv7e-m",
                   artifact="/x/app.elf", elf_files=["/x/app.elf"],
                   build_dirs=["/x"],
                   loci_artifacts=[{"path": "/x/a.o"}], subproject_roots=[])

    after = _run_session_init(target, home).context
    for key in ("compiler", "compiler_path", "build_system", "architecture",
                "artifact", "elf_files", "build_dirs", "loci_artifacts",
                "loci_target", "detection_status", "subproject_roots"):
        assert key not in after, f"{key} outlived the refusal that disarmed it"
    assert after["init_status"] == "unsupported"


@needs_tools
def test_the_scan_key_list_matches_what_the_detector_emits(tmp_path):
    """Both files call this equality load-bearing — "adding a key here claims it
    away from the CLI" — and nothing tested it. If the emit gains a key the
    writer does not own, that key is never cleared; if it loses one, the writer
    deletes something no longer replaced."""
    target = tmp_path / "proj"
    target.mkdir()
    (target / "Makefile").write_text("all:\n\ttrue\n")
    emitted = set(_detect(target, tmp_path / "home"))

    text = (PLUGIN_ROOT / "lib" / "setup-steps.sh").read_text(encoding="utf-8")
    m = re.search(r"^_LOCI_SCAN_KEYS='(\[[^']*\])'", text, re.M)
    assert m, "_LOCI_SCAN_KEYS is not where the writer keeps it"
    assert set(json.loads(m.group(1))) == emitted, (
        "detect-project.sh's emit and the writer's scan-key list have drifted:\n"
        f"  emit only:  {sorted(emitted - set(json.loads(m.group(1))))}\n"
        f"  list only:  {sorted(set(json.loads(m.group(1))) - emitted)}")


@needs_tools
def test_the_identity_keys_are_always_refreshed(tmp_path):
    """``cwd_hash``/``branch_slug`` name the measurement and stats files; every
    branch must write them, including the ones that never scan."""
    home = tmp_path / "home"
    home.mkdir()
    _, s = _initialized_project(tmp_path, home)
    ctx = s.context
    assert ctx["cwd_hash"] and ctx["branch_slug"] and ctx["project_root"]
    assert ctx["cwd_hash"] in s.context_file.name


@needs_tools
@pytest.mark.parametrize("junk", [
    "{not json",
    "[1,2,3]",
    '"a string"',
    "",
    # Two concatenated objects: the read streamed BOTH, merged the session's
    # fields into each, and wrote both back — a file that used to heal itself
    # every session instead propagated for ever, and every downstream
    # `jq -r '.loci_target'` answered with two lines.
    '{"init_status":"ok","loci_target":"tc399"}\n{"a":1}',
])
def test_a_corrupt_context_file_heals_instead_of_propagating(tmp_path, junk):
    """Nothing in a corrupt file can be preserved, and a hook that dies takes
    the whole SessionStart with it. One object out, always."""
    target = tmp_path / "proj"
    target.mkdir()
    (target / "Makefile").write_text("all:\n\ttrue\n")
    home = tmp_path / "home"
    home.mkdir()

    first = _run_session_init(target, home)
    first.context_file.write_text(junk, encoding="utf-8")
    s = _run_session_init(target, home)
    assert s.armed
    assert s.context["detection_status"] == "ok"      # json.loads: exactly one object
    assert s.context.get("a") is None, "the second object survived the heal"


@needs_tools
def test_a_context_path_that_is_a_directory_is_refused(tmp_path):
    """`mv -f "$TMP" "$KEYED"` moves INTO a directory and reports success, so
    the session was told a directory was its context file while a
    `.tmp.<pid>` accumulated inside it every start."""
    target = tmp_path / "proj"
    target.mkdir()
    (target / "Makefile").write_text("all:\n\ttrue\n")
    home = tmp_path / "home"
    home.mkdir()

    first = _run_session_init(target, home)
    path = first.context_file
    path.unlink()
    path.mkdir()

    s = _run_session_init(target, home)
    assert not list(path.glob("*.tmp.*")), "a temp file was left inside the directory"
    # The session still comes up — a broken state directory must never take
    # SessionStart down with it.
    assert "loci version:" in s.ctx


@needs_tools
def test_a_broken_cli_is_reported_even_where_loci_is_inactive(tmp_path):
    """A docs repo is where a user most often asks "why is loci not working".
    The inactive blocks drop the healthy `loci command: loci (on PATH)` line to
    save bytes; they must not drop the line that says something is wrong."""
    target = tmp_path / "docs"
    (target / ".git").mkdir(parents=True)
    (target / "README.md").write_text("# docs")
    home = tmp_path / "home"
    (home / ".loci" / "state").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    # jq stays reachable — without it the hook takes its own deps-missing exit
    # and this would assert nothing about the branch under test. `uv` and `loci`
    # do not: that is the "prerequisite uv is missing" branch.
    # jq and the ordinary Unix tools stay reachable — without them the hook
    # takes its own deps-missing exit, or dies for want of `uname`, and this
    # would assert nothing about the branch under test. `uv` and `loci` live in
    # `~/.local/bin` / the uv shim dir, neither of which is here and neither of
    # which exists under the fixture HOME: that is the "prerequisite uv is
    # missing" branch.
    jq_dir = _to_bash_path(Path(shutil.which("jq")).parent)
    s = _run_session_init(
        target, home,
        PATH=f"{_to_bash_path(bin_dir)}:{jq_dir}:/usr/bin:/bin:/usr/local/bin")
    assert "LOCI: inactive" in s.ctx
    assert "loci: NOT installed" in s.ctx, (
        f"a broken CLI was invisible in an inactive session:\n{s.ctx}")
    assert "loci command: loci (on PATH)" not in s.ctx, (
        "the healthy line is what the inactive block drops")


@needs_tools
def test_legacy_measurements_migrate_even_when_the_cli_wrote_the_context_first(tmp_path):
    """The migration renames THREE files, and `loci init` writes the context
    under the new key by itself. Guarding the pass on that file's existence
    meant a project initialized before its first session-init had its
    measurement and stats history orphaned under the legacy key — silently,
    with `/loci:trends` showing nothing."""
    target = tmp_path / "proj"
    target.mkdir()
    (target / "Makefile").write_text("all:\n\ttrue\n")
    home = tmp_path / "home"
    state = home / ".loci" / "state"
    state.mkdir(parents=True)

    # Learn this project's key and slug from a real run, then stage the legacy
    # trio around it — the CLI's context file included.
    first = _run_session_init(target, home)
    new_hash = first.context["cwd_hash"]
    slug = first.context["branch_slug"]
    legacy = "a" * 12
    (state / f"project-context-{legacy}.json").write_text(
        json.dumps({"project_root": str(target), "cwd_hash": legacy}), encoding="utf-8")
    (state / f"loci-measurements-{legacy}-{slug}.jsonl").write_text('{"f":1}\n',
                                                                    encoding="utf-8")
    (state / f"loci-stats-{legacy}-{slug}.json").write_text('{"functions":3}',
                                                            encoding="utf-8")
    # The marker the previous pass would have left; delete it so this session
    # is the one that migrates (the real first-contact ordering).
    for m in state.glob(".migrated-*"):
        m.unlink()

    _run_session_init(target, home)
    assert (state / f"loci-measurements-{new_hash}-{slug}.jsonl").is_file(), (
        "the measurement history was left orphaned under the legacy key")
    assert (state / f"loci-stats-{new_hash}-{slug}.json").is_file()
    assert not (state / f"loci-measurements-{legacy}-{slug}.jsonl").exists()


@needs_tools
@pytest.mark.skipif(sys.platform != "win32",
                    reason="needs an OS where an open handle blocks a rename")
def test_the_marker_is_withheld_when_a_rename_failed(tmp_path):
    """The marker is what makes the migration a one-shot, so writing it after a
    FAILED `mv` would drop the file on the floor for ever — the retry the
    docstring's "idempotent" promises. Removing the `_migrate_failed` guard left
    all 57 tests green."""
    target = tmp_path / "proj"
    target.mkdir()
    (target / "Makefile").write_text("all:\n\ttrue\n")
    home = tmp_path / "home"
    state = home / ".loci" / "state"
    state.mkdir(parents=True)

    first = _run_session_init(target, home)
    new_hash, slug = first.context["cwd_hash"], first.context["branch_slug"]
    legacy = "b" * 12
    (state / f"project-context-{legacy}.json").write_text(
        json.dumps({"project_root": str(target), "cwd_hash": legacy}), encoding="utf-8")
    measurements = state / f"loci-measurements-{legacy}-{slug}.jsonl"
    measurements.write_text('{"f":1}\n', encoding="utf-8")
    for m in state.glob(".migrated-*"):
        m.unlink()

    # Hold the file open: Windows refuses to rename it out from under us.
    with measurements.open("r+", encoding="utf-8"):
        _run_session_init(target, home)

    assert measurements.is_file(), "the fixture did not actually block the rename"
    assert not list(state.glob(f".migrated-{new_hash}-{slug}")), (
        "the marker was written despite a failed rename, so the retry the "
        "next session owes this file will never happen")

    # …and the next session, with the handle released, completes it.
    _run_session_init(target, home)
    assert (state / f"loci-measurements-{new_hash}-{slug}.jsonl").is_file()
    assert list(state.glob(f".migrated-{new_hash}-{slug}"))


@needs_tools
def test_the_migration_marker_stops_the_scan_repeating(tmp_path):
    """It is a one-shot that used to cost a `jq` and a `stat` per file in the
    state directory on every session — ~13 s of a ~17 s SessionStart against a
    45-project directory."""
    target = tmp_path / "proj"
    target.mkdir()
    (target / "Makefile").write_text("all:\n\ttrue\n")
    home = tmp_path / "home"
    home.mkdir()

    first = _run_session_init(target, home)
    state = home / ".loci" / "state"
    markers = list(state.glob(".migrated-*"))
    assert len(markers) == 1, f"no completion marker was written: {list(state.iterdir())}"
    assert first.context["cwd_hash"] in markers[0].name
    assert first.context["branch_slug"] in markers[0].name, (
        "the marker must be per-branch: the measurement and stats files are "
        "named per branch, so a branch first seen later has its own pair to move")


# ── the recipe walk ─────────────────────────────────────────────────────────

@needs_tools
def test_a_recipe_at_the_repo_root_governs_a_subdirectory_session(tmp_path):
    """One recipe per repo root; a session opened in a subdirectory finds it."""
    home = tmp_path / "home"
    home.mkdir()
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".loci").mkdir()
    (repo / ".loci" / "build.yaml").write_text("schema_version: 1\n")
    sub = repo / "drivers" / "uart"
    sub.mkdir(parents=True)

    s = _run_session_init(sub, home)
    assert "has a recipe but no recorded state" in s.ctx
    assert not s.detector_ran


@needs_tools
def test_the_walk_stops_at_the_git_toplevel(tmp_path):
    """A recipe in an unrelated PARENT of the checkout must not govern it —
    otherwise one stray ``~/projects/.loci/build.yaml`` measures every repo
    underneath it against one target."""
    home = tmp_path / "home"
    home.mkdir()
    parent = tmp_path / "projects"
    (parent / ".loci").mkdir(parents=True)
    (parent / ".loci" / "build.yaml").write_text("schema_version: 1\n")
    repo = parent / "unrelated"
    (repo / ".git").mkdir(parents=True)
    (repo / "notes.md").write_text("# hi")

    s = _run_session_init(repo, home)
    assert "LOCI: inactive (detection: no_project)" in s.ctx
    assert "recipe:" not in s.ctx


@needs_tools
def test_a_recipe_in_home_is_never_adopted(tmp_path):
    """``~/.loci`` is the STATE directory. A ``~/.loci/build.yaml`` there is far
    likelier to be stray state than a deliberate recipe for every project under
    the home directory, and adopting it silently is the worst kind of wrong."""
    home = tmp_path / "home"
    (home / ".loci").mkdir(parents=True)
    (home / ".loci" / "build.yaml").write_text("schema_version: 1\n")
    target = home / "scratch"
    target.mkdir()

    s = _run_session_init(target, home)
    assert "LOCI: inactive (detection: no_project)" in s.ctx
    assert "recipe:" not in s.ctx


# ── advertisements ──────────────────────────────────────────────────────────

@needs_tools
def test_the_available_line_advertises_init(tmp_path):
    target = tmp_path / "proj"
    target.mkdir()
    (target / "Makefile").write_text("all:\n\ttrue\n")
    home = tmp_path / "home"
    home.mkdir()

    s = _run_session_init(target, home)
    available = [ln for ln in s.ctx.splitlines() if ln.startswith("Available:")]
    assert len(available) == 1
    assert "/loci:init" in available[0]

    # And in the initialized block, which is where a user goes to SWITCH target.
    _, s2 = _initialized_project(tmp_path, home / "other")
    available = [ln for ln in s2.ctx.splitlines() if ln.startswith("Available:")]
    assert len(available) == 1 and "/loci:init" in available[0]


# ── `loci_cli_version` — two wrong spellings shipped because nothing drove it ──

@pytest.mark.parametrize("printed,expected", [
    ("loci 0.1.126", "0.1.126"),
    # The greedy-`.*` defeat: `s/.*[^0-9.]\(…\)/\1/` backtracks to the RIGHTMOST
    # match and returned `3.11.5` here — non-empty (so the "not asserted" marker
    # does not fire) AND newer than the pin (so the advisory does not fire), which
    # is round 1's CRITICAL re-entered through the fix for it.
    ("loci 0.1.126 (Python 3.11.5)", "0.1.126"),
    # The `tr -cd '0-9.'` defeat: it deleted the separators inside the tag and
    # produced `0.1.971`, which outranks a 0.1.126 pin.
    ("loci 0.1.97-rc1", "0.1.97"),
    ("v0.1.126", "0.1.126"),
    ("1.0.0-beta+2", "1.0.0"),
    # Multi-line: the first line wins, not a concatenation of all of them.
    ("loci 0.1.126\nbuilt from 9.9.9", "0.1.126"),
    # Nothing parseable is the empty string, which every caller reads as
    # "unknown" — and which now makes the hook say so out loud.
    ("loci", ""),
    ("", ""),
])
def test_the_cli_version_extractor_takes_the_first_dotted_version(printed, expected,
                                                                  tmp_path):
    """Drives the shipped `loci_cli_version`, not a copy of it.

    It had no test at all, which is how two different wrong extractions reached
    the branch — both failing in the same direction, suppressing the stale-CLI
    advisory on a CLI that is behind the pin.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "loci"
    stub.write_text("#!/usr/bin/env bash\ncat <<'V'\n" + printed + "\nV\n",
                    encoding="utf-8", newline="\n")
    stub.chmod(0o755)
    script = (
        'set -uo pipefail\n'
        f'PLUGIN_DIR="{PLUGIN_ROOT.as_posix()}"\n'
        f'PATH="{_to_bash_path(bin_dir)}:$PATH"\n'
        f'. "{(PLUGIN_ROOT / "lib" / "setup-steps.sh").as_posix()}"\n'
        'printf "[%s]" "$(loci_cli_version)"\n'
    )
    proc = subprocess.run([_find_bash(), "-c", script], capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=120)
    assert proc.stdout.strip() == f"[{expected}]", (
        f"`loci --version` printed {printed!r}; extractor said "
        f"{proc.stdout.strip()!r}, expected [{expected}]. {proc.stderr[:300]}")


#: Each row of the `#cli-version-gate` table, by the phrase that makes it that
#: row. Registered because both of round 2's repairs to this table were
#: unpinned: deleting row 4 and dropping row 2's qualifier left the only test
#: that reads the section green, and that restores the two-rows-match defect the
#: qualifier was added for.
_GATE_ROWS = (
    "the advisory, naming a number",
    "no advisory **and** no `loci CLI version:` line",
    "a `loci CLI version: could not be determined` line",
    "no `loci command:` line at all",
)


def test_the_version_gate_table_keeps_all_four_rows_and_their_qualifiers():
    """A four-state gate with three rows sends the model down a branch it has no
    rule for; a row-2 without its qualifier makes two rows match at once.

    Round 2 added both and neither was pinned —
    `test_no_version_gate_reads_a_number_off_a_line_that_carries_none` asserts
    only that the words "no advisory" and "at or above" appear somewhere in the
    section, which survives deleting a whole row.
    """
    contract = (PLUGIN_ROOT / "skills" / "_shared"
                / "loci-runtime-contract.md").read_text(encoding="utf-8")
    flat = re.sub(r"[ \t]+", " ", contract)
    start = flat.index("| What the context shows |")
    end = flat.index("Read the rows in order", start)
    table = flat[start:end]
    rows = [ln for ln in table.splitlines()
            if ln.strip().startswith("|") and not set(ln.strip()) <= set("|- ")]
    # The header plus four states.
    assert len(rows) == 5, (
        f"the gate table has {len(rows) - 1} state rows, not 4:\n"
        + "\n".join(rows))
    for phrase in _GATE_ROWS:
        assert phrase in table, (
            f"the gate table lost the row keyed on {phrase!r}. Every branch of "
            f"session-init.sh emits one of these four shapes; a missing row is a "
            f"state the four version-gated rules have no answer for.")
    # …and row 2's qualifier specifically, because without it rows 2 and 3 both
    # match the unknown-marker state and give OPPOSITE answers.
    assert "no advisory **and** no `loci CLI version:` line" in table, (
        "row 2 no longer excludes the unknown-version marker, so two rows match "
        "the state row 3 exists for")


# ── Go trees at the gate ─────────────────────────────────────────────────────
#
# Classification — "is this tree's build Go, or is the `go.mod` a code
# generator's?" — is `loci init`'s alone since T14 (`init_evidence.detect_build_system`,
# tested in the CLI). The plugin's second answer to that question went with the
# scan, and with it the drift two answers guarantee — three T15 review rounds'
# worth. What the plugin still decides is ARMING, and every one of these shapes
# must arm: each declares a build at its root, and a session that stays inactive
# on a Go module is the defect T15 opened with.

def _go_tree(root: Path, files) -> Path:
    for rel in files:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x\n", encoding="utf-8")
    return root


#: Real shapes from T15's defects and their controls. What each one IS is the
#: CLI's to say (a cgo binding is Go; C firmware with a code generator's `go.mod`
#: is make; cmake outranks everything); that each one ARMS is this gate's.
_GO_SHAPES = [
    ("a pure Go module", ["go.mod", "main.go"]),
    ("a cgo binding, C beside its Go",
     ["go.mod", "sdl/sdl.go", "sdl/audio.go", "sdl/render.go", "sdl/bridge.c",
      "sdl/sdl.h", "sdl/audio.h", "sdl/video.h"]),
    ("a Go module shipping Plan 9 assembly",
     ["go.mod", "xor.go", "xor_amd64.s", "xor_arm64.s", "xor_ppc64.s"]),
    ("C firmware whose go.mod is a code generator's",
     ["Makefile", "go.mod", "src/main.c", "include/a.h", "include/b.h",
      "include/c.h", "startup/boot.S", "tools/gen/main.go",
      "tools/gen/tables.go"]),
    ("one Go tool beside one C source",
     ["Makefile", "go.mod", "src/main.c", "tools/gen.go"]),
    ("uppercase suffixes on both sides",
     ["Makefile", "go.mod", "src/MAIN.C", "src/UTIL.H", "tools/GEN.GO"]),
    ("a Go module whose sources are uppercase", ["go.mod", "MAIN.GO"]),
    ("cmake beside go.mod",
     ["CMakeLists.txt", "go.mod", "a.go", "b.go", "c.go", "main.c"]),
    ("a Go module vendoring C",
     ["go.mod", "main.go", "vendor/dep/a.c", "vendor/dep/b.c",
      "vendor/dep/c.h"]),
]


@needs_tools
@pytest.mark.parametrize("name,files", _GO_SHAPES, ids=[s[0] for s in _GO_SHAPES])
def test_every_go_shape_passes_the_gate(name, files, tmp_path):
    """The behaviour, not the source text: the real script on a real tree."""
    target = _go_tree(tmp_path / "proj", files)
    info = _detect(target, tmp_path / "home")
    assert info["detection_status"] == "ok", (
        f"{name}: the gate answered {info['detection_status']!r}; a declared "
        f"build at the root must arm")
    assert "build_system" not in info, "the plugin is classifying trees again"


# ── todo 027: the fields the keyed file no longer carries ───────────────────

def test_the_writer_owns_no_pruned_key():
    """The four key sets, read off the file that holds them.

    `architecture` was `loci_target` under a second name; `elf_files` and
    `build_dirs` were lists whose one useful entry — the recipe's linked image —
    is the scalar `artifact`. All three belong to DEAD, which is deleted on every
    branch, and to none of the sets that are replaced or carried forward.
    """
    text = (PLUGIN_ROOT / "lib" / "setup-steps.sh").read_text(encoding="utf-8")
    owned = {}
    for name in ("_LOCI_DEAD_KEYS", "_LOCI_SCAN_KEYS", "_LOCI_CLI_KEYS",
                 "_LOCI_RECIPE_KEYS"):
        m = re.search(rf"^{name}='(\[[^']*\])'", text, re.M)
        assert m, f"{name} is not where the writer keeps it"
        owned[name] = set(json.loads(m.group(1)))

    pruned = {"architecture", "elf_files", "build_dirs"}
    assert pruned <= owned["_LOCI_DEAD_KEYS"], (
        f"a pruned key is not deleted on every branch: "
        f"{sorted(pruned - owned['_LOCI_DEAD_KEYS'])}")
    for name in ("_LOCI_SCAN_KEYS", "_LOCI_CLI_KEYS", "_LOCI_RECIPE_KEYS"):
        assert not pruned & owned[name], (
            f"{name} still owns {sorted(pruned & owned[name])}")
    # The scalar that replaced the two lists is carried forward AND mirrored: it
    # is `loci init`'s to write, and it is worthless without the recipe.
    assert "artifact" in owned["_LOCI_CLI_KEYS"]
    assert "artifact" in owned["_LOCI_RECIPE_KEYS"]
    assert "artifact" not in owned["_LOCI_DEAD_KEYS"]


@needs_tools
def test_the_pruned_keys_go_even_where_the_mirror_is_kept(tmp_path):
    """The initialized branch keeps every recorded build fact, so it is the one
    branch a dead key could survive on. A file an older plugin or CLI left them
    in loses them at the next session start, and nothing writes migration code
    for a file that is rebuilt on every SessionStart."""
    home = tmp_path / "home"
    home.mkdir()
    proj, first = _initialized_project(tmp_path, home)
    _amend_context(first, architecture="armv7e-m",
                   elf_files=[_to_bash_path(proj / "build" / "app.elf")],
                   build_dirs=[_to_bash_path(proj / "build")])

    s = _run_session_init(proj, home)

    for key in ("architecture", "elf_files", "build_dirs"):
        assert key not in s.context, f"{key} survived a session start"
    # A prune, not a wipe: the mirror's own facts are still there, and the
    # `artifact:` line is still read out of `artifact`.
    assert s.context["loci_target"] == "armv7e-m"
    assert s.context["artifact"].endswith("app.elf")
    assert "artifact: " in s.ctx and "app.elf" in s.ctx


# ── AAD-7607: a project whose recipe records a binary and no database ─────

@needs_tools
def test_an_artifact_only_project_is_told_its_scope_instead_of_the_auto_runs(tmp_path):
    """The two rules are mutually exclusive, and that is the design.

    Under this recipe `loci analyse prepare` answers `compdb_absent` for every
    source, so "after any Edit/Write … you MUST invoke loci-post-edit" is an
    instruction to go and be refused. Printing both rules would leave the block
    arguing with itself, and a session-start line loses that argument to a skill
    description that says MANDATORY — which is why `_DISARM` names its competitors
    rather than contradicting them in the abstract. So one replaces the other.
    """
    home = tmp_path / "home"
    home.mkdir()
    proj, s = _initialized_project(tmp_path, home, artifact_only=True)

    assert "recipe is artifact-only" in s.ctx, (
        f"the scope was never stated: {s.ctx!r}")
    assert "you MUST invoke the loci:loci-post-edit skill" not in s.ctx, (
        "the auto-run rules survived beside the rule that countermands them")
    # The three things the allowance buys, each of which the ticket's session
    # needed and did not have.
    for promised in ("stack-depth", "memory-report", "control-flow"):
        assert promised in s.ctx, f"{promised} is not named as working here"
    assert "do NOT rebuild or relink" in s.ctx, (
        "the rebuild refusal is the one this ticket was filed for")
    assert "do NOT run /loci:init" in s.ctx


@needs_tools
def test_an_ordinary_project_still_gets_the_auto_run_rules(tmp_path):
    """The non-vacuity control: same fixture, one field different."""
    home = tmp_path / "home"
    home.mkdir()
    proj, s = _initialized_project(tmp_path, home)

    assert "you MUST invoke the loci:loci-post-edit skill" in s.ctx
    assert "recipe is artifact-only" not in s.ctx


@needs_tools
def test_the_artifact_only_block_stays_inside_its_own_allowance(tmp_path):
    """A substitution, so the ceiling is the steady state plus the DIFFERENCE
    between the two rules — not plus the rule.

    The CLI version is pinned here for P68's reason, and this test re-earned it:
    unpinned, it measured 2 826 B against a 2 603 B ceiling on a machine whose
    `loci` was behind the pin this very branch raised — 240 B of version-skew
    advisory, an environment fact, charged to a prose budget. The advisory has its
    own ceiling two tests up; this one is about the substitution.
    """
    home = tmp_path / "home"
    home.mkdir()
    proj, s = _initialized_project(
        tmp_path, home, artifact_only=True,
        env_extra=_cli_version_env(tmp_path, _pinned_cli_version()))
    assert "this plugin pins" not in s.ctx, (
        "a skew advisory is in this block, so the shim is not the `loci` the hook "
        "resolved and the number below is an environment fact")

    size = _modelled_size(s.ctx)
    ceiling = _STEADY_STATE_BUDGET + _ARTIFACT_ONLY_ALLOWANCE
    assert size <= ceiling, (
        f"the artifact-only block models {size} B, over its {ceiling} B ceiling "
        f"({_STEADY_STATE_BUDGET} B steady state + {_ARTIFACT_ONLY_ALLOWANCE} B "
        f"for the substitution)")


@needs_tools
def test_a_false_artifact_only_is_not_a_true_one(tmp_path):
    """`false` and the absent key are the same answer, and neither is `true`.

    The mirror read tests `= "true"` rather than `-n`, because every spelling
    including `false` passes a `-n` — and the answer this must not get wrong is the
    positive: a project WITH a database whose auto-runs got disarmed stops being
    measured.
    """
    home = tmp_path / "home"
    home.mkdir()
    proj, s = _initialized_project(tmp_path, home, artifact_only=False)

    assert "recipe is artifact-only" not in s.ctx
    assert "you MUST invoke the loci:loci-post-edit skill" in s.ctx
