"""_DetectedStackLike — structural protocol for language detector results."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence


@runtime_checkable
class _DetectedStackLike(Protocol):
    """Structural protocol for language detector results."""

    primary_language: str
    secondary_languages: Sequence[str]
    frameworks: Sequence[str]


__all__ = ["_DetectedStackLike"]
