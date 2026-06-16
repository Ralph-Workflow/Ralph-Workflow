"""Pure StuckClassifier that names the WHY of an apparent agent stall.

The classifier is a deterministic, side-effect-free function of its inputs.
It has no clock reads, no I/O, and no module-level mutable state. The
watchdog's evaluate() hook calls it before every non-absolute fire; the gate
returns CONTINUE for any non-STUCK kind so a productive session that does
not look productive is not killed.

Six kinds are exposed:

* THINKING — at least one first-party channel (mcp_tool or subagent_output)
  is fresher than ``activity_evidence_ttl_seconds``. The agent is actively
  producing output; the watchdog must NOT fire.
* LOADING — no first-party channels fresh, but the subagent liveness
  channel is fresh OR classify_quiet returned WAITING_ON_CHILD with
  alive_by in a non-progress state. The agent is loading (starting up or
  waiting on a child); the watchdog must NOT fire.
* WAITING_ON_CONNECTIVITY — the connectivity monitor reports OFFLINE. The
  pipeline already has auto-resume semantics; the watchdog must NOT fire.
* TRANSITIONING — classify_quiet returned RESUMABLE_CONTINUE. The session
  is being reset or resumed; the watchdog must NOT fire.
* DUPLICATE_KILL — state.is_waiting_state is True. The pipeline has already
  committed to a wait; the watchdog must NEVER fire a second time during
  the wait. This is the strongest signal and is checked first.
* STUCK — none of the above. The agent is genuinely quiet with no
  evidence of work. This is the ONLY kind that allows the watchdog to
  fire.

Pure-function property: classify_stuck returns the same kind for the same
inputs, on every call, with no I/O and no clock reads. The watchdog
already owns its own clock; the classifier does not read it.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, TypedDict, runtime_checkable

from ralph.agents.execution_state import AgentExecutionState

if TYPE_CHECKING:
    from ralph.agents.idle_watchdog._evidence_tier import EvidenceSummary

__all__ = ["ClassifyStuckInputs", "StuckKind", "classify_stuck"]


class StuckKind(StrEnum):
    """Why the watchdog thinks the agent is stuck (or is not)."""

    THINKING = "thinking"
    LOADING = "loading"
    WAITING_ON_CONNECTIVITY = "waiting_on_connectivity"
    TRANSITIONING = "transitioning"
    STUCK = "stuck"
    DUPLICATE_KILL = "duplicate_kill"


@runtime_checkable
class _ClassifyQuietCallable(Protocol):
    def __call__(self) -> AgentExecutionState: ...


class ClassifyStuckInputs(TypedDict, total=False):
    """Inputs to the StuckClassifier.

    The TypedDict is untyped on ``classify_quiet`` because the watchdog
    already enforces the protocol at construction time; the classifier
    treats it as a zero-arg callable returning ``AgentExecutionState``.

    Attributes:
        is_waiting_state: True when the pipeline has already entered a wait
            state (the run loop will sleep and re-enter the phase). The
            classifier must NEVER produce STUCK during a wait state; a
            second FIRE during a wait is a "duplicate kill" and is the
            strongest deferral signal.
        connectivity_state: String label of the current connectivity state
            (one of "online", "offline", "unknown", "degraded"). When
            "offline" the classifier returns WAITING_ON_CONNECTIVITY and
            defers. None and "online" do not defer.
        evidence_summary: Per-channel evidence snapshot from
            ``IdleWatchdog.last_evidence_summary(now)``. The classifier
            consults only the per-channel last_at timestamps and the
            ``can_defer`` flag; it does not consult the channel counter
            or kind_breakdown.
        classify_quiet: Zero-arg callable that returns the current
            ``AgentExecutionState``. The classifier consults it ONLY to
            detect WAITING_ON_CHILD + alive_by non-progress (LOADING) and
            RESUMABLE_CONTINUE (TRANSITIONING). It is a callable, not a
            state, so the watchdog can pass its existing
            ``self._classify_quiet`` strategy.
        activity_evidence_ttl_seconds: TTL for first-party and side-channel
            freshness. Must be > 0. None is treated as 0 (no freshness
            deferral).
    """

    is_waiting_state: bool
    connectivity_state: str | None
    evidence_summary: EvidenceSummary
    classify_quiet: _ClassifyQuietCallable
    activity_evidence_ttl_seconds: float | None


def _first_party_fresh(
    summary: EvidenceSummary,
    ttl: float | None,
) -> bool:
    """Return True when at least one first-party channel is fresher than ttl.

    The first-party channels are MCP_TOOL and SUBAGENT_OUTPUT. STDOUT is
    first-party but is the channel the watchdog is trying to judge; it
    never defers its own idle deadline (the channel summary marks
    ``can_defer=False`` for STDOUT).
    """
    if ttl is None or ttl <= 0.0:
        return False
    for channel in summary.channels:
        if channel.tier.value != "first_party":
            continue
        if not channel.can_defer:
            continue
        if channel.age_seconds is not None and channel.age_seconds < ttl:
            return True
    return False


def _subagent_liveness_fresh(
    summary: EvidenceSummary,
    ttl: float | None,
) -> bool:
    """Return True when the subagent liveness side-channel is fresh.

    Side-channel liveness is quality-filtered: the channel is fresh only
    when alive_by is set to a non-progress state (the watchdog records
    alive_by in the summary so the classifier does not need to re-derive
    it). A bare PID existence with no progress signals is NOT fresh.
    """
    if ttl is None or ttl <= 0.0:
        return False
    for channel in summary.channels:
        if channel.channel_name.value != "subagent_liveness":
            continue
        if channel.age_seconds is None or channel.age_seconds >= ttl:
            continue
        # Subagent liveness is only fresh when the channel carries an
        # alive_by label (i.e. the watchdog recorded a non-progress
        # state). Without alive_by, the channel is just bare PID
        # existence and is not evidence of work.
        if channel.alive_by is None:
            continue
        return True
    return False


def classify_stuck(
    *,
    is_waiting_state: bool,
    connectivity_state: str | None,
    evidence_summary: EvidenceSummary,
    classify_quiet: _ClassifyQuietCallable,
    activity_evidence_ttl_seconds: float | None,
) -> StuckKind:
    """Classify the apparent stall into one of the six StuckKind values.

    Pure function: no I/O, no clock reads, no module-level mutable state.
    Returns the same kind for the same inputs on every call.

    Priority order (highest first):
      1. is_waiting_state=True -> DUPLICATE_KILL
      2. connectivity_state=="offline" -> WAITING_ON_CONNECTIVITY
      3. any first-party channel fresh -> THINKING
      4. any side-channel subagent_liveness fresh -> LOADING
      5. classify_quiet is WAITING_ON_CHILD with alive_by in a
         non-progress state (recorded in the liveness channel) -> LOADING
      6. classify_quiet is RESUMABLE_CONTINUE -> TRANSITIONING
      7. else -> STUCK
    """
    return _classify_stuck_inner(
        is_waiting_state=is_waiting_state,
        connectivity_state=connectivity_state,
        evidence_summary=evidence_summary,
        classify_quiet=classify_quiet,
        activity_evidence_ttl_seconds=activity_evidence_ttl_seconds,
    )


def _classify_stuck_inner(  # noqa: PLR0911 - one return per StuckKind; the priority order is the contract
    *,
    is_waiting_state: bool,
    connectivity_state: str | None,
    evidence_summary: EvidenceSummary,
    classify_quiet: _ClassifyQuietCallable,
    activity_evidence_ttl_seconds: float | None,
) -> StuckKind:
    if is_waiting_state:
        return StuckKind.DUPLICATE_KILL
    if connectivity_state == "offline":
        return StuckKind.WAITING_ON_CONNECTIVITY
    ttl = activity_evidence_ttl_seconds
    if _first_party_fresh(evidence_summary, ttl):
        return StuckKind.THINKING
    if _subagent_liveness_fresh(evidence_summary, ttl):
        return StuckKind.LOADING
    quiet_state = classify_quiet()
    if quiet_state == AgentExecutionState.WAITING_ON_CHILD:
        return StuckKind.LOADING
    if quiet_state == AgentExecutionState.RESUMABLE_CONTINUE:
        return StuckKind.TRANSITIONING
    return StuckKind.STUCK
