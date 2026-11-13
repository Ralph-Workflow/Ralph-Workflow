from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.attempt_result import AttemptResult
    from ralph.pipeline.attempt_runner_deps import AttemptRunnerDeps
    from ralph.recovery.controller import RecoveryController


class AgentEntryPoint(StrEnum):
    MAIN_PHASE_LOOP = "main_phase_loop"
    PARALLEL_WORKER = "parallel_worker"
    INTEGRATION_CONTINUATION = "integration_continuation"
    CONFLICT_RESOLUTION = "conflict_resolution"
    COMMIT_GENERATION = "commit_generation"
    SMOKE = "smoke"
    PROJECT_POLICY = "project_policy"
    SESSION_RUNTIME = "session_runtime"
    CANARY_PROBE = "canary_probe"


class _EntryPointRunner(Protocol):
    def __call__(
        self,
        *,
        effect: object | None = ...,
        invocation: object | None = ...,
        config: UnifiedConfig,
        controller: RecoveryController,
        deps: AttemptRunnerDeps,
        state: object | None = ...,
        **execute_kwargs: object,
    ) -> AttemptResult: ...


EntryPointRunner = _EntryPointRunner

_ENTRY_POINT_RUNNERS: Final[dict[AgentEntryPoint, EntryPointRunner | None]] = dict.fromkeys(
    AgentEntryPoint
)


def register_entry_point(entry_point: AgentEntryPoint, runner_fn: EntryPointRunner) -> None:
    if _ENTRY_POINT_RUNNERS[entry_point] is not None:
        raise ValueError(f"entry point already registered: {entry_point.value}")
    _ENTRY_POINT_RUNNERS[entry_point] = runner_fn


def registered_entry_points() -> tuple[AgentEntryPoint, ...]:
    return tuple(
        entry_point for entry_point, runner in _ENTRY_POINT_RUNNERS.items() if runner is not None
    )


def registered_runners() -> Mapping[AgentEntryPoint, EntryPointRunner]:
    return MappingProxyType(
        {
            entry_point: runner
            for entry_point, runner in _ENTRY_POINT_RUNNERS.items()
            if runner is not None
        }
    )


__all__ = (
    "AgentEntryPoint",
    "EntryPointRunner",
    "register_entry_point",
    "registered_entry_points",
    "registered_runners",
)
