"""Optional context for building a PhaseExitModel from a PhaseEntryModel."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExitContext:
    """Optional context for building a PhaseExitModel from a PhaseEntryModel."""

    elapsed_seconds: float = 0.0
    exit_trigger: str | None = None
    content_blocks: int | None = None
    thinking_blocks: int | None = None
    tool_calls: int | None = None
    errors: int | None = None
    artifact_outcome: str = ""
    review_issues_found: bool | None = None
    routing_note: str | None = None
    waiting_status_line: str | None = None
    last_failure_category: str | None = None
