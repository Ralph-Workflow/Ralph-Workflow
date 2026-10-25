"""Run-scoped health ledger for integration resolver agents.

Every path that invokes a resolver (conflict driver, retained-integration
diagnostic, pending-merge repair) asks :func:`choose_resolver` for the next
candidate and reports the outcome through :func:`bench_resolver` /
:func:`clear_resolver`. They share the run's ``AgentFailureLedger`` via the
``RecoveryController`` public seam, ensuring resolution attempt facts are
consistently tracked and that an agent demoted or cooling down in
resolution is skipped by any resolver caller.

Health is strictly partitioned by phase identity (``PHASE_RESOLUTION``).
Benching, cooldown, or demoting an agent in integration conflict resolution
does not contaminate other phases such as ``development`` or ``analysis``,
maintaining per-phase isolation.

Unproductive attempts without a verified result record an attempt fact in the
ledger. When consecutive unproductive attempts reach the configured limit,
the agent enters cooldown with exponential backoff. A verified resolution
clears the agent's failure history for the resolution phase.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.recovery.agent_failure_ledger import AttemptFact
from ralph.recovery.failure_category import FailureCategory

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ralph.recovery.controller import RecoveryController

__all__ = [
    "ResolverChoice",
    "bench_resolver",
    "choose_resolver",
    "clear_resolver",
]


@dataclass(frozen=True, slots=True)
class ResolverChoice:
    """The next resolver to invoke, or how long until one is selectable."""

    agent: str | None
    wait_ms: int


def choose_resolver(controller: RecoveryController, candidates: Sequence[str]) -> ResolverChoice:
    """Return the highest-priority candidate that is not cooling down.

    When every candidate is benched, ``agent`` is ``None`` and ``wait_ms`` is
    the time until the earliest one returns; callers wait that long instead of
    invoking a benched agent.
    """
    if not candidates:
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
    """Route an attempt fact to the failure ledger via public seam."""
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
        ledger.record(fact)
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
    raw_failure: BaseException | str,
    *,
    workspace_changed: bool = False,
) -> None:
    """Record an unproductive resolution attempt for ``agent`` in the resolution phase."""
    is_free_retry = False
    with suppress(Exception):
        classified = controller.classify_conflict_attempt(
            raw_failure, agent=agent, connectivity_state="online"
        )
        is_free_retry = classified.category in (
            FailureCategory.ENVIRONMENTAL,
            FailureCategory.ARTIFACT_VALIDATION,
            FailureCategory.AMBIGUOUS,
        )

    _record_resolver_fact(
        controller,
        agent,
        artifact_valid=False,
        workspace_changed=workspace_changed,
        free_retry_category=is_free_retry,
    )


def clear_resolver(controller: RecoveryController, agent: str) -> None:
    """Forget ``agent``'s failure history in the resolution phase after a verified result."""
    _record_resolver_fact(
        controller,
        agent,
        artifact_valid=True,
    )
    with suppress(AttributeError, Exception):
        controller.reset_backoff(PHASE_RESOLUTION, agent)
