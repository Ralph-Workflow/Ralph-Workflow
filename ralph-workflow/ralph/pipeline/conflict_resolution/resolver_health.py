from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.recovery.agent_failure_ledger import AttemptFact

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from ralph.recovery.conflict_attempt_outcome import ConflictAttemptOutcome
    from ralph.recovery.controller import RecoveryController

__all__ = [
    "ResolverChoice",
    "bench_resolver",
    "choose_resolver",
    "clear_resolver",
]


@dataclass(frozen=True, slots=True)
class ResolverChoice:
    agent: str | None
    wait_ms: int


def choose_resolver(
    controller: RecoveryController,
    candidates: Sequence[str],
    *,
    target_root: Path | None = None,
) -> ResolverChoice:
    if not candidates:
        return ResolverChoice(agent=None, wait_ms=0)
    if target_root is not None:
        from ralph.pipeline._integration_diagnostic_handoff import check_target_worktree_context

        failure_reason = check_target_worktree_context(target_root)
        if failure_reason is not None:
            return ResolverChoice(agent=None, wait_ms=0)
    selection = controller.preferred_agent_index(PHASE_RESOLUTION, candidates)
    if selection.agent is not None:
        return ResolverChoice(agent=selection.agent, wait_ms=0)
    return ResolverChoice(
        agent=None,
        wait_ms=controller.earliest_available_wait_ms(PHASE_RESOLUTION, list(candidates)),
    )


def _record_resolver_fact(
    controller: RecoveryController,
    agent: str,
    *,
    artifact_valid: bool,
    workspace_changed: bool = False,
    free_retry_category: bool = False,
) -> None:
    fact = AttemptFact(
        phase=PHASE_RESOLUTION,
        agent=agent,
        artifact_valid=artifact_valid,
        workspace_changed=workspace_changed,
        free_retry_category=free_retry_category,
    )
    try:
        ledger = controller.ledger
    except AttributeError:
        ledger = None
    if ledger is None:
        logger.error(
            "Cannot record resolver fact: RecoveryController has no ledger (agent='{}', phase='{}')",
            agent,
            PHASE_RESOLUTION,
        )
        raise RuntimeError(
            f"Cannot record resolver fact: RecoveryController has no ledger for agent '{agent}'"
        )
    try:
        outcome = ledger.record(fact)
        if outcome.demoted:
            logger.warning(
                "Agent '{}' demoted in phase '{}' (consecutive={}, total={}, reason={})",
                agent,
                PHASE_RESOLUTION,
                outcome.consecutive_unproductive,
                outcome.total_unproductive,
                outcome.reason,
            )
    except Exception as exc:
        logger.error(
            "Failed to record resolver fact for agent '{}' in phase '{}': {}",
            agent,
            PHASE_RESOLUTION,
            exc,
        )
        raise RuntimeError(f"Failed to record resolver fact for agent '{agent}': {exc}") from exc


def bench_resolver(
    controller: RecoveryController,
    agent: str,
    raw_failure: BaseException | str | None,
    *,
    workspace_changed: bool = False,
    outcome: ConflictAttemptOutcome | None = None,
    unresolved_count: int = 0,
) -> None:
    del raw_failure, outcome, unresolved_count

    _record_resolver_fact(
        controller,
        agent,
        artifact_valid=False,
        workspace_changed=workspace_changed,
        free_retry_category=False,
    )


def clear_resolver(controller: RecoveryController, agent: str) -> None:
    _record_resolver_fact(
        controller,
        agent,
        artifact_valid=True,
    )
    controller.reset_backoff(PHASE_RESOLUTION, agent)
