"""The cheap gate, and the one session block that no longer depends on it.

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

Session start no longer consults the gate (AAD-7531): every directory gets the
same block, and the gate stays on disk with no caller but these tests.
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
    """One session-init run: its hook payload and its log."""

    def __init__(self, payload: dict, state: Path):
        self.payload = payload
        self.state = state

    @property
    def ctx(self) -> str:
        return self.payload["hookSpecificOutput"]["additionalContext"]

    @property
    def log(self) -> str:
        path = self.state / "loci.log"
        return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""

    @property
    def detector_ran(self) -> bool:
        return "start: detect-project" in self.log


def _run_session_init(cwd: Path, home: Path, **extra: str) -> Session:
    state = home / ".loci" / "state"
    # A fresh log per run, so `detector_ran` answers about THIS run.
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
                         env_extra: dict | None = None) -> tuple[Path, Session]:
    proj = tmp_path / "fw"
    (proj / ".loci").mkdir(parents=True)
    (proj / ".loci" / "build.yaml").write_text("schema_version: 1\ntarget: armv7e-m\n")
    (proj / "build").mkdir()
    (proj / "build" / "app.elf").write_bytes(b"\x7fELF")
    return proj, _run_session_init(proj, home, **(env_extra or {}))


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


# ── colcon (ROS 2) workspace roots ──────────────────────────────────────────
#
# A workspace root declares no build of its own: the packages are under `src/`
# and colcon's output is under `build/ install/ log/`. Reproduced on a real ROS
# workspace — the session went inactive AT the workspace root, the directory a
# ROS developer opens, under a sentence saying no build file declares a build
# here, while `cd src/<pkg>` armed fine off the package's own `CMakeLists.txt`.
# The package's file is at depth 3 and the nested walk stops at 2.

def _a_colcon_package(root: Path, rel: str = "src/loci_demo") -> Path:
    pkg = root / rel
    pkg.mkdir(parents=True)
    (pkg / "package.xml").write_text(
        "<package format=\"3\"><name>loci_demo</name></package>\n")
    (pkg / "CMakeLists.txt").write_text("project(loci_demo)\n")
    return pkg


def _colcon_output(root: Path, stamp: str = "build/COLCON_IGNORE") -> None:
    for name in ("build", "install", "log"):
        (root / name).mkdir(parents=True, exist_ok=True)
    marker = root / stamp
    marker.parent.mkdir(parents=True, exist_ok=True)
    # A directory where colcon keeps a symlink (`log/latest_build`): `-e`
    # answers the same for both, and an unelevated Windows runner cannot make a
    # symlink at all.
    marker.mkdir() if marker.name == "latest_build" else marker.write_text("")


@needs_tools
@pytest.mark.parametrize("repo", [False, True])
def test_a_colcon_workspace_root_is_a_project(tmp_path, repo):
    """With and without a `.git` at the root — it was `no_project` both ways,
    and the two take different routes through the gate."""
    target = tmp_path / "ws"
    _a_colcon_package(target)
    _colcon_output(target)
    if repo:
        (target / ".git").mkdir()
    assert _detect(target, tmp_path / "home")["detection_status"] == "ok"


@needs_tools
def test_an_unbuilt_colcon_workspace_is_a_project(tmp_path):
    """Cloned and not yet built is when a session is most likely to be opened,
    and there is no `build/` to recognise it by."""
    target = tmp_path / "ws"
    _a_colcon_package(target)
    assert _detect(target, tmp_path / "home")["detection_status"] == "ok"


@needs_tools
def test_a_vcs_imported_workspace_is_a_project(tmp_path):
    """`vcs import` from a `.repos` file produces `src/<repo>/<pkg>/` — the
    commonest way a real workspace is assembled, one level deeper again."""
    target = tmp_path / "ws"
    _a_colcon_package(target, "src/aurora_robot/loci_demo")
    assert _detect(target, tmp_path / "home")["detection_status"] == "ok"


@needs_tools
def test_the_walk_under_src_is_bounded(tmp_path):
    """Three levels under `src/`, not "somewhere under it". This walk runs on
    every session start, beside the one above it."""
    target = tmp_path / "ws"
    _a_colcon_package(target, "src/a/b/c/loci_demo")
    assert _detect(target, tmp_path / "home")["detection_status"] == "no_project"


@needs_tools
def test_a_built_workspace_with_no_packages_left_is_a_project(tmp_path):
    """`src/` emptied (every package `vcs import`ed and removed) or a build with
    `--base-paths` elsewhere. colcon's own stamp is what says colcon was here."""
    target = tmp_path / "ws"
    (target / "src").mkdir(parents=True)
    _colcon_output(target)
    assert _detect(target, tmp_path / "home")["detection_status"] == "ok"


@needs_tools
@pytest.mark.parametrize("stamp", ["build/COLCON_IGNORE", "install/COLCON_IGNORE",
                                   "log/COLCON_IGNORE", "log/latest_build"])
def test_each_of_colcons_stamps_is_enough(tmp_path, stamp):
    target = tmp_path / "ws"
    target.mkdir()
    _colcon_output(target, stamp)
    assert _detect(target, tmp_path / "home")["detection_status"] == "ok"


@needs_tools
def test_build_install_and_log_without_a_stamp_are_not_evidence(tmp_path):
    """`build`, `install` and `log` are ordinary directory names. The triple on
    its own would arm any hand-rolled tree that happens to keep all three."""
    target = tmp_path / "proj"
    for name in ("build", "install", "log"):
        (target / name).mkdir(parents=True)
    (target / "main.c").write_text("int main(){}")
    assert _detect(target, tmp_path / "home")["detection_status"] == "no_project"


@needs_tools
def test_a_src_directory_of_text_files_does_not_arm(tmp_path):
    """The declared-build rule is unchanged: `src/` is not itself evidence."""
    target = tmp_path / "notes"
    (target / "src" / "chapter").mkdir(parents=True)
    (target / "src" / "chapter" / "intro.md").write_text("# hello\n")
    assert _detect(target, tmp_path / "home")["detection_status"] == "no_project"


@needs_tools
def test_a_container_of_repos_is_not_claimed_by_a_colcon_layout(tmp_path):
    """Under the SAME guard as the depth-2 inference: a directory holding other
    people's repos is not one project because one of them keeps a `src/`."""
    target = tmp_path / "projects"
    for name in ("repo-a", "repo-b"):
        (target / name / ".git").mkdir(parents=True)
    _a_colcon_package(target)
    assert _detect(target, tmp_path / "home")["detection_status"] == "multi_project"


@needs_tools
def test_the_home_tree_is_not_claimed_by_a_colcon_layout(tmp_path):
    """$HOME is excluded from this inference for the reason it is excluded from
    the other one: a single `~/src/<pkg>/package.xml` would claim every
    directory in the home tree."""
    home = tmp_path / "home"
    _a_colcon_package(home)
    assert _detect(home, home)["detection_status"] == "no_project"


@needs_tools
def test_a_package_directory_still_arms_on_its_own(tmp_path):
    """It always did — off the package's own root `CMakeLists.txt` — and the
    workspace rule must not have taken that away. `loci init` there is what
    reports where the workspace is."""
    target = tmp_path / "ws"
    pkg = _a_colcon_package(target)
    _colcon_output(target)
    assert _detect(pkg, tmp_path / "home")["detection_status"] == "ok"


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
def test_every_session_gets_the_same_project_free_block(tmp_path):
    """AAD-7531: nothing about a project is decided at session start."""
    shapes = {}
    docs = tmp_path / "docs"
    (docs / ".git").mkdir(parents=True)
    (docs / "README.md").write_text("# docs")
    shapes["docs"] = docs
    container = tmp_path / "container"
    for name in ("a", "b"):
        (container / name / ".git").mkdir(parents=True)
    shapes["container"] = container
    make = tmp_path / "make"
    make.mkdir()
    (make / "Makefile").write_text("all:\n\ttrue\n")
    shapes["makefile"] = make
    rec = tmp_path / "rec"
    (rec / ".loci").mkdir(parents=True)
    (rec / ".loci" / "build.yaml").write_text("schema_version: 1\n")
    shapes["recipe"] = rec

    blocks = {}
    # The CLI-version half of the block is pinned, or the comparison decides on the
    # developer's machine: a stale global `loci` (WSL's was 0.2.51 under a 0.2.57 pin)
    # adds an advisory that names `<home>/.loci/state/loci-cli-install.log`, and four
    # homes then made four blocks — with nothing about the launch directory in them.
    env = _cli_version_env(tmp_path, _pinned_cli_version())
    for name, target in shapes.items():
        home = tmp_path / f"home-{name}"
        home.mkdir()
        s = _run_session_init(target, home, **env)
        assert not s.detector_ran, name
        assert not list((home / ".loci" / "state").glob("project-context-*.json")), name
        blocks[name] = s.ctx

    assert len(set(blocks.values())) == 1, "the block depends on the launch directory"
    ctx = blocks["docs"]
    for present in ("LOCI auto-run rules:", "LOCI init rule:", "Available:"):
        assert present in ctx
    for gone in ("LOCI target:", "project context:", "recipe:", "artifact:",
                 "Branch:", "inactive"):
        assert gone not in ctx, gone


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
def test_the_session_block_fits_its_budget(tmp_path):
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
    budget = _STEADY_STATE_BUDGET + _legacy_line_allowance()
    assert size <= budget, (
        f"the session block models {size} B at production path lengths "
        f"(budget {budget} B = the ~2.0 KB of acceptance "
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


def _legacy_bash() -> bool:
    """bash 3 with no newer bash on the host: the profile `lib/bash-compat.sh`
    describes (AAD-7771). `${v//pat/rep}` there costs O(matches x length^2) —
    ~2 ms per match at 16 KB — so the hooks read a 4 KB prefix and the guard
    tokenises 4 KB; the 64 KB shapes in this file are not what that host runs.
    Probed once per process, on the bash the tests drive."""
    if _legacy_bash.cache is None:
        out = subprocess.run([_find_bash(), "-c", 'printf %s "${BASH_VERSINFO[0]}"'],
                             capture_output=True, text=True, timeout=30)
        v = out.stdout.strip()
        _legacy_bash.cache = v.isdigit() and int(v) < 4
    return _legacy_bash.cache


_legacy_bash.cache = None

#: What the one legacy-profile line (`bash: 3.2.57 (stock macOS) …`) may add to
#: the block, and only where it is printed: a Mac with stock bash and no newer
#: one installed (AAD-7771). It names the reduced guard and the fix, once.
_LEGACY_LINE_ALLOWANCE = 260


def _legacy_line_allowance() -> int:
    return _LEGACY_LINE_ALLOWANCE if _legacy_bash() else 0

#: The steady-state budget the two ceilings below are measured against. It was a
#: flat 2 048 B until todo 042 spelled every skill as `/loci:<name>`: the seven
#: names in the `Available:` line each cost 5 more bytes, and one spelling for a
#: command the user types is worth 35 B of a 2 KB block.
_STEADY_STATE_BUDGET = 2048 + 35


@needs_tools
def test_the_session_block_carries_the_skew_advisory_within_its_own_allowance(tmp_path):
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
    ceiling = _STEADY_STATE_BUDGET + _SKEW_ADVISORY_ALLOWANCE + _legacy_line_allowance()
    assert size <= ceiling, (
        f"the session block models {size} B with the version-skew advisory, "
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
    "skills/_shared/house-rules.md",
    # Two of the four gates (frame sizes below 0.1.107, and the differ's own note)
    # moved here with the compile route on 22 Sep, todo [082]. The anchor itself
    # stays in the house rules; only the rules citing it moved.
    "skills/_shared/compile-route.md",
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
                / "house-rules.md").read_text(encoding="utf-8")
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
    ceiling = _STEADY_STATE_BUDGET + _UNKNOWN_MARKER_ALLOWANCE + _legacy_line_allowance()
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


# ── a broken CLI, where a user asks why ─────────────────────────────────────

@needs_tools
def test_a_broken_cli_is_reported_in_a_docs_repo(tmp_path):
    """A docs repo is where a user most often asks "why is loci not working"."""
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
    # `augment_path` adds these two directories whenever they exist, whatever
    # PATH says, so a `uv` living in one of them makes the "uv is missing"
    # branch unreachable on that host — Homebrew's uv on a Mac (AAD-7771). The
    # branch under test is then not a property this host can show.
    for fixed in ("/usr/local/bin", "/opt/homebrew/bin"):
        if (Path(fixed) / "uv").exists():
            pytest.skip(f"uv is installed at {fixed}/uv, a directory "
                        "session-init.sh always puts on PATH, so the "
                        "'prerequisite uv is missing' branch cannot be reached "
                        "on this host")
    s = _run_session_init(
        target, home,
        PATH=f"{_to_bash_path(bin_dir)}:{jq_dir}:/usr/bin:/bin:/usr/local/bin")
    assert "loci: NOT installed" in s.ctx, f"a broken CLI was invisible:\n{s.ctx}"


# ── the recipe walk ─────────────────────────────────────────────────────────

def _find_recipe(start: Path, home: Path) -> str:
    lib = _to_bash_path(PLUGIN_ROOT / "lib" / "setup-steps.sh")
    res = subprocess.run(
        [_find_bash(), "-c", f'. "{lib}" && _loci_find_recipe "$1" || true', "_",
         _to_bash_path(start)],
        env=_env(home), capture_output=True, text=True, encoding="utf-8", timeout=30)
    return res.stdout.strip()


@needs_tools
def test_a_recipe_at_the_repo_root_governs_a_subdirectory(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".loci").mkdir()
    (repo / ".loci" / "build.yaml").write_text("schema_version: 1\n")
    sub = repo / "drivers" / "uart"
    sub.mkdir(parents=True)

    assert _find_recipe(sub, home) == _to_bash_path(repo.resolve() / ".loci" / "build.yaml")


@needs_tools
def test_the_walk_stops_at_the_git_toplevel(tmp_path):
    """One stray ``~/projects/.loci/build.yaml`` must not govern every repo under it."""
    home = tmp_path / "home"
    home.mkdir()
    parent = tmp_path / "projects"
    (parent / ".loci").mkdir(parents=True)
    (parent / ".loci" / "build.yaml").write_text("schema_version: 1\n")
    repo = parent / "unrelated"
    (repo / ".git").mkdir(parents=True)

    assert _find_recipe(repo, home) == ""


@needs_tools
def test_a_recipe_in_home_is_never_adopted(tmp_path):
    """``~/.loci`` is the state directory, so a recipe there is stray state."""
    home = tmp_path / "home"
    (home / ".loci").mkdir(parents=True)
    (home / ".loci" / "build.yaml").write_text("schema_version: 1\n")
    target = home / "scratch"
    target.mkdir()

    assert _find_recipe(target, home) == ""


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
                / "house-rules.md").read_text(encoding="utf-8")
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


# ── AAD-7607: a project whose recipe records a binary and no database ─────


def test_the_legacy_bash_line_is_printed_under_bash_3_and_nowhere_else(tmp_path):
    """One line, only on a host running the legacy profile (stock macOS bash
    with no newer bash — `lib/bash-compat.sh`), naming the reduced guard and
    the fix; and within the allowance the two budget tests grant it."""
    home = tmp_path / "home"
    home.mkdir()
    proj, s = _initialized_project(
        tmp_path, home, env_extra=_cli_version_env(tmp_path, _pinned_cli_version()))
    lines = [l for l in s.ctx.splitlines() if l.startswith("bash: ")]
    if _legacy_bash():
        assert len(lines) == 1 and "brew install bash" in lines[0], s.ctx
        assert len(lines[0].encode()) + 1 <= _LEGACY_LINE_ALLOWANCE, len(lines[0].encode())
    else:
        assert not lines, lines
