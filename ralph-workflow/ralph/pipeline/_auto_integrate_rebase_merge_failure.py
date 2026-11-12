from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.git.rebase._rebase_kind import RebaseKind
from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline.auto_integrate_outcome import record_skip

if TYPE_CHECKING:
    from ralph.git.rebase.rebase import RebaseFailed
    from ralph.pipeline.rebase_state import RebaseState

_ENVIRONMENTAL_REBASE_KINDS = frozenset(
    {
        RebaseKind.CONCURRENT_OPERATION,
        RebaseKind.ENVIRONMENT_FAILURE,
        RebaseKind.REFERENCE_UPDATE_FAILED,
        RebaseKind.REPOSITORY_CORRUPT,
    }
)

_ENVIRONMENTAL_IO_MARKERS = (
    "disk full",
    "filesystem",
    "i/o error",
    "io error",
    "no space left",
    "permission denied",
    "read-only",
    "too many open files",
    "worktree directory was deleted",
)


def _rebase_failure_detail(failure: RebaseFailed) -> str:
    metadata = failure.kind.metadata
    for key in ("reason", "details", "operation"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return failure.kind.kind.value


def _environmental_rebase_reason(failure: RebaseFailed) -> str | None:
    kind = failure.kind.kind
    detail = _rebase_failure_detail(failure)
    if kind in _ENVIRONMENTAL_REBASE_KINDS:
        category = kind.value
    elif kind is RebaseKind.UNKNOWN:
        lowered = detail.lower()
        if "timed out" in lowered or "timeout" in lowered:
            category = "timeout"
        elif "killed" in lowered or "sigkill" in lowered:
            category = "killed"
        elif "lock" in lowered or "concurrent" in lowered:
            category = "concurrent_operation"
        elif any(marker in lowered for marker in _ENVIRONMENTAL_IO_MARKERS):
            category = "io_error"
        else:
            return None
    else:
        return None
    return f"rebase failed for an environmental reason ({category}): {detail}"


def _failed_rebase_reason(failure: RebaseFailed) -> str:
    environmental = _environmental_rebase_reason(failure)
    if environmental is not None:
        return environmental
    return f"rebase failed ({failure.kind.kind.value}): {_rebase_failure_detail(failure)}"


def _failed_rebase_state(failure: RebaseFailed, target: str) -> RebaseState:
    return record_skip(
        reason=_failed_rebase_reason(failure),
        target=target,
        failure_cause=ResolutionFailureCause.CONTEXT_FAILURE,
    )
