"""Contract test: PipelineSnapshot must expose budget_progress keyed by policy counter name.

The legacy scalar fields (iteration, total_iterations, reviewer_pass,
total_reviewer_passes) must be absent. The replacement is a generic
budget_progress dict keyed by the policy-declared counter name.
"""

from __future__ import annotations

import pytest

from ralph.display.snapshot import SnapshotContext, snapshot_from_state
from ralph.pipeline.state import PipelineState
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactsPolicy,
    BudgetCounterConfig,
    PhaseCommitPolicy,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    PolicyBundle,
    PostCommitRoute,
    PostCommitRouteWhen,
)


@pytest.fixture(scope="module")
def _cycles_bundle() -> PolicyBundle:
    """Policy bundle that uses 'cycles' as the only budget counter (no 'iteration')."""
    pipeline = PipelinePolicy(
        entry_phase="work",
        terminal_phase="done",
        budget_counters={
            "cycles": BudgetCounterConfig(
                tracks_budget=True,
                description="Work cycles",
                default_max=3,
            ),
        },
        phases={
            "work": PhaseDefinition(
                drain="work",
                role="execution",
                transitions=PhaseTransition(on_success="done"),
                commit_policy=PhaseCommitPolicy(increments_counter="cycles"),
            ),
            "done": PhaseDefinition(
                drain="done",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(on_success="done"),
            ),
        },
        post_commit_routes=[
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="work", budget_state="remaining"),
                target="work",
            ),
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="work", budget_state="exhausted"),
                target="done",
            ),
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="work", budget_state="no_review"),
                target="done",
            ),
        ],
    )
    agents = AgentsPolicy(
        agent_chains={"work_chain": AgentChainConfig(agents=["fake-agent"])},
        agent_drains={
            "work": AgentDrainConfig(chain="work_chain"),
            "done": AgentDrainConfig(chain="work_chain"),
        },
    )
    return PolicyBundle(agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy())


def test_snapshot_has_budget_progress_not_legacy_fields(
    _cycles_bundle: PolicyBundle,
) -> None:
    """PipelineSnapshot must expose budget_progress, not iteration/reviewer_pass fields."""
    state = PipelineState.from_policy(_cycles_bundle.pipeline)
    state = state.with_outer_progress("cycles", 1)
    state = state.with_budget_cap("cycles", 3)

    snap = snapshot_from_state(
        state,
        SnapshotContext(pipeline_policy=_cycles_bundle.pipeline),
    )

    assert hasattr(snap, "budget_progress"), (
        "PipelineSnapshot does not have 'budget_progress' dict field."
    )
    assert "cycles" in snap.budget_progress, (
        "budget_progress does not contain the 'cycles' counter."
    )
    assert snap.budget_progress["cycles"].completed == 1
    assert snap.budget_progress["cycles"].cap == 3

    assert not hasattr(snap, "iteration"), (
        "PipelineSnapshot still has legacy 'iteration' scalar field."
    )
    assert not hasattr(snap, "total_iterations"), (
        "PipelineSnapshot still has legacy 'total_iterations' scalar field."
    )
    assert not hasattr(snap, "reviewer_pass"), (
        "PipelineSnapshot still has legacy 'reviewer_pass' scalar field."
    )
    assert not hasattr(snap, "total_reviewer_passes"), (
        "PipelineSnapshot still has legacy 'total_reviewer_passes' scalar field."
    )


def test_snapshot_budget_progress_empty_when_no_counters() -> None:
    """budget_progress is an empty dict when no budget counters are declared."""
    policy = PipelinePolicy(
        entry_phase="start",
        terminal_phase="done",
        phases={
            "start": PhaseDefinition(
                drain="d",
                role="execution",
                transitions=PhaseTransition(on_success="done"),
            ),
            "done": PhaseDefinition(
                drain="d",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(on_success="done"),
            ),
        },
    )
    state = PipelineState.from_policy(policy)
    snap = snapshot_from_state(
        state,
        SnapshotContext(pipeline_policy=policy),
    )
    assert snap.budget_progress == {}
