"""Black-box scenario tests for agent-failure handling through the public seam.

Per PRODUCT_CRITERIA.md §6 and PLAN.md §4 (U1):
Scenarios 1-9 assert ONLY observable behavior (invocation sequence, run end state,
and log output) through public interfaces without inspecting private counters.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest
from loguru import logger

from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import Verbosity
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.phases import register_role_handlers
from ralph.phases.execution import record_workspace_snapshot
from ralph.phases.required_artifacts import resolve_phase_required_artifact
from ralph.pipeline import checkpoint, run_loop
from ralph.recovery.failure_category import FailureCategory
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps
from tests.recovery._scenario_harness import (
    AgentAttemptResult,
    _apply_attempt_side_effects,
    _build_policy_bundle,
    _ScenarioRegistry,
    _seed_valid_plan,
    run_scenario,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from ralph.agents.invoke import InvokeOptions
    from ralph.config.agent_config import AgentConfig


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

    A modifies workspace files on each attempt (the scripted agent really
    writes them, and Ralph observes the change itself) but never produces the
    required artifact. Consecutive count resets, but total limit still demotes
    A after 9 attempts. B then runs and succeeds.
    """

    def script(agent: str, _phase: str, _attempt: int, _workspace: object) -> AgentAttemptResult:
        if agent == "agent_b":
            return AgentAttemptResult(produce_artifact=True)
        return AgentAttemptResult(produce_artifact=False, modify_workspace=True)

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
        stop_after_invocations=10,
        advance_clock_on_wait=True,
    )

    # Agent A was demoted after 3 attempts, B ran, then after clock advanced A ran again
    assert outcome.invocations[:3] == ["agent_a", "agent_a", "agent_a"]
    assert "agent_a" in outcome.invocations[4:], "Agent A must be retried after cooldown expiry"

    # Assert growing backoff from observable transcript selection logs
    log_text = " ".join(outcome.logs)
    cooldown_re: re.Pattern[str] = re.compile(r"skipped agent_a: cooldown \((\d+)ms")
    cooldown_matches: list[str] = cooldown_re.findall(log_text)
    matches = [int(m) for m in cooldown_matches]
    distinct_cooldowns: list[int] = []
    for m in matches:
        if not distinct_cooldowns or m != distinct_cooldowns[-1]:
            distinct_cooldowns.append(m)
    assert len(distinct_cooldowns) >= 2, (
        f"Expected at least 2 distinct cooldowns for agent_a in logs, got {distinct_cooldowns}"
    )
    assert distinct_cooldowns[1] > distinct_cooldowns[0], (
        f"Next cooldown must be longer (growing backoff): got {distinct_cooldowns}"
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
        max_iterations=25,
    )

    # Must terminate without infinite loop and end through exhaustion path
    assert len(outcome.invocations) <= 20
    assert outcome.final_state.phase == "failed_terminal"
    assert outcome.exit_code == 1
    assert not outcome.final_state.is_waiting_state


@pytest.mark.parametrize(
    "category",
    [
        FailureCategory.ENVIRONMENTAL,
        FailureCategory.AMBIGUOUS,
        FailureCategory.ARTIFACT_VALIDATION,
        FailureCategory.AGENT,
    ],
    ids=[
        "environmental",
        "ambiguous",
        "artifact_validation",
        "agent_inactivity_timeout",
    ],
)
def test_scenario_6_every_free_retry_category_is_bounded_identically(
    tmp_path: Path, category: FailureCategory
) -> None:
    """Scenario 6: Every free-retry category is bounded the same way.

    The four parametrized failure categories are:
      - FailureCategory.ENVIRONMENTAL: connection and network errors.
      - FailureCategory.AMBIGUOUS: unclassified runtime exceptions.
      - FailureCategory.ARTIFACT_VALIDATION: missing or invalid required artifacts.
      - FailureCategory.AGENT: agent execution failures; repeated inactivity timeout
        is mapped to and enters as FailureCategory.AGENT (which raises
        AgentInactivityTimeoutError via the test harness).

    Each category is bounded identically by consecutive_limit=3, demoting agent A
    to agent B after three consecutive unproductive attempts.
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

    Stop and resume mid-streak; remaining attempts are preserved across
    checkpoint serialization and process restart. Run 2 resumes through the
    production restoration path: run_loop.run with no injected controller, so
    the run loop's own controller builder restores the ledger from the loaded
    checkpoint's ledger_snapshot.
    """

    def script_run_1(
        _agent: str, _phase: str, _attempt: int, _workspace: object
    ) -> AgentAttemptResult:
        return AgentAttemptResult(produce_artifact=False)

    # First run: stop mid-streak after 2 failures of agent_a
    outcome_run_1 = run_scenario(
        tmp_path,
        script=script_run_1,
        agents=("agent_a", "agent_b"),
        stop_after_invocations=2,
    )
    assert outcome_run_1.invocations == ["agent_a", "agent_a"]

    # Save to disk checkpoint
    checkpoint_path = tmp_path / "checkpoint.json"
    checkpoint.save(outcome_run_1.final_state, checkpoint_path)
    loaded_state = checkpoint.load(checkpoint_path)
    assert loaded_state is not None
    assert loaded_state.ledger_snapshot is not None, (
        "Disk checkpoint must persist and restore ledger_snapshot"
    )

    def script_run_2(
        agent: str, _phase: str, _attempt: int, _workspace: object
    ) -> AgentAttemptResult:
        if agent == "agent_b":
            return AgentAttemptResult(produce_artifact=True)
        return AgentAttemptResult(produce_artifact=False)

    # Second run: resume through the PRODUCTION restoration path. No
    # controller and no recovery_controller_factory are injected, so
    # run_loop.run's own _build_recovery_controller rebuilds the
    # AgentFailureLedger from the loaded checkpoint's ledger_snapshot. The
    # startup collaborators and the recovery clock arrive through the
    # supported PipelineDeps boundary — no production symbol is patched.
    workspace = FsWorkspace(tmp_path)
    _seed_valid_plan(workspace)
    bundle = _build_policy_bundle(("agent_a", "agent_b"))
    register_role_handlers(bundle.pipeline)

    invocations_run_2: list[str] = []

    def fake_invoke_agent(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
        _clock: object = None,
    ) -> Iterator[str]:
        del prompt_file, options, _clock
        agent = config.cmd
        invocations_run_2.append(agent)
        result = script_run_2(agent, "development", len(invocations_run_2), workspace)
        record_workspace_snapshot(workspace, "development")
        _apply_attempt_side_effects(result, agent, len(invocations_run_2), workspace)
        return iter([f"Output from {agent}\n"])

    config = UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": False,
                "max_same_agent_retries": 0,
                "agent_max_consecutive_unproductive": 3,
            }
        }
    )

    # The resumed run's clock continues from where run 1's fake clock left
    # off, so any cooldown timestamps persisted in the ledger snapshot are
    # expired at resume — exactly what the production SystemClock guarantees.
    resume_clock = FakeClock(start=outcome_run_1.clock.monotonic())

    deps = make_test_pipeline_deps(
        make_display_context(),
        policy_bundle=bundle,
        registry_factory=lambda _cfg: _ScenarioRegistry(),
        artifact_requirements_resolver=resolve_phase_required_artifact,
        recovery_controller_factory=None,  # run_loop must build its own controller
        invoke_agent=fake_invoke_agent,
        resolve_workspace_scope_fn=lambda _start: WorkspaceScope(tmp_path),
        write_start_commit_fn=lambda _root: None,
        validate_custom_mcp_servers_fn=lambda _root: 0,
        recovery_clock=resume_clock,
    )

    captured_logs: list[str] = []

    def log_sink(msg: object) -> None:
        captured_logs.append(str(msg))

    sink_id = logger.add(log_sink, level="DEBUG")
    try:
        exit_code = run_loop.run(
            config,
            initial_state=loaded_state,
            pipeline_deps=deps,
            verbosity=Verbosity.QUIET,
        )
    finally:
        logger.remove(sink_id)

    assert exit_code == 0
    # In Run 2, agent_a fails 1 more time (completing streak of 3) and is demoted to agent_b.
    # Had the streak not survived the restart, agent_a would have been invoked 3 more times.
    assert invocations_run_2 == ["agent_a", "agent_b"]
    # The preserved streak is visible in the production transcript: after the third
    # consecutive failure, agent selection skips agent_a behind a fresh cooldown and
    # picks agent_b — observable in the logged selection evidence, no ledger internals.
    log_text = " ".join(captured_logs)
    assert re.search(r"Selected agent agent_b \(skipped agent_a: cooldown \(\d+ms", log_text), (
        "Expected agent selection to skip agent_a behind a cooldown after the resumed streak"
    )
    # End state: the resumed run persisted its terminal state through the public
    # checkpoint, and the phase completed.
    resumed_state = checkpoint.load(checkpoint_path)
    assert resumed_state is not None and resumed_state.phase == "complete"
