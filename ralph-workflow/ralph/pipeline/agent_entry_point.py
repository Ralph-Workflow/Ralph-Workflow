from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from types import MappingProxyType, SimpleNamespace
from typing import TYPE_CHECKING, Final, cast

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.pipeline.attempt_result import AttemptResult


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


EntryPointRunner = Callable[[object], "AttemptResult"]

_ENTRY_POINT_REGISTRY: Final = SimpleNamespace(
    runners=cast("dict[AgentEntryPoint, EntryPointRunner]", {})
)


def _mutable_entry_point_runners() -> dict[AgentEntryPoint, EntryPointRunner]:
    return cast(
        "dict[AgentEntryPoint, EntryPointRunner]",
        _ENTRY_POINT_REGISTRY.runners,
    )


def register_entry_point(entry_point: AgentEntryPoint, runner_fn: EntryPointRunner) -> None:
    runners = _mutable_entry_point_runners()
    if entry_point in runners:
        raise ValueError(f"entry point already registered: {entry_point.value}")
    runners[entry_point] = runner_fn


def registered_entry_points() -> tuple[AgentEntryPoint, ...]:
    return tuple(_mutable_entry_point_runners())


def registered_runners() -> Mapping[AgentEntryPoint, EntryPointRunner]:
    return MappingProxyType(_mutable_entry_point_runners())


__all__ = (
    "AgentEntryPoint",
    "EntryPointRunner",
    "register_entry_point",
    "registered_entry_points",
    "registered_runners",
)
