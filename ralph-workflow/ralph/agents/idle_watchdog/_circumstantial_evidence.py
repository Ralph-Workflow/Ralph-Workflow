"""Snapshot dataclass for silent-agent diagnostic evidence.

Extracted from ``idle_watchdog.py`` so the module satisfies the
repo-structure audit's one-top-level-class rule. Also the single source
of truth for the bounded-output significance rule shared by the
completion gate (``ralph/agents/invoke/_completion.py``) and the live
broken-agent timer (``ralph/agents/invoke/_process_reader.py``): both
must agree on how much output separates "a silent provider" from "a
productive agent that failed its artifact contract".
"""

from __future__ import annotations

from dataclasses import dataclass

#: Maximum nonblank output lines a stream may carry and still count as
#: structurally small (harness-only) for the broken-agent classification.
BROKEN_AGENT_MAX_NONBLANK_LINES: int = 2
#: Maximum total output bytes a stream may carry and still count as
#: structurally small (harness-only) for the broken-agent classification.
BROKEN_AGENT_MAX_OUTPUT_BYTES: int = 256


def is_structurally_small_output(
    *,
    nonblank_line_count: int,
    total_bytes: int,
) -> bool:
    """Return whether an observed output stream is structurally tiny.

    This is intentionally small-structure only: up to
    ``BROKEN_AGENT_MAX_NONBLANK_LINES`` nonblank lines and up to
    ``BROKEN_AGENT_MAX_OUTPUT_BYTES`` total bytes. Substantial output
    follows the normal resumable/retry path even when every line was
    classified as non-meaningful for the invocation that produced it --
    a run that streamed a large transcript but never submitted its
    required artifact is an artifact-submission failure, not provider
    unavailability.
    """
    if nonblank_line_count > BROKEN_AGENT_MAX_NONBLANK_LINES:
        return False
    return total_bytes <= BROKEN_AGENT_MAX_OUTPUT_BYTES


def is_structurally_small_bounded_output(bounded_output: list[str]) -> bool:
    """Apply :func:`is_structurally_small_output` to bounded output lines."""
    return is_structurally_small_output(
        nonblank_line_count=sum(1 for line in bounded_output if line.strip()),
        total_bytes=sum(len(line.encode("utf-8")) for line in bounded_output),
    )


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


__all__ = [
    "BROKEN_AGENT_MAX_NONBLANK_LINES",
    "BROKEN_AGENT_MAX_OUTPUT_BYTES",
    "CircumstantialEvidence",
    "is_structurally_small_bounded_output",
    "is_structurally_small_output",
]
