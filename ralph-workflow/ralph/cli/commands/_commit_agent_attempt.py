from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CommitAgentAttempt:
    """Result of a single commit-message agent invocation attempt."""

    message: str = ""
    skipped: bool = False
    failure_detail: str = ""
    parsed_output: list[str] = field(default_factory=list)
    raw_output: list[str] = field(default_factory=list)
    resume_session_id: str | None = None
