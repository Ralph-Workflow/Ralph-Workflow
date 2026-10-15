"""AgentBudgetRegistry and RecoveryController durability tests.

Split from :mod:`tests.test_retry_budget_durability` so neither module
carries more than one top-level test class (repo-structure policy).
"""

from __future__ import annotations

from ralph.recovery.agent_budget_registry import AgentBudgetRegistry
from ralph.recovery.classifier import ClassifiedFailure, FailureCategory
from ralph.recovery.seed_budget_registry import seed_budget_registry


def _make_failure(
    *,
    category: FailureCategory = FailureCategory.AGENT,
    reason: str = "transient error",
    agent: str = "claude",
    phase: str = "development",
    counts: bool = True,
) -> ClassifiedFailure:
    return ClassifiedFailure(
        category,
        reason,
        agent,
        phase,
        counts,
        None,
        reason,
    )


class TestSeedBudgetRegistryWithPrior:
    """seed_budget_registry preserves consumed attempts from prior state."""

    def test_seed_preserves_consumed_budget(self) -> None:
        from ralph.policy.models import (
            AgentChainConfig,
            AgentDrainConfig,
            AgentsPolicy,
            ArtifactsPolicy,
            PhaseDefinition,
            PhaseTransition,
            PipelinePolicy,
            PolicyBundle,
            RecoveryPolicy,
        )

        phases = {
            "development": PhaseDefinition(
                drain="default",
                transitions=PhaseTransition(on_success="complete"),
            ),
        }
        chains = {
            "default": AgentChainConfig(agents=["claude", "gemini"], max_retries=3),
        }
        drains = {
            "default": AgentDrainConfig(chain="default"),
        }
        bundle = PolicyBundle(
            pipeline=PipelinePolicy(
                phases=phases,
                entry_phase="development",
                terminal_phase="complete",
                recovery=RecoveryPolicy(failed_route="failed_terminal"),
            ),
            artifacts=ArtifactsPolicy(artifacts={}),
            agents=AgentsPolicy(agent_chains=chains, agent_drains=drains),
        )

        prior = AgentBudgetRegistry().set_budget("development", "claude", max_retries=3, consumed=3)
        assert prior.is_exhausted("development", "claude")

        # Seed with prior
        seeded = seed_budget_registry(bundle, prior=prior)
        assert seeded.is_exhausted("development", "claude")
        claude_state = seeded.get("development", "claude")
        assert claude_state is not None
        assert claude_state.consumed == 3
        assert claude_state.remaining == 0

        # Unused agent starts with consumed=0
        gemini_state = seeded.get("development", "gemini")
        assert gemini_state is not None
        assert gemini_state.consumed == 0
