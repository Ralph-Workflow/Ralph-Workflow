
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.pipeline._integration_action import IntegrationAction
from ralph.pipeline._integration_record_facts import apply_record_facts
from ralph.pipeline.integration_ladder import LadderStepResult, run_ladder_rung
from ralph.pipeline.integration_observation import (
    DirtShape,
    TargetVsFeature,
    WorktreeVerdict,
    classify_worktree,
    read_worktree_porcelain,
)
from ralph.pipeline.integration_owner import (
    RecordFactOutcome,
    decide_integration_verdict,
    ladder_rung_for_record_fact_outcome,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.pipeline.auto_integrate_record import IntegrationRecord
    from ralph.pipeline.integration_actions import Clock
    from ralph.pipeline.integration_attempt_ledger import AttemptLedger
    from ralph.pipeline.integration_decision import CleanupLadderRung
    from ralph.pipeline.integration_ladder import (
        EnvironmentProbe,
        FileQuarantine,
        GitPlumbing,
    )
    from ralph.pipeline.integration_observation import Observation

__all__ = [
    "PlanningSyncVerdict",
    "decide_planning_sync",
    "decide_seam_action",
    "execute_planning_ladder_rung",
]


@dataclass(frozen=True)
class PlanningSyncVerdict:

    action: IntegrationAction
    observation: Observation
    worktree: WorktreeVerdict
    dirt_shape: DirtShape
    in_sync: bool
    may_plan: bool
    reason: str
    ladder_rung: CleanupLadderRung | None = None
    record_fact: RecordFactOutcome | None = None


def decide_seam_action(
    root: Path,
    *,
    record: IntegrationRecord | None = None,
    agent_active: bool = False,
    ledger_path: Path | None = None,
    retained: bool = False,
    target: str | None = None,
    remote: str | None = None,
) -> IntegrationAction | RecordFactOutcome:
    verdict = decide_integration_verdict(
        root,
        record=record,
        agent_active=agent_active,
        ledger_path=ledger_path,
        target=target,
        remote=remote,
    )
    return apply_record_facts(
        verdict.action,
        record,
        retained=retained,
        observation=verdict.observation,
    )


def decide_planning_sync(
    root: Path,
    *,
    record: IntegrationRecord | None = None,
    agent_active: bool = False,
    ledger_path: Path | None = None,
    retained: bool = False,
    target: str | None = None,
    remote: str | None = None,
) -> PlanningSyncVerdict:
    verdict = decide_integration_verdict(
        root,
        record=record,
        agent_active=agent_active,
        ledger_path=ledger_path,
        target=target,
        remote=remote,
    )
    action = apply_record_facts(
        verdict.action,
        record,
        retained=retained,
        observation=verdict.observation,
    )
    worktree = classify_worktree(read_worktree_porcelain(root))
    in_sync = verdict.observation.target_vs_feature in (
        TargetVsFeature.EQUAL,
        TargetVsFeature.AHEAD,
    )
    record_fact: RecordFactOutcome | None = None
    ladder_rung = verdict.ladder_rung
    if isinstance(action, RecordFactOutcome):
        record_fact = action
        ladder_rung = ladder_rung_for_record_fact_outcome(action)
        action = IntegrationAction.CONTINUE_GIT_OPERATION
    may_plan = in_sync and worktree.dispatch_allowed and record_fact is None
    if record_fact is not None:
        reason = (
            f"planning held: durable record facts cannot prove a next step "
            f"({record_fact.value}); preserving work to backup refs and escalating "
            f"through cleanup-ladder rung {ladder_rung_for_record_fact_outcome(record_fact).name.lower()}"
        )
    elif may_plan:
        reason = "planning may proceed: branch is in sync and the worktree gate allows dispatch"
    elif not in_sync:
        reason = (
            "planning held: branch does not contain the target tip "
            f"({verdict.observation.target_vs_feature.value}); integration will run first"
        )
    else:
        reason = f"planning held: {worktree.reason}"
    return PlanningSyncVerdict(
        action=action,
        observation=verdict.observation,
        worktree=worktree,
        dirt_shape=worktree.dirt_shape,
        in_sync=in_sync,
        may_plan=may_plan,
        reason=reason,
        ladder_rung=ladder_rung,
        record_fact=record_fact,
    )


def execute_planning_ladder_rung(
    root: Path,
    verdict: PlanningSyncVerdict,
    *,
    ledger: AttemptLedger,
    target: str | None = None,
    expected_target_sha: str | None = None,
    git: GitPlumbing | None = None,
    fs: FileQuarantine | None = None,
    env: EnvironmentProbe | None = None,
    clock: Clock | None = None,
    state_dir: Path | None = None,
) -> LadderStepResult | None:
    if verdict.ladder_rung is None:
        return None
    return run_ladder_rung(
        verdict.ladder_rung,
        root=root,
        ledger=ledger,
        git=git,
        fs=fs,
        env=env,
        clock=clock,
        state_dir=state_dir,
        target="main" if target is None else target,
        in_progress_kind=verdict.observation.in_progress_kind,
        expected_target_sha=expected_target_sha,
    )
