"""Optional context for building a PhaseExitModel from a PhaseEntryModel."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExitContext:
    """Optional context for building a PhaseExitModel from a PhaseEntryModel."""

    elapsed_seconds: float = 0.0
    exit_trigger: str | None = None
    content_blocks: int = 0
    thinking_blocks: int = 0
    tool_calls: int = 0
    errors: int = 0
    artifact_outcome: str = ""
    review_issues_found: bool | None = None
    routing_note: str | None = None
    waiting_status_line: str | None = None
    last_failure_category: str | None = None
