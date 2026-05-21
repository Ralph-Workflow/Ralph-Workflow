"""Immutable pipeline state model.

This module defines PipelineState - the single source of truth for pipeline
execution progress. It serves dual purposes:
1. Runtime State: Tracks current phase, iteration counters, agent chain state
2. Checkpoint Payload: Serializes to JSON for resume functionality

PipelineState is IMMUTABLE from the reducer's perspective. State transitions
occur exclusively through the reduce function.

POLICY-DRIVEN STATE TRACKING
==============================
Loop counters (loop_iterations / loop_caps) and phase chains (phase_chains)
are keyed by policy-declared names, not hardcoded field names. This enables
custom workflows with arbitrary phase and counter names to work without
modifying source code.

Budget counters (budget_caps / outer_progress) track the cap and completed
cycles for each policy-declared budget counter. Remaining budget is always
derived: remaining = max(0, cap - progress).

Legacy checkpoint fields (budget fields only) are migrated to the generic
dicts at deserialise time via the _migrate_legacy_state_fields model_validator.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, cast

from pydantic import Field, field_validator, model_validator

from ralph.config.enums import PipelinePhase
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerState

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
    """Immutable snapshot of pipeline execution state.

    This is the checkpoint payload - the single source of truth for pipeline progress.
    Serialize it to JSON to save state; deserialize to resume interrupted runs.

    GENERIC TRACKING FIELDS (policy-keyed):
        phase_chains: Per-phase agent chain state keyed by canonical phase name.
        loop_iterations: Loop iteration counters keyed by iteration_state_field name.
        loop_caps: Loop iteration caps keyed by iteration_state_field name.
        budget_caps: Max budget keyed by budget counter name (seeded from policy).
        outer_progress: Completed cycle counts keyed by budget counter name.
        Remaining budget is derived on-demand: max(0, cap - progress).
    """

    phase: PipelinePhase = _UNSET_PHASE
    previous_phase: PipelinePhase | None = None

    # Review outcome tracking (replaces direct review_issues_found writes)
    review_outcome: str | None = None

    # Generic per-phase chain state (keyed by canonical phase name from policy)
    phase_chains: dict[str, AgentChainState] = Field(default_factory=dict)

    # Generic loop iteration tracking (keyed by iteration_state_field from loop_policy)
    loop_iterations: dict[str, int] = Field(default_factory=dict)
    loop_caps: dict[str, int] = Field(default_factory=dict)

    # Generic budget counter tracking (keyed by budget counter name from budget_counters)
    # Remaining budget is derived: max(0, budget_caps[k] - outer_progress[k])
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

    # Policy-derived fields (set at startup and after phase transitions)
    policy_entry_phase: PipelinePhase = _UNSET_PHASE
    policy_format_version: int | None = None
    current_drain: str | None = None

    work_units: tuple[WorkUnit, ...] = Field(default_factory=tuple)
    worker_states: dict[str, WorkerState] = Field(default_factory=dict)

    # Recovery observability fields — all have defaults so legacy checkpoints load cleanly
    recovery_cycle_count: int = 0
    fallover_history: tuple[FalloverRecord, ...] = Field(default_factory=tuple)
    last_failure_category: str | None = None
    last_connectivity_state: str = "unknown"
    recovery_cycle_cap: int = Field(default=200, ge=1)
    last_retry_delay_ms: int = 0
    last_agent_session_id: str | None = None
    session_preserve_retry_pending: bool = False

    @model_validator(mode="after")
    def _validate_phase_set(self) -> PipelineState:
        if self.phase == _UNSET_PHASE:
            raise ValueError(
                "PipelineState requires phase to be set from PipelinePolicy.entry_phase "
                "before construction; use PipelineState.from_policy(policy) "
                "or pass phase= explicitly."
            )
        return self

    @classmethod
    def from_policy(cls, policy: PipelinePolicy, **overrides: object) -> PipelineState:
        """Construct initial pipeline state from a loaded PipelinePolicy.

        The entry phase is derived from policy.entry_phase so no workflow
        entry semantics are embedded in this class.
        """
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
        """Migrate legacy checkpoint fields into generic dicts.

        Handles old checkpoints that stored:
        - Typed chain fields → migrated into phase_chains
        - Legacy budget fields → migrated into budget_caps / outer_progress

        Legacy budget_remaining values are converted to outer_progress by computing
        progress = cap - remaining, so the derived remaining stays the same.
        """
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

        # Migrate very-old scalar remaining fields to outer_progress via progress = cap - remaining
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

        # Migrate legacy budget_remaining dict into outer_progress via progress = cap - remaining
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
        # Drop legacy budget_remaining — no longer a field
        d.pop("budget_remaining", None)

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

    @field_validator("loop_caps", mode="before")
    @classmethod
    def _coerce_loop_caps(cls, v: object) -> dict[str, int]:
        if v is None:
            return {}
        if isinstance(v, dict):
            return {str(k): int(val) for k, val in v.items()}
        raise TypeError(f"Expected dict for loop_caps, got {type(v).__name__!r}")

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
        """Check if pipeline has reached a terminal success state.

        Args:
            policy: PipelinePolicy. Compares current phase against
                policy.terminal_phase to determine completion.

        Raises:
            RuntimeError: When policy is None (routing requires loaded policy).
        """
        return self.phase == policy.terminal_phase

    def current_agent(self) -> str | None:
        """Get the current agent for the active phase."""
        chain = self.chain_for_phase(self.phase)
        if chain is None:
            return None
        if not chain.agents or chain.current_index >= len(chain.agents):
            return None
        return chain.agents[chain.current_index]

    def remaining_retries(self) -> int:
        """Calculate remaining retries for current agent."""
        chain = self.chain_for_phase(self.phase)
        if chain is None:
            return 0
        return max(0, 3 - chain.retries)

    def advance_agent(self) -> PipelineState:
        """Advance to the next agent in the fallback chain."""
        chain = self.chain_for_phase(self.phase)
        if chain is None:
            return self
        new_chain = AgentChainState(
            agents=chain.agents,
            current_index=min(chain.current_index + 1, len(chain.agents) - 1),
            retries=0,
        )
        return self.with_phase_chain(self.phase, new_chain)

    def chain_for_phase(self, phase: PipelinePhase | str) -> AgentChainState | None:
        """Get the tracked agent chain state for a phase, if any."""
        return self.phase_chains.get(str(phase))

    def with_phase_chain(
        self,
        phase: PipelinePhase | str,
        chain: AgentChainState,
    ) -> PipelineState:
        """Return a copy with the chain state for the given phase updated."""
        phase_key = str(phase)
        new_chains = {**self.phase_chains, phase_key: chain}
        return self.copy_with(phase_chains=new_chains)

    def with_drain(self, drain: DrainName | None) -> PipelineState:
        """Return a copy with the current_drain set."""
        return self.copy_with(current_drain=drain)

    def get_loop_iteration(self, field_name: str) -> int:
        """Get the loop iteration counter for a policy-declared iteration field.

        Args:
            field_name: The iteration_state_field value from PhaseLoopPolicy.

        Returns:
            Current iteration count (0 when not yet set).
        """
        return self.loop_iterations.get(field_name, 0)

    def with_loop_iteration(self, field_name: str, value: int) -> PipelineState:
        """Return a copy with the specified loop iteration field set to value.

        Args:
            field_name: The iteration_state_field value from PhaseLoopPolicy.
            value: New iteration count.

        Returns:
            New PipelineState with the iteration counter updated.
        """
        return self.copy_with(
            loop_iterations={**self.loop_iterations, field_name: value},
        )

    def get_budget_remaining(self, counter_name: str) -> int:
        """Get the remaining budget for a policy-declared budget counter.

        Args:
            counter_name: The budget counter name from PhaseCommitPolicy.increments_counter.

        Returns:
            Remaining budget count, derived as max(0, cap - completed).
        """
        return max(
            0,
            self.budget_caps.get(counter_name, 0) - self.outer_progress.get(counter_name, 0),
        )

    def get_budget_cap(self, counter_name: str) -> int:
        """Get the budget cap for a policy-declared budget counter.

        Args:
            counter_name: The budget counter name.

        Returns:
            Budget cap (maximum allowed), or 0 if not set.
        """
        return self.budget_caps.get(counter_name, 0)

    def get_outer_progress(self, counter_name: str) -> int:
        """Get the completed cycle count for a policy-declared budget counter."""
        return self.outer_progress.get(counter_name, 0)

    def with_outer_progress(self, counter_name: str, value: int) -> PipelineState:
        """Return a copy with the specified outer progress counter set to value."""
        return self.copy_with(outer_progress={**self.outer_progress, counter_name: value})

    def with_budget_cap(self, counter_name: str, value: int) -> PipelineState:
        """Return a copy with the specified budget cap set to value."""
        return self.copy_with(
            budget_caps={**self.budget_caps, counter_name: value},
        )

    def with_fallover_record(self, record: FalloverRecord) -> PipelineState:
        """Return a copy with one additional fallover record, trimmed to cycle cap."""
        return self.copy_with(fallover_history=(*self.fallover_history, record))

    def copy_with(self, **updates: object) -> PipelineState:
        """Return a copy with updates applied in a typed-safe manner."""
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


# Resolve forward references from TYPE_CHECKING imports at runtime
PipelineState.model_rebuild(
    _types_namespace={
        "PipelinePhase": PipelinePhase,
        "WorkUnit": WorkUnit,
        "WorkerState": WorkerState,
    }
)

__all__ = [
    "AgentChainState",
    "CommitState",
    "FalloverRecord",
    "PipelineState",
    "RebaseState",
    "RunMetrics",
]
