from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ralph.agents.invoke import AgentInactivityTimeoutError
from tests.recovery._scenario_harness import (
    AgentAttemptResult,
    run_scenario,
)

if TYPE_CHECKING:
    from pathlib import Path

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_scenario_ordinary_agent_failure_route_workspace_changes_demotes_at_nine": "operator rule: an agent is either on cooldown or not; the demotion log and total_unproductive_limit_reached reason no longer exist, so the test asserts one unproductive_limit cooldown_decision line for agent_a in development with total=9",
    "test_scenario_varying_output_cannot_evade_the_consecutive_limit": "operator rule: an agent is either on cooldown or not; the demotion log no longer exists, so the test asserts one unproductive_limit cooldown_decision line for agent_a in development with consecutive=3",
    "test_scenario_missing_observation_never_inherits_the_previous_attempt_fact": "operator rule: an agent is either on cooldown or not; the demotion log and consecutive_unproductive_limit_reached reason no longer exist, so the test asserts one unproductive_limit cooldown_decision line for agent_a in development with consecutive=3 total=4",
}

_COOLDOWN_PATTERN = re.compile(
    r"cooldown_decision phase=development agent=agent_a reason=unproductive_limit .*"
    r"unproductive_attempts consecutive=(\d+) total=(\d+)"
)


def _cooldown_counts(logs: list[str]) -> list[tuple[int, int]]:
    counts: list[tuple[int, int]] = []
    for line in logs:
        match = _COOLDOWN_PATTERN.search(line)
        if match is not None:
            counts.append((int(match.group(1)), int(match.group(2))))
    return counts


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

    assert set(_cooldown_counts(outcome.logs)) == {(0, 9)}, (
        "the unproductive_limit cooldown must be logged with its phase, agent and the total that "
        "started it"
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

    assert set(_cooldown_counts(outcome.logs)) == {(3, 3)}, (
        "the unproductive_limit cooldown must be logged with its phase, agent and the "
        "consecutive count that started it"
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

    assert set(_cooldown_counts(outcome.logs)) == {(3, 4)}, (
        "the unproductive_limit cooldown must be logged with its phase, agent and the "
        "consecutive count that started it"
    )
