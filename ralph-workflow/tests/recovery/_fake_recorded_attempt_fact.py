from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RecordedAttemptFact:
    phase: str
    agent: str
    artifact_valid: bool
    workspace_changed: bool | None
    free_retry_category: bool
