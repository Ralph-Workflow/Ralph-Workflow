from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from ralph.phases.required_artifacts import retry_fingerprint_path, retry_hint_path

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import PipelinePolicy

NO_PROGRESS_REROUTE_LIMIT = 3
OVERRIDDEN_SUCCESS_LIMIT = 3


class RerouteAction(Enum):
    REROUTE = "reroute"
    FRESH_ENTRY = "fresh_entry"
    FAIL = "fail"


def reroute_signature(failing_phase: str, target_phase: str, cause: str) -> str:
    return f"{failing_phase} -> {target_phase}: {cause}"


def record_reroute(state: PipelineState, signature: str) -> tuple[RerouteAction, int]:
    count = (
        state.no_progress_reroute_count + 1
        if state.no_progress_reroute_signature == signature
        else 1
    )
    if count < NO_PROGRESS_REROUTE_LIMIT:
        return RerouteAction.REROUTE, count
    if count == NO_PROGRESS_REROUTE_LIMIT:
        return RerouteAction.FRESH_ENTRY, count
    return RerouteAction.FAIL, count


def with_reroute_recorded(state: PipelineState, signature: str, count: int) -> PipelineState:
    return state.copy_with(
        no_progress_reroute_signature=signature,
        no_progress_reroute_count=count,
    )


def with_agent_invoked(state: PipelineState) -> PipelineState:
    if state.no_progress_reroute_signature is None and state.no_progress_reroute_count == 0:
        return state
    return state.copy_with(no_progress_reroute_signature=None, no_progress_reroute_count=0)


def success_overrides_exhausted(state: PipelineState) -> bool:
    return (
        state.overridden_success_phase == state.phase
        and state.overridden_success_count + 1 >= OVERRIDDEN_SUCCESS_LIMIT
    )


def with_success_override_recorded(
    state: PipelineState, next_state: PipelineState, *, overridden: bool
) -> PipelineState:
    if overridden and next_state.phase == state.phase:
        count = (
            state.overridden_success_count + 1
            if state.overridden_success_phase == state.phase
            else 1
        )
        return next_state.copy_with(
            overridden_success_phase=state.phase, overridden_success_count=count
        )
    if next_state.overridden_success_phase is None and next_state.overridden_success_count == 0:
        return next_state
    return next_state.copy_with(overridden_success_phase=None, overridden_success_count=0)


def clear_phase_continuation(
    workspace_root: Path,
    phases: tuple[str, ...],
    pipeline_policy: PipelinePolicy,
) -> None:
    for phase in phases:
        for relative in (
            retry_hint_path(phase, pipeline_policy=pipeline_policy),
            retry_fingerprint_path(phase, pipeline_policy=pipeline_policy),
        ):
            (workspace_root / relative).unlink(missing_ok=True)


def no_progress_failure_reason(failing_phase: str, cause: str, count: int) -> str:
    return (
        f"phase {failing_phase} cannot start: {cause}; "
        f"{count} identical re-routes, including one fresh-entry retry, "
        "invoked no agent and changed nothing"
    )


def recovery_cap_failure_reason(failing_phase: str, cause: str, cycle_cap: int) -> str:
    return f"phase {failing_phase} cannot start: {cause}; recovery cycle cap {cycle_cap} reached"
