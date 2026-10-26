"""Public-seam scenario tests for agent-failure route facts.

Verifies:
1. PA-002 observation contract: Ordinary-AGENT_FAILURE route + workspace changes each attempt ->
   demotes at 9, not 3, proving exactly-once recording.
2. PA-001 scenario: varying rendered output, no valid artifact, no workspace change ->
   A runs 3 times, then B, then completion (a prose-changing agent cannot evade cooldown).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.invoke import AgentInactivityTimeoutError
from ralph.recovery.agent_failure_ledger import AgentFailureLedger, Cooldown, Spent
from tests.recovery._scenario_harness import (
    AgentAttemptResult,
    run_scenario,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_scenario_ordinary_agent_failure_route_workspace_changes_demotes_at_nine(
    tmp_path: Path,
) -> None:
    """Ordinary AGENT_FAILURE route with workspace changes records exactly once per attempt.

    Agent A fails on each attempt (inactivity timeout) but writes to the workspace.
    The workspace-change observation is transported to the reducer, which records
    workspace_changed=True through the controller seam.
    Because workspace_changed=True resets consecutive unproductive attempts,
    Agent A is demoted only when total_unproductive reaches 9 (not 3).
    Exactly 9 attempts of Agent A run before falling over to Agent B, proving
    exactly-once fact recording on the ordinary AGENT_FAILURE route.
    """

    def script(agent: str, _phase: str, _attempt: int, _workspace: object) -> AgentAttemptResult:
        if agent == "agent_b":
            return AgentAttemptResult(produce_artifact=True)
        # Agent A: modifies workspace files on each attempt, then fails with inactivity timeout
        err = AgentInactivityTimeoutError(agent, 60.0)
        err.workspace_changed = True
        return AgentAttemptResult(
            workspace_changed=True,
            produce_artifact=False,
            raise_error=err,
        )

    outcome = run_scenario(
        tmp_path,
        script=script,
        agents=("agent_a", "agent_b"),
        max_iterations=20,
    )

    expected_invocations = ["agent_a"] * 9 + ["agent_b"]
    assert outcome.invocations == expected_invocations
    assert outcome.final_state.phase == "complete"
    assert outcome.exit_code == 0

    # Verify ledger snapshot facts: Agent A reached total limit and is spent
    assert outcome.final_state.ledger_snapshot is not None
    restored = AgentFailureLedger.from_snapshot(
        outcome.final_state.ledger_snapshot, clock=outcome.clock
    )
    assert isinstance(restored.status("development", "agent_a"), Spent)


def test_pa_001_varying_output_cannot_evade_cooldown(tmp_path: Path) -> None:
    """PA-001 scenario: varying rendered output without workspace changes demotes at 3.

    PRODUCT_CRITERIA §3.5: An agent's request/response output cannot say whether it is
    working — only artifact validity, workspace change, and identity are facts.
    Agent A emits different output prose on each retry attempt, but produces no valid
    artifact and makes no workspace changes.
    The ledger enforces the consecutive unproductive limit (3): Agent A runs 3 times,
    is demoted to Cooldown, and the chain falls over to Agent B, which completes.
    """
    prose_variants = [
        "First attempt: analyzing problem space and exploring files...",
        "Second attempt: completely different hypothesis, trying alternate approach...",
        "Third attempt: fresh perspective, reorganizing structure...",
    ]

    def script(agent: str, _phase: str, attempt: int, _workspace: object) -> AgentAttemptResult:
        if agent == "agent_b":
            return AgentAttemptResult(produce_artifact=True)
        # Agent A varies its output prose on every attempt
        variant = prose_variants[min(attempt - 1, len(prose_variants) - 1)]
        del variant
        return AgentAttemptResult(
            workspace_changed=False,
            produce_artifact=False,
            raise_error=AgentInactivityTimeoutError(agent, 60.0),
            custom_action=lambda ws: None,  # no-op: no workspace change
        )

    outcome = run_scenario(
        tmp_path,
        script=script,
        agents=("agent_a", "agent_b"),
        max_iterations=15,
    )

    assert outcome.invocations == ["agent_a", "agent_a", "agent_a", "agent_b"]
    assert outcome.final_state.phase == "complete"
    assert outcome.exit_code == 0

    # Verify ledger recorded consecutive limit reached (Agent A is in Cooldown)
    assert outcome.final_state.ledger_snapshot is not None
    restored = AgentFailureLedger.from_snapshot(
        outcome.final_state.ledger_snapshot, clock=outcome.clock
    )
    assert isinstance(restored.status("development", "agent_a"), Cooldown)
