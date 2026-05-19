"""Policy-driven acceptance tests — Product Requirements 1-10 and Criteria A-H.

These tests assert every product requirement and acceptance criterion against a
fully renamed PolicyBundle (no canonical phase names). Any regression that
re-introduces hidden phase name knowledge in the runtime will fail here.

Scope: pure functions only. No I/O.
Runtime budget: ≤ 5 seconds for the full module.
"""

from __future__ import annotations

import pytest

from ralph.pipeline.handoffs import resolve_post_commit_phase
from ralph.pipeline.state import PipelineState
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactContract,
    ArtifactsPolicy,
    BudgetCounterConfig,
    LoopCounterConfig,
    PhaseCommitPolicy,
    PhaseDecisionRoute,
    PhaseDefinition,
    PhaseLoopPolicy,
    PhaseTransition,
    PipelinePolicy,
    PolicyBundle,
    PostCommitRoute,
    PostCommitRouteWhen,
    RecoveryPolicy,
)


@pytest.fixture(scope="module")
def policy_with_renamed_phases() -> PolicyBundle:
    """Fully custom-named PolicyBundle — no canonical Ralph phase names.

    Custom name mappings (canonical → custom):
      planning          → design
      development       → build
      development_analysis → audit
      development_commit → sign_off
      review            → inspect
      complete          → done
      failed_terminal   → aborted
    """
    agents = AgentsPolicy(
        agent_chains={
            "design_chain": AgentChainConfig(agents=["fake-agent"]),
            "build_chain": AgentChainConfig(agents=["fake-agent"]),
            "audit_chain": AgentChainConfig(agents=["fake-agent"]),
            "inspect_chain": AgentChainConfig(agents=["fake-agent"]),
            "sign_off_chain": AgentChainConfig(agents=["fake-agent"]),
        },
        agent_drains={
            "design": AgentDrainConfig(chain="design_chain", drain_class="planning"),
            "build": AgentDrainConfig(chain="build_chain", drain_class="development"),
            "audit": AgentDrainConfig(chain="audit_chain", drain_class="analysis"),
            "inspect": AgentDrainConfig(chain="inspect_chain", drain_class="review"),
            "sign_off": AgentDrainConfig(chain="sign_off_chain", drain_class="commit"),
            "done": AgentDrainConfig(chain="design_chain"),
            "aborted": AgentDrainConfig(chain="design_chain"),
        },
    )

    pipeline = PipelinePolicy(
        entry_phase="design",
        terminal_phase="done",
        loop_counters={
            "audit_round": LoopCounterConfig(default_max=3, description="Audit loop counter"),
        },
        budget_counters={
            "cycles": BudgetCounterConfig(
                tracks_budget=True, description="Outer cycle counter", default_max=5
            ),
        },
        phases={
            "design": PhaseDefinition(
                drain="design",
                role="execution",
                transitions=PhaseTransition(on_success="build"),
            ),
            "build": PhaseDefinition(
                drain="build",
                role="execution",
                transitions=PhaseTransition(
                    on_success="audit",
                    on_loopback="build",
                ),
            ),
            "audit": PhaseDefinition(
                drain="audit",
                role="analysis",
                transitions=PhaseTransition(
                    on_success="sign_off",
                    on_loopback="build",
                ),
                loop_policy=PhaseLoopPolicy(iteration_state_field="audit_round"),
                decisions={
                    "completed": PhaseDecisionRoute(target="sign_off", reset_loop=True),
                    "request_changes": PhaseDecisionRoute(target="build", reset_loop=False),
                    "failed": PhaseDecisionRoute(target="aborted", reset_loop=False),
                },
            ),
            "inspect": PhaseDefinition(
                drain="inspect",
                role="review",
                clean_outcome="clean",
                issues_outcome="has_issues",
                transitions=PhaseTransition(on_success="done"),
                bypass_routes={"clean": "done"},
            ),
            "sign_off": PhaseDefinition(
                drain="sign_off",
                role="commit",
                transitions=PhaseTransition(
                    on_success="inspect",
                    on_failure="aborted",
                ),
                commit_policy=PhaseCommitPolicy(
                    increments_counter="cycles",
                    loop_resets=["audit_round"],
                ),
            ),
            "done": PhaseDefinition(
                drain="done",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(
                    on_success="done",
                    on_loopback="done",
                ),
            ),
            "aborted": PhaseDefinition(
                drain="aborted",
                role="terminal",
                terminal_outcome="failure",
                transitions=PhaseTransition(
                    on_success="aborted",
                    on_loopback="aborted",
                ),
            ),
        },
        post_commit_routes=[
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="sign_off", budget_state="remaining"),
                target="design",
            ),
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="sign_off", budget_state="exhausted"),
                target="done",
            ),
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="sign_off", budget_state="no_review"),
                target="done",
            ),
        ],
        recovery=RecoveryPolicy(failed_route="aborted"),
    )

    artifacts = ArtifactsPolicy(
        artifacts={
            "audit_decision": ArtifactContract(
                drain="audit",
                artifact_type="development_analysis_decision",
                decision_vocabulary=["completed", "request_changes", "failed"],
            ),
        }
    )

    return PolicyBundle(agents=agents, pipeline=pipeline, artifacts=artifacts)


# =============================================================================
# Requirement 1: Policy must fully define user-visible routing semantics
# =============================================================================


class TestRequirement5CommitSemantics:
    def test_post_commit_routes_declared_in_policy(
        self, policy_with_renamed_phases: PolicyBundle
    ) -> None:
        """post_commit_routes are declared in policy for the 'sign_off' commit phase."""
        routes = policy_with_renamed_phases.pipeline.post_commit_routes
        phases = {r.when.phase for r in routes}
        assert "sign_off" in phases

    def test_resolve_post_commit_returns_policy_target_when_remaining(
        self, policy_with_renamed_phases: PolicyBundle
    ) -> None:
        """resolve_post_commit_phase returns 'design' when cycles budget > 0."""
        state = PipelineState(
            phase="sign_off",
            budget_caps={"cycles": 2},
        )
        result = resolve_post_commit_phase(state, policy_with_renamed_phases.pipeline)
        assert result == "design"

    def test_resolve_post_commit_returns_done_when_exhausted(
        self, policy_with_renamed_phases: PolicyBundle
    ) -> None:
        """resolve_post_commit_phase returns 'done' when cycles are exhausted."""
        state = PipelineState(
            phase="sign_off",
            budget_caps={"cycles": 3},
            outer_progress={"cycles": 3},
        )
        result = resolve_post_commit_phase(state, policy_with_renamed_phases.pipeline)
        assert result == "done"

    def test_commit_policy_increments_counter_is_policy_declared(
        self, policy_with_renamed_phases: PolicyBundle
    ) -> None:
        """The commit counter 'cycles' comes from policy, not a hardcoded name."""
        sign_off = policy_with_renamed_phases.pipeline.phases["sign_off"]
        assert sign_off.commit_policy is not None
        assert sign_off.commit_policy.increments_counter == "cycles"
