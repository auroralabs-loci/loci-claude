"""The spellings one home directory can reach a hook under (AAD-8026).

A hook that walks up from an edited file stops at `$HOME`, and it walks the
directory `pwd -P` names. That name and `$HOME` are two spellings of one
directory far more often than a test rooted at a plain `tmp_path` shows:

* **Git Bash answers `pwd -P` through its mount table.** `%TEMP%` is mounted at
  `/tmp`, so a directory entered as `C:/Users/…/Temp/x` comes back `/tmp/x`,
  while a `$HOME` handed over as `/c/Users/…/Temp/x` stays that — and `cd` +
  `pwd -P` on the `/c/…` form answers `/c/…` again, so resolving `$HOME` the
  way the walk resolves its start does not reconcile the two.
* **A native `$HOME` from a Windows process is converted before bash runs**
  (MSYS rewrites `HOME`, `PATH`, `TMP` on import): `C:\\Users\\…\\Temp\\x` arrives
  as `/tmp/x`. One exported by a shell that is already running is not, so the
  hook can also meet `C:\\…`, `C:/…` and a UNC `\\\\host\\share\\…` as written —
  the `in-shell` shapes.
* **A linked home and a case variant** are one directory under two names on
  every host that has them.

`SHAPES` names them; `spell()` returns how a test hands one over, and skips
where the host cannot produce it. `argv()` is the command that starts a hook
with it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

SHAPES = ("as-given", "trailing-slash", "native", "native-in-shell",
          "drive-in-shell", "unc-in-shell", "other-case", "linked")

# `$1` is the home, `$2` the hook. `exec` so the hook is the process the test
# waits on, with the payload still on stdin.
_EXPORT_THEN_RUN = 'HOME=$1; export HOME; exec "$BASH" "$2"'


@dataclass(frozen=True)
class Spelled:
    #: `HOME` in the environment the hook's bash is started with.
    env: str
    #: When set, `HOME` is exported by a running bash instead, as written.
    shell: str | None = None


def _bash_path(p: Path) -> str:
    s = Path(p).as_posix()
    if len(s) > 2 and s[1] == ":" and s[2] == "/":
        return f"/{s[0].lower()}/{s[3:]}"
    return s


def link_dir(link: Path, target: Path) -> bool:
    """A directory link, by whatever means this platform allows without a
    privilege: a symlink, else a junction on Windows."""
    try:
        link.symlink_to(target, target_is_directory=True)
        return link.is_dir()
    except (OSError, NotImplementedError):
        pass
    if sys.platform == "win32":
        proc = subprocess.run(
            ["cmd.exe", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, text=True,
            env={**os.environ, "MSYS_NO_PATHCONV": "1"})
        return proc.returncode == 0 and link.is_dir()
    return False


def spell(shape: str, home: Path) -> Spelled:
    """`home` (an existing directory) as `shape` spells it."""
    if shape not in SHAPES:         # before any skip: a typo must not skip quietly
        raise ValueError(f"unknown home spelling {shape!r}")
    posix = _bash_path(home)
    windows = sys.platform == "win32"
    if shape == "as-given":
        return Spelled(posix)
    if shape == "trailing-slash":
        return Spelled(posix + "/")
    if shape == "other-case":
        other = home.parent / home.name.swapcase()
        if other.name == home.name or not other.is_dir():
            pytest.skip("this filesystem tells the two cases apart")
        return Spelled(_bash_path(other))
    if shape == "linked":
        link = home.parent / f"{home.name}-link"
        if not link_dir(link, home):
            pytest.skip("this platform will not create a directory link unprivileged")
        return Spelled(_bash_path(link))
    if not windows:
        pytest.skip("a drive-letter spelling exists on Windows only")
    if shape == "native":
        return Spelled(str(home))
    if shape == "native-in-shell":
        return Spelled(posix, shell=str(home))
    if shape == "unc-in-shell":
        # The drive's administrative share: `\\localhost\c$\Users\…`.
        native = str(home)
        unc = f"\\\\localhost\\{native[0].lower()}${native[2:]}"
        if native[1:3] != ":\\" or not Path(unc).is_dir():
            pytest.skip("this host does not serve the drive's administrative share")
        return Spelled(posix, shell=unc)
    return Spelled(posix, shell=home.as_posix())        # drive-in-shell


def relative(home: Path, start: Path | str) -> Spelled:
    """`home` as a path relative to `start`, the directory the hook runs in.
    Exported by the running shell on every host: MSYS makes a relative `HOME`
    absolute when it imports one from a Windows process."""
    try:
        rel = Path(os.path.relpath(home, start)).as_posix()
    except ValueError:
        pytest.skip("the home and the hook's directory are on different drives")
    return Spelled(rel, shell=rel)


def textual(home: Path, *, slash: bool = False) -> Spelled:
    """The one spelling `pwd -P` itself gives `home`, so the two compare equal
    as text — with a trailing separator when `slash`. Under Git Bash that is the
    mount-table form, which is what MSYS turns a native `HOME` into; elsewhere
    it is the resolved path."""
    if sys.platform == "win32":
        return Spelled(str(home) + ("\\" if slash else ""))
    return Spelled(os.path.realpath(home) + ("/" if slash else ""))


def argv(bash: str, hook: str, spelled: Spelled | None) -> list[str]:
    """The command that runs `hook` (a bash-spelled path) under `spelled`."""
    if spelled is None or spelled.shell is None:
        return [bash, hook]
    return [bash, "-c", _EXPORT_THEN_RUN, "hook", spelled.shell, hook]
