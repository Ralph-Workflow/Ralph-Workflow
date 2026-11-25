from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


def default_protected_roots() -> tuple[Path, ...]:
    system_temp = Path(tempfile.gettempdir())
    return (Path("/"), Path.home(), system_temp, system_temp.parent)


def _may_be_workspace(candidate: Path, protected: Sequence[Path]) -> bool:
    return not any(root == candidate or root.is_relative_to(candidate) for root in protected)


def resolve_session_workspace_root(
    path: Path, *, protected_roots: Sequence[Path] | None = None
) -> Path:
    resolved_path = Path(path).resolve()
    roots = default_protected_roots() if protected_roots is None else protected_roots
    protected = tuple(Path(root).resolve() for root in roots)
    if resolved_path.parent.name == ".agent":
        owner = resolved_path.parent.parent.resolve()
        if _may_be_workspace(owner, protected):
            return owner
    cursor = resolved_path.parent
    while cursor != cursor.parent:
        if (cursor / ".agent").is_dir() and _may_be_workspace(cursor.resolve(), protected):
            return cursor.resolve()
        cursor = cursor.parent
    fallback = resolved_path.parent.parent.parent.resolve()
    if _may_be_workspace(fallback, protected):
        return fallback
    return resolved_path.parent


__all__ = ["default_protected_roots", "resolve_session_workspace_root"]
