
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

    THINKING = "thinking"
    LOADING = "loading"
    WAITING_ON_CONNECTIVITY = "waiting_on_connectivity"
    TRANSITIONING = "transitioning"
    STUCK = "stuck"
    DUPLICATE_KILL = "duplicate_kill"
    SILENT_SUBAGENT = "silent_subagent"


@runtime_checkable
class _ClassifyQuietCallable(Protocol):
    def __call__(self) -> AgentExecutionState: ...


class ClassifyStuckInputs(TypedDict, total=False):

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
    if ttl is None or ttl <= 0.0:
        return False
    for channel in summary.channels:
        if channel.channel_name.value != "subagent_liveness":
            continue
        if channel.age_seconds is None or channel.age_seconds >= ttl:
            continue
        if not channel.can_defer:
            continue
        return True
    return False


def _silent_subagent_path(
    summary: EvidenceSummary,
    silent_subagent_seconds: float,
) -> bool:
    if silent_subagent_seconds <= 0:
        return False

    for channel in summary.channels:
        name = (
            channel.channel_name.value
            if hasattr(channel.channel_name, "value")
            else str(channel.channel_name)
        )
        if name != "subagent_liveness":
            continue
        if (channel.counter or 0) >= 1 and channel.alive_by is not None:
            return False

    has_subagent_evidence = False
    any_subagent_stale = False
    for channel in summary.channels:
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
    return _classify_stuck_inner(
        is_waiting_state=is_waiting_state,
        connectivity_state=connectivity_state,
        evidence_summary=evidence_summary,
        classify_quiet=classify_quiet,
        activity_evidence_ttl_seconds=activity_evidence_ttl_seconds,
        silent_subagent_seconds=silent_subagent_seconds,
        corroboration=corroboration,
    )


def _classify_stuck_inner(
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
    del corroboration
    quiet_state = classify_quiet()
    if quiet_state == AgentExecutionState.WAITING_ON_CHILD:
        return StuckKind.LOADING
    if quiet_state == AgentExecutionState.RESUMABLE_CONTINUE:
        return StuckKind.TRANSITIONING
    if (
        silent_subagent_seconds is not None
        and silent_subagent_seconds > 0
        and _silent_subagent_path(evidence_summary, silent_subagent_seconds)
    ):
        return StuckKind.SILENT_SUBAGENT
    return StuckKind.STUCK
