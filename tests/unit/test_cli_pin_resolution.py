"""A stale plugin version's hooks must never downgrade the installed loci CLI.

``LOCI_CLI_VERSION`` lives inside each plugin version's ``lib/setup-steps.sh``, so
after ``/plugin update`` the still-running OLD hooks ask for an older CLI and
``uv tool install --force`` rolls it back. Real occurrence: plugin 0.1.102
(pin 0.1.101) downgraded a working 0.1.103.

These drive ``ensure-loci-cli.sh`` with stub ``loci``/``uv`` binaries on PATH and
assert on the argv the stub ``uv`` recorded.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.unit.test_session_init_stale_version import (
    _find_bash,
    _has_bash,
    _stage_version,
    _to_bash_path,
)


PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
pytestmark = pytest.mark.skipif(not _has_bash(), reason="bash not available")


def _stage_with_pin(cache_root: Path, version: str, cli_pin: str) -> Path:
    """Stage a plugin version, then rewrite its CLI pin and add the ensure hook."""
    d = _stage_version(cache_root, version)
    shutil.copy(PLUGIN_ROOT / "hooks" / "ensure-loci-cli.sh", d / "hooks")

    steps = d / "lib" / "setup-steps.sh"
    text = steps.read_text(encoding="utf-8")
    patched = text.replace(
        'LOCI_CLI_VERSION="', f'LOCI_CLI_VERSION="{cli_pin}"  # was: ', 1
    )
    assert patched != text, "pin line not found in setup-steps.sh"
    steps.write_text(patched, encoding="utf-8")
    return d


def _stub_bin(tmp_path: Path, loci_version: str) -> tuple[Path, Path]:
    """A PATH dir with stub `loci` and `uv`; uv appends its argv to a log."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv_log = tmp_path / "uv-argv.log"

    loci = bin_dir / "loci"
    loci.write_text(f'#!/usr/bin/env bash\necho "loci {loci_version}"\n')
    loci.chmod(0o755)

    uv = bin_dir / "uv"
    uv.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> {_to_bash_path(uv_log)}\n'
        "exit 0\n"
    )
    uv.chmod(0o755)
    return bin_dir, uv_log


def _run_ensure(hook: Path, home: Path, bin_dir: Path) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "HOME": _to_bash_path(home),
        "PATH": f"{_to_bash_path(bin_dir)}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    # ensure_loci short-circuits when _LOCI_BOOTSTRAP is set (conftest sets it to
    # keep other tests offline) — the install path is exactly what we're testing.
    env.pop("_LOCI_BOOTSTRAP", None)
    env.pop("LOCI_DEV_CLI_PATH", None)
    return subprocess.run(
        [_find_bash(), _to_bash_path(hook)],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _installs(uv_log: Path) -> list[str]:
    if not uv_log.exists():
        return []
    return [ln for ln in uv_log.read_text(encoding="utf-8").splitlines() if ln.strip()]


@pytest.fixture
def home(tmp_path: Path) -> Path:
    h = tmp_path / "home"
    (h / ".loci" / "state").mkdir(parents=True)
    return h


def test_stale_hook_does_not_downgrade_cli(tmp_path: Path, home: Path):
    """The 0.1.102-pins-0.1.101 case: installed CLI is newer, leave it alone."""
    cache_root = tmp_path / "cache" / "loci" / "loci"
    cache_root.mkdir(parents=True)
    older = _stage_with_pin(cache_root, "0.1.102", "0.1.101")
    _stage_with_pin(cache_root, "0.1.105", "0.1.104")

    bin_dir, uv_log = _stub_bin(tmp_path, "0.1.104")
    res = _run_ensure(older / "hooks" / "ensure-loci-cli.sh", home, bin_dir)
    assert res.returncode == 0, res.stderr

    installs = _installs(uv_log)
    assert not any("0.1.101" in ln for ln in installs), (
        f"stale hook downgraded the CLI to its own pin: {installs}"
    )
    assert not installs, f"expected no install at all (0.1.104 satisfies the pin): {installs}"


def test_stale_hook_installs_authoritative_pin(tmp_path: Path, home: Path):
    """Installed CLI is behind: the stale hook installs the NEWER version's pin,
    so the upgrade lands without waiting for a restart."""
    cache_root = tmp_path / "cache" / "loci" / "loci"
    cache_root.mkdir(parents=True)
    older = _stage_with_pin(cache_root, "0.1.102", "0.1.101")
    _stage_with_pin(cache_root, "0.1.105", "0.1.104")

    bin_dir, uv_log = _stub_bin(tmp_path, "0.1.98")
    res = _run_ensure(older / "hooks" / "ensure-loci-cli.sh", home, bin_dir)
    assert res.returncode == 0, res.stderr

    installs = _installs(uv_log)
    assert any("loci-tools==0.1.104" in ln for ln in installs), (
        f"expected the authoritative pin 0.1.104; got: {installs}"
    )


def test_pin_below_installed_is_never_applied(tmp_path: Path, home: Path):
    """The pin is a floor, so even the current version pinning backwards is a
    no-op. Deliberate: a bad CLI release is fixed by rolling forward."""
    cache_root = tmp_path / "cache" / "loci" / "loci"
    cache_root.mkdir(parents=True)
    newest = _stage_with_pin(cache_root, "0.1.106", "0.1.103")

    bin_dir, uv_log = _stub_bin(tmp_path, "0.1.104")
    res = _run_ensure(newest / "hooks" / "ensure-loci-cli.sh", home, bin_dir)
    assert res.returncode == 0, res.stderr

    assert not _installs(uv_log), (
        f"0.1.104 is newer than the 0.1.103 pin — nothing should be installed: "
        f"{_installs(uv_log)}"
    )


def test_source_change_still_reinstalls_at_equal_version(tmp_path: Path, home: Path):
    """The floor must not swallow a SOURCE change: editable 0.1.104 -> pinned
    0.1.104 has no version signal at all, only the recorded spec."""
    cache_root = tmp_path / "cache" / "loci" / "loci"
    cache_root.mkdir(parents=True)
    only = _stage_with_pin(cache_root, "0.1.105", "0.1.104")

    state = home / ".loci" / "state"
    (state / "loci-cli-status.json").write_text(
        '{"status":"installed","spec":"--editable /home/x/loci-cli",'
        '"version":"0.1.104"}'
    )

    bin_dir, uv_log = _stub_bin(tmp_path, "0.1.104")
    res = _run_ensure(only / "hooks" / "ensure-loci-cli.sh", home, bin_dir)
    assert res.returncode == 0, res.stderr

    installs = _installs(uv_log)
    assert any("loci-tools==0.1.104" in ln for ln in installs), (
        f"editable->pinned switch at the same version was missed: {installs}"
    )


def test_fresh_install_uses_own_pin(tmp_path: Path, home: Path):
    """Single cache dir (fresh install): the hook's own pin is authoritative."""
    cache_root = tmp_path / "cache" / "loci" / "loci"
    cache_root.mkdir(parents=True)
    only = _stage_with_pin(cache_root, "0.1.105", "0.1.104")

    bin_dir, uv_log = _stub_bin(tmp_path, "0.1.98")
    res = _run_ensure(only / "hooks" / "ensure-loci-cli.sh", home, bin_dir)
    assert res.returncode == 0, res.stderr

    installs = _installs(uv_log)
    assert any("loci-tools==0.1.104" in ln for ln in installs), (
        f"expected the pin 0.1.104; got: {installs}"
    )
