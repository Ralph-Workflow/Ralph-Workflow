from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PublicationGateFailed:
    phase: str
    detail: str


__all__ = ["PublicationGateFailed"]
