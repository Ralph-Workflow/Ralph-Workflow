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
                    iteration_state_field="build_loop", loopback_review_outcome="has_issues"
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


class TestCustomPipelineCounters:
    """Budget and loop counter tracking tests."""

    def test_build_pass_increments_on_commit_success(self, custom_bundle: PolicyBundle) -> None:
        policy = custom_bundle.pipeline
        state = _initial_state(policy)

        state, _ = reducer_reduce(state, PipelineEvent.AGENT_SUCCESS, policy)  # kickoff→build
        state, _ = reducer_reduce(state, PipelineEvent.AGENT_SUCCESS, policy)  # build→gate
        state, _ = reducer_reduce(state, PipelineEvent.ANALYSIS_SUCCESS, policy)  # gate→seal

        assert state.get_outer_progress("build_pass") == 0
        state, _ = reducer_reduce(state, PipelineEvent.COMMIT_SUCCESS, policy)  # seal→verify
        assert state.get_outer_progress("build_pass") == 1

    def test_build_pass_advances_on_commit_skipped(self, custom_bundle: PolicyBundle) -> None:
        policy = custom_bundle.pipeline
        state = _initial_state(policy)

        state, _ = reducer_reduce(state, PipelineEvent.AGENT_SUCCESS, policy)  # kickoff→build
        state, _ = reducer_reduce(state, PipelineEvent.AGENT_SUCCESS, policy)  # build→gate
        state, _ = reducer_reduce(state, PipelineEvent.ANALYSIS_SUCCESS, policy)  # gate→seal

        state, _ = reducer_reduce(state, PipelineEvent.COMMIT_SKIPPED, policy)  # seal→verify
        assert state.get_outer_progress("build_pass") == 1

    def test_build_loop_counter_resets_after_commit(self, custom_bundle: PolicyBundle) -> None:
        policy = custom_bundle.pipeline
        state = _initial_state(policy)

        state, _ = reducer_reduce(state, PipelineEvent.AGENT_SUCCESS, policy)  # kickoff→build
        state, _ = reducer_reduce(state, PipelineEvent.AGENT_SUCCESS, policy)  # build→gate
        state, _ = reducer_reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)  # gate→build
        assert state.get_loop_iteration("build_loop") == 1

        state, _ = reducer_reduce(state, PipelineEvent.AGENT_SUCCESS, policy)  # build→gate
        state, _ = reducer_reduce(state, PipelineEvent.ANALYSIS_SUCCESS, policy)  # gate→seal
        state, _ = reducer_reduce(state, PipelineEvent.COMMIT_SUCCESS, policy)  # seal→verify
        assert state.get_loop_iteration("build_loop") == 0
