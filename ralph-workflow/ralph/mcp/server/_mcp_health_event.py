"""Bounded sink for MCP supervisor stage availability decisions.

LR (lifecycle owner) calls :func:`record_stage` at each availability
decision; incident triage reads ``_EVENTS`` to recover the exact
sequence of available/unavailable verdicts the supervisor emitted.
"""

from __future__ import annotations

from collections import deque

# bounded-accumulator-ok: cap=64
_EVENTS: deque[tuple[str, bool]] = deque(maxlen=64)


def record_stage(stage: str, *, available: bool) -> None:
    """Append an immutable ``(stage, available)`` record to ``_EVENTS``."""
    _EVENTS.append((stage, available))


__all__ = ["record_stage"]
