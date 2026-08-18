"""The pinned CLI upgrade must actually reach the user's machine.

Three ways it silently didn't, all observed as "plugin updated, `loci` stayed on
an old version, nothing said so":

* a lock left behind by a killed installer vetoed every later install,
* another `loci` earlier on PATH answered the version check the installer's
  shim was supposed to answer, so it reinstalled forever with no visible change,
* and session-init only ever reported the CLI being ABSENT, never stale.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.unit.test_cli_pin_resolution import (
    _installs,
    _run_ensure,
    _stage_with_pin,
    _stub_bin,
)
from tests.unit.test_session_init_stale_version import (
    _find_bash,
    _has_bash,
    _stage_version,
    _to_bash_path,
)


PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
pytestmark = pytest.mark.skipif(not _has_bash(), reason="bash not available")


def _ps_supports_args() -> bool:
    """`ps -o args= -p` is how the lock tells a real holder from a recycled PID.
    Git Bash's ps lacks it; there the hook falls back to the lock's age instead,
    which this test cannot exercise inside its timeout."""
    try:
        r = subprocess.run(
            ["ps", "-o", "args=", "-p", str(os.getpid())],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and bool(r.stdout.strip())


@pytest.fixture
def home(tmp_path: Path) -> Path:
    h = tmp_path / "home"
    (h / ".loci" / "state").mkdir(parents=True)
    return h


@pytest.mark.skipif(not _ps_supports_args(), reason="ps -o args= unavailable")
def test_recycled_pid_in_stale_lock_does_not_veto_the_install(tmp_path: Path, home: Path):
    """A killed installer skips its EXIT trap and strands the lock. If the PID it
    recorded is later reused by an unrelated live process, `kill -0` succeeds and
    the upgrade is blocked in every future session — silently, forever."""
    cache_root = tmp_path / "cache" / "loci" / "loci"
    cache_root.mkdir(parents=True)
    plugin = _stage_with_pin(cache_root, "0.1.120", "0.1.120")

    lock = home / ".loci" / "state" / "ensure-loci-cli.lock"
    lock.mkdir()
    # This test process: alive, and definitely not an ensure-loci-cli run.
    (lock / "pid").write_text(str(os.getpid()))

    bin_dir, uv_log = _stub_bin(tmp_path, "0.1.101")
    res = _run_ensure(plugin / "hooks" / "ensure-loci-cli.sh", home, bin_dir)

    assert res.returncode == 0, res.stderr
    assert any("0.1.120" in ln for ln in _installs(uv_log)), (
        "stale lock held by a recycled PID blocked the upgrade: "
        f"{_installs(uv_log)}"
    )


def test_uv_shim_outranks_a_shadowing_loci(tmp_path: Path):
    """`uv tool install` writes the shim into the uv bin dir. If any other `loci`
    wins the PATH lookup, the pin check reads a binary the installer never
    touches: it reinstalls every session and the version never moves."""
    fake_home = tmp_path / "home"
    uv_bin = fake_home / ".local" / "bin"
    uv_bin.mkdir(parents=True)
    (uv_bin / "loci").write_text("#!/usr/bin/env bash\necho 'loci 0.1.121'\n")
    (uv_bin / "loci").chmod(0o755)

    shadow = tmp_path / "opt" / "homebrew" / "bin"
    shadow.mkdir(parents=True)
    (shadow / "loci").write_text("#!/usr/bin/env bash\necho 'loci 0.1.101'\n")
    (shadow / "loci").chmod(0o755)

    script = (
        f'PLUGIN_DIR={_to_bash_path(PLUGIN_ROOT)}\n'
        f'. "$PLUGIN_DIR/lib/setup-steps.sh"\n'
        f'PATH={_to_bash_path(shadow)}:$PATH\n'
        "augment_path\n"
        "command -v loci\n"
        "loci_cli_version\n"
    )
    res = subprocess.run(
        [_find_bash(), "-c", script],
        env={**os.environ, "HOME": _to_bash_path(fake_home)},
        capture_output=True, text=True, timeout=60,
    )
    assert res.returncode == 0, res.stderr
    resolved, version = res.stdout.split("\n")[0], res.stdout.split("\n")[1]
    assert resolved == f"{_to_bash_path(uv_bin)}/loci", (
        f"shadowing binary won the lookup: {resolved}"
    )
    assert version == "0.1.121", version


def test_session_init_reports_a_stale_cli(tmp_path: Path):
    """Present-but-behind used to report nothing at all: every health branch
    tested absence, so a CLI that never upgraded was invisible to the session."""
    cache_root = tmp_path / "cache" / "loci" / "loci"
    cache_root.mkdir(parents=True)
    plugin = _stage_version(cache_root, "0.1.121")
    steps = plugin / "lib" / "setup-steps.sh"
    steps.write_text(
        steps.read_text(encoding="utf-8").replace(
            'LOCI_CLI_VERSION="', 'LOCI_CLI_VERSION="0.1.121"  # was: ', 1
        ),
        encoding="utf-8",
    )

    home = tmp_path / "home"
    (home / ".loci" / "state").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "loci").write_text("#!/usr/bin/env bash\necho 'loci 0.1.101'\n")
    (bin_dir / "loci").chmod(0o755)

    env = {
        **os.environ,
        "HOME": _to_bash_path(home),
        "LOCI_STATE_DIR": _to_bash_path(home / ".loci" / "state"),
        "PATH": f"{_to_bash_path(bin_dir)}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    env.pop("LOCI_DEV_CLI_PATH", None)  # a dev checkout floats and is exempt
    res = subprocess.run(
        [_find_bash(), _to_bash_path(plugin / "hooks" / "session-init.sh")],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert res.returncode == 0, res.stderr

    payload = json.loads(res.stdout)
    ctx = payload["hookSpecificOutput"]["additionalContext"]
    assert "0.1.101" in ctx and "0.1.121" in ctx, (
        f"stale CLI not reported in the session context:\n{ctx[:1500]}"
    )
    assert "0.1.101" in payload.get("systemMessage", ""), (
        "a stale CLI must be visible to the user, not just to Claude"
    )
