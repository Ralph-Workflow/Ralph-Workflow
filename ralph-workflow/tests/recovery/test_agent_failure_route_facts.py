"""Public-seam scenario tests for agent-failure route facts.

PRODUCT_CRITERIA §3 and §6.2/§6.6: the observable behaviour of the single
failure owner must be provable from the outside. Every assertion here is on

- which agents ran, and in what order,
- the terminal outcome of the run (phase and exit code), and
- what the run logged,

plus the output the scripted agent actually emitted. No test in this module
reads a ledger entry, a snapshot field, a private counter or an exception
attribute: the facts Ralph decides on are observed by Ralph itself, through
the production workspace-change probe over the real temporary workspace.
"""

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
    """§6.2: the ordinary agent-failure route demotes at 9 when the workspace changed.

    Agent A runs, writes a file into the workspace and then fails with an
    inactivity timeout -- the shape of the reported bug. The attempt is
    measured by PRODUCTION: the invocation boundary takes the workspace
    baseline before the agent runs and measures the change when the attempt
    fails, so the fact Ralph decides on is a real diff of the real temporary
    workspace. Nothing in this test writes that fact anywhere.

    Because the workspace changed, every attempt of A counts as
    productive-but-incomplete: the consecutive count restarts every time
    while the total count keeps growing. A is therefore demoted after NINE
    attempts, not after three, and the chain falls over to agent B, which
    completes the phase.

    The demotion itself is observable in the log, with phase, agent, counts
    and reason.
    """

    def script(agent: str, _phase: str, _attempt: int, _workspace: object) -> AgentAttemptResult:
        if agent == "agent_b":
            return AgentAttemptResult(produce_artifact=True)
        # Agent A: modifies workspace files on each attempt, then fails with
        # an inactivity timeout (the "repeated timeout" free-retry category).
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

    # The demotion is observable: phase, agent, counts and reason are logged.
    assert any("Agent 'agent_a' demoted in phase 'development'" in line for line in outcome.logs), (
        "the demotion must be logged with its phase and agent"
    )
    assert any("total_unproductive_limit_reached" in line for line in outcome.logs), (
        "the demotion must be logged with the reason it was reached"
    )


def test_scenario_varying_output_cannot_evade_the_consecutive_limit(tmp_path: Path) -> None:
    """§3.5 / §6.1: varying output cannot buy an agent more attempts.

    PRODUCT_CRITERIA §3.5: an agent's output cannot say whether it is
    working -- only artifact validity, workspace change and identity are
    facts. Agent A emits genuinely DIFFERENT prose on every attempt, but
    produces no valid artifact and changes nothing. The prose really reaches
    the pipeline (it is emitted, not computed and dropped), and it buys the
    agent nothing: the consecutive limit demotes A after three attempts and
    the chain falls over to agent B, which completes.

    The attempts end without a valid artifact, so Ralph observes each of them
    itself: the workspace is observed UNCHANGED for every attempt, which is
    what keeps the consecutive count growing.
    """
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

    # The prose was really emitted, and each attempt said something else.
    assert outcome.agent_outputs[:3] == list(prose_variants)
    assert len(set(outcome.agent_outputs[:3])) == 3

    # The demotion is observable in the log.
    assert any("Agent 'agent_a' demoted in phase 'development'" in line for line in outcome.logs), (
        "the demotion must be logged with its phase and agent"
    )


def test_scenario_missing_observation_never_inherits_the_previous_attempt_fact(
    tmp_path: Path,
) -> None:
    """§4.3: every attempt is observed on its own.

    Agent A changes the workspace on its FIRST attempt only. The second and
    third attempts change nothing, and Ralph must observe them as they are:
    an attempt without a workspace change does not inherit the previous
    attempt's change. The consecutive count therefore reaches its limit and A
    is demoted after four attempts -- not after the total limit of nine,
    which is what an inherited fact would produce.
    """

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

    # The consecutive limit, not the total limit, is what demoted A.
    assert any("Agent 'agent_a' demoted in phase 'development'" in line for line in outcome.logs), (
        "the demotion must be logged with its phase and agent"
    )
    assert any("consecutive_unproductive_limit_reached" in line for line in outcome.logs), (
        "the demotion must be logged with the reason it was reached"
    )
