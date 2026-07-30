"""Path-filter helpers shared by the indexed search/grep paths.

Ponytail: small, single-purpose helper so the indexed grep/search
code path can apply the same path/include/exclude semantics as the
live grep branch without dragging in the full workspace traversal
machinery. The filter is a pure function of (path) -> bool.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Callable, Sequence


def compile_path_filter(
    *,
    path_prefix: str | None,
    include_globs: Sequence[str] | None,
    exclude_globs: Sequence[str] | None,
) -> Callable[[str], bool] | None:
    """Return a filter function or None when no filter is needed.

    The filter accepts a normalized workspace-relative POSIX path
    and returns True when the path passes every constraint.
    Constraints apply in this order:

    * ``path_prefix`` (when non-empty) requires the path to equal
      the prefix or start with ``prefix + '/'``.
    * ``include_globs`` (when non-empty) requires the path to match
      at least one glob (case-sensitive ``fnmatch``).
    * ``exclude_globs`` (when non-empty) rejects paths that match
      any glob.
    """
    normalized_prefix = (path_prefix or "").rstrip("/")
    includes: tuple[str, ...] = tuple(include_globs or ())
    excludes: tuple[str, ...] = tuple(exclude_globs or ())
    if not normalized_prefix and not includes and not excludes:
        return None

    def _filter(path: str) -> bool:
        if (
            normalized_prefix
            and path != normalized_prefix
            and not path.startswith(normalized_prefix + "/")
        ):
            return False
        if includes and not any(fnmatch.fnmatch(path, g) for g in includes):
            return False
        return not (excludes and any(fnmatch.fnmatch(path, g) for g in excludes))

    return _filter
