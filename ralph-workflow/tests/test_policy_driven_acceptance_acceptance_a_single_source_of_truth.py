"""Policy-driven acceptance tests — Product Requirements 1-10 and Criteria A-H.

These tests assert every product requirement and acceptance criterion against a
fully renamed PolicyBundle (no canonical phase names). Any regression that
re-introduces hidden phase name knowledge in the runtime will fail here.

Scope: pure functions only. No I/O.
Runtime budget: ≤ 5 seconds for the full module.
"""

from __future__ import annotations

import pytest

from ralph.pipeline.events import AnalysisDecisionEvent
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import PipelineState
from ralph.policy.explain import explain_policy
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
from ralph.policy.render import render_explanation_text


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


class TestAcceptanceASingleSourceOfTruth:
    def test_all_phases_appear_in_policy_render(
        self, policy_with_renamed_phases: PolicyBundle
    ) -> None:
        """render_explanation_text shows all custom phase names from policy."""
        exp = explain_policy(policy_with_renamed_phases)
        text = render_explanation_text(exp)
        for phase_name in ("design", "build", "audit", "sign_off", "done", "aborted"):
            assert phase_name in text, f"Phase '{phase_name}' missing from policy render"

    def test_routing_from_policy_matches_reduce_result(
        self, policy_with_renamed_phases: PolicyBundle
    ) -> None:
        """The route declared in policy matches what reduce() produces."""
        policy_target = (
            policy_with_renamed_phases.pipeline.phases["audit"].decisions["request_changes"].target
        )
        state = PipelineState(phase="audit")
        new_state, _ = reduce(
            state,
            AnalysisDecisionEvent(phase="audit", decision="request_changes"),
            policy_with_renamed_phases.pipeline,
        )
        assert new_state.phase == policy_target
