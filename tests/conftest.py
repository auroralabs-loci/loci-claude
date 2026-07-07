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
