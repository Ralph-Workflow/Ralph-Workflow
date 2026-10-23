"""Run-scoped health ledger for integration resolver agents.

Every path that invokes a resolver (conflict driver, retained-integration
diagnostic, pending-merge repair) asks :func:`choose_resolver` for the next
candidate and reports the outcome through :func:`bench_resolver` /
:func:`clear_resolver`. They share one ``RecoveryController`` -- and so one
unavailability store -- so a resolver that failed on one path is not offered
by another.

The ledger is keyed by agent name, not by phase: an agent benched here is also
unavailable to every other phase of the run (same account, same quota), and a
verified success elsewhere clears it here.

Benching is failure-agnostic: any attempt that ends without a verified result
benches the agent with exponential backoff. The classifier only picks a longer
cooldown when it recognises the failure (quota, auth, ...); an unrecognised
message is never a reason to call the same agent again immediately.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.recovery.unavailability_reason import UnavailabilityReason

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ralph.recovery.controller import RecoveryController

__all__ = ["ResolverChoice", "bench_resolver", "choose_resolver", "clear_resolver"]


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


def bench_resolver(
    controller: RecoveryController,
    agent: str,
    raw_failure: BaseException | str,
) -> None:
    """Bench ``agent`` after an attempt that produced no verified result."""
    # A resolver that printed a quota message reached its provider, so the
    # classifier is told the network is up; without that it never names quota.
    classified = controller.classify_conflict_attempt(
        raw_failure, agent=agent, connectivity_state="online"
    )
    reason = classified.unavailability_reason or UnavailabilityReason.PROCESS_EXITED
    controller.unavailability_store.mark_unavailable(PHASE_RESOLUTION, agent, reason)


def clear_resolver(controller: RecoveryController, agent: str) -> None:
    """Forget ``agent``'s failure history after a verified result."""
    controller.reset_backoff(PHASE_RESOLUTION, agent)
