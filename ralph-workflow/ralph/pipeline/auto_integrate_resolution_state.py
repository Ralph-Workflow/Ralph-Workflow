from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from ralph.pipeline._integration_action import RebaseOutcomeKind
from ralph.pipeline.auto_integrate_record import apply_containment_release
from ralph.pipeline.integration_resolution import persisted_integration_resolution_verdict
from ralph.pipeline.rebase_state import RebaseState

if TYPE_CHECKING:
    from pathlib import Path

CONFLICT_RESOLUTION_STRATEGIES = (
    "rebase_resolver",
    "refresh_retry",
    "merge_instead",
    "resolver_with_history",
)

__all__ = [
    "CONFLICT_RESOLUTION_STRATEGIES",
    "ContainmentVerdict",
    "contained_land",
    "containment_release",
    "evaluate_integration_containment",
    "preserve_unresolved_resolution_state",
    "reconcile_stale_unresolved_state",
    "retains_unresolved_resolution_state",
]


@dataclass(frozen=True, slots=True)
class ContainmentVerdict:
    contained: bool
    target: str | None
    feature_sha: str | None
    target_sha: str | None
    evidence: str

    @property
    def reason(self) -> str:
        answer = "contains" if self.contained else "does not contain"
        return f"'{self.target}' {answer} feature tip {self.feature_sha} ({self.evidence})"


def evaluate_integration_containment(
    root: Path | None,
    target: str | None,
    *,
    feature_sha: str | None = None,
    proven: bool = False,
) -> ContainmentVerdict:
    target_name = target or ""
    if root is None or not target_name:
        return ContainmentVerdict(
            proven,
            target_name,
            feature_sha,
            None,
            "caller proved the work already landed" if proven else "no repository named",
        )
    from ralph.git.merge import MERGE_STATE_IN_PROGRESS, branch_sha, merge_state
    from ralph.git.merge_obstructions import ancestry_state
    from ralph.git.operations import get_head_sha
    from ralph.git.rebase.rebase import rebase_in_progress

    try:
        if rebase_in_progress(root) or merge_state(root) == MERGE_STATE_IN_PROGRESS:
            return ContainmentVerdict(
                False, target_name, feature_sha, None, "integration operation in flight"
            )
        head = feature_sha or get_head_sha(root)
        target_sha = branch_sha(root, target_name)
        answer = ancestry_state(root, head, target_sha or target_name)
    except Exception as exc:
        logger.warning("auto_integrate: containment query on '{}' failed: {}", target_name, exc)
        return ContainmentVerdict(
            False, target_name, feature_sha, None, f"git could not answer ancestry: {exc}"
        )
    return ContainmentVerdict(
        answer is True,
        target_name,
        head,
        target_sha,
        "git merge-base --is-ancestor" if answer is not None else "git could not answer ancestry",
    )


def containment_release(
    root: Path | None,
    target: str | None,
    state: RebaseState,
    *,
    proven: bool = False,
) -> RebaseState:
    return apply_containment_release(
        state, root, evaluate_integration_containment(root, target, proven=proven)
    )


def contained_land(root: Path, target: str, *, require_in_sync: bool = False) -> RebaseState | None:
    verdict = evaluate_integration_containment(root, target)
    if not verdict.contained:
        return None
    if require_in_sync:
        from ralph.git.branch_relation import branch_relation

        if branch_relation(root, target).needs_catch_up:
            return None
    logger.info("auto_integrate: {}", verdict.reason)
    return apply_containment_release(
        RebaseState(
            last_outcome=RebaseOutcomeKind.FAST_FORWARDED,
            last_target=target,
            fast_forwarded=True,
        ),
        root,
        verdict,
    )


def reconcile_stale_unresolved_state(
    state: RebaseState,
    *,
    root: Path | None = None,
    target: str | None = None,
) -> RebaseState:
    return containment_release(root, target, state, proven=True)


def retains_unresolved_resolution_state(state: RebaseState) -> bool:
    return persisted_integration_resolution_verdict(state) is not None


def preserve_unresolved_resolution_state(
    outcome: RebaseState | None,
    *,
    prior: RebaseState,
) -> RebaseState | None:
    if outcome is None:
        return prior if retains_unresolved_resolution_state(prior) else None
    if outcome.fast_forwarded:
        return containment_release(None, outcome.last_target, outcome, proven=True)
    if not retains_unresolved_resolution_state(prior):
        return outcome

    update: dict[str, object] = {}
    if prior.integration_retry_attempt != outcome.integration_retry_attempt:
        update["integration_retry_attempt"] = prior.integration_retry_attempt
    if prior.resolution_exhausted and not outcome.resolution_exhausted:
        update["resolution_exhausted"] = True
        update["resolution_exhaustion_reason"] = prior.resolution_exhaustion_reason
    if prior.integration_unresolved and not outcome.integration_unresolved:
        update["unresolved_integration_carried"] = True
    if not update:
        return outcome
    return outcome.model_copy(update=update)
