"""Execution result event for the pipeline."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionResultEvent:
    """Event emitted when an execution phase reports its artifact status."""

    phase: str
    status: str
