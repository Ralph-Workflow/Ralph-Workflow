from __future__ import annotations

from collections.abc import Callable, Sequence


def compile_path_filter(
    *,
    path_prefix: str | None,
    include_globs: Sequence[str] | None,
    exclude_globs: Sequence[str] | None,
) -> Callable[[str], bool] | None:
    normalized_prefix = (path_prefix or "").rstrip("/")
    includes: tuple[str, ...] = tuple(include_globs or ())
    excludes: tuple[str, ...] = tuple(exclude_globs or ())
    if not normalized_prefix and not includes and not excludes:
        return None
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
