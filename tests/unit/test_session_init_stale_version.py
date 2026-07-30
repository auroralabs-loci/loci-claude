"""The SessionStart hook must advertise the surviving plugin version, not $0's.

When Claude Code launches the hook from an older ``CLAUDE_PLUGIN_ROOT`` (a
mid-session upgrade, or a cached stale path), deriving the advertised plugin
dir from ``$0``'s location points at a dir the upgrade is about to delete, and
the next tool call hits ``No such file or directory``. The hook instead scans
the cache root for the highest-semver version that still has both
``.claude-plugin/plugin.json`` and ``lib/``. Internal sourcing keeps using
``PLUGIN_DIR`` — the divergence matters for paths referenced after the hook
returns, and for the loci-CLI pin (see test_cli_pin_resolution.py).
"""

from __future__ import annotations

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


def _to_bash_path(p: Path) -> str:
    s = p.as_posix()
    m = re.match(r"^([A-Za-z]):/(.*)$", s)
    if m:
        return f"/{m.group(1).lower()}/{m.group(2)}"
    return s


def _stage_version(cache_root: Path, version: str) -> Path:
    """Stage a fake installed plugin version under cache_root/<version>/ with
    the real session-init.sh and lib/ (all the hook sources/runs)."""
    d = cache_root / version
    (d / "hooks").mkdir(parents=True)
    (d / "lib").mkdir(parents=True)
    (d / ".claude-plugin").mkdir(parents=True)

    shutil.copy(PLUGIN_ROOT / "hooks" / "session-init.sh", d / "hooks")
    for src in (PLUGIN_ROOT / "lib").iterdir():
        if src.is_file():
            shutil.copy(src, d / "lib")
        elif src.is_dir() and src.name != "__pycache__":
            shutil.copytree(src, d / "lib" / src.name)

    (d / ".claude-plugin" / "plugin.json").write_text(
        f'{{"name":"loci","version":"{version}"}}'
    )
    return d


def _run_hook(hook_script: Path, home: Path) -> subprocess.CompletedProcess:
    # _LOCI_BOOTSTRAP=1 (set in conftest) keeps the hook from installing the CLI
    # over the network during tests.
    env = {
        **os.environ,
        "HOME": _to_bash_path(home),
        "LOCI_STATE_DIR": _to_bash_path(home / ".loci" / "state"),
    }
    return subprocess.run(
        [_find_bash(), _to_bash_path(hook_script)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _context_block(stdout: str) -> str:
    import json
    payload = json.loads(stdout)
    return payload["hookSpecificOutput"]["additionalContext"]


@pytest.mark.skipif(not _has_bash(), reason="bash not available")
def test_older_hook_advertises_newer_installed_version(tmp_path: Path):
    """Hook launched from the older cache dir must emit the newer version's
    plugin dir, so the first tool call doesn't hit ENOENT after the upgrade
    deletes the older dir."""
    cache_root = tmp_path / "cache" / "loci" / "loci"
    cache_root.mkdir(parents=True)
    older = _stage_version(cache_root, "0.1.10")
    newer = _stage_version(cache_root, "0.1.20")

    home = tmp_path / "home"
    home.mkdir()

    # Run the OLDER version's hook script directly — simulates Claude Code
    # launching a stale CLAUDE_PLUGIN_ROOT.
    res = _run_hook(older / "hooks" / "session-init.sh", home)
    assert res.returncode == 0, f"hook exited {res.returncode}\n{res.stderr}"

    ctx = _context_block(res.stdout)
    newer_posix = _to_bash_path(newer)
    older_posix = _to_bash_path(older)

    assert "loci version: 0.1.20" in ctx, (
        f"Expected newer version advertised; got:\n{ctx[:1500]}"
    )
    assert f"plugin dir: {newer_posix}" in ctx, (
        f"Expected plugin dir under {newer_posix}; got:\n{ctx[:1500]}"
    )
    # Sanity: nothing in the context should point at the older version's
    # cache dir, otherwise the first tool call still gets ENOENT after the
    # upgrade cleanup runs.
    assert f"{older_posix}/lib/" not in ctx, (
        f"Advertised paths still reference the stale older dir:\n{ctx[:1500]}"
    )


@pytest.mark.skipif(not _has_bash(), reason="bash not available")
def test_single_version_cache_uses_own_dir(tmp_path: Path):
    """Sanity check: when only one version is installed (the common case),
    the resolver picks that version — not an empty string, not a fallback to
    a non-existent sibling."""
    cache_root = tmp_path / "cache" / "loci" / "loci"
    cache_root.mkdir(parents=True)
    only = _stage_version(cache_root, "0.1.42")

    home = tmp_path / "home"
    home.mkdir()

    res = _run_hook(only / "hooks" / "session-init.sh", home)
    assert res.returncode == 0, f"hook exited {res.returncode}\n{res.stderr}"

    ctx = _context_block(res.stdout)
    only_posix = _to_bash_path(only)
    assert "loci version: 0.1.42" in ctx
    assert f"plugin dir: {only_posix}" in ctx


@pytest.mark.skipif(not _has_bash(), reason="bash not available")
def test_dev_install_outside_cache_falls_back_to_plugin_dir(tmp_path: Path):
    """When the script lives outside the standard cache layout (dev install
    from source, ad-hoc symlink), the resolver must fall back to PLUGIN_DIR
    rather than scanning an unrelated directory full of non-loci subdirs."""
    # Stage a plugin dir whose parent has noise that must not be picked up.
    parent = tmp_path / "Projects"
    parent.mkdir()
    (parent / "some-other-tool").mkdir()
    (parent / "1.2.3-ish-not-loci").mkdir()  # numeric noise: no plugin.json/lib
    plugin = _stage_version(parent, "0.1.68")

    home = tmp_path / "home"
    home.mkdir()

    res = _run_hook(plugin / "hooks" / "session-init.sh", home)
    assert res.returncode == 0, f"hook exited {res.returncode}\n{res.stderr}"

    ctx = _context_block(res.stdout)
    plugin_posix = _to_bash_path(plugin)
    assert "loci version: 0.1.68" in ctx
    assert f"plugin dir: {plugin_posix}" in ctx
