"""Black-box tests for the pure StuckClassifier that gates the idle watchdog.

The classifier is a pure function: given a snapshot of evidence and waiting
state, it names the WHY of an apparent stall. The watchdog consults the
classifier before every non-absolute fire; the gate returns CONTINUE for any
non-STUCK kind, which stops the dumb-kill pattern where the watchdog fires
during legitimate work.

These tests use FakeClock so they complete in <2s combined. They do NOT call
time.sleep, do NOT start real subprocesses, and do NOT touch the network.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog._evidence_tier import (
    CHANNEL_DEFERS_BY_DEFAULT,
    ChannelEvidenceSummary,
    ChannelName,
    EvidenceSummary,
    EvidenceTier,
)
from ralph.agents.idle_watchdog._stuck_classifier import (
    ClassifyStuckInputs,
    StuckKind,
    classify_stuck,
)
from ralph.agents.idle_watchdog.corroboration_snapshot import CorroborationSnapshot
from ralph.process.child_liveness import AliveBy

_TTL_SECONDS = 30.0
_NOW = 1000.0


def _summary_with_channel(
    *,
    channel: ChannelName,
    last_at: float | None,
    can_defer: bool = True,
) -> EvidenceSummary:
    """Build a one-channel evidence summary for a single first-party channel."""
    if last_at is None:
        age: float | None = None
    else:
        age = max(0.0, _NOW - last_at)
    counter = 1 if last_at is not None else None
    return EvidenceSummary(
        channels=(
            ChannelEvidenceSummary(
                channel_name=channel,
                tier=(
                    (CHANNEL_DEFERS_BY_DEFAULT[channel] and EvidenceTier.FIRST_PARTY)
                    or EvidenceTier.SIDE_CHANNEL
                ),
                last_at=last_at,
                age_seconds=age,
                counter=counter,
                can_defer=can_defer,
            ),
        )
    )


def _multi_summary(
    *,
    subagent_output_at: float | None = None,
    subagent_liveness_at: float | None = None,
    alive_by: AliveBy | None = None,
) -> EvidenceSummary:
    """Build a full 5-channel summary with controlled timestamps."""
    channels: list[ChannelEvidenceSummary] = []
    # STDOUT - always stale in the test cases
    channels.append(
        ChannelEvidenceSummary(
            channel_name=ChannelName.STDOUT,
            tier=EvidenceTier.FIRST_PARTY,
            last_at=None,
            age_seconds=None,
            counter=None,
            can_defer=False,
        )
    )
    # MCP_TOOL
    channels.append(
        ChannelEvidenceSummary(
            channel_name=ChannelName.MCP_TOOL,
            tier=EvidenceTier.FIRST_PARTY,
            last_at=None,
            age_seconds=None,
            counter=None,
            can_defer=True,
        )
    )
    # SUBAGENT_OUTPUT
    sub_out_age = None if subagent_output_at is None else max(0.0, _NOW - subagent_output_at)
    channels.append(
        ChannelEvidenceSummary(
            channel_name=ChannelName.SUBAGENT_OUTPUT,
            tier=EvidenceTier.FIRST_PARTY,
            last_at=subagent_output_at,
            age_seconds=sub_out_age,
            counter=1 if subagent_output_at is not None else None,
            can_defer=True,
        )
    )
    # SUBAGENT_LIVENESS
    sub_liv_age = None if subagent_liveness_at is None else max(0.0, _NOW - subagent_liveness_at)
    # The classifier requires can_defer=True for the subagent_liveness
    # channel to count as fresh. The watchdog's _subagent_liveness_summary
    # sets can_defer=True only for process-monitor live-subagent signals;
    # the test helper exercises the classifier contract directly and
    # therefore sets can_defer=True when the liveness timestamp is set.
    sub_liv_can_defer = subagent_liveness_at is not None
    channels.append(
        ChannelEvidenceSummary(
            channel_name=ChannelName.SUBAGENT_LIVENESS,
            tier=EvidenceTier.SIDE_CHANNEL,
            last_at=subagent_liveness_at,
            age_seconds=sub_liv_age,
            counter=1 if subagent_liveness_at is not None else None,
            alive_by=alive_by,
            can_defer=sub_liv_can_defer,
        )
    )
    # WORKSPACE
    channels.append(
        ChannelEvidenceSummary(
            channel_name=ChannelName.WORKSPACE,
            tier=EvidenceTier.SIDE_CHANNEL,
            last_at=None,
            age_seconds=None,
            counter=None,
            can_defer=False,
        )
    )
    return EvidenceSummary(channels=tuple(channels))


@dataclass
class _ClassifyQuietStub:
    state: AgentExecutionState = AgentExecutionState.ACTIVE

    def __call__(self) -> AgentExecutionState:
        return self.state


def _inputs(
    *,
    is_waiting_state: bool = False,
    connectivity_state: str | None = "online",
    evidence_summary: EvidenceSummary | None = None,
    classify_quiet_state: AgentExecutionState = AgentExecutionState.ACTIVE,
) -> ClassifyStuckInputs:
    return cast(
        "ClassifyStuckInputs",
        {
            "is_waiting_state": is_waiting_state,
            "connectivity_state": connectivity_state,
            "evidence_summary": evidence_summary or _multi_summary(),
            "classify_quiet": _ClassifyQuietStub(state=classify_quiet_state),
            "activity_evidence_ttl_seconds": _TTL_SECONDS,
        },
    )


def test_is_waiting_state_true_returns_duplicate_kill() -> None:
    """A duplicate FIRE during a wait state must never be produced.

    is_waiting_state=True is the strongest signal: the pipeline has already
    decided to wait, so the watchdog must defer to the run-loop's wait
    semantics and return DUPLICATE_KILL regardless of any first-party
    evidence.
    """
    kind = classify_stuck(**_inputs(is_waiting_state=True))
    assert kind == StuckKind.DUPLICATE_KILL


def test_offline_connectivity_returns_waiting_on_connectivity() -> None:
    """Offline connectivity -> WAITING_ON_CONNECTIVITY.

    The pipeline already has a ConnectivityMonitor that pauses/resumes on
    network loss; the watchdog must NOT fire while connectivity is offline
    because the agent may simply be unable to reach its transport.
    """
    kind = classify_stuck(**_inputs(connectivity_state="offline"))
    assert kind == StuckKind.WAITING_ON_CONNECTIVITY


def test_fresh_subagent_output_returns_thinking() -> None:
    """A fresh subagent_output channel implies the agent is THINKING.

    subagent_output is first-party evidence: a subagent that just wrote a
    line is doing real work, not wedged. The agent is in the "thinking"
    phase of producing output.
    """
    summary = _multi_summary(subagent_output_at=_NOW - 5.0)
    kind = classify_stuck(**_inputs(evidence_summary=summary))
    assert kind == StuckKind.THINKING


def test_fresh_subagent_liveness_without_first_party_returns_loading() -> None:
    """Subagent liveness fresh but no first-party channels -> LOADING.

    A live subagent with no captured output is in the LOADING phase: it
    exists, it is alive, but the watchdog has no first-party evidence yet.
    This is the case during the first 30s of a subagent's lifetime, when
    it is starting up but has not yet produced a line.
    """
    summary = _multi_summary(
        subagent_liveness_at=_NOW - 5.0,
        alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
    )
    kind = classify_stuck(**_inputs(evidence_summary=summary))
    assert kind == StuckKind.LOADING


def test_os_descendant_alive_no_fresh_channels_returns_loading() -> None:
    """alive_by=OS_DESCENDANT_ONLY_STALE_PROGRESS + WAITING_ON_CHILD + no
    fresh channels -> LOADING. The agent is loading (i.e. waiting for
    a subprocess to make progress), not STUCK.
    """
    summary = _multi_summary(alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS)
    kind = classify_stuck(
        **_inputs(
            evidence_summary=summary,
            classify_quiet_state=AgentExecutionState.WAITING_ON_CHILD,
        )
    )
    assert kind == StuckKind.LOADING


def test_resumable_continue_returns_transitioning() -> None:
    """classify_quiet returns RESUMABLE_CONTINUE -> TRANSITIONING.

    A session reset or resumable exit is a transition state, not a stuck
    state. The watchdog must defer the verdict and let the run-loop
    handle the session transition.
    """
    kind = classify_stuck(
        **_inputs(classify_quiet_state=AgentExecutionState.RESUMABLE_CONTINUE)
    )
    assert kind == StuckKind.TRANSITIONING


def test_no_channels_active_returns_stuck() -> None:
    """All channels stale, no waiting state, classify_quiet=ACTIVE -> STUCK.

    The agent looks quiet with no first-party evidence and no live
    subagent. This is the only kind where the watchdog is permitted to
    fire.
    """
    kind = classify_stuck(**_inputs())
    assert kind == StuckKind.STUCK


def test_classify_stuck_is_pure() -> None:
    """classify_stuck must be a pure function of its inputs.

    Calling it twice with the same inputs must return the same kind. No
    hidden state, no I/O, no clock reads.
    """
    inputs = _inputs(connectivity_state="offline")
    kind1 = classify_stuck(**inputs)
    kind2 = classify_stuck(**inputs)
    assert kind1 == kind2
    assert kind1 == StuckKind.WAITING_ON_CONNECTIVITY


def test_priority_order_waiting_beats_offline() -> None:
    """When multiple signals are present, is_waiting_state wins first.

    is_waiting_state=True is the strongest signal because it means the
    pipeline has already committed to a wait. Connectivity offline is
    secondary: the pipeline may be on a wait cycle that pre-dates the
    connectivity state change.
    """
    kind = classify_stuck(
        **_inputs(is_waiting_state=True, connectivity_state="offline")
    )
    assert kind == StuckKind.DUPLICATE_KILL


def test_priority_order_offline_beats_thinking() -> None:
    """Offline connectivity beats fresh first-party channels.

    If the agent produced a fragment but the transport is offline, the
    watchdog should classify as WAITING_ON_CONNECTIVITY (the network is
    the problem, not the agent). A fresh first-party channel is evidence
    of work but cannot override the transport-level outage.
    """
    summary = _multi_summary(subagent_output_at=_NOW - 5.0)
    kind = classify_stuck(
        **_inputs(connectivity_state="offline", evidence_summary=summary)
    )
    assert kind == StuckKind.WAITING_ON_CONNECTIVITY


# ---------------------------------------------------------------------------
# Corroboration non-decisiveness contract
# ---------------------------------------------------------------------------
#
# The ``corroboration`` parameter is plumbed through ``classify_stuck`` so the
# watchdog can surface the live ``CorroborationSnapshot`` to the classifier at
# every fire path. The classifier's CURRENT verdict policy is intentionally
# NON-DECISIVE on corroboration alone: the value is accepted as a parameter
# but is not consulted when the classifier chooses a ``StuckKind``. The
# watchdog's own evaluators (``_is_no_progress_quiet``,
# ``_effective_waiting_ceiling``, etc.) own the ``alive_by``-driven
# deferrals; the classifier's job is to label the apparent stall, not to
# re-derive a wait/defer verdict from a different snapshot.
#
# The reason for this design: keeping the classifier verdict policy stable
# means future classifier extensions (e.g. distinguishing truly-dead-child
# scenarios from process-monitor-only live signals) can use the corroboration
# parameter without breaking the existing fire decision. The call site stays
# the same; only the classifier's internal branches change.
#
# These tests pin the contract: a non-None ``alive_by`` in the corroboration
# MUST NOT change the verdict returned by ``classify_stuck``. If a future
# PR adds corroboration-based decision logic to the classifier, these tests
# must be updated to reflect the new contract AND the module docstring +
# ``ClassifyStuckInputs.corroboration`` documentation must be aligned with
# the new behavior. Adding decision logic without updating the tests and
# docs is a regression of the analysis-feedback contract for AC-05.
# ---------------------------------------------------------------------------


def test_corroboration_is_plumbed_but_does_not_change_stuck_verdict() -> None:
    """A live corroboration does NOT change a STUCK verdict.

    All channels are stale, is_waiting_state=False, connectivity=online,
    classify_quiet=ACTIVE -> the verdict is STUCK. The corroboration
    parameter is plumbed but does not change the verdict: both
    corroboration=None and corroboration with alive_by=FRESH_PROGRESS
    return StuckKind.STUCK.
    """
    inputs = _inputs()

    kind_no_corr = classify_stuck(**inputs)
    kind_live_corr = classify_stuck(
        **inputs,
        corroboration=CorroborationSnapshot(
            alive_by=AliveBy.FRESH_PROGRESS,
            scoped_child_active=True,
            scoped_child_count=1,
        ),
    )
    kind_stale_corr = classify_stuck(
        **inputs,
        corroboration=CorroborationSnapshot(
            alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
        ),
    )
    kind_dead_corr = classify_stuck(
        **inputs,
        corroboration=CorroborationSnapshot(alive_by=None),
    )

    assert kind_no_corr == StuckKind.STUCK
    assert kind_live_corr == StuckKind.STUCK
    assert kind_stale_corr == StuckKind.STUCK
    assert kind_dead_corr == StuckKind.STUCK


def test_corroboration_does_not_change_thinking_verdict() -> None:
    """A live corroboration does NOT change a THINKING verdict.

    A fresh subagent_output channel implies THINKING. The corroboration
    parameter is plumbed but does not change the verdict: both
    corroboration=None and corroboration with alive_by=FRESH_PROGRESS
    return StuckKind.THINKING.
    """
    summary = _multi_summary(subagent_output_at=_NOW - 5.0)
    inputs = _inputs(evidence_summary=summary)

    kind_no_corr = classify_stuck(**inputs)
    kind_live_corr = classify_stuck(
        **inputs,
        corroboration=CorroborationSnapshot(
            alive_by=AliveBy.FRESH_PROGRESS,
            scoped_child_active=True,
        ),
    )

    assert kind_no_corr == StuckKind.THINKING
    assert kind_live_corr == StuckKind.THINKING


def test_corroboration_does_not_change_loading_verdict_via_subagent_liveness() -> None:
    """LOADING via subagent_liveness is unchanged by corroboration alive_by.

    The fresh subagent_liveness channel implies LOADING. The
    corroboration parameter is plumbed but does not change the verdict:
    both corroboration=None and corroboration with alive_by=FRESH_PROGRESS
    return StuckKind.LOADING. This is the path the watchdog uses to defer
    dumb kills when a process monitor reports a live subagent.
    """
    summary = _multi_summary(
        subagent_liveness_at=_NOW - 5.0,
        alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
    )
    inputs = _inputs(evidence_summary=summary)

    kind_no_corr = classify_stuck(**inputs)
    kind_live_corr = classify_stuck(
        **inputs,
        corroboration=CorroborationSnapshot(
            alive_by=AliveBy.FRESH_PROGRESS,
            scoped_child_active=True,
        ),
    )

    assert kind_no_corr == StuckKind.LOADING
    assert kind_live_corr == StuckKind.LOADING


def test_corroboration_does_not_change_offline_verdict() -> None:
    """WAITING_ON_CONNECTIVITY beats corroboration alive_by.

    Even with a live corroboration (alive_by=FRESH_PROGRESS), the offline
    connectivity state still wins: the classifier returns
    WAITING_ON_CONNECTIVITY. The corroboration does not change the
    verdict; the network state is the problem, not the agent.
    """
    inputs = _inputs(connectivity_state="offline")

    kind_no_corr = classify_stuck(**inputs)
    kind_live_corr = classify_stuck(
        **inputs,
        corroboration=CorroborationSnapshot(
            alive_by=AliveBy.FRESH_PROGRESS,
            scoped_child_active=True,
        ),
    )

    assert kind_no_corr == StuckKind.WAITING_ON_CONNECTIVITY
    assert kind_live_corr == StuckKind.WAITING_ON_CONNECTIVITY


def test_corroboration_does_not_change_duplicate_kill_verdict() -> None:
    """DUPLICATE_KILL is the strongest signal and is not changed by corroboration.

    is_waiting_state=True wins first. The corroboration does not change
    the verdict: both corroboration=None and corroboration with
    alive_by=FRESH_PROGRESS return StuckKind.DUPLICATE_KILL.
    """
    inputs = _inputs(is_waiting_state=True)

    kind_no_corr = classify_stuck(**inputs)
    kind_live_corr = classify_stuck(
        **inputs,
        corroboration=CorroborationSnapshot(
            alive_by=AliveBy.FRESH_PROGRESS,
            scoped_child_active=True,
        ),
    )

    assert kind_no_corr == StuckKind.DUPLICATE_KILL
    assert kind_live_corr == StuckKind.DUPLICATE_KILL
