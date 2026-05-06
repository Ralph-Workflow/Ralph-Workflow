"""Policy-driven acceptance tests — Product Requirements 1-10 and Criteria A-H.

These tests assert every product requirement and acceptance criterion against a
fully renamed PolicyBundle (no canonical phase names). Any regression that
re-introduces hidden phase name knowledge in the runtime will fail here.

Scope: pure functions only. No I/O.
Runtime budget: ≤ 5 seconds for the full module.
"""

from __future__ import annotations

import pytest

from ralph.mcp.protocol.capability_mapping import (
    DrainClass,
    PolicyMode,
    drain_class_for_session,
    drain_to_policy_mode,
)
from ralph.pipeline.events import AnalysisDecisionEvent
from ralph.pipeline.handoffs import resolve_post_commit_phase
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
from ralph.policy.render import render_explanation_ascii, render_explanation_text
from ralph.policy.validation import PolicyValidationError, validate_policy_completeness


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


class TestRequirement1RoutingSemantics:
    def test_analysis_decision_routes_to_policy_declared_target(
        self, policy_with_renamed_phases: PolicyBundle
    ) -> None:
        """AnalysisDecisionEvent routes to the target declared in policy, not a hardcoded name."""
        state = PipelineState(phase="audit")
        new_state, _ = reduce(
            state,
            AnalysisDecisionEvent(phase="audit", decision="request_changes"),
            policy_with_renamed_phases.pipeline,
        )
        assert new_state.phase == "build", (
            "request_changes should route to 'build' per policy decisions, not any hardcoded name"
        )

    def test_analysis_completed_routes_to_sign_off(
        self, policy_with_renamed_phases: PolicyBundle
    ) -> None:
        """completed decision routes to 'sign_off' per policy, not 'development_commit'."""
        state = PipelineState(phase="audit")
        new_state, _ = reduce(
            state,
            AnalysisDecisionEvent(phase="audit", decision="completed"),
            policy_with_renamed_phases.pipeline,
        )
        assert new_state.phase == "sign_off"

    def test_all_decisions_are_policy_declared(
        self, policy_with_renamed_phases: PolicyBundle
    ) -> None:
        """Every decision for the audit phase maps to a policy-declared target."""
        audit_def = policy_with_renamed_phases.pipeline.phases["audit"]
        assert audit_def.decisions["completed"].target == "sign_off"
        assert audit_def.decisions["request_changes"].target == "build"
        assert audit_def.decisions["failed"].target == "aborted"


# =============================================================================
# Requirement 2: Policy must define phase behavior classes
# =============================================================================


class TestRequirement2PhaseBehaviorClasses:
    def test_phase_role_comes_from_policy(self, policy_with_renamed_phases: PolicyBundle) -> None:
        """The role of 'audit' is 'analysis' because the policy declares it, not the name."""
        phases = policy_with_renamed_phases.pipeline.phases
        assert phases["audit"].role == "analysis"
        assert phases["sign_off"].role == "commit"
        assert phases["build"].role == "execution"
        assert phases["done"].role == "terminal"

    def test_explain_policy_reflects_policy_declared_roles(
        self, policy_with_renamed_phases: PolicyBundle
    ) -> None:
        """explain_policy produces role labels from policy, not from phase name recognition."""
        exp = explain_policy(policy_with_renamed_phases)
        audit = next(p for p in exp.phases if p.name == "audit")
        sign_off = next(p for p in exp.phases if p.name == "sign_off")
        assert audit.role == "analysis"
        assert sign_off.role == "commit"


# =============================================================================
# Requirement 3: Workflow-level fallback behavior must be policy-owned
# =============================================================================


class TestRequirement3FallbackBehavior:
    def test_recovery_route_comes_from_policy(
        self, policy_with_renamed_phases: PolicyBundle
    ) -> None:
        """The failed_route in recovery is declared in policy."""
        recovery = policy_with_renamed_phases.pipeline.recovery
        assert recovery.failed_route is not None

    def test_explain_policy_exposes_recovery_config(
        self, policy_with_renamed_phases: PolicyBundle
    ) -> None:
        """explain_policy exposes the recovery route from policy."""
        exp = explain_policy(policy_with_renamed_phases)
        assert exp.recovery is not None
        assert exp.recovery.cycle_cap > 0


# =============================================================================
# Requirement 4: Analysis and review loop behavior must be policy-owned
# =============================================================================


class TestRequirement4AnalysisLoopBehavior:
    def test_loop_policy_declared_in_policy(self, policy_with_renamed_phases: PolicyBundle) -> None:
        """The 'audit' phase has a loop_policy declared in policy."""
        audit_def = policy_with_renamed_phases.pipeline.phases["audit"]
        assert audit_def.loop_policy is not None
        assert audit_def.loop_policy.iteration_state_field == "audit_round"
        assert policy_with_renamed_phases.pipeline.loop_counters["audit_round"].default_max == 3  # noqa: PLR2004

    def test_explain_exposes_loop_policy(self, policy_with_renamed_phases: PolicyBundle) -> None:
        """explain_policy surfaces loop policy from the policy declaration."""
        exp = explain_policy(policy_with_renamed_phases)
        audit = next(p for p in exp.phases if p.name == "audit")
        assert audit.loop_policy is not None
        assert audit.loop_policy.max_iterations == 3  # noqa: PLR2004

    def test_loop_counter_is_policy_declared(
        self, policy_with_renamed_phases: PolicyBundle
    ) -> None:
        """Loop counter 'audit_round' is declared in policy, not hardcoded."""
        assert "audit_round" in policy_with_renamed_phases.pipeline.loop_counters
        lc = policy_with_renamed_phases.pipeline.loop_counters["audit_round"]
        assert lc.default_max == 3  # noqa: PLR2004


# =============================================================================
# Requirement 5: Commit semantics must be policy-owned
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


# =============================================================================
# Requirement 6: Verification semantics must be policy-owned
# =============================================================================


class TestRequirement6VerificationSemantics:
    def _bundle_with_verification(self) -> PolicyBundle:
        from ralph.policy.models import PhaseVerificationPolicy  # noqa: PLC0415

        return PolicyBundle(
            agents=AgentsPolicy(
                agent_chains={"c": AgentChainConfig(agents=["agent"])},
                agent_drains={
                    "v_drain": AgentDrainConfig(chain="c"),
                    "done": AgentDrainConfig(chain="c"),
                    "fail": AgentDrainConfig(chain="c"),
                },
            ),
            pipeline=PipelinePolicy(
                entry_phase="gate",
                terminal_phase="done",
                phases={
                    "gate": PhaseDefinition(
                        drain="v_drain",
                        role="verification",
                        verification=PhaseVerificationPolicy(
                            kind="artifact",
                            gate_for="advancement",
                            on_failure_route="fail",
                        ),
                        transitions=PhaseTransition(on_success="done"),
                    ),
                    "done": PhaseDefinition(
                        drain="done",
                        role="terminal",
                        terminal_outcome="success",
                        transitions=PhaseTransition(on_success="done"),
                    ),
                    "fail": PhaseDefinition(
                        drain="fail",
                        role="terminal",
                        terminal_outcome="failure",
                        transitions=PhaseTransition(on_success="fail", on_loopback="fail"),
                    ),
                },
            ),
            artifacts=ArtifactsPolicy(),
        )

    def test_verification_kind_is_policy_declared(self) -> None:
        """Verification kind comes from policy declaration, not phase name."""
        bundle = self._bundle_with_verification()
        exp = explain_policy(bundle)
        gate = next(p for p in exp.phases if p.name == "gate")
        assert gate.verification is not None
        assert gate.verification.kind == "artifact"

    def test_verification_failure_route_is_policy_declared(self) -> None:
        """Verification on_failure_route comes from policy."""
        bundle = self._bundle_with_verification()
        exp = explain_policy(bundle)
        gate = next(p for p in exp.phases if p.name == "gate")
        assert gate.verification is not None
        assert gate.verification.on_failure_route == "fail"


# =============================================================================
# Requirement 7: Terminal behavior must be policy-owned
# =============================================================================


class TestRequirement7TerminalBehavior:
    def test_terminal_success_phase_declared_in_policy(
        self, policy_with_renamed_phases: PolicyBundle
    ) -> None:
        """The terminal success phase 'done' is declared in policy with role=terminal."""
        done = policy_with_renamed_phases.pipeline.phases["done"]
        assert done.role == "terminal"
        assert done.terminal_outcome == "success"

    def test_terminal_failure_phase_declared_in_policy(
        self, policy_with_renamed_phases: PolicyBundle
    ) -> None:
        """The terminal failure phase 'aborted' is declared in policy with role=terminal."""
        aborted = policy_with_renamed_phases.pipeline.phases["aborted"]
        assert aborted.role == "terminal"
        assert aborted.terminal_outcome == "failure"

    def test_only_policy_declared_terminal_phases_exist(
        self, policy_with_renamed_phases: PolicyBundle
    ) -> None:
        """No undeclared terminal phases exist; all terminals are in policy."""
        terminal_phases = [
            name
            for name, phase in policy_with_renamed_phases.pipeline.phases.items()
            if phase.role == "terminal"
        ]
        assert set(terminal_phases) == {"done", "aborted"}

    def test_explain_policy_exposes_terminal_outcomes(
        self, policy_with_renamed_phases: PolicyBundle
    ) -> None:
        """explain_policy lists terminal outcomes from policy."""
        exp = explain_policy(policy_with_renamed_phases)
        outcome_phases = {to.phase for to in exp.terminal_outcomes}
        assert "done" in outcome_phases
        assert "aborted" in outcome_phases


# =============================================================================
# Requirement 8: Parallel behavior must be policy-owned where relevant
# =============================================================================


class TestRequirement8ParallelBehavior:
    def test_has_parallelization_reflects_policy(
        self, policy_with_renamed_phases: PolicyBundle
    ) -> None:
        """has_parallelization field comes from the policy declaration per phase."""
        exp = explain_policy(policy_with_renamed_phases)
        for phase in exp.phases:
            phase_def = policy_with_renamed_phases.pipeline.phases.get(phase.name)
            expected = phase_def is not None and phase_def.parallelization is not None
            assert phase.has_parallelization == expected, (
                f"has_parallelization mismatch for phase '{phase.name}'"
            )

    def test_no_phase_has_parallelization_in_custom_bundle(
        self, policy_with_renamed_phases: PolicyBundle
    ) -> None:
        """Custom bundle declares no parallelization — all phases correctly report False."""
        exp = explain_policy(policy_with_renamed_phases)
        for phase in exp.phases:
            assert not phase.has_parallelization


# =============================================================================
# Requirement 9: Artifact and evidence expectations must be policy-owned
# =============================================================================


class TestRequirement9ArtifactExpectations:
    def test_artifact_contract_declared_in_policy(
        self, policy_with_renamed_phases: PolicyBundle
    ) -> None:
        """ArtifactsPolicy contains the artifact contract declared for 'audit' drain."""
        artifacts = policy_with_renamed_phases.artifacts
        assert "audit_decision" in artifacts.artifacts
        ac = artifacts.artifacts["audit_decision"]
        assert ac.drain == "audit"
        assert "completed" in ac.decision_vocabulary
        assert "request_changes" in ac.decision_vocabulary

    def test_decision_vocabulary_covers_all_audit_decisions(
        self, policy_with_renamed_phases: PolicyBundle
    ) -> None:
        """The artifact's decision_vocabulary covers all decisions declared in the phase."""
        audit_def = policy_with_renamed_phases.pipeline.phases["audit"]
        ac = policy_with_renamed_phases.artifacts.artifacts["audit_decision"]
        for decision_name in audit_def.decisions:
            assert decision_name in ac.decision_vocabulary, (
                f"Decision '{decision_name}' not covered by artifact vocabulary"
            )


# =============================================================================
# Requirement 10: Recovery behavior must be policy-owned at the workflow level
# =============================================================================


class TestRequirement10RecoveryBehavior:
    def test_recovery_config_comes_from_policy(
        self, policy_with_renamed_phases: PolicyBundle
    ) -> None:
        """The recovery config (cycle_cap, preserve_session_on_categories) is from policy."""
        recovery = policy_with_renamed_phases.pipeline.recovery
        assert recovery.cycle_cap >= 0

    def test_failed_route_points_to_declared_terminal_phase(
        self, policy_with_renamed_phases: PolicyBundle
    ) -> None:
        """recovery.failed_route points to a policy-declared terminal failure phase."""
        recovery = policy_with_renamed_phases.pipeline.recovery
        failed_route = recovery.failed_route
        phases = policy_with_renamed_phases.pipeline.phases
        # The failed route should point to a terminal failure phase or be the legacy alias
        if failed_route in phases:
            assert phases[failed_route].role == "terminal"
            assert phases[failed_route].terminal_outcome == "failure"

    def test_explain_exposes_recovery_terminal_route(
        self, policy_with_renamed_phases: PolicyBundle
    ) -> None:
        """explain_policy surfaces the terminal_recovery_route from policy."""
        exp = explain_policy(policy_with_renamed_phases)
        assert exp.recovery is not None
        assert exp.recovery.terminal_recovery_route is not None


# =============================================================================
# Acceptance A: Policy is the single source of workflow truth
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


# =============================================================================
# Acceptance B: Built-in phase names are not carrying secret behavior
# =============================================================================


class TestAcceptanceBBuiltInNames:
    def test_canonical_phase_names_absent_from_custom_bundle_render(
        self, policy_with_renamed_phases: PolicyBundle
    ) -> None:
        """No canonical Ralph phase names appear as phase headers in the custom bundle render."""
        exp = explain_policy(policy_with_renamed_phases)
        ascii_out = render_explanation_ascii(exp)
        text_out = render_explanation_text(exp)
        combined = ascii_out + "\n" + text_out
        for absent in (
            "planning",
            "development_analysis",
            "review_analysis",
            "development_commit",
            "review_commit",
        ):
            # Check box labels and phase headers specifically
            assert f"| {absent} |" not in combined, (
                f"Canonical name '| {absent} |' must not appear in custom-policy render"
            )
            assert f"Phase: {absent}" not in combined, (
                f"Canonical name 'Phase: {absent}' must not appear in custom-policy render"
            )

    def test_custom_drain_resolves_without_canonical_name(
        self, policy_with_renamed_phases: PolicyBundle
    ) -> None:
        """Custom drain 'audit' resolves via explicit drain_class, not by name matching."""
        agents_policy = policy_with_renamed_phases.agents
        dc = drain_class_for_session("audit", agents_policy)
        assert dc == DrainClass.ANALYSIS

    def test_custom_drain_policy_mode_resolves(
        self, policy_with_renamed_phases: PolicyBundle
    ) -> None:
        """drain_to_policy_mode resolves 'sign_off' via explicit drain_class='commit'."""
        agents_policy = policy_with_renamed_phases.agents
        pm = drain_to_policy_mode("sign_off", agents_policy)
        assert pm == PolicyMode.COMMIT


# =============================================================================
# Acceptance C: Changing workflow behavior is primarily a policy exercise
# =============================================================================


class TestAcceptanceCChangingBehavior:
    def test_different_policy_produces_different_routing(
        self, policy_with_renamed_phases: PolicyBundle
    ) -> None:
        """Two different policies route the same decision to different targets."""
        policy_a = policy_with_renamed_phases.pipeline

        # Build a variant bundle where request_changes routes to "inspect" instead
        alt_pipeline = PipelinePolicy(
            entry_phase="design",
            terminal_phase="done",
            loop_counters={
                "audit_round": LoopCounterConfig(default_max=2, description="alt audit"),
            },
            budget_counters={
                "cycles": BudgetCounterConfig(
                    tracks_budget=True, description="alt cycles", default_max=5
                ),
            },
            phases={
                "design": PhaseDefinition(
                    drain="design_alt",
                    role="execution",
                    transitions=PhaseTransition(on_success="audit"),
                ),
                "audit": PhaseDefinition(
                    drain="audit_alt",
                    role="analysis",
                    transitions=PhaseTransition(on_success="done", on_loopback="design"),
                    loop_policy=PhaseLoopPolicy(iteration_state_field="audit_round"),
                    decisions={
                        "completed": PhaseDecisionRoute(target="done", reset_loop=True),
                        "request_changes": PhaseDecisionRoute(target="design", reset_loop=False),
                        "failed": PhaseDecisionRoute(target="done", reset_loop=False),
                    },
                ),
                "done": PhaseDefinition(
                    drain="done_alt",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(on_success="done"),
                ),
            },
        )

        state = PipelineState(phase="audit")
        result_a, _ = reduce(
            state,
            AnalysisDecisionEvent(phase="audit", decision="request_changes"),
            policy_a,
        )
        result_b, _ = reduce(
            state,
            AnalysisDecisionEvent(phase="audit", decision="request_changes"),
            alt_pipeline,
        )
        # Policy A routes to "build"; policy B routes to "design"
        assert result_a.phase == "build"
        assert result_b.phase == "design"
        assert result_a.phase != result_b.phase


# =============================================================================
# Acceptance D: Incomplete policy is rejected, not padded with hidden semantics
# =============================================================================


class TestAcceptanceDIncompleteRejected:
    def test_bundle_missing_analysis_loop_policy_fails_validation(self) -> None:
        """A policy with an analysis-role phase but no loop_policy fails validation."""
        # Artifact contract satisfies pydantic's analysis-phase check;
        # missing loop_policy is what validate_policy_completeness must catch.
        bundle = PolicyBundle(
            agents=AgentsPolicy(
                agent_chains={"c": AgentChainConfig(agents=["agent"])},
                agent_drains={
                    "work_drain": AgentDrainConfig(chain="c"),
                    "done": AgentDrainConfig(chain="c"),
                    "fail": AgentDrainConfig(chain="c"),
                },
            ),
            pipeline=PipelinePolicy(
                entry_phase="work",
                terminal_phase="done",
                phases={
                    "work": PhaseDefinition(
                        drain="work_drain",
                        role="analysis",
                        transitions=PhaseTransition(on_success="done"),
                        decisions={
                            "ok": PhaseDecisionRoute(target="done", reset_loop=True),
                        },
                        # Missing: loop_policy — required for analysis role
                    ),
                    "done": PhaseDefinition(
                        drain="done",
                        role="terminal",
                        terminal_outcome="success",
                        transitions=PhaseTransition(on_success="done"),
                    ),
                    "fail": PhaseDefinition(
                        drain="fail",
                        role="terminal",
                        terminal_outcome="failure",
                        transitions=PhaseTransition(on_success="fail", on_loopback="fail"),
                    ),
                },
            ),
            artifacts=ArtifactsPolicy(
                artifacts={
                    "work_result": ArtifactContract(
                        drain="work_drain",
                        artifact_type="development_analysis_decision",
                        decision_vocabulary=["ok"],
                    ),
                }
            ),
        )
        with pytest.raises(PolicyValidationError):
            validate_policy_completeness(bundle)

    def test_valid_custom_bundle_passes_validation(
        self, policy_with_renamed_phases: PolicyBundle
    ) -> None:
        """The custom renamed bundle passes validate_policy_completeness."""
        validate_policy_completeness(policy_with_renamed_phases)


# =============================================================================
# Acceptance E: Documentation can honestly claim policy-driven orchestration
# =============================================================================


class TestAcceptanceEDocumentation:
    def test_custom_drain_class_overrides_name_inference(
        self, policy_with_renamed_phases: PolicyBundle
    ) -> None:
        """Drain 'design' has explicit drain_class='planning'; resolution uses policy."""
        agents_policy = policy_with_renamed_phases.agents
        # 'design' would NOT match via substring because 'planning' is not in 'design'
        dc = drain_class_for_session("design", agents_policy)
        assert dc == DrainClass.PLANNING

    def test_custom_bundle_explains_without_canonical_knowledge(
        self, policy_with_renamed_phases: PolicyBundle
    ) -> None:
        """explain_policy works on the custom bundle without any built-in phase knowledge."""
        exp = explain_policy(policy_with_renamed_phases)
        assert exp.entry_phase == "design"
        assert exp.terminal_phase == "done"
        assert len(exp.phases) == 7  # noqa: PLR2004


# =============================================================================
# Acceptance F: Users can inspect, explain, and visualize active behavior
# =============================================================================


class TestAcceptanceFInspectAndVisualize:
    def test_ascii_render_contains_entry_phase_box(
        self, policy_with_renamed_phases: PolicyBundle
    ) -> None:
        """render_explanation_ascii contains the entry phase in a box."""
        exp = explain_policy(policy_with_renamed_phases)
        ascii_out = render_explanation_ascii(exp)
        assert "design" in ascii_out

    def test_ascii_render_contains_loopback_annotation(
        self, policy_with_renamed_phases: PolicyBundle
    ) -> None:
        """render_explanation_ascii marks loopback routes visually."""
        exp = explain_policy(policy_with_renamed_phases)
        ascii_out = render_explanation_ascii(exp)
        assert "loopback" in ascii_out.lower() or "<<" in ascii_out

    def test_text_render_explains_routing_decisions(
        self, policy_with_renamed_phases: PolicyBundle
    ) -> None:
        """render_explanation_text includes decision routing explanation sentences."""
        exp = explain_policy(policy_with_renamed_phases)
        text = render_explanation_text(exp)
        assert "Explanation:" in text
        assert "request_changes" in text

    def test_text_render_includes_post_commit_route_sentences(
        self, policy_with_renamed_phases: PolicyBundle
    ) -> None:
        """render_explanation_text includes post_commit_route sentences."""
        exp = explain_policy(policy_with_renamed_phases)
        text = render_explanation_text(exp)
        assert "Explanation: after commit phase" in text

    def test_text_render_includes_parallel_rejection_sentences(
        self, policy_with_renamed_phases: PolicyBundle
    ) -> None:
        """render_explanation_text includes parallel execution rejection sentences."""
        exp = explain_policy(policy_with_renamed_phases)
        text = render_explanation_text(exp)
        assert "Explanation: parallel execution is rejected" in text


# =============================================================================
# Acceptance G: Documentation and tooling make the workflow visually legible
# =============================================================================


class TestAcceptanceGVisualLegibility:
    def test_ascii_render_has_phase_boxes(self, policy_with_renamed_phases: PolicyBundle) -> None:
        """ASCII render contains phase boxes for key phases."""
        exp = explain_policy(policy_with_renamed_phases)
        ascii_out = render_explanation_ascii(exp)
        for phase_name in ("design", "build", "audit", "sign_off"):
            assert phase_name in ascii_out, f"Phase '{phase_name}' missing from ASCII render"

    def test_text_render_has_phases_section(self, policy_with_renamed_phases: PolicyBundle) -> None:
        """Text render has a PHASES section listing all phases."""
        exp = explain_policy(policy_with_renamed_phases)
        text = render_explanation_text(exp)
        assert "PHASES" in text

    def test_text_render_has_loop_counters_section(
        self, policy_with_renamed_phases: PolicyBundle
    ) -> None:
        """Text render has a LOOP COUNTERS section for policy-declared counters."""
        exp = explain_policy(policy_with_renamed_phases)
        text = render_explanation_text(exp)
        assert "LOOP COUNTERS" in text
        assert "audit_round" in text

    def test_text_render_has_budget_counters_section(
        self, policy_with_renamed_phases: PolicyBundle
    ) -> None:
        """Text render has a BUDGET COUNTERS section for policy-declared counters."""
        exp = explain_policy(policy_with_renamed_phases)
        text = render_explanation_text(exp)
        assert "BUDGET COUNTERS" in text
        assert "cycles" in text


# =============================================================================
# Acceptance H: Policy stays focused on workflow meaning, not runtime plumbing
# =============================================================================


class TestAcceptanceHPolicyVsPlumbing:
    def test_text_render_does_not_expose_internal_plumbing(
        self, policy_with_renamed_phases: PolicyBundle
    ) -> None:
        """render_explanation_text doesn't expose internal mechanics like watchdog, auth, etc."""
        exp = explain_policy(policy_with_renamed_phases)
        text = render_explanation_text(exp)
        plumbing_terms = ["watchdog", "subprocess", "auth_failure", "connectivity"]
        for term in plumbing_terms:
            assert term not in text.lower(), (
                f"Internal plumbing term '{term}' must not appear in policy explanation"
            )

    def test_policy_contains_workflow_meaning(
        self, policy_with_renamed_phases: PolicyBundle
    ) -> None:
        """Policy contains workflow-level concepts: phases, routes, decisions, commit."""
        exp = explain_policy(policy_with_renamed_phases)
        text = render_explanation_text(exp)
        assert "On success" in text
        assert "commit" in text.lower()
        assert "analysis" in text.lower()

    def test_policy_validation_covers_workflow_semantics_not_plumbing(
        self, policy_with_renamed_phases: PolicyBundle
    ) -> None:
        """validate_policy_completeness validates workflow semantics (roles, routes, decisions)."""
        validate_policy_completeness(policy_with_renamed_phases)
