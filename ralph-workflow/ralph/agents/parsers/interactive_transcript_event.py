"""Semantic event model for interactive Claude transcript parsing."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InteractiveTranscriptEvent:
    """Semantic event extracted from the interactive Claude transcript surface."""

    kind: str
    text: str
