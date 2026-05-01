"""Tests for the three new strict policy validators.

Covers:
- _validate_skip_invocation_has_on_success
- _validate_parallelization_consistency
- _validate_cli_counter_overrides
"""

from __future__ import annotations

from typing import cast

import pytest

from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactsPolicy,
    BudgetCounterConfig,
    DrainName,
    PhaseDefinition,
    PhaseParallelization,
    PhaseTransition,
    PipelinePolicy,
    PolicyBundle,
    RecoveryPolicy,
)
from ralph.policy.validation import PolicyValidationError, validate_policy_completeness


def _minimal_agents(drains: list[str]) -> AgentsPolicy:
    chains = {d: AgentChainConfig(agents=["claude"]) for d in drains}
    agent_drains = cast(
        "dict[DrainName, AgentDrainConfig]",
        {d: AgentDrainConfig(chain=d) for d in drains},
    )
    return AgentsPolicy(agent_chains=chains, agent_drains=agent_drains)


def _terminal_phase(drain: str = "complete", outcome: str = "success") -> PhaseDefinition:
    return PhaseDefinition(
        drain=drain,
        role="terminal",
        terminal_outcome=outcome,
        transitions=PhaseTransition(on_success=drain, on_loopback=drain),
    )


def _minimal_bundle_with_phases(phases: dict[str, PhaseDefinition]) -> PolicyBundle:
    drains = list(phases.keys())
    agents = _minimal_agents(drains)
    pipeline = PipelinePolicy(
        phases=phases,
        entry_phase=drains[0],
        terminal_phase="complete",
        recovery=RecoveryPolicy(failed_route="complete"),
    )
    return PolicyBundle(agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={}))


class TestSkipInvocationHasOnSuccess:
    """skip_invocation=true requires transitions.on_success."""

    def test_skip_invocation_without_on_success_raises(self) -> None:
        # Use model_construct to bypass Pydantic's required-field and cross-reference
        # validators so we can create the exact invalid state the policy validator
        # is designed to catch (skip_invocation=True with no on_success target).
        invalid_transitions = PhaseTransition.model_construct(
            on_success=None,
            on_failure=None,
            on_loopback=None,
        )
        work_phase = PhaseDefinition.model_construct(
            drain="work",
            transitions=invalid_transitions,
            role="execution",
            skip_invocation=True,
            bypass_routes={},
            decisions={},
            verification=None,
            parallelization=None,
        )
        policy = PipelinePolicy.model_construct(
            phases={"work": work_phase, "complete": _terminal_phase()},
            entry_phase="work",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
            loop_counters={},
            budget_counters={},
            post_commit_routes=[],
        )
        bundle = PolicyBundle.model_construct(
            agents=_minimal_agents(["work", "complete"]),
            pipeline=policy,
            artifacts=ArtifactsPolicy(artifacts={}),
        )
        with pytest.raises(
            PolicyValidationError,
            match=r"skip_invocation=true requires transitions\.on_success",
        ):
            validate_policy_completeness(bundle)

    def test_skip_invocation_true_with_on_success_set_passes(self) -> None:
        phases = {
            "work": PhaseDefinition(
                drain="work",
                role="execution",
                skip_invocation=True,
                transitions=PhaseTransition(on_success="complete"),
            ),
            "complete": _terminal_phase(),
        }
        bundle = _minimal_bundle_with_phases(phases)
        validate_policy_completeness(bundle)  # must not raise

    def test_skip_invocation_false_without_on_success_does_not_raise_from_this_check(
        self,
    ) -> None:
        """skip_invocation=False with no on_success may still fail other validators."""
        phases = {
            "work": PhaseDefinition(
                drain="work",
                role="execution",
                skip_invocation=False,
                transitions=PhaseTransition(on_success="complete"),
            ),
            "complete": _terminal_phase(),
        }
        bundle = _minimal_bundle_with_phases(phases)
        validate_policy_completeness(bundle)  # must not raise due to skip_invocation check


class TestParallelizationConsistency:
    """parallelization.max_work_units must be >= max_parallel_workers."""

    def test_max_work_units_less_than_max_parallel_workers_raises(self) -> None:
        phases = {
            "work": PhaseDefinition(
                drain="work",
                role="execution",
                transitions=PhaseTransition(on_success="complete"),
                parallelization=PhaseParallelization(
                    max_parallel_workers=5,
                    max_work_units=3,
                ),
            ),
            "complete": _terminal_phase(),
        }
        bundle = _minimal_bundle_with_phases(phases)
        with pytest.raises(
            PolicyValidationError,
            match=r"max_work_units.*must be >=.*max_parallel_workers",
        ):
            validate_policy_completeness(bundle)

    def test_max_work_units_equal_to_max_parallel_workers_passes(self) -> None:
        phases = {
            "work": PhaseDefinition(
                drain="work",
                role="execution",
                transitions=PhaseTransition(on_success="complete"),
                parallelization=PhaseParallelization(
                    max_parallel_workers=4,
                    max_work_units=4,
                ),
            ),
            "complete": _terminal_phase(),
        }
        bundle = _minimal_bundle_with_phases(phases)
        validate_policy_completeness(bundle)  # must not raise

    def test_max_work_units_greater_than_max_parallel_workers_passes(self) -> None:
        phases = {
            "work": PhaseDefinition(
                drain="work",
                role="execution",
                transitions=PhaseTransition(on_success="complete"),
                parallelization=PhaseParallelization(
                    max_parallel_workers=2,
                    max_work_units=10,
                ),
            ),
            "complete": _terminal_phase(),
        }
        bundle = _minimal_bundle_with_phases(phases)
        validate_policy_completeness(bundle)  # must not raise

    def test_phase_without_parallelization_is_not_checked(self) -> None:
        phases = {
            "work": PhaseDefinition(
                drain="work",
                role="execution",
                transitions=PhaseTransition(on_success="complete"),
            ),
            "complete": _terminal_phase(),
        }
        bundle = _minimal_bundle_with_phases(phases)
        validate_policy_completeness(bundle)  # must not raise


class TestCliCounterOverrides:
    """CLI counter overrides must reference declared budget_counters."""

    def _bundle_with_budget_counter(self, counter_name: str) -> PolicyBundle:
        agents = _minimal_agents(["work", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "work": PhaseDefinition(
                    drain="work",
                    role="execution",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": _terminal_phase(),
            },
            entry_phase="work",
            terminal_phase="complete",
            budget_counters={counter_name: BudgetCounterConfig(tracks_budget=False, default_max=0)},
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        return PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )

    def test_unknown_counter_override_raises(self) -> None:
        bundle = self._bundle_with_budget_counter("my_counter")
        with pytest.raises(PolicyValidationError, match="unknown_counter"):
            validate_policy_completeness(
                bundle, cli_counter_overrides={"unknown_counter": 3}
            )

    def test_declared_counter_override_passes(self) -> None:
        bundle = self._bundle_with_budget_counter("my_counter")
        validate_policy_completeness(
            bundle, cli_counter_overrides={"my_counter": 3}
        )  # must not raise

    def test_no_overrides_kwarg_does_not_validate_counters(self) -> None:
        bundle = self._bundle_with_budget_counter("my_counter")
        validate_policy_completeness(bundle)  # no cli_counter_overrides — must not raise

    def test_none_overrides_does_not_validate_counters(self) -> None:
        bundle = self._bundle_with_budget_counter("my_counter")
        validate_policy_completeness(bundle, cli_counter_overrides=None)  # must not raise

    def test_error_message_lists_declared_counters(self) -> None:
        bundle = self._bundle_with_budget_counter("declared_counter")
        with pytest.raises(PolicyValidationError) as exc_info:
            validate_policy_completeness(
                bundle, cli_counter_overrides={"bad_counter": 1}
            )
        error_msg = str(exc_info.value)
        assert "bad_counter" in error_msg
        assert "declared_counter" in error_msg


class TestLegacyFieldsRejected:
    """Tests for removed/legacy fields that must be rejected at construction time."""

    def test_failed_route_failed_alias_rejected(self) -> None:
        """RecoveryPolicy rejects 'failed' as failed_route (removed pseudo-phase alias)."""
        with pytest.raises(ValueError, match="'failed' is no longer accepted"):
            RecoveryPolicy(failed_route="failed")

    def test_drain_class_substring_inference_rejected(self) -> None:
        """Custom drain without drain_class raises PolicyValidationError.

        Pre-v2 behavior silently inferred drain class from name substrings
        (e.g. a drain named 'custom_fixer_drain' resolved to DrainClass.FIX).
        That inference was removed; an explicit drain_class is required.
        """
        from ralph.mcp.protocol.capability_mapping import drain_class_for_session  # noqa: PLC0415

        agents = AgentsPolicy(
            agent_chains={"chain": AgentChainConfig(agents=["claude"])},
            agent_drains={"custom_fixer_drain": AgentDrainConfig(chain="chain")},
        )
        with pytest.raises(PolicyValidationError):
            drain_class_for_session("custom_fixer_drain", agents)

    def test_drain_class_missing_rejected_under_strict_validation(self) -> None:
        """validate_drain_contracts rejects drains missing drain_class when strict.

        When forbid_sibling_drain_inference=true, every pipeline-used drain must
        declare drain_class explicitly in agents.toml.
        """
        from ralph.policy.validation import validate_drain_contracts  # noqa: PLC0415

        agents = AgentsPolicy(
            forbid_sibling_drain_inference=True,
            agent_chains={"chain": AgentChainConfig(agents=["claude"])},
            agent_drains={
                "custom_work": AgentDrainConfig(chain="chain"),
                "complete": AgentDrainConfig(chain="chain"),
            },
        )
        pipeline = PipelinePolicy(
            phases={
                "custom_work": PhaseDefinition(
                    drain="custom_work",
                    role="execution",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(on_success="complete"),
                ),
            },
            entry_phase="custom_work",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(
            agents=agents,
            pipeline=pipeline,
            artifacts=ArtifactsPolicy(artifacts={}),
        )
        with pytest.raises(PolicyValidationError, match="no explicit drain_class"):
            validate_drain_contracts(bundle)

    def test_drain_class_present_passes_strict_validation(self) -> None:
        """validate_drain_contracts passes when all drains have explicit drain_class."""
        from ralph.policy.validation import validate_drain_contracts  # noqa: PLC0415

        agents = AgentsPolicy(
            forbid_sibling_drain_inference=True,
            agent_chains={"chain": AgentChainConfig(agents=["claude"])},
            agent_drains={
                "custom_work": AgentDrainConfig(chain="chain", drain_class="development"),
                "complete": AgentDrainConfig(chain="chain", drain_class="development"),
            },
        )
        pipeline = PipelinePolicy(
            phases={
                "custom_work": PhaseDefinition(
                    drain="custom_work",
                    role="execution",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(on_success="complete"),
                ),
            },
            entry_phase="custom_work",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(
            agents=agents,
            pipeline=pipeline,
            artifacts=ArtifactsPolicy(artifacts={}),
        )
        validate_drain_contracts(bundle)  # must not raise

    def test_legacy_phase_field_requires_commit_rejected(self) -> None:
        """PhaseDefinition with requires_commit=True is rejected with an actionable error."""
        from pydantic import ValidationError  # noqa: PLC0415

        with pytest.raises(ValidationError, match="requires_commit has been removed"):
            PhaseDefinition(
                drain="build",
                role="execution",
                requires_commit=True,
                transitions=PhaseTransition(on_success="done"),
            )

    def test_legacy_phase_field_embeds_analysis_rejected(self) -> None:
        """PhaseDefinition with embeds_analysis=True is rejected with an actionable error."""
        from pydantic import ValidationError  # noqa: PLC0415

        with pytest.raises(ValidationError, match="embeds_analysis has been removed"):
            PhaseDefinition(
                drain="build",
                role="execution",
                embeds_analysis=True,
                transitions=PhaseTransition(on_success="done"),
            )
