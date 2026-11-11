from __future__ import annotations

import os
import sys
from pathlib import Path

_pythonpath_entries = frozenset(
    entry for entry in os.environ.get("PYTHONPATH", "").split(os.pathsep) if entry
)
if _pythonpath_entries:
    sys.path[:] = [p for p in sys.path if p not in _pythonpath_entries]

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
