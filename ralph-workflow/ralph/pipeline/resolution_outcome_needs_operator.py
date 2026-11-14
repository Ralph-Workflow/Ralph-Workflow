from dataclasses import dataclass

from ralph.git.resolution_failure_cause import ResolutionFailureCause

__all__ = ["NeedsOperator"]


@dataclass(frozen=True, slots=True)
class NeedsOperator:
    cause: ResolutionFailureCause
    paths: tuple[str, ...]
    command: str
