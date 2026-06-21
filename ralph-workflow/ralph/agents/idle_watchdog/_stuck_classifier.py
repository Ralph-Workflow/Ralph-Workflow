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
  channel is fresh (a real live-subagent signal from a process
  monitor, can_defer=True) OR classify_quiet returned
  WAITING_ON_CHILD. The agent is loading (starting up or waiting
  on a child); the watchdog must NOT fire. Stale-child signals
  from the corroborator (alive_by in {OS_DESCENDANT_ONLY_STALE_PROGRESS,
  CPU_IDLE_WHILE_ALIVE, LOG_STALE_WHILE_ALIVE} with can_defer=False)
  are NOT counted as fresh — the no-progress / os_descendant_only
  ceilings are the authoritative signal for those.
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
    from ralph.agents.idle_watchdog.corroboration_snapshot import (
        CorroborationSnapshot,
    )

__all__ = ["ClassifyStuckInputs", "StuckKind", "classify_stuck"]


class StuckKind(StrEnum):
    """Why the watchdog thinks the agent is stuck (or is not)."""

    THINKING = "thinking"
    LOADING = "loading"
    WAITING_ON_CONNECTIVITY = "waiting_on_connectivity"
    TRANSITIONING = "transitioning"
    STUCK = "stuck"
    DUPLICATE_KILL = "duplicate_kill"
    # Diagnostic-only kind (NEW in this PR). Returned by
    # ``classify_stuck`` when the subagent channel has evidence
    # (count >= 1) but the most recent subagent_progress is older
    # than ``silent_subagent_seconds`` AND there is no first-party
    # or side-channel activity AND ``classify_quiet`` returned
    # ACTIVE. The watchdog surfaces this label on the
    # ``last_fire_reason`` property for post-mortem diagnostics
    # parallel to ``DEFERRED_BY_STUCK_CLASSIFIER`` so an operator
    # can see WHY a would-be fire was deferred ("a subagent
    # dispatched then went silent for >180s"). SILENT_SUBAGENT
    # is a non-FIRE label and is NOT one of the values that drive
    # a fire reason.
    SILENT_SUBAGENT = "silent_subagent"


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
        corroboration: Optional LIVE ``CorroborationSnapshot`` from the
            watchdog's corroborator. The value is plumbed into the
            classifier so the gate can surface the live corroboration at
            every fire path (the analysis-feedback contract for
            ``CHILDREN_PERSIST_TOO_LONG`` and ``NO_OUTPUT_AT_START``).
            The classifier's CURRENT verdict policy is INTENTIONALLY
            NON-DECISIVE on corroboration alone: the value is accepted
            as a parameter but is not consulted when the classifier
            chooses a ``StuckKind``. The watchdog's own evaluators
            (``_is_no_progress_quiet``, ``_effective_waiting_ceiling``,
            etc.) own the ``alive_by``-driven deferrals; the
            classifier's job is to label the apparent stall, not to
            re-derive a wait/defer verdict from a different snapshot.
            Exposing the parameter keeps the call site stable so future
            classifier extensions (e.g. distinguishing truly-dead-child
            scenarios from process-monitor-only live signals) can use
            it without changing the call site. If a future PR makes
            the classifier verdict depend on ``corroboration.alive_by``,
            it MUST update this docstring AND the
            ``test_corroboration_*`` regression tests in
            ``tests/agents/idle_watchdog/test_stuck_classifier.py`` to
            reflect the new contract.
    """

    is_waiting_state: bool
    connectivity_state: str | None
    evidence_summary: EvidenceSummary
    classify_quiet: _ClassifyQuietCallable
    activity_evidence_ttl_seconds: float | None
    corroboration: CorroborationSnapshot


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
    """Return True when the subagent liveness side-channel is fresh AND defers.

    Side-channel liveness is quality-filtered: the channel is fresh
    only when ``can_defer`` is True. The watchdog's
    ``_subagent_liveness_summary`` sets ``can_defer=True`` when a
    process monitor has confirmed at least one live subagent (a real
    liveness signal) and ``can_defer=False`` otherwise (corroborator
    signals for stale children). This is the alignment that lets
    the classifier's branch 4 distinguish:

      * "live child from process monitor" -- defers the gate so the
        dumb-kill protection applies (the classifier returns
        LOADING via this branch).
      * "stale child from corroborator" -- does NOT defer, so the
        no_progress / os_descendant_only ceilings can fire (the
        classifier returns STUCK via the fall-through).

    A bare PID existence with no alive_by label and ``can_defer=False``
    is NOT fresh.

    The no-progress / os_descendant_only ceilings live in the
    watchdog's own evaluator (``_evaluate_no_progress_quiet`` and
    the OS-descendant-only branch of ``_handle_waiting_branch``).
    Those evaluators run BEFORE the classifier consults the
    subagent_liveness channel, and the classifier now agrees with
    the watchdog's own ``_channel_evidence_active`` policy: only a
    real live-subagent signal (can_defer=True) defers the gate.
    """
    if ttl is None or ttl <= 0.0:
        return False
    for channel in summary.channels:
        if channel.channel_name.value != "subagent_liveness":
            continue
        if channel.age_seconds is None or channel.age_seconds >= ttl:
            continue
        # Subagent liveness is fresh only when the watchdog has
        # explicitly marked the channel as deferrable
        # (can_defer=True). The watchdog's own
        # ``_subagent_liveness_summary`` sets can_defer=True ONLY
        # for the process-monitor live-subagent path; the
        # classifier now matches that policy so the gate defers
        # for real liveness signals but not for the
        # no_progress / os_descendant_only ceiling cases.
        if not channel.can_defer:
            continue
        return True
    return False


def _silent_subagent_path(
    summary: EvidenceSummary,
    silent_subagent_seconds: float,
) -> bool:
    """Return True when a subagent channel has evidence but is stale.

    Specifically: at least one subagent channel (SUBAGENT_OUTPUT or
    SUBAGENT_LIVENESS) has ``counter >= 1`` AND ``last_at`` is older
    than ``silent_subagent_seconds``.  This is the SILENT_SUBAGENT
    diagnostic: a subagent was dispatched, made progress at least
    once, then went silent for >silent_subagent_seconds.

    The function is a pure deterministic helper used only by
    :func:`classify_stuck` and only when ``silent_subagent_seconds > 0``.
    The caller is responsible for the priority ordering (the
    classifier checks this AFTER the LOADING branches so a
    fresh subagent liveness signal still defers).
    """
    if silent_subagent_seconds <= 0:
        return False
    has_subagent_evidence = False
    any_subagent_stale = False
    for channel in summary.channels:
        # ``channel_name`` may be an enum or a string (depending on
        # the call site); normalize to a string for comparison.
        name = (
            channel.channel_name.value
            if hasattr(channel.channel_name, "value")
            else str(channel.channel_name)
        )
        if name not in {"subagent_output", "subagent_liveness"}:
            continue
        if (channel.counter or 0) < 1:
            continue
        has_subagent_evidence = True
        if channel.age_seconds is None or channel.age_seconds >= silent_subagent_seconds:
            any_subagent_stale = True
    return has_subagent_evidence and any_subagent_stale


def classify_stuck(
    *,
    is_waiting_state: bool,
    connectivity_state: str | None,
    evidence_summary: EvidenceSummary,
    classify_quiet: _ClassifyQuietCallable,
    activity_evidence_ttl_seconds: float | None,
    silent_subagent_seconds: float | None = None,
    corroboration: CorroborationSnapshot | None = None,
) -> StuckKind:
    """Classify the apparent stall into one of the seven StuckKind values.

    Pure function: no I/O, no clock reads, no module-level mutable state.
    Returns the same kind for the same inputs on every call.

    Priority order (highest first):
      1. is_waiting_state=True -> DUPLICATE_KILL
      2. connectivity_state=="offline" -> WAITING_ON_CONNECTIVITY
      3. any first-party channel fresh -> THINKING
      4. any side-channel subagent_liveness fresh -> LOADING
      5. classify_quiet is WAITING_ON_CHILD -> LOADING
      6. classify_quiet is RESUMABLE_CONTINUE -> TRANSITIONING
      7. silent_subagent path: subagent channel has evidence but the
         most recent signal is older than ``silent_subagent_seconds``,
         no first-party / side-channel activity, classify_quiet is
         ACTIVE -> SILENT_SUBAGENT
      8. else -> STUCK

    The ``corroboration`` parameter is plumbed for the analysis-feedback
    contract (the gate can surface the live ``CorroborationSnapshot`` to
    the classifier at every fire path) but does NOT participate in the
    priority order. The watchdog's own evaluators own the
    ``alive_by``-driven deferrals; the classifier labels the apparent
    stall, it does not re-derive the wait/defer verdict.
    """
    return _classify_stuck_inner(
        is_waiting_state=is_waiting_state,
        connectivity_state=connectivity_state,
        evidence_summary=evidence_summary,
        classify_quiet=classify_quiet,
        activity_evidence_ttl_seconds=activity_evidence_ttl_seconds,
        silent_subagent_seconds=silent_subagent_seconds,
        corroboration=corroboration,
    )


def _classify_stuck_inner(  # noqa: PLR0911 - one return per StuckKind; the priority order is the contract
    *,
    is_waiting_state: bool,
    connectivity_state: str | None,
    evidence_summary: EvidenceSummary,
    classify_quiet: _ClassifyQuietCallable,
    activity_evidence_ttl_seconds: float | None,
    silent_subagent_seconds: float | None,
    corroboration: CorroborationSnapshot | None,
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
    # The ``corroboration`` parameter is plumbed for the analysis-feedback
    # contract (the watchdog threads the LIVE CorroborationSnapshot into
    # the classifier at every fire path) but is INTENTIONALLY not
    # consulted by the current verdict policy. The watchdog's own
    # evaluators (``_is_no_progress_quiet``, ``_effective_waiting_ceiling``)
    # own the ``alive_by``-driven deferrals; the classifier labels the
    # apparent stall, it does not re-derive the wait/defer verdict. Future
    # classifier extensions can use the parameter without changing the
    # call site. See ``ClassifyStuckInputs.corroboration`` for the full
    # contract and the regression tests in
    # ``tests/agents/idle_watchdog/test_stuck_classifier.py`` that pin it.
    del corroboration
    quiet_state = classify_quiet()
    if quiet_state == AgentExecutionState.WAITING_ON_CHILD:
        return StuckKind.LOADING
    if quiet_state == AgentExecutionState.RESUMABLE_CONTINUE:
        return StuckKind.TRANSITIONING
    # SILENT_SUBAGENT diagnostic (branch 7): a subagent channel has
    # evidence but the most recent signal is older than the
    # silent-subagent threshold. This is a post-mortem label
    # (parallel to DEFERRED_BY_STUCK_CLASSIFIER) that tells the
    # operator "a subagent was dispatched but went silent for
    # >180s".  Checked AFTER the WAITING_ON_CHILD / RESUMABLE_CONTINUE
    # branches so a healthy agent that is legitimately waiting for
    # a child does not get mislabeled as SILENT_SUBAGENT.
    if (
        silent_subagent_seconds is not None
        and silent_subagent_seconds > 0
        and _silent_subagent_path(evidence_summary, silent_subagent_seconds)
    ):
        return StuckKind.SILENT_SUBAGENT
    return StuckKind.STUCK
