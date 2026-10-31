"""Path-filter helpers shared by the indexed search/grep paths.

Ponytail: small, single-purpose helper so the indexed grep/search
code path can apply the same path/include/exclude semantics as the
live grep branch without dragging in the full workspace traversal
machinery. The filter is a pure function of (path) -> bool.
"""

from __future__ import annotations

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
      at least one glob.
    * ``exclude_globs`` (when non-empty) rejects paths that match
      any glob.

    Globs are matched by :func:`ralph.mcp.tools.workspace.match_glob`,
    the matcher live grep uses, so the indexed and live branches select
    the same files: matching is per path segment and case-sensitive,
    ``*`` and ``?`` never cross a ``/``, ``**`` spans any number of
    directories (including none), a single-segment pattern matches any
    segment of the path, and a multi-segment pattern matches the path's
    trailing segments.
    """
    normalized_prefix = (path_prefix or "").rstrip("/")
    includes: tuple[str, ...] = tuple(include_globs or ())
    excludes: tuple[str, ...] = tuple(exclude_globs or ())
    if not normalized_prefix and not includes and not excludes:
        return None
    # Imported here: ``ralph.mcp.tools.workspace`` reaches the explore
    # store, which reaches this module.
    from ralph.mcp.tools.workspace import match_glob

    def _filter(path: str) -> bool:
        if (
            normalized_prefix
            and path != normalized_prefix
            and not path.startswith(normalized_prefix + "/")
        ):
            return False
        if includes and not any(match_glob(path, g) for g in includes):
            return False
        return not (excludes and any(match_glob(path, g) for g in excludes))

    return _filter
