"""The rejection outcome of applying a batch of text edits."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RejectedTextEdits:
    """An edit missed; ``payload`` is the structured tool-error body."""

    payload: dict[str, object]
