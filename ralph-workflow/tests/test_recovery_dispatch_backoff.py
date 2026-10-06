"""Regression coverage for conflict-strategy recovery backoff."""

from __future__ import annotations

from ralph.pipeline.events import PhaseFailureEvent
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import PipelineState
from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy, RecoveryPolicy


def _policy() -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            "development": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(on_success="complete", on_loopback="development"),
            ),
            "failed_terminal": PhaseDefinition(
                drain="development",
                role="terminal",
                terminal_outcome="failure",
                transitions=PhaseTransition(on_success="failed_terminal"),
            ),
        },
        entry_phase="development",
        terminal_phase="complete",
        recovery=RecoveryPolicy(failed_route="failed_terminal"),
    )


def test_conflict_strategy_retry_uses_bounded_exponential_backoff() -> None:
    """The next rung cannot immediately hot-loop at the recovery boundary."""
    state, _ = reduce(
        PipelineState(phase="development"),
        PhaseFailureEvent(
            phase="development",
            reason="integration conflict requires resolution: unresolved shared.txt",
            recoverable=False,
        ),
        _policy(),
    )

    assert state.rebase.conflict_strategy_index == 1
    assert state.last_retry_delay_ms == 2000


def test_integration_recovery_backoff_survives_restart_and_exhaustion() -> None:
    from ralph.pipeline.agent_chain_state import AgentChainState
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.policy.models import (
        AgentChainConfig,
        AgentDrainConfig,
        AgentsPolicy,
        ArtifactsPolicy,
        PolicyBundle,
    )
    from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
    from ralph.recovery.events import FailureEvent, FalloverEvent
    from ralph.recovery.failure_category import FailureCategory

    for configured_delay in (250, 0):
        bundle = PolicyBundle(
            pipeline=_policy(),
            artifacts=ArtifactsPolicy(artifacts={}),
            agents=AgentsPolicy(
                agent_chains={
                    "development": AgentChainConfig(
                        agents=["committer"], retry_delay_ms=configured_delay
                    )
                },
                agent_drains={"development": AgentDrainConfig(chain="development")},
            ),
        )
        state = PipelineState(
            phase="development",
            recovery_epoch=300,
            phase_chains={"development": AgentChainState(agents=["committer"])},
            rebase=RebaseState(recovery_record_retained=True, resolution_exhausted=True),
        )
        previous_delay = 0
        for _attempt in range(10):
            controller = RecoveryController(options=RecoveryControllerOptions(policy_bundle=bundle))
            emitted: list[FailureEvent | FalloverEvent] = []
            controller.event_bus.subscribe(emitted.append)
            state, effects = reduce(
                PipelineState.model_validate_json(state.model_dump_json()).copy_with(
                    last_retry_delay_ms=0
                ),
                PhaseFailureEvent(
                    phase="development",
                    recoverable=True,
                    reason=(
                        "owned continuation deferred"
                        if configured_delay
                        else "integration conflict requires resolution: retained for agent continuation"
                    ),
                    failure_category=FailureCategory.INTEGRATION if configured_delay else None,
                ),
                bundle.pipeline,
                recovery=controller,
            )
            assert state.last_failure_category == "integration"
            assert len(emitted) == 1
            event = emitted[0]
            assert isinstance(event, FailureEvent) and event.category == "integration"
            assert event.agent is None and not event.counted_against_budget
            assert "Unknown fault" not in event.reason
            if _attempt == 0:
                assert state.last_retry_delay_ms == (configured_delay or 1000)
            assert 0 < state.last_retry_delay_ms <= 30_000
            assert state.last_retry_delay_ms >= previous_delay
            assert state.phase == "development" and state.current_agent() == "committer"
            assert state.rebase.recovery_record_retained and state.rebase.resolution_exhausted
            assert not effects
            chain = state.chain_for_phase("development")
            assert chain is not None and chain.retries == 0
            previous_delay = state.last_retry_delay_ms
        assert previous_delay == 30_000
        assert state.rebase.integration_retry_attempt < 10

        from ralph.pipeline.auto_integrate_resolution_state import (
            preserve_unresolved_resolution_state,
        )

        complete = preserve_unresolved_resolution_state(
            state.rebase.model_copy(update={"fast_forwarded": True}),
            prior=state.rebase,
        )
        assert complete is not None and complete.integration_retry_attempt == 0
        from ralph.pipeline.auto_integrate_resolution_state import reconcile_stale_unresolved_state

        assert reconcile_stale_unresolved_state(state.rebase).integration_retry_attempt == 0
