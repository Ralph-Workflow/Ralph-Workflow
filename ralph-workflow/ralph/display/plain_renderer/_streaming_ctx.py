"""Context bundle for streaming block management."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class _StreamingCtx:
    """Context bundle for streaming block management helpers."""

    unit_id: str
    kind: str
    content: str
    base_tag: str
    timestamp: str
