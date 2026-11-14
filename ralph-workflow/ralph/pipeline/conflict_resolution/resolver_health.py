from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.recovery.attempt_facts import AttemptFacts

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


def bench_resolver(
    controller: RecoveryController,
    agent: str,
    raw_failure: BaseException | str | None,
    *,
    workspace_changed: bool = False,
    outcome: ConflictAttemptOutcome | None = None,
    unresolved_count: int = 0,
) -> None:
    del outcome, unresolved_count
    exc: BaseException | None = raw_failure if isinstance(raw_failure, BaseException) else None
    facts = AttemptFacts(
        artifact_valid=False,
        workspace_changed=workspace_changed,
    )
    controller.finalize_attempt(
        PHASE_RESOLUTION,
        agent,
        facts=facts,
        attempt_failed=True,
        exc=exc,
    )


def clear_resolver(controller: RecoveryController, agent: str) -> None:
    controller.record_attempt_fact(
        PHASE_RESOLUTION,
        agent,
        artifact_valid=True,
        workspace_changed=False,
    )
    controller.reset_backoff(PHASE_RESOLUTION, agent)
