from __future__ import annotations

import os
import sys
from pathlib import Path

# Strip PYTHONPATH-derived entries from sys.path before any other conftest
# or test module imports anything under `ralph.*`. An ambient PYTHONPATH
# inherited from the invoking shell (e.g. a pipx-installed copy of this
# package on some other interpreter's site-packages) can shadow this
# project's own editable `ralph` install, so `import ralph...` resolves
# against a stale, broken copy (missing compiled extensions such as
# `pydantic_core._pydantic_core`) instead of the local source. This is the
# in-process mirror of `ralph/verify.py`'s subprocess-level `PYTHONPATH`
# clearing: `make verify` spawns each step with `PYTHONPATH=""`, but a
# standalone `uv run pytest ...` invocation is not spawned through that
# runner, so pytest's own process must sanitize its `sys.path` itself. This
# file is the repository root `conftest.py`, collected before
# `tests/conftest.py` and any other test module, so the fix must live here
# to take effect before the first `ralph.*` import anywhere in the suite.
_pythonpath_entries = frozenset(
    entry for entry in os.environ.get("PYTHONPATH", "").split(os.pathsep) if entry
)
if _pythonpath_entries:
    sys.path[:] = [p for p in sys.path if p not in _pythonpath_entries]

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
