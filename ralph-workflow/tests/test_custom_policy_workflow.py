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

from ralph.pipeline.handoffs import resolve_post_commit_phase
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
)
from ralph.policy.render import render_explanation_ascii, render_explanation_text
from ralph.policy.validation import validate_policy_completeness


def _make_chain(name: str) -> tuple[str, AgentChainConfig]:
    return name, AgentChainConfig(agents=["fake-agent"])


def _make_drain(drain: str, chain: str) -> tuple[str, AgentDrainConfig]:
    return drain, AgentDrainConfig(chain=chain)


@pytest.fixture(scope="session")
def custom_bundle() -> PolicyBundle:
    """Build a fully custom-named PolicyBundle without any disk I/O."""
    agents = AgentsPolicy(
        agent_chains=dict([
            _make_chain("design_chain"),
            _make_chain("build_chain"),
            _make_chain("audit_chain"),
            _make_chain("sign_off_chain"),
        ]),
        agent_drains=dict([
            _make_drain("design", "design_chain"),
            _make_drain("build", "build_chain"),
            _make_drain("audit", "audit_chain"),
            _make_drain("sign_off", "sign_off_chain"),
        ]),
    )

    pipeline = PipelinePolicy(
        entry_phase="design",
        terminal_phase="done",
        loop_counters={
            "audit_round": LoopCounterConfig(default_max=2, description="Audit loop counter"),
        },
        budget_counters={
            "cycles": BudgetCounterConfig(tracks_budget=True, description="Outer cycle counter"),
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
                loop_policy=PhaseLoopPolicy(
                    max_iterations=2,
                    iteration_state_field="audit_round",
                ),
                decisions={
                    "completed": PhaseDecisionRoute(target="sign_off", reset_loop=True),
                    "request_changes": PhaseDecisionRoute(target="build", reset_loop=False),
                    "failed": PhaseDecisionRoute(target="failed", reset_loop=False),
                },
            ),
            "sign_off": PhaseDefinition(
                drain="sign_off",
                role="commit",
                transitions=PhaseTransition(
                    on_success="done",
                    on_failure="failed",
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


class TestCustomPolicyLoadsAndValidates:
    def test_custom_policy_loads_and_validates(self, custom_bundle: PolicyBundle) -> None:
        """validate_policy_completeness raises nothing for the custom-named bundle."""
        validate_policy_completeness(custom_bundle)


class TestCustomPolicyExplainRendersCustomNames:
    def test_custom_policy_explain_renders_custom_names(
        self, custom_bundle: PolicyBundle
    ) -> None:
        """ASCII and text renders contain custom phase names; default phase names are absent."""
        explanation = explain_policy(custom_bundle)
        ascii_output = render_explanation_ascii(explanation)
        text_output = render_explanation_text(explanation)

        combined = ascii_output + "\n" + text_output

        for expected_name in ("design", "build", "audit", "sign_off", "done"):
            assert expected_name in combined, f"Expected phase name '{expected_name}' in output"

        # Check that default phase names don't appear as phase headers or box labels.
        # We check "Phase: <name>" (text section) and "| <name> |" (ASCII box) patterns.
        # We do NOT check plain substrings because role labels like
        # "analysis (agent reviews output...)" legitimately contain "review".
        for absent_phase in ("planning", "development", "review"):
            assert f"Phase: {absent_phase}" not in combined, (
                f"Default phase 'Phase: {absent_phase}' must not appear in custom-policy output"
            )
            assert f"| {absent_phase} |" not in combined, (
                f"Default phase box '| {absent_phase} |' must not appear in custom-policy output"
            )


class TestCustomPolicyResolvePostCommit:
    def test_resolve_returns_design_when_cycles_remaining(
        self, custom_bundle: PolicyBundle
    ) -> None:
        """resolve_post_commit_phase returns 'design' when cycles budget > 0."""
        state = PipelineState(
            phase="sign_off",
            budget_remaining={"cycles": 2},
            outer_progress={"cycles": 0},
        )
        result = resolve_post_commit_phase(state, custom_bundle.pipeline)
        assert result == "design"

    def test_resolve_returns_done_when_cycles_exhausted(
        self, custom_bundle: PolicyBundle
    ) -> None:
        """resolve_post_commit_phase returns 'done' when cycles budget = 0 (no_review)."""
        state = PipelineState(
            phase="sign_off",
            budget_remaining={"cycles": 0},
            outer_progress={"cycles": 2},
        )
        result = resolve_post_commit_phase(state, custom_bundle.pipeline)
        assert result == "done"


class TestCustomPolicyLoopCounterDict:
    def test_loop_counter_increments_on_dict_only(self) -> None:
        """Custom loop counter writes to dict; legacy development_analysis_iteration stays 0."""
        state = PipelineState()
        updated = state.with_loop_iteration("audit_round", 1)

        assert updated.get_loop_iteration("audit_round") == 1
        assert updated.development_analysis_iteration == 0
