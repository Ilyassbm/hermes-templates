"""Test bootstrap: make the running Hermes install importable.

The packer prefers Hermes' own YAML reader and its secret redactor, so the tests
run against the real ones when a Hermes install is present (``HERMES_ROOT``, by
default the container's ``/opt/hermes``) and fall back to the local patterns
otherwise.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

HERMES_ROOT = Path(os.environ.get("HERMES_ROOT", "/opt/hermes"))
if HERMES_ROOT.is_dir() and str(HERMES_ROOT) not in sys.path:
    sys.path.insert(0, str(HERMES_ROOT))

PLUGIN_DIR = Path(__file__).resolve().parent.parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))
