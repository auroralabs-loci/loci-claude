"""Project detection must require evidence anchored in the tree (AAD-7392).

Pre-fix, detect-project.sh always produced an answer: a PATH compiler plus
``uname -m`` fabricated a valid LOCI target for any directory, and the deep
maxdepth-10 scans claimed a parent dir full of unrelated repos via some
subproject's Makefile. The session hook then armed the mandatory
preflight/post-edit rules in a dir that isn't a project at all.

The gate adds two non-project verdicts:
- ``no_project``    — no C/C++/Rust build files, sources, or binaries anchored
                      to this tree
- ``multi_project`` — a container of independent projects (each with its own
                      repo or root build file), not a project itself

and session-init.sh must not inject the mandatory auto-run rules for either.
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
    s = p.as_posix()
    m = re.match(r"^([A-Za-z]):/(.*)$", s)
    if m:
        return f"/{m.group(1).lower()}/{m.group(2)}"
    return s


def _detect(target: Path, home: Path) -> dict:
    env = {
        **os.environ,
        "HOME": _to_bash_path(home),
        "LOCI_STATE_DIR": _to_bash_path(home / ".loci" / "state"),
    }
    res = subprocess.run(
        [_find_bash(), _to_bash_path(PLUGIN_ROOT / "lib" / "detect-project.sh"),
         _to_bash_path(target)],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert res.returncode == 0, f"detect-project.sh exited {res.returncode}\n{res.stderr}"
    return json.loads(res.stdout)


def _run_session_init(cwd: Path, home: Path) -> dict:
    env = {
        **os.environ,
        "HOME": _to_bash_path(home),
        "LOCI_STATE_DIR": _to_bash_path(home / ".loci" / "state"),
    }
    res = subprocess.run(
        [_find_bash(), _to_bash_path(PLUGIN_ROOT / "hooks" / "session-init.sh")],
        env=env, cwd=cwd, capture_output=True, text=True, timeout=60,
        stdin=subprocess.DEVNULL,
    )
    assert res.returncode == 0, f"session-init.sh exited {res.returncode}\n{res.stderr}"
    return json.loads(res.stdout)


needs_tools = pytest.mark.skipif(
    not (_has_bash() and _has_jq()), reason="bash and jq required"
)


@needs_tools
def test_empty_dir_is_no_project(tmp_path):
    """An empty dir must not inherit a project identity from the host's
    compiler and architecture."""
    target = tmp_path / "empty"
    target.mkdir()
    info = _detect(target, tmp_path / "home")
    assert info["detection_status"] == "no_project"
    assert info["loci_compatible"] is False
    assert info["loci_target"] is None


@needs_tools
def test_container_of_git_repos_is_multi_project(tmp_path):
    target = tmp_path / "projects"
    for name in ("repo-a", "repo-b"):
        (target / name / ".git").mkdir(parents=True)
        (target / name / "main.c").write_text("int main(){}")
    info = _detect(target, tmp_path / "home")
    assert info["detection_status"] == "multi_project"
    assert info["loci_compatible"] is False
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
def test_single_foreign_subrepo_is_no_project(tmp_path):
    """One sub-repo's sources must not claim the parent dir — depth-2 source
    evidence belongs to the subproject, not to CWD."""
    target = tmp_path / "wrapper"
    (target / "repo" / ".git").mkdir(parents=True)
    (target / "repo" / "main.c").write_text("int main(){}")
    info = _detect(target, tmp_path / "home")
    assert info["detection_status"] == "no_project"


@needs_tools
def test_root_makefile_project_is_ok(tmp_path):
    target = tmp_path / "proj"
    target.mkdir()
    (target / "Makefile").write_text("all:\n\ttrue\n")
    (target / "main.c").write_text("int main(){}")
    info = _detect(target, tmp_path / "home")
    assert info["detection_status"] == "ok"
    assert info["build_system"] == "make"


@needs_tools
def test_loose_sources_in_cwd_is_ok(tmp_path):
    """Sources directly in CWD count even without git or a build file."""
    target = tmp_path / "loose"
    target.mkdir()
    (target / "main.c").write_text("int main(){}")
    info = _detect(target, tmp_path / "home")
    assert info["detection_status"] == "ok"


@needs_tools
def test_git_repo_with_deep_sources_is_ok(tmp_path):
    """A single repo may keep sources deep (src/app/...) with no root build
    file — still a project."""
    target = tmp_path / "repo"
    (target / ".git").mkdir(parents=True)
    (target / "src" / "app").mkdir(parents=True)
    (target / "src" / "app" / "main.c").write_text("int main(){}")
    info = _detect(target, tmp_path / "home")
    assert info["detection_status"] == "ok"


@needs_tools
def test_repo_with_submodules_is_ok(tmp_path):
    """A repo whose sources live in submodules (`.git` is a FILE there) is
    still one project."""
    target = tmp_path / "repo"
    (target / ".git").mkdir(parents=True)
    (target / "sdk").mkdir()
    (target / "sdk" / ".git").write_text("gitdir: ../.git/modules/sdk")
    (target / "sdk" / "drv.c").write_text("int f(){}")
    info = _detect(target, tmp_path / "home")
    assert info["detection_status"] == "ok"


@needs_tools
def test_submodule_checkout_as_cwd_is_ok(tmp_path):
    """Opening a session inside a submodule: `.git` is a file, not a dir."""
    target = tmp_path / "sub"
    target.mkdir()
    (target / ".git").write_text("gitdir: ../.git/modules/sub")
    (target / "main.c").write_text("int main(){}")
    info = _detect(target, tmp_path / "home")
    assert info["detection_status"] == "ok"


@needs_tools
def test_repo_subdir_with_submodules_is_not_multi_project(tmp_path):
    """A repo SUBDIR whose children are submodules is one project's interior,
    not a container of independent projects — the gate must find the `.git`
    in the ancestor."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    target = repo / "firmware"
    for name in ("sdk", "rtos"):
        (target / name).mkdir(parents=True)
        (target / name / ".git").write_text(f"gitdir: ../../.git/modules/{name}")
        (target / name / "os.c").write_text("int g(){}")
    info = _detect(target, tmp_path / "home")
    assert info["detection_status"] == "ok"


@needs_tools
def test_loci_build_cache_counts_as_evidence(tmp_path):
    """.loci-build/ only exists because LOCI analyzed this tree before — it
    must anchor the dir as a project even with no build file or sources."""
    target = tmp_path / "proj"
    (target / ".loci-build" / "armv6-m").mkdir(parents=True)
    (target / ".loci-build" / "armv6-m" / "mod.o").write_bytes(b"\x7fELF")
    info = _detect(target, tmp_path / "home")
    assert info["detection_status"] == "ok"


@needs_tools
def test_home_dir_is_not_claimed_by_depth2_sources(tmp_path):
    """$HOME keeps its repos at ~/Projects/<repo> — deeper than the subproject
    scan looks — so the n==0 fall-through must not let one stray download
    (~/Downloads/foo.out) claim the entire home tree."""
    home = tmp_path / "home"
    (home / "Downloads").mkdir(parents=True)
    (home / "Downloads" / "STM32_demo.out").write_bytes(b"\x7fELF")
    (home / "Projects" / "fw").mkdir(parents=True)
    (home / "Projects" / "fw" / "Makefile").write_text("all:\n\ttrue\n")
    (home / "Projects" / "fw" / "main.c").write_text("int main(){}")
    info = _detect(home, home)
    assert info["detection_status"] == "no_project"
    assert info["project_type"] == "none"


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
def test_dir_under_home_still_uses_depth2_fallback(tmp_path):
    """Only $HOME itself is excluded — an ordinary dir below it keeps the
    depth-2 fall-through."""
    home = tmp_path / "home"
    target = home / "scratch"
    (target / "demo").mkdir(parents=True)
    (target / "demo" / "main.c").write_text("int main(){}")
    info = _detect(target, home)
    assert info["detection_status"] == "ok"


@needs_tools
def test_git_repo_without_c_sources_is_no_project(tmp_path):
    """A repo of scripts/docs (no C/C++/Rust anything) must stay inactive."""
    target = tmp_path / "repo"
    (target / ".git").mkdir(parents=True)
    (target / "index.js").write_text("console.log(1)")
    info = _detect(target, tmp_path / "home")
    assert info["detection_status"] == "no_project"


@needs_tools
def test_session_init_omits_mandatory_rules_in_container(tmp_path):
    """The hook must not arm preflight/post-edit auto-run rules when the CWD
    is a container of projects, and must say LOCI is inactive."""
    target = tmp_path / "projects"
    for name in ("repo-a", "repo-b"):
        (target / name / ".git").mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()

    payload = _run_session_init(target, home)
    ctx = payload["hookSpecificOutput"]["additionalContext"]
    assert "LOCI: inactive" in ctx
    assert "multi_project" in ctx
    assert "MUST invoke" not in ctx
    assert "LOCI auto-run rules" not in ctx
    # Version/plugin-dir lines survive so upgrade plumbing keeps working.
    assert "loci version:" in ctx
    assert "plugin dir:" in ctx


@needs_tools
def test_session_init_keeps_mandatory_rules_in_project(tmp_path):
    target = tmp_path / "proj"
    target.mkdir()
    (target / "Makefile").write_text("all:\n\ttrue\n")
    (target / "main.c").write_text("int main(){}")
    home = tmp_path / "home"
    home.mkdir()

    payload = _run_session_init(target, home)
    ctx = payload["hookSpecificOutput"]["additionalContext"]
    assert "LOCI auto-run rules" in ctx
    assert "LOCI: inactive" not in ctx


@needs_tools
def test_stub_context_is_persisted_for_non_project(tmp_path):
    """Skills that read the keyed context file must find an explicit
    non-project status instead of a fabricated target."""
    target = tmp_path / "empty"
    target.mkdir()
    home = tmp_path / "home"
    home.mkdir()

    _run_session_init(target, home)
    state = home / ".loci" / "state"
    stubs = list(state.glob("project-context-*.json"))
    assert len(stubs) == 1
    info = json.loads(stubs[0].read_text())
    assert info["detection_status"] == "no_project"
    assert info["loci_compatible"] is False
