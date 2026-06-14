"""Initial pipeline state creation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.pipeline.effect_router import agents_for_phase
from ralph.pipeline.handoffs import resolve_phase_drain
from ralph.pipeline.state import AgentChainState, CommitState, PipelineState, RebaseState

if TYPE_CHECKING:
    from ralph.config.models import UnifiedConfig
    from ralph.policy.models import AgentsPolicy, PipelinePolicy


def _initial_phase_chains(
    config: UnifiedConfig,
    *,
    agents_policy: AgentsPolicy | None,
    pipeline_policy: PipelinePolicy | None,
) -> dict[str, AgentChainState]:
    if pipeline_policy is None:
        return {}
    return {
        phase_name: AgentChainState(
            agents=agents_for_phase(
                config,
                phase_name,
                agents_policy=agents_policy,
                pipeline_policy=pipeline_policy,
            )
        )
        for phase_name in pipeline_policy.phases
    }


def create_initial_state(
    config: UnifiedConfig,
    *,
    agents_policy: AgentsPolicy | None = None,
    pipeline_policy: PipelinePolicy,
    counter_overrides: dict[str, int] | None = None,
) -> PipelineState:
    """Create initial pipeline state from configuration."""
    entry_phase = pipeline_policy.entry_phase
    phase_chains = _initial_phase_chains(
        config,
        agents_policy=agents_policy,
        pipeline_policy=pipeline_policy,
    )

    caps: dict[str, int] = {
        name: cfg.default_max for name, cfg in pipeline_policy.budget_counters.items()
    }
    if "iteration" in caps:
        caps["iteration"] = config.general.developer_iters
    if counter_overrides:
        caps.update(counter_overrides)

    return PipelineState(
        phase=entry_phase,
        budget_caps=caps,
        phase_chains=phase_chains,
        rebase=RebaseState(),
        commit=CommitState(),
        policy_entry_phase=entry_phase,
        policy_format_version=2 if pipeline_policy.entry_block is not None else 1,
        current_drain=resolve_phase_drain(entry_phase, pipeline_policy),
    )
