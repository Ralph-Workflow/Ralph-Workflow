"""Typed result returned by an agent executor."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["WorkerResult"]


@dataclass(frozen=True)
class WorkerResult:
    """Immutable result returned by an executor after a work unit finishes.

    ``exit_code`` mirrors the subprocess exit status; 0 indicates success.
    ``final_message`` is the last status line emitted by the agent.
    ``duration_ms`` is the wall-clock elapsed time for the unit.
    """

    unit_id: str
    exit_code: int
    final_message: str
    duration_ms: int
