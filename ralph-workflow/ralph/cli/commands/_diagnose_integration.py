from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from ralph.git.merge import observe_unmerged_paths
from ralph.pipeline._integration_status import IntegrationStatus, build_integration_status
from ralph.pipeline.auto_integrate_record import read_record
from ralph.pipeline.checkpoint import load as load_checkpoint
from ralph.pipeline.integration_director import observe as observe_integration
from ralph.pipeline.integration_observation import (
    InProgressKind,
    Observation,
    TargetVsFeature,
    WorktreeState,
)
from ralph.pipeline.integration_strategy import (
    IntegrationPolicy,
    IntegrationRelation,
    IntegrationWorkShape,
    ObservedIntegration,
    StrategyDecision,
    choose_integration_strategy,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.pipeline.auto_integrate_record import IntegrationRecord
    from ralph.pipeline.state import PipelineState


def _persisted_integration_status(
    workspace_root: Path,
    view: IntegrationLifecycleView | None = None,
) -> IntegrationStatus | None:
    state = load_checkpoint(workspace_root / ".agent" / "checkpoint.json")
    if state is None:
        return _status_from_view(view)
    rebase = state.rebase
    if not rebase.resolution_exhausted and not rebase.integration_unresolved:
        return _status_from_view(view)
    reason = rebase.resolution_exhaustion_reason or rebase.last_reason or ""
    prefix, separator, _detail = reason.partition(":")
    cause = prefix if separator and prefix.isupper() else rebase.last_failure_cause.value
    paths = tuple(rebase.last_conflict_paths)
    return build_integration_status(
        cause=cause,
        paths_with_reasons=tuple((path, "persisted integration conflict") for path in paths),
        command=f"git add -- {' '.join(shlex.quote(p) for p in paths)}" if paths else "",
        agents_can_finish=False,
        done_count=None if view is None else view.done_count,
        remaining_count=None if view is None else view.remaining_count,
        relation=None if view is None or view.relation is None else view.relation.value,
        strategy=(None if view is None or view.decision is None else view.decision.strategy.value),
        strategy_reason=None if view is None or view.decision is None else view.decision.reason,
        at_risk=() if view is None else view.at_risk,
    )


def _status_from_view(view: IntegrationLifecycleView | None) -> IntegrationStatus | None:
    if view is None:
        return None
    return build_integration_status(
        cause=view.state_text,
        command="",
        done_count=view.done_count,
        remaining_count=view.remaining_count,
        relation=None if view.relation is None else view.relation.value,
        strategy=None if view.decision is None else view.decision.strategy.value,
        strategy_reason=None if view.decision is None else view.decision.reason,
        at_risk=view.at_risk,
    )


@dataclass(frozen=True)
class IntegrationLifecycleView:
    state_text: str
    relation: IntegrationRelation | None
    decision: StrategyDecision | None
    done_count: int
    remaining_count: int
    at_risk: tuple[str, ...]


_RELATION_BY_TARGET_VS_FEATURE: Final[dict[TargetVsFeature, IntegrationRelation]] = {
    TargetVsFeature.EQUAL: IntegrationRelation.EQUAL,
    TargetVsFeature.AHEAD: IntegrationRelation.BEHIND,
    TargetVsFeature.BEHIND: IntegrationRelation.AHEAD,
    TargetVsFeature.DIVERGED: IntegrationRelation.DIVERGED,
}


def _lifecycle_rows(view: IntegrationLifecycleView) -> list[tuple[object, ...]]:
    rows: list[tuple[object, ...]] = [
        (
            "Relation (feature vs target)",
            view.relation.value if view.relation is not None else "unknown",
        ),
        (
            "Chosen strategy",
            view.decision.strategy.value if view.decision is not None else "unknown",
        ),
        (
            "Why this strategy",
            view.decision.reason
            if view.decision is not None
            else "no strategy could be chosen from the observed relation",
        ),
        ("Done (paths resolved)", str(view.done_count)),
        ("Remaining (paths unresolved)", str(view.remaining_count)),
        ("At risk", "\n".join(view.at_risk) or "nothing"),
    ]
    return rows


def _read_durable_record(workspace_root: Path) -> IntegrationRecord | None:
    try:
        return read_record(workspace_root)
    except Exception:
        return None


def _read_checkpoint_state(workspace_root: Path) -> PipelineState | None:
    try:
        return load_checkpoint(workspace_root / ".agent" / "checkpoint.json")
    except Exception:
        return None


def _conflict_paths(
    record: IntegrationRecord | None,
    state: PipelineState | None,
) -> tuple[str, ...]:
    if record is not None and record.resolving_paths:
        return tuple(record.resolving_paths)
    if state is not None and state.rebase.last_conflict_paths:
        return tuple(state.rebase.last_conflict_paths)
    return ()


def _lifecycle_state_text(
    record: IntegrationRecord | None,
    observation: Observation,
) -> str:
    in_progress = observation.in_progress_kind
    if record is None and in_progress is InProgressKind.NONE:
        return "nothing in flight — no integration record and no paused operation"
    if record is None:
        return f"in flight — {in_progress.name.lower()} in progress without an integration record"
    if not record.is_integrating:
        return f"not integrating — record phase '{record.phase}' on '{record.target}'"
    if in_progress is InProgressKind.NONE:
        return f"in flight — {record.phase} onto '{record.target}'"
    return (
        f"in flight — {record.phase} onto '{record.target}'; {in_progress.name.lower()} in progress"
    )


def _worktree_risk(observation: Observation) -> tuple[str, ...]:
    if observation.worktree is WorktreeState.TRACKED_CHANGES:
        return ("uncommitted tracked changes in the worktree",)
    return ()


def _integration_lifecycle_view(workspace_root: Path) -> IntegrationLifecycleView | None:
    try:
        observation = observe_integration(workspace_root)
    except Exception:
        return None
    record = _read_durable_record(workspace_root)
    state = _read_checkpoint_state(workspace_root)
    try:
        unmerged_observation = observe_unmerged_paths(workspace_root)
    except Exception:
        unmerged_observation = None
    unmerged_paths = (
        tuple(unmerged_observation.paths)
        if unmerged_observation is not None and unmerged_observation.query_ok
        else ()
    )
    conflict_paths = _conflict_paths(record, state)
    relation = _RELATION_BY_TARGET_VS_FEATURE.get(observation.target_vs_feature)
    if record is not None and record.pre_feature_sha and record.pre_target_sha:
        from ralph.pipeline.integration_director import (
            _TARGET_VS_FEATURE_BY_RELATION,
            _project_known_ancestry,
        )

        ancestry = _project_known_ancestry(
            workspace_root,
            record.pre_target_sha,
            record.pre_feature_sha,
            None,
        )
        target_vs_feat = _TARGET_VS_FEATURE_BY_RELATION.get(ancestry)
        if target_vs_feat is not None:
            relation = _RELATION_BY_TARGET_VS_FEATURE.get(target_vs_feat, relation)
    conflicts_seen = bool(conflict_paths or unmerged_paths)
    decision = (
        choose_integration_strategy(
            ObservedIntegration(
                relation=relation,
                work_shape=IntegrationWorkShape(conflicts_seen=conflicts_seen),
            ),
            IntegrationPolicy(),
        )
        if relation is not None
        else None
    )
    remaining_paths = unmerged_paths or conflict_paths
    remaining = len(remaining_paths)
    done = max(0, len(conflict_paths) - remaining)
    return IntegrationLifecycleView(
        state_text=_lifecycle_state_text(record, observation),
        relation=relation,
        decision=decision,
        done_count=done,
        remaining_count=remaining,
        at_risk=remaining_paths or _worktree_risk(observation),
    )
