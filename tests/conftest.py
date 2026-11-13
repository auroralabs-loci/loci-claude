"""Root conftest — shared setup for the LOCI plugin test suite."""

import os
import sys
from pathlib import Path

# session-init.sh skips its loci-CLI self-install when _LOCI_BOOTSTRAP is set,
# keeping the SessionStart-hook subprocess tests offline and deterministic.
os.environ["_LOCI_BOOTSTRAP"] = "1"

# Make the plugin root importable (defensive — surviving tests use absolute
# paths, but this keeps ad-hoc `import`s from the repo root working).
_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))


# ── the host a hook test runs under (AAD-7790) ──────────────────────────────
#
# Parametrised over Claude Code and GitHub Copilot CLI. A module opts every
# test in with `pytest.mark.usefixtures("host")` in its `pytestmark`; a single
# test takes `host` by name. Its helpers read the active host through
# `tests.fixtures.copilot_payloads.current()`.

import pytest  # noqa: E402

from tests.fixtures import copilot_payloads as _hosts  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _eval_results_out_of_the_checkout(tmp_path_factory):
    """Every `run_evals.sh` a test starts writes its run directory under the
    session's temporary directory, never under the checkout's `eval-results/`.

    Two test files start the real harness, and the harness wrote each run to
    `<checkout>/eval-results/<timestamp>/` — 36 run directories per suite. The
    directory is ignored by git, so nothing showed and nothing was ever removed: 7,981 run
    directories and 8.2 GB on one machine, the reports people actually cite lost
    among them, and a tree so large that a lint walking the checkout timed out
    and ended the suite (AAD-8230).

    Session-wide and in the ENVIRONMENT on purpose: a test builds its child's
    environment from `os.environ`, so a harness run inherits this without the
    test knowing — including one written next year. The helpers that read a
    run's results back name a root of their own on top of it.
    """
    root = tmp_path_factory.mktemp("eval-results-root") / "eval-results"
    before = os.environ.get("LOCI_EVAL_RESULTS_DIR")
    os.environ["LOCI_EVAL_RESULTS_DIR"] = root.as_posix()
    try:
        yield root
    finally:
        if before is None:
            os.environ.pop("LOCI_EVAL_RESULTS_DIR", None)
        else:
            os.environ["LOCI_EVAL_RESULTS_DIR"] = before


@pytest.fixture(params=_hosts.HOSTS, ids=_hosts.HOSTS)
def host(request):
    h = _hosts.Host(request.param)
    _hosts.activate(h)
    try:
        yield h
    finally:
        _hosts.deactivate(h)
