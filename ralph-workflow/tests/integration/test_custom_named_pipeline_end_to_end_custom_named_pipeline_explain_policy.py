from __future__ import annotations

import pytest

from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.explain import explain_policy
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactContract,
    ArtifactsPolicy,
    BudgetCounterConfig,
    GroupPolicyBlock,
    IndividualPolicyBlock,
    LifecyclePhasePolicy,
    LoopCounterConfig,
    PhaseCommitPolicy,
    PhaseDecisionRoute,
    PhaseDefinition,
    PhaseLoopPolicy,
    PhaseParallelization,
    PhaseTransition,
    PipelinePolicy,
    PolicyBundle,
    PostCommitRoute,
    PostCommitRouteWhen,
    RecoveryPolicy,
)
from ralph.policy.render import render_explanation_ascii, render_explanation_text

_AUDIT_MAX = 2
_CYCLE_CAP = 5

CANONICAL_PHASE_NAMES = frozenset(
    {
        "planning",
        "development",
        "development_analysis",
        "development_commit",
        "review",
        "review_analysis",
        "review_commit",
        "fix",
        "complete",
        "failed",
    }
)


def _build_custom_bundle() -> PolicyBundle:
    pipeline = PipelinePolicy(
        entry_block="cycle_block",
        entry_phase="design",
        terminal_phase="done",
        blocks={
            "cycle_block": GroupPolicyBlock(
                child_blocks=["design", "build", "audit", "sign_off", "done", "rejected"],
                completion_block="sign_off",
                increments_counter="cycles",
                loop_resets=["audit_round"],
            ),
            "design": IndividualPolicyBlock(
                phase_name="design",
                phase=PhaseDefinition(
                    drain="design",
                    role="execution",
                    transitions=PhaseTransition(on_success="build"),
                ),
            ),
            "build": IndividualPolicyBlock(
                phase_name="build",
                phase=PhaseDefinition(
                    drain="build",
                    role="execution",
                    transitions=PhaseTransition(on_success="audit"),
                    parallelization=PhaseParallelization(
                        max_parallel_workers=3,
                        max_work_units=9,
                        require_allowed_directories=True,
                        post_fanout_verification=False,
                    ),
                ),
            ),
            "audit": IndividualPolicyBlock(
                phase_name="audit",
                phase=PhaseDefinition(
                    drain="audit",
                    role="analysis",
                    transitions=PhaseTransition(on_success="sign_off", on_loopback="build"),
                    loop_policy=PhaseLoopPolicy(iteration_state_field="audit_round"),
                    decisions={
                        "approved": PhaseDecisionRoute(target="sign_off", reset_loop=True),
                        "request_changes": PhaseDecisionRoute(target="build", reset_loop=False),
                        "blocked": PhaseDecisionRoute(target="rejected", reset_loop=False),
                    },
                ),
            ),
            "sign_off": IndividualPolicyBlock(
                phase_name="sign_off",
                phase=PhaseDefinition(
                    drain="sign_off",
                    role="commit",
                    transitions=PhaseTransition(on_success="done", on_failure="rejected"),
                    commit_policy=PhaseCommitPolicy(
                        increments_counter="cycles",
                        loop_resets=["audit_round"],
                    ),
                ),
            ),
            "done": IndividualPolicyBlock(
                phase_name="done",
                phase=PhaseDefinition(
                    drain="done",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(on_success="done", on_loopback="done"),
                ),
            ),
            "rejected": IndividualPolicyBlock(
                phase_name="rejected",
                phase=PhaseDefinition(
                    drain="rejected",
                    role="terminal",
                    terminal_outcome="failure",
                    transitions=PhaseTransition(on_success="rejected", on_loopback="rejected"),
                ),
            ),
        },
        lifecycle_phases={
            "sign_off": LifecyclePhasePolicy(
                lifecycle_name="cycle_block",
                completion_block="sign_off",
                increments_counter="cycles",
                loop_resets=["audit_round"],
            )
        },
        loop_counters={
            "audit_round": LoopCounterConfig(
                default_max=_AUDIT_MAX,
                description="Audit iteration counter",
            ),
        },
        budget_counters={
            "cycles": BudgetCounterConfig(
                default_max=_CYCLE_CAP,
                tracks_budget=True,
                description="Outer iteration budget",
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
                transitions=PhaseTransition(on_success="audit"),
                parallelization=PhaseParallelization(
                    max_parallel_workers=3,
                    max_work_units=9,
                    require_allowed_directories=True,
                    post_fanout_verification=False,
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
                    "approved": PhaseDecisionRoute(target="sign_off", reset_loop=True),
                    "request_changes": PhaseDecisionRoute(target="build", reset_loop=False),
                    "blocked": PhaseDecisionRoute(target="rejected", reset_loop=False),
                },
            ),
            "sign_off": PhaseDefinition(
                drain="sign_off",
                role="commit",
                transitions=PhaseTransition(on_success="done", on_failure="rejected"),
                commit_policy=PhaseCommitPolicy(
                    increments_counter="cycles",
                    loop_resets=["audit_round"],
                ),
            ),
            "done": PhaseDefinition(
                drain="done",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(on_success="done", on_loopback="done"),
            ),
            "rejected": PhaseDefinition(
                drain="rejected",
                role="terminal",
                terminal_outcome="failure",
                transitions=PhaseTransition(on_success="rejected", on_loopback="rejected"),
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
        recovery=RecoveryPolicy(failed_route="rejected"),
    )

    agents = AgentsPolicy(
        agent_chains={"main": AgentChainConfig(agents=["claude"])},
        agent_drains={
            "design": AgentDrainConfig(chain="main"),
            "build": AgentDrainConfig(chain="main"),
            "audit": AgentDrainConfig(chain="main"),
            "sign_off": AgentDrainConfig(chain="main"),
            "done": AgentDrainConfig(chain="main"),
            "rejected": AgentDrainConfig(chain="main"),
        },
    )

    artifacts = ArtifactsPolicy(
        artifacts={
            "audit_decision": ArtifactContract(
                drain="audit",
                artifact_type="development_analysis_decision",
                decision_vocabulary=["approved", "request_changes", "blocked"],
            ),
        }
    )

    return PolicyBundle(agents=agents, pipeline=pipeline, artifacts=artifacts)


def _initial_state() -> PipelineState:
    return PipelineState(
        phase="design",
        phase_chains={
            "design": AgentChainState(agents=["claude"]),
            "build": AgentChainState(agents=["claude"]),
            "audit": AgentChainState(agents=["claude"]),
            "sign_off": AgentChainState(agents=["claude"]),
        },
    )


@pytest.fixture(scope="module")
def custom_bundle() -> PolicyBundle:
    return _build_custom_bundle()


class TestCustomNamedPipelineExplainPolicy:
    def test_ascii_contains_custom_phase_names(self, custom_bundle: PolicyBundle) -> None:
        explanation = explain_policy(custom_bundle)
        output = render_explanation_ascii(explanation)

        for name in ("design", "build", "audit", "sign_off", "done", "rejected"):
            assert name in output, f"Expected custom phase name '{name}' in ASCII output"

    def test_ascii_excludes_canonical_phase_names(self, custom_bundle: PolicyBundle) -> None:
        explanation = explain_policy(custom_bundle)
        output = render_explanation_ascii(explanation)

        canonical_box_names = {
            "planning",
            "development",
            "review",
            "fix",
            "development_analysis",
            "development_commit",
            "review_analysis",
            "review_commit",
        }
        for name in canonical_box_names:
            assert f"| {name}" not in output, (
                f"Canonical phase name '{name}' appeared as a phase box in custom pipeline output"
            )

    def test_ascii_fanout_annotation_present(self, custom_bundle: PolicyBundle) -> None:
        explanation = explain_policy(custom_bundle)
        output = render_explanation_ascii(explanation)
        assert ">>> FAN_OUT" in output

    def test_ascii_loopback_annotation_present(self, custom_bundle: PolicyBundle) -> None:
        explanation = explain_policy(custom_bundle)
        output = render_explanation_ascii(explanation)
        assert "<<==[loopback]==" in output

    def test_ascii_terminal_markers_present(self, custom_bundle: PolicyBundle) -> None:
        explanation = explain_policy(custom_bundle)
        output = render_explanation_ascii(explanation)
        assert "==SUCCESS==>" in output
        assert "==FAILURE==>" in output

    def test_text_explanation_preserves_custom_block_and_lifecycle_metadata(
        self, custom_bundle: PolicyBundle
    ) -> None:
        explanation = explain_policy(custom_bundle)
        output = render_explanation_text(explanation)

        assert "Entry block" in output
        assert "cycle_block" in output
        assert "LIFECYCLE COMPLETION" in output
        assert "sign_off" in output
        for canonical in (
            "planning",
            "development",
            "development_analysis",
            "development_commit",
            "review_analysis",
            "review_commit",
        ):
            assert f"Phase: {canonical}" not in output
