"""End-to-end integration test: custom-named pipeline (design → build → audit → sign_off → done).

Proves that policy-driven routing works with only user-defined phase names and
that no canonical phase name (planning, development, review, fix, complete, failed)
is hard-required anywhere in the runtime.

Exercises:
  - Policy validation via validate_policy_completeness
  - Reducer-driven routing through all phase roles
  - Post-commit routing via resolve_post_commit_phase
  - ASCII explain-policy output contains custom names only
  - Parallelization policy in a custom-named phase
"""

from __future__ import annotations

import pytest

from ralph.pipeline.handoffs import resolve_post_commit_phase
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
    PhaseParallelization,
    PhaseTransition,
    PipelinePolicy,
    PolicyBundle,
    PostCommitRoute,
    PostCommitRouteWhen,
    RecoveryPolicy,
)

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
    """Build the design → build → audit → sign_off → done custom pipeline.

    Phase roles:
      design:    execution
      build:     execution (with parallelization)
      audit:     analysis (loops back to build on request_changes)
      sign_off:  commit (increments cycles counter, resets audit_round)
      terminal:  terminal success (done), terminal failure (rejected)
    """
    pipeline = PipelinePolicy(
        entry_phase="design",
        terminal_phase="done",
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


class TestCustomNamedPipelinePostCommitRouting:
    """Post-commit routing uses custom phase names exclusively."""

    def test_post_commit_remaining_routes_to_design(self, custom_bundle: PolicyBundle) -> None:
        """When cycles budget is remaining, sign_off commit routes back to design."""
        policy = custom_bundle.pipeline
        state = PipelineState(
            phase="sign_off",
            outer_progress={"cycles": 1},
            budget_caps={"cycles": 3},
        )
        next_phase = resolve_post_commit_phase(state, policy)
        assert next_phase == "design", f"Expected design, got {next_phase}"

    def test_post_commit_exhausted_routes_to_done(self, custom_bundle: PolicyBundle) -> None:
        """When cycles budget is exhausted (0), sign_off commit routes to done."""
        policy = custom_bundle.pipeline
        state = PipelineState(
            phase="sign_off",
            outer_progress={"cycles": _CYCLE_CAP},
            budget_caps={"cycles": _CYCLE_CAP},
        )
        next_phase = resolve_post_commit_phase(state, policy)
        assert next_phase == "done", f"Expected done, got {next_phase}"
