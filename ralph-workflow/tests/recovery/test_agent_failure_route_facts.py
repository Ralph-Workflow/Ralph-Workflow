from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.invoke import AgentInactivityTimeoutError
from tests.recovery._scenario_harness import (
    AgentAttemptResult,
    run_scenario,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_scenario_ordinary_agent_failure_route_workspace_changes_demotes_at_nine(
    tmp_path: Path,
) -> None:

    def script(agent: str, _phase: str, _attempt: int, _workspace: object) -> AgentAttemptResult:
        if agent == "agent_b":
            return AgentAttemptResult(produce_artifact=True)
        return AgentAttemptResult(
            modify_workspace=True,
            produce_artifact=False,
            raise_error=AgentInactivityTimeoutError(agent, 60.0),
        )

    outcome = run_scenario(
        tmp_path,
        script=script,
        agents=("agent_a", "agent_b"),
        max_iterations=20,
    )

    assert outcome.invocations == ["agent_a"] * 9 + ["agent_b"]
    assert outcome.final_state.phase == "complete"
    assert outcome.exit_code == 0

    assert any("Agent 'agent_a' demoted in phase 'development'" in line for line in outcome.logs), (
        "the demotion must be logged with its phase and agent"
    )
    assert any("total_unproductive_limit_reached" in line for line in outcome.logs), (
        "the demotion must be logged with the reason it was reached"
    )


def test_scenario_varying_output_cannot_evade_the_consecutive_limit(tmp_path: Path) -> None:
    prose_variants = (
        "First attempt: analyzing the problem space and exploring the files...",
        "Second attempt: a completely different hypothesis, trying another approach...",
        "Third attempt: a fresh perspective, reorganizing the whole structure...",
    )

    def script(agent: str, _phase: str, attempt: int, _workspace: object) -> AgentAttemptResult:
        if agent == "agent_b":
            return AgentAttemptResult(produce_artifact=True)
        variant = prose_variants[min(attempt - 1, len(prose_variants) - 1)]
        return AgentAttemptResult(
            modify_workspace=False,
            produce_artifact=False,
            output_lines=(variant,),
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

    assert outcome.agent_outputs[:3] == list(prose_variants)
    assert len(set(outcome.agent_outputs[:3])) == 3

    assert any("Agent 'agent_a' demoted in phase 'development'" in line for line in outcome.logs), (
        "the demotion must be logged with its phase and agent"
    )


def test_scenario_missing_observation_never_inherits_the_previous_attempt_fact(
    tmp_path: Path,
) -> None:

    def script(agent: str, _phase: str, attempt: int, _workspace: object) -> AgentAttemptResult:
        if agent == "agent_b":
            return AgentAttemptResult(produce_artifact=True)
        return AgentAttemptResult(modify_workspace=(attempt == 1), produce_artifact=False)

    outcome = run_scenario(
        tmp_path,
        script=script,
        agents=("agent_a", "agent_b"),
        max_iterations=15,
    )

    assert outcome.invocations == ["agent_a"] * 4 + ["agent_b"]
    assert outcome.final_state.phase == "complete"
    assert outcome.exit_code == 0

    assert any("Agent 'agent_a' demoted in phase 'development'" in line for line in outcome.logs), (
        "the demotion must be logged with its phase and agent"
    )
    assert any("consecutive_unproductive_limit_reached" in line for line in outcome.logs), (
        "the demotion must be logged with the reason it was reached"
    )
