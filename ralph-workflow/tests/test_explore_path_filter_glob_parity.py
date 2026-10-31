"""Regression tests: indexed grep's include/exclude globs match live grep's.

``compile_path_filter`` used ``fnmatch`` over the whole path while live grep
uses the segment-aware ``match_glob`` (``**`` spans directories, a bare name
matches any segment, ``*`` stays within one segment). The same grep call
returned different files depending on whether the index answered it.
"""

from __future__ import annotations

import pytest

from ralph.mcp.explore.path_filter import compile_path_filter
from ralph.mcp.tools.workspace import match_glob

_CASES = [
    # (kind, pattern, path, path is returned)
    ("include", "**/*.py", "setup.py", True),
    ("include", "**/*.py", "src/pkg/mod.py", True),
    ("include", "tests", "tests/test_a.py", True),
    ("include", "src/*.py", "src/sub/deep.py", False),
    ("include", "src/*.py", "src/top.py", True),
    ("include", "*.PY", "src/top.py", False),
    ("exclude", "vendor", "vendor/lib/x.py", False),
    ("exclude", "*.min.js", "web/app.min.js", False),
    ("exclude", "*.min.js", "web/app.js", True),
]


@pytest.mark.parametrize(("kind", "pattern", "path", "returned"), _CASES)
def test_indexed_glob_filter_matches_live_grep(
    kind: str, pattern: str, path: str, returned: bool
) -> None:
    path_filter = compile_path_filter(
        path_prefix=None,
        include_globs=[pattern] if kind == "include" else None,
        exclude_globs=[pattern] if kind == "exclude" else None,
    )
    assert path_filter is not None
    live_returned = (
        match_glob(path, pattern) if kind == "include" else not match_glob(path, pattern)
    )

    assert path_filter(path) is returned
    assert live_returned is returned
