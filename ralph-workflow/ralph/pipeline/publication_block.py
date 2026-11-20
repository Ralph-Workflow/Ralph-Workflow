from __future__ import annotations

from enum import StrEnum


class PublicationBlock(StrEnum):
    GATE_FAILED = "publication_gate_failed"
    GATE_TIMED_OUT = "publication_gate_timed_out"


__all__ = ["PublicationBlock"]
