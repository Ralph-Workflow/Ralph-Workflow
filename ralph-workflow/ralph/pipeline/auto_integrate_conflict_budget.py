from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ralph.pipeline._integration_action import RebaseOutcomeKind
from ralph.pipeline.auto_integrate_backoff import MAX_RETRY_DEADLINE_HORIZON_SECONDS

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ralph.pipeline.rebase_state import RebaseState
    from ralph.recovery.agent_failure_ledger import AgentFailureLedger

MAX_TRACKED_CONFLICT_ATTEMPTS = 64

__all__ = [
    "MAX_TRACKED_CONFLICT_ATTEMPTS",
    "ConflictIdentity",
    "apply_conflict_budget",
    "finish_conflict_attempt",
    "prior_conflict_count",
    "resolve_failure_ledger",
    "resolver_allowed",
    "start_conflict_attempt",
]


@dataclass(frozen=True, slots=True)
class ConflictIdentity:
    feature_sha: str | None = None
    target_sha: str | None = None
    conflicted_paths: tuple[str, ...] = ()
    stage_oids: tuple[str, ...] = ()
    scope: str = "feature"

    def matches(self, state: RebaseState) -> bool:
        recorded_scope = state.last_conflict_scope
        if self.scope and recorded_scope and self.scope != recorded_scope:
            return False
        if (
            self.conflicted_paths
            and state.last_conflict_paths
            and self.conflicted_paths != state.last_conflict_paths
        ):
            return False
        if (
            self.stage_oids
            and state.last_conflict_stage_oids
            and self.stage_oids != state.last_conflict_stage_oids
        ):
            return False
        recorded = (state.last_conflict_feature_sha, state.last_conflict_target_sha)
        observed = (self.feature_sha, self.target_sha)
        if None in recorded or None in observed:
            return True
        return recorded == observed


_UNKNOWN_IDENTITY = ConflictIdentity()


def prior_conflict_count(
    state: RebaseState,
    target: str,
    identity: ConflictIdentity = _UNKNOWN_IDENTITY,
    *,
    attempts: int | None = None,
) -> int:
    if state.last_target != target:
        return 0
    if not identity.matches(state):
        return 0
    if state.last_conflict_strategy_index != state.conflict_strategy_index:
        return 0
    count = max(0, state.consecutive_conflicts)
    if attempts is not None:
        return min(attempts, count)
    return count


def resolve_failure_ledger(
    ledger: AgentFailureLedger | None,
) -> AgentFailureLedger | None:
    if ledger is not None:
        return ledger
    try:
        from ralph.pipeline.conflict_resolution.rebase_loop import (
            active_rebase_resolution_session,
        )

        session = active_rebase_resolution_session()
    except Exception:
        return None
    if session is None:
        return None
    from ralph.pipeline.conflict_resolution.chain_candidates import session_ledger

    return session_ledger(session)


def _is_same_conflict(
    state: RebaseState,
    target: str,
    identity: ConflictIdentity,
) -> bool:
    return (
        state.last_target == target
        and identity.matches(state)
        and state.last_conflict_strategy_index == state.conflict_strategy_index
    )


def _ledger_candidates_available(
    ledger: AgentFailureLedger,
    phase: str,
    candidates: Sequence[str],
) -> bool:
    if candidates:
        return any(ledger.is_available(phase, a) for a in candidates)
    snap = ledger.snapshot()
    raw_entries = snap.get("entries")
    if isinstance(raw_entries, dict):
        phase_agents: list[str] = []
        for val in raw_entries.values():
            if isinstance(val, dict):
                p = val.get("phase")
                a = val.get("agent")
                if isinstance(p, str) and p == phase and isinstance(a, str) and a:
                    phase_agents.append(a)
        if phase_agents:
            return any(ledger.is_available(phase, a) for a in phase_agents)
    return True


def resolver_allowed(
    state: RebaseState,
    target: str,
    identity: ConflictIdentity = _UNKNOWN_IDENTITY,
    *,
    ledger: AgentFailureLedger | None = None,
    candidates: Sequence[str] = (),
    phase: str = "rebase_conflict_resolution",
    **kwargs: object,
) -> bool:
    del kwargs
    if _is_same_conflict(state, target, identity) and state.resolution_exhausted:
        return False

    if ledger is None:
        return True

    return _ledger_candidates_available(ledger, phase, candidates)


@runtime_checkable
class _SupportsMonotonic(Protocol):
    def monotonic(self) -> float: ...


@runtime_checkable
class _SupportsTime(Protocol):
    def time(self) -> float: ...


@runtime_checkable
class _SupportsCallSeconds(Protocol):
    def __call__(self) -> float: ...


def _read_clock_seconds(clock: object) -> float:
    if clock is None:
        raise TypeError("clock must not be None; provide an injected clock abstraction")
    if isinstance(clock, _SupportsMonotonic):
        try:
            return float(clock.monotonic())
        except (TypeError, ValueError) as exc:
            raise TypeError(f"clock.monotonic() failed to return numeric seconds: {exc}") from exc
    if isinstance(clock, _SupportsTime):
        try:
            return float(clock.time())
        except (TypeError, ValueError) as exc:
            raise TypeError(f"clock.time() failed to return numeric seconds: {exc}") from exc
    if isinstance(clock, _SupportsCallSeconds):
        try:
            return float(clock())
        except (TypeError, ValueError) as exc:
            raise TypeError(f"clock callable failed to return numeric seconds: {exc}") from exc
    raise TypeError(
        f"Injected clock {type(clock).__name__!r} must be callable or provide .monotonic() or .time()"
    )


def apply_conflict_budget(
    record: RebaseState,
    *,
    prior: RebaseState,
    target: str,
    resolver_suppressed: bool,
    identity: ConflictIdentity = _UNKNOWN_IDENTITY,
    attempts: int = MAX_TRACKED_CONFLICT_ATTEMPTS,
    retry_deadline: float | None = None,
    clock: object = None,
    cooldown_seconds: float | None = None,
) -> RebaseState:
    carried = prior_conflict_count(prior, target, identity, attempts=attempts)
    effective_deadline = retry_deadline
    if effective_deadline is None and clock is not None and cooldown_seconds is not None:
        cooldown = min(float(cooldown_seconds), MAX_RETRY_DEADLINE_HORIZON_SECONDS)
        effective_deadline = _read_clock_seconds(clock) + cooldown

    if record.fast_forwarded:
        updated = record.record_consecutive_conflicts(0, cap=attempts).set_retry_deadline(None)
        return updated.model_copy(
            update={
                "last_conflict_feature_sha": None,
                "last_conflict_target_sha": None,
                "last_conflict_paths": (),
                "last_conflict_stage_oids": (),
                "last_conflict_scope": "",
                "last_conflict_strategy_index": 0,
            }
        )
    if record.last_outcome != RebaseOutcomeKind.CONFLICT:
        deadline_value = (
            effective_deadline
            if effective_deadline is not None
            else (
                record.retry_deadline if record.retry_deadline is not None else prior.retry_deadline
            )
        )
        updated = (
            record.record_consecutive_conflicts(carried, cap=attempts)
            .set_retry_deadline(deadline_value)
            .project_conflict_strategy_from(prior)
        )
        return updated.model_copy(
            update={
                "last_conflict_feature_sha": prior.last_conflict_feature_sha,
                "last_conflict_target_sha": prior.last_conflict_target_sha,
                "last_conflict_paths": prior.last_conflict_paths,
                "last_conflict_stage_oids": prior.last_conflict_stage_oids,
                "last_conflict_scope": prior.last_conflict_scope,
                "last_conflict_strategy_index": prior.last_conflict_strategy_index,
            }
        )

    deadline_value = (
        effective_deadline
        if effective_deadline is not None
        else (record.retry_deadline if record.retry_deadline is not None else prior.retry_deadline)
    )
    updated = (
        record.record_consecutive_conflicts(min(attempts, carried + 1), cap=attempts)
        .set_retry_deadline(deadline_value)
        .project_conflict_strategy_from(prior)
    )
    update: dict[str, object] = {
        "last_conflict_feature_sha": identity.feature_sha,
        "last_conflict_target_sha": identity.target_sha,
        "last_conflict_paths": identity.conflicted_paths,
        "last_conflict_stage_oids": identity.stage_oids,
        "last_conflict_scope": identity.scope,
        "last_conflict_strategy_index": prior.conflict_strategy_index,
    }
    if resolver_suppressed:
        update["last_reason"] = (
            f"conflict resolution budget exhausted for '{target}' after "
            f"{min(carried, attempts)} unresolved attempts; escalating to the next resolution strategy"
        )
    return updated.model_copy(update=update)


_MAX_ACTIVE_CONFLICT_ATTEMPTS = 64
_ACTIVE_CONFLICT_ATTEMPTS: OrderedDict[tuple[object, ...], None] = OrderedDict()


def _conflict_attempt_key(identity: ConflictIdentity) -> tuple[object, ...]:
    return (
        identity.feature_sha,
        identity.target_sha,
        identity.conflicted_paths,
        identity.stage_oids,
        identity.scope,
    )


def start_conflict_attempt(identity: ConflictIdentity) -> bool:
    key = _conflict_attempt_key(identity)
    if key in _ACTIVE_CONFLICT_ATTEMPTS:
        return False
    while len(_ACTIVE_CONFLICT_ATTEMPTS) >= _MAX_ACTIVE_CONFLICT_ATTEMPTS:
        _ACTIVE_CONFLICT_ATTEMPTS.popitem(last=False)
    _ACTIVE_CONFLICT_ATTEMPTS[key] = None
    return True


def finish_conflict_attempt(identity: ConflictIdentity) -> None:
    _ACTIVE_CONFLICT_ATTEMPTS.pop(_conflict_attempt_key(identity), None)
