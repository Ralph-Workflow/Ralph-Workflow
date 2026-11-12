from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import TYPE_CHECKING, Final, cast

from pydantic import Field, field_validator, model_validator

from ralph.config.enums import PipelinePhase
from ralph.phases.phase_timing_record import PhaseTimingRecord
from ralph.pipeline.agent_retry_intent import AgentRetryIntent
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerState

from ._wait_reason import WaitReason
from .state_models import (
    AgentChainState,
    CommitState,
    FalloverRecord,
    RebaseState,
    RunMetrics,
    _FrozenPipelineStateModel,
)

if TYPE_CHECKING:
    from ralph.policy.models import DrainName, PipelinePolicy

_UNSET_PHASE: Final[str] = "__unset__"
_DEFAULT_RECOVERY_CYCLE_CAP: Final[int] = 200


def _legacy_chain_agents(chain: dict[str, object]) -> tuple[str, ...]:
    raw_agents = chain.get("agents")
    if not isinstance(raw_agents, list | tuple):
        return ()
    return tuple(agent for agent in raw_agents if isinstance(agent, str) and agent)


def _legacy_chain_current_agent(
    chain: dict[str, object],
    agents: tuple[str, ...],
) -> str | None:
    raw_index = chain.get("current_index", 0)
    if isinstance(raw_index, bool) or not isinstance(raw_index, int):
        return None
    if raw_index < 0 or raw_index >= len(agents):
        return None
    return agents[raw_index]


def _migrate_legacy_guard_into_ledger(d: dict[str, object]) -> None:
    from ralph.recovery.agent_failure_ledger import (
        LEDGER_SNAPSHOT_MALFORMED_REASON,
        LEGACY_DEFAULT_AGENT,
        LEGACY_IDENTITY_UNATTRIBUTABLE_REASON,
        _LegacyGuardSource,
        migrate_legacy_guard_history,
    )

    raw_guard = d.pop("progress_guard", None)
    raw_chains = d.get("phase_chains")
    phase = str(d.get("phase") or "development")

    sources: list[_LegacyGuardSource] = []
    chain_agents: dict[str, str | None] = {}
    if isinstance(raw_chains, dict):
        for phase_key, chain_val in raw_chains.items():
            if not isinstance(chain_val, dict):
                continue
            chain_guard = chain_val.pop("progress_guard", None)
            agents = _legacy_chain_agents(chain_val)
            current_agent = _legacy_chain_current_agent(chain_val, agents)
            chain_agents[str(phase_key)] = current_agent
            if chain_guard is None:
                continue
            sources.append(
                _LegacyGuardSource(
                    payload=chain_guard,
                    phase=str(phase_key),
                    agent=current_agent,
                    candidate_agents=agents,
                    fail_closed_reason=(None if agents else LEGACY_IDENTITY_UNATTRIBUTABLE_REASON),
                )
            )

    raw_intent = d.get("agent_retry_intent")
    intent_guard: object = None
    intent_agent: str | None = None
    if isinstance(raw_intent, dict):
        intent_guard = raw_intent.pop("progress_guard", None)
        raw_failed_agent = raw_intent.get("failed_agent_name")
        if isinstance(raw_failed_agent, str) and raw_failed_agent:
            intent_agent = raw_failed_agent

    active_agent = chain_agents.get(phase) or intent_agent or LEGACY_DEFAULT_AGENT
    if raw_guard is not None:
        sources.append(_LegacyGuardSource(payload=raw_guard, phase=phase, agent=active_agent))
    if intent_guard is not None:
        sources.append(
            _LegacyGuardSource(
                payload=intent_guard,
                phase=phase,
                agent=intent_agent or active_agent,
            )
        )

    raw_snapshot = d.get("ledger_snapshot")
    stored_snapshot: Mapping[str, object] | None = None
    snapshot_unreadable = raw_snapshot is not None
    if isinstance(raw_snapshot, Mapping):
        if "entries" in raw_snapshot and not isinstance(raw_snapshot["entries"], Mapping):
            snapshot_unreadable = True
        else:
            stored_snapshot = raw_snapshot
            snapshot_unreadable = False
    if snapshot_unreadable:
        sources.append(
            _LegacyGuardSource(
                payload=raw_snapshot,
                phase=phase,
                agent=active_agent,
                fail_closed_reason=LEDGER_SNAPSHOT_MALFORMED_REASON,
            )
        )

    migrated = migrate_legacy_guard_history(sources, existing_snapshot=stored_snapshot)
    if migrated is not None:
        d["ledger_snapshot"] = migrated


def _migrate_counter_field(
    d: dict[str, object],
    target: dict[str, object],
    legacy_field: str,
    counter_name: str,
) -> None:
    if counter_name not in target and legacy_field in d:
        target[counter_name] = d[legacy_field]


def _resolved_recovery_cycle_cap(raw_cap: object) -> int:
    if raw_cap is None:
        return _DEFAULT_RECOVERY_CYCLE_CAP
    if isinstance(raw_cap, int):
        cap = raw_cap
    elif isinstance(raw_cap, str):
        try:
            cap = int(raw_cap)
        except ValueError:
            return _DEFAULT_RECOVERY_CYCLE_CAP
    else:
        return _DEFAULT_RECOVERY_CYCLE_CAP
    return cap if cap >= 1 else _DEFAULT_RECOVERY_CYCLE_CAP


def _normalize_fallover_history_for_cap(
    history: object,
    recovery_cycle_cap: object,
) -> tuple[FalloverRecord, ...]:
    if history is None:
        return ()
    if not isinstance(history, list | tuple):
        raise TypeError(
            f"Expected list or tuple for fallover_history, got {type(history).__name__!r}"
        )
    records = tuple(
        FalloverRecord.model_validate(item)
        if isinstance(item, dict)
        else cast("FalloverRecord", item)
        for item in history
    )
    cap = _resolved_recovery_cycle_cap(recovery_cycle_cap)
    if len(records) <= cap:
        return records
    return records[-cap:]


class PipelineState(_FrozenPipelineStateModel):
    phase: PipelinePhase = _UNSET_PHASE
    previous_phase: PipelinePhase | None = None
    integration_commit_resume_phase: PipelinePhase | None = None

    review_outcome: str | None = None

    phase_chains: dict[str, AgentChainState] = Field(default_factory=dict)

    loop_iterations: dict[str, int] = Field(default_factory=dict)

    budget_caps: dict[str, int] = Field(default_factory=dict)
    outer_progress: dict[str, int] = Field(default_factory=dict)

    rebase: RebaseState = Field(default_factory=RebaseState)
    commit: CommitState = Field(default_factory=CommitState)
    metrics: RunMetrics = Field(default_factory=RunMetrics)
    checkpoint_saved_count: int = 0
    recovery_epoch: int = 0
    interrupted_by_user: bool = False
    git_auth_configured: bool = False
    pr_created: bool = False
    pr_url: str | None = None
    push_count: int = 0
    last_error: str | None = None
    last_reviewed_sha: str | None = None

    rollback_pending: bool = False
    rollback_ref: str | None = None
    rollback_baseline_ref: str | None = None
    commit_review_rejections: int = 0
    approved_tree_sha: str | None = None
    commit_approval_required: bool = False
    last_review_rejection_reason: str | None = None

    policy_entry_phase: PipelinePhase = _UNSET_PHASE
    policy_format_version: int | None = None
    current_drain: str | None = None
    post_commit_phase_override: PipelinePhase | None = None
    pending_cycle_outcome: str | None = None
    execution_cycle_charged: bool = False
    last_execution_result_status: str | None = None
    cycle_timing_start_index: int = 0
    cycle_timebox_active: bool = False
    cycle_timebox_consumed_seconds: float = 0.0
    cycle_timebox_started_at_epoch: float | None = None
    cycle_timebox_redirect_reason: str | None = None
    cycle_timebox_redirects: int = 0
    dev_timebox_active: bool = False
    dev_timebox_consumed_seconds: float = 0.0
    dev_timebox_started_at_epoch: float | None = None
    dev_timebox_redirect_reason: str | None = None

    work_units: tuple[WorkUnit, ...] = Field(default_factory=tuple)
    worker_states: dict[str, WorkerState] = Field(default_factory=dict)
    phase_timings: tuple[PhaseTimingRecord, ...] = Field(default_factory=tuple)

    recovery_cycle_count: int = 0
    fallover_history: tuple[FalloverRecord, ...] = Field(default_factory=tuple)
    last_failure_category: str | None = None
    last_connectivity_state: str = "unknown"
    recovery_cycle_cap: int = Field(default=200, ge=1)
    last_retry_delay_ms: int = 0
    last_agent_session_id: str | None = None
    agent_retry_intent: AgentRetryIntent = Field(default_factory=AgentRetryIntent)
    last_unavailability_reason: str | None = None
    is_waiting_state: bool = False
    wait_reason: WaitReason | None = None
    wait_until_monotonic_ms: int | None = None
    ledger_snapshot: dict[str, object] | None = None
    dead_agent_verdict_record: dict[str, object] | None = None

    @model_validator(mode="after")
    def _validate_phase_set(self) -> PipelineState:
        if self.phase == _UNSET_PHASE:
            raise ValueError(
                "PipelineState requires phase to be set from PipelinePolicy.entry_phase "
                "before construction; use PipelineState.from_policy(policy) "
                "or pass phase= explicitly."
            )
        if not isinstance(self.agent_retry_intent, AgentRetryIntent):
            raise RuntimeError(
                "PipelineState.agent_retry_intent must be AgentRetryIntent; "
                f"got {type(self.agent_retry_intent).__name__}"
            )
        return self

    @classmethod
    def from_policy(cls, policy: PipelinePolicy, **overrides: object) -> PipelineState:
        payload: dict[str, object] = {
            "phase": policy.entry_phase,
            "policy_entry_phase": policy.entry_phase,
            "policy_format_version": 2 if policy.entry_block is not None else 1,
            **overrides,
        }
        return cls.model_validate(payload)

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_state_fields(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        d = cast("dict[str, object]", dict(data))

        _raw_bc = d.get("budget_caps")
        budget_caps_data: dict[str, object] = dict(
            cast("dict[str, object]", _raw_bc) if _raw_bc is not None else {}
        )
        _raw_op = d.get("outer_progress")
        outer_progress_data: dict[str, object] = dict(
            cast("dict[str, object]", _raw_op) if _raw_op is not None else {}
        )
        _migrate_counter_field(d, budget_caps_data, "total_iterations", "iteration")
        _migrate_counter_field(d, budget_caps_data, "total_reviewer_passes", "reviewer_pass")
        _migrate_counter_field(d, outer_progress_data, "iteration", "iteration")
        _migrate_counter_field(d, outer_progress_data, "reviewer_pass", "reviewer_pass")

        for _br_field, _counter in (
            ("development_budget_remaining", "iteration"),
            ("review_budget_remaining", "reviewer_pass"),
        ):
            _scalar_br = d.get(_br_field)
            if (
                _scalar_br is not None
                and _counter not in outer_progress_data
                and _counter in budget_caps_data
            ):
                _cap = int(cast("int", budget_caps_data[_counter]))
                _rem = int(cast("int", _scalar_br))
                outer_progress_data[_counter] = max(0, _cap - _rem)

        _raw_br = d.get("budget_remaining")
        if isinstance(_raw_br, dict):
            legacy_br = cast("dict[str, object]", _raw_br)
            for counter, remaining_val in legacy_br.items():
                if counter not in outer_progress_data and counter in budget_caps_data:
                    cap = int(cast("int", budget_caps_data[counter]))
                    remaining = int(cast("int", remaining_val))
                    outer_progress_data[counter] = max(0, cap - remaining)

        d["budget_caps"] = budget_caps_data
        d["outer_progress"] = outer_progress_data
        if "fallover_history" in d:
            d["fallover_history"] = _normalize_fallover_history_for_cap(
                d.get("fallover_history"),
                d.get("recovery_cycle_cap"),
            )
        d.pop("budget_remaining", None)

        _migrate_legacy_guard_into_ledger(d)

        return d

    @field_validator("work_units", mode="before")
    @classmethod
    def _coerce_work_units(cls, v: object) -> tuple[WorkUnit, ...]:
        if v is None:
            return ()
        if isinstance(v, list):
            return tuple(v)
        if isinstance(v, tuple):
            return v
        raise TypeError(f"Expected list or tuple for work_units, got {type(v).__name__!r}")

    @field_validator("worker_states", mode="before")
    @classmethod
    def _coerce_worker_states(cls, v: object) -> dict[str, WorkerState]:
        if v is None:
            return {}
        if isinstance(v, dict):
            return v
        raise TypeError(f"Expected dict for worker_states, got {type(v).__name__!r}")

    @field_validator("fallover_history", mode="before")
    @classmethod
    def _coerce_fallover_history(cls, v: object) -> tuple[FalloverRecord, ...]:
        if v is None:
            return ()
        if isinstance(v, list):
            return tuple(
                FalloverRecord.model_validate(item) if isinstance(item, dict) else item
                for item in v
            )
        if isinstance(v, tuple):
            return v
        raise TypeError(f"Expected list or tuple for fallover_history, got {type(v).__name__!r}")

    @field_validator("phase_chains", mode="before")
    @classmethod
    def _coerce_phase_chains(cls, v: object) -> dict[str, AgentChainState]:
        if v is None:
            return {}
        if isinstance(v, dict):
            return {
                str(key): AgentChainState.model_validate(value)
                if isinstance(value, dict)
                else cast("AgentChainState", value)
                for key, value in v.items()
            }
        raise TypeError(f"Expected dict for phase_chains, got {type(v).__name__!r}")

    @field_validator("loop_iterations", mode="before")
    @classmethod
    def _coerce_loop_iterations(cls, v: object) -> dict[str, int]:
        if v is None:
            return {}
        if isinstance(v, dict):
            return {str(k): int(val) for k, val in v.items()}
        raise TypeError(f"Expected dict for loop_iterations, got {type(v).__name__!r}")

    @field_validator("budget_caps", mode="before")
    @classmethod
    def _coerce_budget_caps(cls, v: object) -> dict[str, int]:
        if v is None:
            return {}
        if isinstance(v, dict):
            return {str(k): int(val) for k, val in v.items()}
        raise TypeError(f"Expected dict for budget_caps, got {type(v).__name__!r}")

    @field_validator("outer_progress", mode="before")
    @classmethod
    def _coerce_outer_progress(cls, v: object) -> dict[str, int]:
        if v is None:
            return {}
        if isinstance(v, dict):
            return {str(k): int(val) for k, val in v.items()}
        raise TypeError(f"Expected dict for outer_progress, got {type(v).__name__!r}")

    def is_complete(self, policy: PipelinePolicy) -> bool:
        return self.phase == policy.terminal_phase

    def current_agent(self) -> str | None:
        chain = self.chain_for_phase(self.phase)
        if chain is None:
            return None
        if not chain.agents or chain.current_index >= len(chain.agents):
            return None
        return chain.agents[chain.current_index]

    def chain_for_phase(self, phase: PipelinePhase | str) -> AgentChainState | None:
        return self.phase_chains.get(str(phase))

    def with_phase_chain(
        self,
        phase: PipelinePhase | str,
        chain: AgentChainState,
    ) -> PipelineState:
        phase_key = str(phase)
        new_chains = {**self.phase_chains, phase_key: chain}
        return self.copy_with(phase_chains=new_chains)

    def with_drain(self, drain: DrainName | None) -> PipelineState:
        return self.copy_with(current_drain=drain)

    def get_loop_iteration(self, field_name: str) -> int:
        return self.loop_iterations.get(field_name, 0)

    def with_loop_iteration(self, field_name: str, value: int) -> PipelineState:
        return self.copy_with(
            loop_iterations={**self.loop_iterations, field_name: value},
        )

    def get_budget_remaining(self, counter_name: str) -> int:
        return max(
            0,
            self.budget_caps.get(counter_name, 0) - self.outer_progress.get(counter_name, 0),
        )

    def get_budget_cap(self, counter_name: str) -> int:
        return self.budget_caps.get(counter_name, 0)

    def get_outer_progress(self, counter_name: str) -> int:
        return self.outer_progress.get(counter_name, 0)

    def with_outer_progress(self, counter_name: str, value: int) -> PipelineState:
        return self.copy_with(outer_progress={**self.outer_progress, counter_name: value})

    def with_budget_cap(self, counter_name: str, value: int) -> PipelineState:
        return self.copy_with(
            budget_caps={**self.budget_caps, counter_name: value},
        )

    def with_fallover_record(self, record: FalloverRecord) -> PipelineState:
        return self.copy_with(fallover_history=(*self.fallover_history, record))

    def with_phase_timing(self, record: PhaseTimingRecord) -> PipelineState:
        return self.copy_with(phase_timings=(*self.phase_timings, record))

    def with_parallel_execution_cleared(self) -> PipelineState:
        return self.model_copy(update={"work_units": (), "worker_states": {}})

    def set_waiting(self, reason: WaitReason, until_monotonic_ms: int | None) -> PipelineState:
        return self.model_copy(
            update={
                "is_waiting_state": True,
                "wait_reason": reason,
                "wait_until_monotonic_ms": until_monotonic_ms,
            }
        )

    def clear_waiting(self) -> PipelineState:
        return self.model_copy(
            update={
                "is_waiting_state": False,
                "wait_reason": None,
                "wait_until_monotonic_ms": None,
            }
        )

    def copy_with(self, **updates: object) -> PipelineState:
        if self.work_units and "work_units" in updates and updates["work_units"] != self.work_units:
            updates = {k: v for k, v in updates.items() if k != "work_units"}
        if "fallover_history" in updates or "recovery_cycle_cap" in updates:
            updates = {
                **updates,
                "fallover_history": _normalize_fallover_history_for_cap(
                    updates.get("fallover_history", self.fallover_history),
                    updates.get("recovery_cycle_cap", self.recovery_cycle_cap),
                ),
            }
        return self.model_copy(update=updates)


PipelineState.model_rebuild(
    _types_namespace={
        "PipelinePhase": PipelinePhase,
        "AgentRetryIntent": AgentRetryIntent,
        "WorkUnit": WorkUnit,
        "WorkerState": WorkerState,
        "PhaseTimingRecord": PhaseTimingRecord,
        "timedelta": timedelta,
    }
)

__all__ = [
    "AgentChainState",
    "CommitState",
    "FalloverRecord",
    "PipelineState",
    "RebaseState",
    "RunMetrics",
    "WaitReason",
]
