"""Workspace snapshot value object."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkspaceSnapshot:
    """One coherent workspace observation used within a logical request.

    ``content`` is populated only for regular UTF-8 files. Callers reuse it
    for hashing and response rendering rather than opening the path again.
    """

    stat: dict[str, object]
    content: str | None
