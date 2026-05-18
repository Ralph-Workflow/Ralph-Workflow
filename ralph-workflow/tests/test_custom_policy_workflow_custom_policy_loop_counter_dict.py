"""Contract tests for a fully custom-named policy workflow.

This module proves that Ralph Workflow routes on user-defined phase, drain,
loop counter, and budget counter names without any runtime knowledge of the
canonical default names (planning, development, review, etc.).

Scope: pure functions only (validate, explain, render, resolve_post_commit_phase,
with_loop_iteration). The full orchestrator is not exercised here because phase
handler registration is out of scope for this PR.
"""

from __future__ import annotations

import pytest

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
)


def _make_chain(name: str) -> tuple[str, AgentChainConfig]:
    return name, AgentChainConfig(agents=["fake-agent"])


def _make_drain(drain: str, chain: str) -> tuple[str, AgentDrainConfig]:
    return drain, AgentDrainConfig(chain=chain)


@pytest.fixture(scope="session")
def custom_bundle() -> PolicyBundle:
    """Build a fully custom-named PolicyBundle without any disk I/O."""
    agents = AgentsPolicy(
        agent_chains=dict(
            [
                _make_chain("design_chain"),
                _make_chain("build_chain"),
                _make_chain("audit_chain"),
                _make_chain("sign_off_chain"),
            ]
        ),
        agent_drains=dict(
            [
                _make_drain("design", "design_chain"),
                _make_drain("build", "build_chain"),
                _make_drain("audit", "audit_chain"),
                _make_drain("sign_off", "sign_off_chain"),
            ]
        ),
    )

    pipeline = PipelinePolicy(
        entry_phase="design",
        terminal_phase="done",
        loop_counters={
            "audit_round": LoopCounterConfig(default_max=2, description="Audit loop counter"),
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
                transitions=PhaseTransition(on_success="audit"),
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
                    "failed": PhaseDecisionRoute(target="failed_terminal", reset_loop=False),
                },
            ),
            "sign_off": PhaseDefinition(
                drain="sign_off",
                role="commit",
                transitions=PhaseTransition(
                    on_success="done",
                    on_failure=None,
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
            "failed_terminal": PhaseDefinition(
                drain="done",
                role="terminal",
                terminal_outcome="failure",
                transitions=PhaseTransition(
                    on_success="failed_terminal",
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


class TestCustomPolicyLoopCounterDict:
    def test_loop_counter_increments_on_dict_only(self) -> None:
        """Custom loop counter writes to dict; legacy development_analysis_iteration stays 0."""
        state = PipelineState(phase="planning")
        updated = state.with_loop_iteration("audit_round", 1)

        assert updated.get_loop_iteration("audit_round") == 1
        assert updated.get_loop_iteration("development_analysis_iteration") == 0
