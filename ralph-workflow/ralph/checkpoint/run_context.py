"""Run lineage helpers for checkpoint payloads."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TypedDict
from uuid import uuid4


class RunContextDict(TypedDict):
    """JSON-safe representation of run lineage data for checkpointing."""

    run_id: str
    parent_run_id: str | None
    resume_count: int
    actual_developer_runs: int
    actual_reviewer_runs: int
    recovery_cycle_count: int
    fallover_history: list[dict[str, object]]
    last_failure_category: str | None


@dataclass(frozen=True)
class RunContext:
    """Track run lineage and actual completed work counts."""

    run_id: str
    parent_run_id: str | None = None
    resume_count: int = 0
    actual_developer_runs: int = 0
    actual_reviewer_runs: int = 0
    recovery_cycle_count: int = 0
    fallover_history: list[dict[str, object]] = field(default_factory=list)
    last_failure_category: str | None = None

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
            recovery_cycle_count=previous.recovery_cycle_count,
            fallover_history=list(previous.fallover_history),
            last_failure_category=previous.last_failure_category,
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
            "recovery_cycle_count": self.recovery_cycle_count,
            "fallover_history": list(self.fallover_history),
            "last_failure_category": self.last_failure_category,
        }
