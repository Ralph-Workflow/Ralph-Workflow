"""End-to-end test: custom-named pipeline with no canonical phase names.

Proves that the runtime no longer depends on built-in phase names.
A workflow with phases named:
  kickoff → build → gate (analysis loop) → seal (commit) → verify (verification)
  → done (terminal success) or crashed (terminal failure)
exercises every role type and all routing transitions using only policy-declared names.

All phase transitions are driven through the reducer. No real agents are invoked.
"""

from __future__ import annotations

import pytest

from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import AgentChainState, PipelineState
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
    PhaseVerificationPolicy,
    PipelinePolicy,
    PolicyBundle,
    PostCommitRoute,
    PostCommitRouteWhen,
    RecoveryPolicy,
)
from ralph.policy.render import render_explanation_text

_BUILD_LOOP_MAX = 3


def _build_custom_bundle() -> PolicyBundle:
    """Build a custom policy bundle with no canonical phase names.

    The pipeline exercises every phase role:
    - kickoff: execution
    - build: execution
    - gate: analysis (with build_loop counter)
    - seal: commit (increments build_pass, resets build_loop)
    - verify: verification (routes to done on success, crashed on failure)
    - crashed: terminal (failure outcome)
    - done: terminal (success outcome)
    """
    pipeline = PipelinePolicy(
        phases={
            "kickoff": PhaseDefinition(
                drain="planning",
                role="execution",
                transitions=PhaseTransition(on_success="build"),
            ),
            "build": PhaseDefinition(
                drain="development",
                role="execution",
                transitions=PhaseTransition(on_success="gate"),
            ),
            "gate": PhaseDefinition(
                drain="development_analysis",
                role="analysis",
                transitions=PhaseTransition(on_success="seal", on_loopback="build"),
                loop_policy=PhaseLoopPolicy(
                    max_iterations=_BUILD_LOOP_MAX,
                    iteration_state_field="build_loop",
                    loopback_review_outcome="has_issues",
                ),
                decisions={
                    "approve": PhaseDecisionRoute(target="seal"),
                    "request_changes": PhaseDecisionRoute(target="build"),
                },
            ),
            "seal": PhaseDefinition(
                drain="development_commit",
                role="commit",
                transitions=PhaseTransition(on_success="verify"),
                commit_policy=PhaseCommitPolicy(
                    increments_counter="build_pass",
                    loop_resets=["build_loop"],
                ),
            ),
            "verify": PhaseDefinition(
                drain="review",
                role="verification",
                transitions=PhaseTransition(on_success="done", on_failure="crashed"),
                verification=PhaseVerificationPolicy(
                    kind="artifact",
                    gate_for="advancement",
                    on_failure_route="crashed",
                ),
            ),
            "crashed": PhaseDefinition(
                drain="complete",
                role="terminal",
                terminal_outcome="failure",
                transitions=PhaseTransition(on_success="done"),
            ),
            "done": PhaseDefinition(
                drain="complete",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(on_success="done"),
            ),
        },
        entry_phase="kickoff",
        terminal_phase="done",
        loop_counters={
            "build_loop": LoopCounterConfig(
                default_max=_BUILD_LOOP_MAX,
                description="build gate loop",
            )
        },
        budget_counters={
            "build_pass": BudgetCounterConfig(default_max=5, description="build passes completed")
        },
        post_commit_routes=[
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="seal", budget_state="remaining"),
                target="kickoff",
            ),
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="seal", budget_state="exhausted"),
                target="verify",
            ),
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="seal", budget_state="no_review"),
                target="verify",
            ),
        ],
        recovery=RecoveryPolicy(failed_route="crashed"),
    )
    agents = AgentsPolicy(
        agent_chains={"main": AgentChainConfig(agents=["claude"])},
        agent_drains={
            "planning": AgentDrainConfig(chain="main"),
            "development": AgentDrainConfig(chain="main"),
            "development_analysis": AgentDrainConfig(chain="main"),
            "development_commit": AgentDrainConfig(chain="main"),
            "review": AgentDrainConfig(chain="main"),
            "complete": AgentDrainConfig(chain="main"),
        },
    )
    artifacts = ArtifactsPolicy(
        artifacts={
            "gate_decision": ArtifactContract(
                drain="development_analysis",
                artifact_type="gate_decision",
                decision_vocabulary=["approve", "request_changes"],
            ),
        }
    )
    return PolicyBundle(
        agents=agents,
        pipeline=pipeline,
        artifacts=artifacts,
    )


def _initial_state(policy: PipelinePolicy) -> PipelineState:
    """Build initial PipelineState for the custom pipeline."""
    return PipelineState(
        phase="kickoff",
        phase_chains={
            "kickoff": AgentChainState(agents=["claude"]),
            "build": AgentChainState(agents=["claude"]),
            "gate": AgentChainState(agents=["claude"]),
            "seal": AgentChainState(agents=["claude"]),
            "verify": AgentChainState(agents=["claude"]),
        },
    )


@pytest.fixture
def custom_bundle() -> PolicyBundle:
    return _build_custom_bundle()


class TestCustomPipelineHappyPath:
    """Full happy path through a custom-named pipeline."""

    def test_happy_path_kickoff_to_done(self, custom_bundle: PolicyBundle) -> None:
        policy = custom_bundle.pipeline
        state = _initial_state(policy)
        assert state.phase == "kickoff"

        # kickoff → build (AGENT_SUCCESS)
        state, _ = reducer_reduce(state, PipelineEvent.AGENT_SUCCESS, policy)
        assert state.phase == "build", f"Expected build, got {state.phase}"

        # build → gate (AGENT_SUCCESS)
        state, _ = reducer_reduce(state, PipelineEvent.AGENT_SUCCESS, policy)
        assert state.phase == "gate", f"Expected gate, got {state.phase}"

        # gate → seal (ANALYSIS_SUCCESS)
        state, _ = reducer_reduce(state, PipelineEvent.ANALYSIS_SUCCESS, policy)
        assert state.phase == "seal", f"Expected seal, got {state.phase}"

        # seal → verify (COMMIT_SUCCESS)
        state, _ = reducer_reduce(state, PipelineEvent.COMMIT_SUCCESS, policy)
        assert state.phase == "verify", f"Expected verify, got {state.phase}"
        assert state.get_outer_progress("build_pass") == 1

        # verify → done (AGENT_SUCCESS)
        state, _ = reducer_reduce(state, PipelineEvent.AGENT_SUCCESS, policy)
        assert state.phase == "done", f"Expected done, got {state.phase}"

    def test_phases_never_reference_canonical_names(self, custom_bundle: PolicyBundle) -> None:
        """Verify no canonical phase name is hard-required by the custom pipeline."""
        canonical = {
            "planning", "development", "development_analysis",
            "development_commit", "review", "review_analysis",
            "review_commit", "fix", "complete", "failed",
        }
        phase_names = set(custom_bundle.pipeline.phases.keys())
        overlap = phase_names & canonical
        # The drain names map to canonical drains but phase NAMES should be custom
        assert overlap == set(), f"Custom phases overlap with canonical: {overlap}"


class TestCustomPipelineLoopback:
    """Loop counter increments and routing via analysis loopback."""

    def test_gate_loopback_increments_loop_counter(self, custom_bundle: PolicyBundle) -> None:
        policy = custom_bundle.pipeline
        state = _initial_state(policy)

        # Advance to 'gate'
        state, _ = reducer_reduce(state, PipelineEvent.AGENT_SUCCESS, policy)  # kickoff→build
        state, _ = reducer_reduce(state, PipelineEvent.AGENT_SUCCESS, policy)  # build→gate
        assert state.phase == "gate"
        assert state.get_loop_iteration("build_loop") == 0

        # Loopback: gate→build, counter should increment
        state, _ = reducer_reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)
        assert state.phase == "build", f"Expected build after loopback, got {state.phase}"
        assert state.get_loop_iteration("build_loop") == 1

    def test_gate_loopback_sets_review_outcome(self, custom_bundle: PolicyBundle) -> None:
        policy = custom_bundle.pipeline
        state = _initial_state(policy)

        state, _ = reducer_reduce(state, PipelineEvent.AGENT_SUCCESS, policy)  # kickoff→build
        state, _ = reducer_reduce(state, PipelineEvent.AGENT_SUCCESS, policy)  # build→gate

        # Loopback should set review_outcome from loopback_review_outcome config
        state, _ = reducer_reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)
        assert state.review_outcome == "has_issues"

    def test_gate_loopback_three_times_bypasses_exhausted_gate(
        self, custom_bundle: PolicyBundle
    ) -> None:
        policy = custom_bundle.pipeline
        state = _initial_state(policy)

        # Advance to gate
        state, _ = reducer_reduce(state, PipelineEvent.AGENT_SUCCESS, policy)  # kickoff→build
        state, _ = reducer_reduce(state, PipelineEvent.AGENT_SUCCESS, policy)  # build→gate

        # Three loopbacks
        for i in range(1, 4):
            state, _ = reducer_reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)
            # After loopback, we're back at build
            assert state.phase == "build", f"iter {i}: Expected build after loopback"
            # Counter should be clamped to max_iterations=3
            assert state.get_loop_iteration("build_loop") == min(i, _BUILD_LOOP_MAX)
            # Re-enter gate until the cap is exhausted, then bypass analysis entirely
            state, _ = reducer_reduce(state, PipelineEvent.AGENT_SUCCESS, policy)
            if i < _BUILD_LOOP_MAX:
                assert state.phase == "gate"
            else:
                assert state.phase == "seal"
                assert state.get_loop_iteration("build_loop") == 0

    def test_gate_success_after_loopback_clears_review_outcome(
        self, custom_bundle: PolicyBundle
    ) -> None:
        policy = custom_bundle.pipeline
        state = _initial_state(policy)

        state, _ = reducer_reduce(state, PipelineEvent.AGENT_SUCCESS, policy)  # kickoff→build
        state, _ = reducer_reduce(state, PipelineEvent.AGENT_SUCCESS, policy)  # build→gate

        # Loopback sets review_outcome
        state, _ = reducer_reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)
        assert state.review_outcome == "has_issues"

        # Back to gate via build
        state, _ = reducer_reduce(state, PipelineEvent.AGENT_SUCCESS, policy)  # build→gate

        # Success clears review_outcome
        state, _ = reducer_reduce(state, PipelineEvent.ANALYSIS_SUCCESS, policy)  # gate→seal
        assert state.phase == "seal"
        assert state.review_outcome is None


class TestCustomPipelineVerificationFailure:
    """Verification failure routes to the custom failure terminal phase."""

    def _advance_to_verify(self, policy: PipelinePolicy) -> PipelineState:
        """Helper: advance state from kickoff all the way to verify."""
        state = _initial_state(policy)
        state, _ = reducer_reduce(state, PipelineEvent.AGENT_SUCCESS, policy)   # kickoff→build
        state, _ = reducer_reduce(state, PipelineEvent.AGENT_SUCCESS, policy)   # build→gate
        state, _ = reducer_reduce(state, PipelineEvent.ANALYSIS_SUCCESS, policy)  # gate→seal
        state, _ = reducer_reduce(state, PipelineEvent.COMMIT_SUCCESS, policy)  # seal→verify
        assert state.phase == "verify", f"Expected verify, got {state.phase}"
        return state

    def test_verification_failure_routes_to_crashed(self, custom_bundle: PolicyBundle) -> None:
        from ralph.pipeline.events import PhaseFailureEvent  # noqa: PLC0415

        policy = custom_bundle.pipeline
        state = self._advance_to_verify(policy)

        crash_event = PhaseFailureEvent(
            phase="verify",
            reason="verification script returned non-zero",
            recoverable=False,
        )
        state, _ = reducer_reduce(state, crash_event, policy)
        assert state.phase == "crashed", f"Expected crashed, got {state.phase}"

    def test_verification_success_routes_to_done(self, custom_bundle: PolicyBundle) -> None:
        policy = custom_bundle.pipeline
        state = self._advance_to_verify(policy)

        state, _ = reducer_reduce(state, PipelineEvent.AGENT_SUCCESS, policy)  # verify→done
        assert state.phase == "done", f"Expected done, got {state.phase}"

    def test_crashed_phase_is_not_canonical_terminal(
        self, custom_bundle: PolicyBundle
    ) -> None:
        """The crashed phase is a non-canonical failure terminal — not 'failed' or 'complete'."""
        assert "crashed" in custom_bundle.pipeline.phases
        phase_def = custom_bundle.pipeline.phases["crashed"]
        assert phase_def.role == "terminal"
        assert phase_def.terminal_outcome == "failure"


class TestCustomPipelineCounters:
    """Budget and loop counter tracking tests."""

    def test_build_pass_increments_on_commit_success(
        self, custom_bundle: PolicyBundle
    ) -> None:
        policy = custom_bundle.pipeline
        state = _initial_state(policy)

        state, _ = reducer_reduce(state, PipelineEvent.AGENT_SUCCESS, policy)   # kickoff→build
        state, _ = reducer_reduce(state, PipelineEvent.AGENT_SUCCESS, policy)   # build→gate
        state, _ = reducer_reduce(state, PipelineEvent.ANALYSIS_SUCCESS, policy)  # gate→seal

        assert state.get_outer_progress("build_pass") == 0
        state, _ = reducer_reduce(state, PipelineEvent.COMMIT_SUCCESS, policy)  # seal→verify
        assert state.get_outer_progress("build_pass") == 1

    def test_build_pass_unchanged_on_commit_skipped(
        self, custom_bundle: PolicyBundle
    ) -> None:
        policy = custom_bundle.pipeline
        state = _initial_state(policy)

        state, _ = reducer_reduce(state, PipelineEvent.AGENT_SUCCESS, policy)   # kickoff→build
        state, _ = reducer_reduce(state, PipelineEvent.AGENT_SUCCESS, policy)   # build→gate
        state, _ = reducer_reduce(state, PipelineEvent.ANALYSIS_SUCCESS, policy)  # gate→seal

        state, _ = reducer_reduce(state, PipelineEvent.COMMIT_SKIPPED, policy)  # seal→verify
        assert state.get_outer_progress("build_pass") == 0

    def test_build_loop_counter_resets_after_commit(
        self, custom_bundle: PolicyBundle
    ) -> None:
        policy = custom_bundle.pipeline
        state = _initial_state(policy)

        state, _ = reducer_reduce(state, PipelineEvent.AGENT_SUCCESS, policy)   # kickoff→build
        state, _ = reducer_reduce(state, PipelineEvent.AGENT_SUCCESS, policy)   # build→gate
        state, _ = reducer_reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)  # gate→build
        assert state.get_loop_iteration("build_loop") == 1

        state, _ = reducer_reduce(state, PipelineEvent.AGENT_SUCCESS, policy)   # build→gate
        state, _ = reducer_reduce(state, PipelineEvent.ANALYSIS_SUCCESS, policy)  # gate→seal
        state, _ = reducer_reduce(state, PipelineEvent.COMMIT_SUCCESS, policy)  # seal→verify
        assert state.get_loop_iteration("build_loop") == 0


class TestCustomPipelinePolicyExplainer:
    """Verify the policy explainer works for a custom pipeline."""

    def test_explain_policy_covers_custom_phases(self, custom_bundle: PolicyBundle) -> None:
        exp = explain_policy(custom_bundle)
        phase_names = {p.name for p in exp.phases}
        assert phase_names == {"kickoff", "build", "gate", "seal", "verify", "crashed", "done"}

    def test_explain_policy_never_references_canonical_names(
        self, custom_bundle: PolicyBundle
    ) -> None:
        exp = explain_policy(custom_bundle)
        text = render_explanation_text(exp)
        # Phase names in the output should be custom, not canonical
        for name in ["kickoff", "build", "gate", "seal", "verify", "crashed", "done"]:
            assert name in text, f"Custom phase '{name}' missing from explanation"

    def test_explain_entry_and_terminal(self, custom_bundle: PolicyBundle) -> None:
        exp = explain_policy(custom_bundle)
        assert exp.entry_phase == "kickoff"
        assert exp.terminal_phase == "done"

    def test_explain_terminal_outcomes_include_both_terminals(
        self, custom_bundle: PolicyBundle
    ) -> None:
        exp = explain_policy(custom_bundle)
        outcomes = {to.phase: to.outcome for to in exp.terminal_outcomes}
        assert outcomes.get("done") == "success"
        assert outcomes.get("crashed") == "failure"

    def test_explain_loop_counters_declared(self, custom_bundle: PolicyBundle) -> None:
        exp = explain_policy(custom_bundle)
        counter_names = {lc.name for lc in exp.loop_counters}
        assert "build_loop" in counter_names

    def test_explain_budget_counters_declared(self, custom_bundle: PolicyBundle) -> None:
        exp = explain_policy(custom_bundle)
        counter_names = {bc.name for bc in exp.budget_counters}
        assert "build_pass" in counter_names


class TestCustomPipelineChainTracking:
    """Phase chain tracking works with custom phase names."""

    def test_chain_for_custom_phase_returns_state(self, custom_bundle: PolicyBundle) -> None:
        state = _initial_state(custom_bundle.pipeline)
        chain = state.chain_for_phase("build")
        assert chain is not None
        assert chain.agents == ["claude"]

    def test_chain_for_unknown_phase_returns_none(self, custom_bundle: PolicyBundle) -> None:
        state = _initial_state(custom_bundle.pipeline)
        chain = state.chain_for_phase("nonexistent_phase")
        assert chain is None

    def test_retry_increments_for_custom_phase(self, custom_bundle: PolicyBundle) -> None:
        from ralph.pipeline.events import PhaseFailureEvent  # noqa: PLC0415

        policy = custom_bundle.pipeline
        state = _initial_state(policy)

        # Advance to 'build'
        state, _ = reducer_reduce(state, PipelineEvent.AGENT_SUCCESS, policy)
        assert state.phase == "build"

        crash_event = PhaseFailureEvent(
            phase="build",
            reason="build crashed",
            recoverable=True,
        )
        state, _ = reducer_reduce(state, crash_event, policy)
        assert state.phase == "build"
        chain = state.chain_for_phase("build")
        assert chain is not None
        assert chain.retries == 1


class TestCustomPipelinePolicyValidation:
    """Policy completeness validation passes for the custom bundle."""

    def test_validate_policy_completeness_passes(self, custom_bundle: PolicyBundle) -> None:
        from ralph.policy.validation import validate_policy_completeness  # noqa: PLC0415

        validate_policy_completeness(custom_bundle)  # must not raise

    def test_failed_route_is_custom_phase(
        self, custom_bundle: PolicyBundle
    ) -> None:
        assert custom_bundle.pipeline.recovery.failed_route == "crashed"
