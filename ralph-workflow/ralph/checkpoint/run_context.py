"""Run lineage helpers for checkpoint payloads."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TypedDict
from uuid import uuid4


class RunContextDict(TypedDict):
    run_id: str
    parent_run_id: str | None
    resume_count: int
    actual_developer_runs: int
    actual_reviewer_runs: int


@dataclass(frozen=True)
class RunContext:
    """Track run lineage and actual completed work counts."""

    run_id: str
    parent_run_id: str | None = None
    resume_count: int = 0
    actual_developer_runs: int = 0
    actual_reviewer_runs: int = 0

    @classmethod
    def new(cls) -> RunContext:
        """Create a fresh run context."""
        return cls(run_id=str(uuid4()))

    @classmethod
    def resumed_from(cls, previous: RunContext) -> RunContext:
        """Create a new run context for a resumed session."""
        return cls(
            run_id=str(uuid4()),
            parent_run_id=previous.run_id,
            resume_count=previous.resume_count + 1,
            actual_developer_runs=previous.actual_developer_runs,
            actual_reviewer_runs=previous.actual_reviewer_runs,
        )

    def record_developer_iteration(self) -> RunContext:
        """Return a copy with one more completed developer iteration."""
        return replace(self, actual_developer_runs=self.actual_developer_runs + 1)

    def record_reviewer_pass(self) -> RunContext:
        """Return a copy with one more completed reviewer pass."""
        return replace(self, actual_reviewer_runs=self.actual_reviewer_runs + 1)

    def to_dict(self) -> RunContextDict:
        """Return a JSON-safe dictionary representation."""
        return {
            "run_id": self.run_id,
            "parent_run_id": self.parent_run_id,
            "resume_count": self.resume_count,
            "actual_developer_runs": self.actual_developer_runs,
            "actual_reviewer_runs": self.actual_reviewer_runs,
        }
