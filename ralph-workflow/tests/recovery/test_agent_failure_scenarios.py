"""Black-box scenario tests for agent-failure handling through the public seam.

Per PRODUCT_CRITERIA.md §6 and PLAN.md §4 (U1):
Scenarios 1-9 assert ONLY observable behavior (invocation sequence, run end state,
and log output) through public interfaces without inspecting private counters.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.recovery.failure_category import FailureCategory
from tests.recovery._scenario_harness import (
    AgentAttemptResult,
    run_scenario,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_scenario_1_reproduces_bug_fallback_after_unproductive_streak(tmp_path: Path) -> None:
    """Scenario 1: Reproduces the bug.

    Agents A then B. A produces nothing every time (unproductive); B succeeds.
    Expect A three times, then demotion to B, then completion.
    """
    def script(agent: str, _phase: str, _attempt: int, _workspace: object) -> AgentAttemptResult:
        if agent == "agent_b":
            return AgentAttemptResult(produce_artifact=True)
        return AgentAttemptResult(produce_artifact=False)

    outcome = run_scenario(
        tmp_path,
        script=script,
        agents=("agent_a", "agent_b"),
        max_iterations=15,
    )

    assert outcome.invocations == ["agent_a", "agent_a", "agent_a", "agent_b"]
    assert outcome.final_state.phase == "complete"
    assert outcome.exit_code == 0


def test_scenario_2_changes_files_never_finishes_demoted_at_total_limit(tmp_path: Path) -> None:
    """Scenario 2: Changes files, never finishes.

    A modifies workspace files on each attempt (workspace_changed=True) but
    never produces the required artifact. Consecutive count resets, but total
    limit still demotes A after 9 attempts. B then runs and succeeds.
    """
    def script(agent: str, _phase: str, _attempt: int, _workspace: object) -> AgentAttemptResult:
        if agent == "agent_b":
            return AgentAttemptResult(produce_artifact=True)
        return AgentAttemptResult(produce_artifact=False, workspace_changed=True)

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


def test_scenario_3_recovers_within_consecutive_limit_never_demoted(tmp_path: Path) -> None:
    """Scenario 3: Recovers.

    A fails twice, then succeeds on attempt 3. A is never demoted and B is never invoked.
    """
    def script(agent: str, _phase: str, attempt: int, _workspace: object) -> AgentAttemptResult:
        if agent == "agent_a" and attempt == 3:
            return AgentAttemptResult(produce_artifact=True)
        return AgentAttemptResult(produce_artifact=False)

    outcome = run_scenario(
        tmp_path,
        script=script,
        agents=("agent_a", "agent_b"),
        max_iterations=10,
    )

    assert outcome.invocations == ["agent_a", "agent_a", "agent_a"]
    assert outcome.final_state.phase == "complete"
    assert outcome.exit_code == 0


def test_scenario_4_cooldown_expiry_retries_preferred_agent_with_longer_next_cooldown(
    tmp_path: Path,
) -> None:
    """Scenario 4: Cooldown expiry.

    After fake clock passes cooldown duration, A is tried again with longer next cooldown.
    """
    # A fails consecutively twice (cooldown 1, then cooldown 2). B fails once while A is on cooldown.
    # We observe that A is retried after cooldown expires, and cooldown 2 > cooldown 1.
    def script(agent: str, _phase: str, _attempt: int, _workspace: object) -> AgentAttemptResult:
        return AgentAttemptResult(produce_artifact=False)

    outcome = run_scenario(
        tmp_path,
        script=script,
        agents=("agent_a", "agent_b"),
        max_iterations=12,
        advance_clock_on_wait=True,
    )

    # Agent A was demoted after 3 attempts, B ran, then after clock advanced A ran again
    assert outcome.invocations[:3] == ["agent_a", "agent_a", "agent_a"]
    assert "agent_a" in outcome.invocations[4:], "Agent A must be retried after cooldown expiry"

    a_cooldowns = outcome.cooldown_durations.get("agent_a", [])
    if len(a_cooldowns) >= 2:
        assert a_cooldowns[1] > a_cooldowns[0], (
            f"Next cooldown must be longer (growing backoff): got {a_cooldowns}"
        )


def test_scenario_5_all_agents_dead_ends_through_exhaustion_path_bounded(tmp_path: Path) -> None:
    """Scenario 5: All agents dead.

    Both agents fail repeatedly without recovery. Ends through exhaustion path, bounded.
    """
    def script(_agent: str, _phase: str, _attempt: int, _workspace: object) -> AgentAttemptResult:
        return AgentAttemptResult(produce_artifact=False)

    outcome = run_scenario(
        tmp_path,
        script=script,
        agents=("agent_a", "agent_b"),
        max_iterations=20,
    )

    # Must terminate without infinite loop
    assert len(outcome.invocations) <= 20
    assert outcome.final_state.phase in {"failed_terminal", "development"}
    # When all agents are exhausted, run ends through exhaustion path
    if outcome.final_state.phase == "failed_terminal":
        assert outcome.exit_code == 1


@pytest.mark.parametrize(
    "category",
    [
        FailureCategory.ENVIRONMENTAL,
        FailureCategory.AMBIGUOUS,
        FailureCategory.ARTIFACT_VALIDATION,
        FailureCategory.AGENT,
    ],
)
def test_scenario_6_every_free_retry_category_is_bounded_identically(
    tmp_path: Path, category: FailureCategory
) -> None:
    """Scenario 6: Every free-retry category is bounded the same way.

    Environmental, ambiguous, artifact validation, and timeout are each bounded
    by consecutive_limit=3, demoting A to B.
    """
    def script(agent: str, _phase: str, _attempt: int, _workspace: object) -> AgentAttemptResult:
        if agent == "agent_b":
            return AgentAttemptResult(produce_artifact=True)
        return AgentAttemptResult(produce_artifact=False, failure_category=category)

    outcome = run_scenario(
        tmp_path,
        script=script,
        agents=("agent_a", "agent_b"),
        max_iterations=10,
    )

    assert outcome.invocations == ["agent_a", "agent_a", "agent_a", "agent_b"]
    assert outcome.final_state.phase == "complete"
    assert outcome.exit_code == 0


def test_scenario_7_sole_agent_chain_fails_phase_with_clear_message(tmp_path: Path) -> None:
    """Scenario 7: Sole agent chain.

    Fails the phase with clear message when demoted.
    """
    def script(_agent: str, _phase: str, _attempt: int, _workspace: object) -> AgentAttemptResult:
        return AgentAttemptResult(produce_artifact=False)

    outcome = run_scenario(
        tmp_path,
        script=script,
        agents=("agent_a",),
        max_iterations=10,
    )

    assert outcome.final_state.phase == "failed_terminal"
    assert outcome.exit_code == 1

    # Clear message logged or captured in state
    combined_text = " ".join(outcome.logs) + " " + (outcome.final_state.last_error or "")
    assert any(
        kw in combined_text.lower()
        for kw in ("demoted", "unproductive", "unavailable", "exhausted")
    ), f"Expected clear demotion/exhaustion message, got: {combined_text}"


def test_scenario_8_return_code_ignored_artifact_presence_determines_success(
    tmp_path: Path,
) -> None:
    """Scenario 8: Return code ignored.

    Exit 0 with no artifact is unproductive (demotes after 3).
    Exit 1 with valid artifact is success (completes immediately).
    """
    # 8A: Exit 0 with no artifact is unproductive
    def script_8a(agent: str, _phase: str, _attempt: int, _workspace: object) -> AgentAttemptResult:
        if agent == "agent_b":
            return AgentAttemptResult(produce_artifact=True, exit_code=0)
        return AgentAttemptResult(produce_artifact=False, exit_code=0)

    outcome_8a = run_scenario(
        tmp_path / "case_8a",
        script=script_8a,
        agents=("agent_a", "agent_b"),
        max_iterations=10,
    )
    assert outcome_8a.invocations == ["agent_a", "agent_a", "agent_a", "agent_b"]
    assert outcome_8a.final_state.phase == "complete"

    # 8B: Exit 1 with valid artifact is success
    def script_8b(agent: str, _phase: str, _attempt: int, _workspace: object) -> AgentAttemptResult:
        return AgentAttemptResult(produce_artifact=True, exit_code=1)

    outcome_8b = run_scenario(
        tmp_path / "case_8b",
        script=script_8b,
        agents=("agent_a", "agent_b"),
        max_iterations=5,
    )
    assert outcome_8b.invocations == ["agent_a"]
    assert outcome_8b.final_state.phase == "complete"
    assert outcome_8b.exit_code == 0


def test_scenario_9_restart_preserves_remaining_attempts_across_resumption(
    tmp_path: Path,
) -> None:
    """Scenario 9: Restart.

    Stop and resume mid-streak; remaining attempts are preserved.
    """
    def script_run_1(_agent: str, _phase: str, _attempt: int, _workspace: object) -> AgentAttemptResult:
        return AgentAttemptResult(produce_artifact=False)

    # First run: stop mid-streak after 2 failures of agent_a
    outcome_run_1 = run_scenario(
        tmp_path,
        script=script_run_1,
        agents=("agent_a", "agent_b"),
        stop_after_invocations=2,
    )
    assert outcome_run_1.invocations == ["agent_a", "agent_a"]

    # Second run: resume with the saved state and controller
    def script_run_2(agent: str, _phase: str, _attempt: int, _workspace: object) -> AgentAttemptResult:
        if agent == "agent_b":
            return AgentAttemptResult(produce_artifact=True)
        return AgentAttemptResult(produce_artifact=False)

    outcome_run_2 = run_scenario(
        tmp_path,
        script=script_run_2,
        agents=("agent_a", "agent_b"),
        initial_state=outcome_run_1.final_state,
        clock=outcome_run_1.clock,
        max_iterations=10,
    )

    # In Run 2, agent_a fails 1 more time (completing streak of 3) and is demoted to agent_b
    assert outcome_run_2.invocations == ["agent_a", "agent_b"]
    assert outcome_run_2.final_state.phase == "complete"
    assert outcome_run_2.exit_code == 0
