
from __future__ import annotations

from dataclasses import dataclass

BROKEN_AGENT_MAX_NONBLANK_LINES: int = 2
BROKEN_AGENT_MAX_OUTPUT_BYTES: int = 256


def is_structurally_small_output(
    *,
    nonblank_line_count: int,
    total_bytes: int,
) -> bool:
    if nonblank_line_count > BROKEN_AGENT_MAX_NONBLANK_LINES:
        return False
    return total_bytes <= BROKEN_AGENT_MAX_OUTPUT_BYTES


def is_structurally_small_bounded_output(bounded_output: list[str]) -> bool:
    return is_structurally_small_output(
        nonblank_line_count=sum(1 for line in bounded_output if line.strip()),
        total_bytes=sum(len(line.encode("utf-8")) for line in bounded_output),
    )


@dataclass
class CircumstantialEvidence:

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
