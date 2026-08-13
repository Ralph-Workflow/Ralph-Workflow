"""Snapshot model for silent-agent circumstantial evidence."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CircumstantialEvidence:
    """Snapshot of the evidence used to diagnose a silent agent invocation."""

    process_alive: bool
    has_stdout_bytes: bool
    has_meaningful_output: bool
    captured_session_id: str | None
    has_session_id_captured: bool
    process_started_at: float | None
    elapsed_seconds: float


__all__ = ["CircumstantialEvidence"]
