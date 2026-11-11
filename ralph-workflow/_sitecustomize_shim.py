
from __future__ import annotations

import os
import sys
from pathlib import Path

_site_packages_dir = Path(__file__).resolve().parent
_project_root_candidate = Path(sys.prefix).parent

_pythonpath_roots: list[str] = []
for _raw in os.environ.get("PYTHONPATH", "").split(os.pathsep):
    _entry = _raw.strip()
    if _entry and (Path(_entry) / "ralph" / "__init__.py").is_file():
        if _entry not in _pythonpath_roots:
            _pythonpath_roots.append(_entry)
        _resolved = str(Path(_entry).resolve())
        if _resolved not in _pythonpath_roots:
            _pythonpath_roots.append(_resolved)

_priority = [str(_site_packages_dir), *_pythonpath_roots, str(_project_root_candidate)]
_present_priority = [p for p in _priority if p in sys.path]
if _present_priority:
    _rest = [p for p in sys.path if p not in _priority]
    sys.path[:] = _present_priority + _rest
