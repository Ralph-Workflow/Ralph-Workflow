"""Classification mapping of unmerged paths with optional per-path reasons."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.pipeline.conflict_resolution.sight import ConflictSight

__all__ = ["ConflictClassification"]


class ConflictClassification(dict[str, "ConflictSight"]):
    """Classification mapping of unmerged paths with optional per-path reasons."""

    def __init__(
        self,
        mapping: Mapping[str, ConflictSight] | None = None,
        *,
        reasons: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(mapping or {})
        self.reasons: dict[str, str] = dict(reasons or {})
