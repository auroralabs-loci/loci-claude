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


@pytest.fixture(params=_hosts.HOSTS, ids=_hosts.HOSTS)
def host(request):
    h = _hosts.Host(request.param)
    _hosts.activate(h)
    try:
        yield h
    finally:
        _hosts.deactivate(h)
