from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from loguru import logger

from ralph.pipeline.integration_diagnostic import (
    PLANNING_HANDOFF_FILENAME,
    read_planning_handoff_record,
)

if TYPE_CHECKING:
    from ralph.pipeline.integration_attempt_ledger import AttemptLedger
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import PipelinePolicy

__all__ = [
    "PLANNING_HANDOFF_CONSUMED_KEY",
    "PlanningHandoffContinuation",
    "continue_with_planning_handoff",
    "resolve_planning_handoff_path",
]

PLANNING_HANDOFF_CONSUMED_KEY: Final[str] = "handoff:planning"
_PRESERVED_HANDOFF_EVIDENCE_KEY: Final[str] = "preserved:planning-handoff"


@dataclass(frozen=True, slots=True)
class PlanningHandoffContinuation:
    handoff: dict[str, object]
    handoff_path: Path
    next_phase: str
    state: PipelineState | None = None


def resolve_planning_handoff_path(
    *,
    handoff_path: Path | None = None,
    ledger: AttemptLedger | None = None,
    state_dir: Path | None = None,
) -> Path | None:
    if handoff_path is not None:
        return handoff_path
    if ledger is not None:
        recorded = ledger.read().ladder_evidence.get(_PRESERVED_HANDOFF_EVIDENCE_KEY)
        if isinstance(recorded, str) and recorded:
            return Path(recorded)
    if state_dir is not None:
        candidate = state_dir / PLANNING_HANDOFF_FILENAME
        if candidate.is_file():
            return candidate
    return None


def _handoff_summary(handoff: dict[str, object]) -> str:
    bundle = handoff.get("bundle", "")
    carried = handoff.get("carried", ())
    not_carried = handoff.get("not_carried", ())
    return (
        "last-resort planning handoff: "
        f"bundle={bundle}; carried={carried!r}; not_carried={not_carried!r}"
    )


def _clear_abandoned_integration(root: Path) -> None:
    from ralph.pipeline.auto_integrate_record import clear_record

    try:
        clear_record(root)
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        logger.warning("planning handoff: could not clear abandoned integration record: {}", exc)


def _advance_to_planning(
    state: PipelineState,
    *,
    pipeline_policy: PipelinePolicy,
    handoff: dict[str, object],
    next_phase: str,
) -> PipelineState:
    from ralph.pipeline.progress import advance_phase
    from ralph.pipeline.rebase_state import RebaseState

    target = next_phase if next_phase in pipeline_policy.phases else pipeline_policy.entry_phase
    advanced = advance_phase(state, target, policy=pipeline_policy)
    return advanced.copy_with(
        last_error=_handoff_summary(handoff),
        rebase=RebaseState(),
    ).clear_waiting()


def continue_with_planning_handoff(
    root: Path,
    *,
    handoff_path: Path | None = None,
    ledger: AttemptLedger | None = None,
    state_dir: Path | None = None,
    state: PipelineState | None = None,
    pipeline_policy: PipelinePolicy | None = None,
) -> PlanningHandoffContinuation | None:
    from ralph.pipeline._integration_ladder_evidence import _record_evidence

    path = resolve_planning_handoff_path(
        handoff_path=handoff_path,
        ledger=ledger,
        state_dir=state_dir,
    )
    if path is None:
        return None
    handoff = read_planning_handoff_record(path)
    if handoff is None:
        return None

    next_phase_raw = handoff.get("next_phase", "planning")
    next_phase = (
        next_phase_raw if isinstance(next_phase_raw, str) and next_phase_raw else "planning"
    )

    _clear_abandoned_integration(root)
    if ledger is not None:
        _record_evidence(ledger, PLANNING_HANDOFF_CONSUMED_KEY, str(path))
        _record_evidence(ledger, "transition:autonomous", "planning")

    new_state: PipelineState | None = None
    if state is not None and pipeline_policy is not None:
        target = next_phase if next_phase in pipeline_policy.phases else pipeline_policy.entry_phase
        if (
            state.phase == target
            and isinstance(state.last_error, str)
            and "last-resort planning handoff:" in state.last_error
        ):
            new_state = state
        else:
            new_state = _advance_to_planning(
                state,
                pipeline_policy=pipeline_policy,
                handoff=handoff,
                next_phase=next_phase,
            )

    logger.warning(
        "last-resort planning hand-over consumed {}; next_phase={}; bundle={}",
        path,
        next_phase,
        handoff.get("bundle"),
    )
    return PlanningHandoffContinuation(
        handoff=handoff,
        handoff_path=path,
        next_phase=next_phase,
        state=new_state,
    )
