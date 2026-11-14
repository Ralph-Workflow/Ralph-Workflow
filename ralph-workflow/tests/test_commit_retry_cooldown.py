from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

import ralph.cli.commands.commit as commit_cli
from ralph.agents.invoke import AgentInvocationError
from ralph.agents.invoke._direct_mcp_recovery import run_with_direct_mcp_recovery
from ralph.agents.registry import AgentRegistry
from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import AgentConfig, GeneralConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline.plumbing import commit_plumbing
from ralph.policy.models import AgentsPolicy
from ralph.recovery.agent_failure_ledger import Cooldown
from tests._pipeline_deps_factory import make_test_pipeline_deps

if TYPE_CHECKING:
    from collections.abc import Iterator

    from _pytest.monkeypatch import MonkeyPatch

    from ralph.pipeline.agent_entry_point import AgentEntryPoint
    from ralph.pipeline.attempt_result import AttemptResult
    from ralph.pipeline.attempt_runner_deps import AttemptRunnerDeps
    from ralph.recovery.controller import RecoveryController

_COMMIT_PHASE = "commit"
_COMMIT_AGENT_NAME = "commit-agent"
_COMMIT_SESSION_ID = "commit-session-1"
_COMMIT_COOLDOWN_REASON = "consecutive_unproductive_limit_reached"
_COMMIT_LEDGER_KEY = f"{_COMMIT_PHASE}:{_COMMIT_AGENT_NAME}"
_MIN_OUTER_ATTEMPTS = 2

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_commit_outer_retry_regression_owner_controller_preserves_cooldown": (
        "the check no longer records two attempt facts on a hand-built controller; "
        "it drives the real commit outer retry loop and reads the owner's public "
        "cooldown status on the recovery owner that served every outer attempt, so "
        "a fresh owner per outer attempt can no longer pass"
    ),
    "test_commit_outer_retry_owner_rebuilt_per_attempt_loses_the_bench_state": (
        "the new check pins the regression the cooldown contract depends on: an owner "
        "rebuilt per outer attempt never accumulates the unproductive charges, so no "
        "owner reports a cooldown and the bench state cannot survive the outer retries"
    ),
}


@dataclass
class _CommitRouteRun:
    clock: FakeClock
    result: commit_plumbing.CommitAgentResult
    owners: list[RecoveryController]
    agent_invocations: list[str]
    benched_reinvocations: list[int]


def test_retry_budget_exhaustion_surfaces_failure_without_agent_choice_verdict() -> None:
    calls = 0
    failure = AgentInvocationError(
        "commit-agent",
        1,
        "Model returned an empty response with no tool calls",
        parsed_output=["invalid Files section"],
    )

    def run_attempt(_session_id: str | None, _capture_session_id: object) -> str:
        nonlocal calls
        calls += 1
        raise failure

    with pytest.raises(AgentInvocationError) as exc_info:
        run_with_direct_mcp_recovery(
            run_attempt,
            max_retries=2,
            reset_tool_registry=lambda: None,
        )

    assert calls == 3
    assert exc_info.value is failure
    diagnostic = "\n".join(exc_info.value.parsed_output)
    assert "retry budget exhausted after 3 attempts (2 retries)" in diagnostic
    assert "cause: AgentInvocationError" in diagnostic
    assert "identical failure signatures" not in diagnostic
    assert "try the next eligible agent" not in diagnostic


def test_commit_failure_records_failed_outcome_on_display_surface(tmp_path: Path) -> None:
    from rich.console import Console

    from ralph.display.context import make_display_context
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline.commit_executor import execute_commit_effect, set_last_commit_error
    from ralph.pipeline.effects import CommitEffect
    from ralph.pipeline.events import PipelineEvent

    artifact_path = tmp_path / ".agent" / "artifacts" / "commit_message.md"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        "---\ntype: commit\nsubject: feat: test failing commit\n---\n",
        encoding="utf-8",
    )

    console = Console(record=True, force_terminal=False, width=120, color_system=None)
    display = ParallelDisplay(make_display_context(console=console, force_width=120))

    def failing_create_commit(
        _root: Path | str,
        _msg: str,
        *,
        expected_head: str,
    ) -> object:
        raise RuntimeError("Git create_commit failed: cannot lock ref")

    def _always_has_work(_repo: Path) -> bool:
        return True

    try:
        event = execute_commit_effect(
            CommitEffect(message_file=str(artifact_path)),
            tmp_path,
            display=display,
            create_commit_fn=failing_create_commit,
            has_commit_work_fn=_always_has_work,
        )

        assert event == PipelineEvent.COMMIT_FAILURE
        assert display.last_phase_artifact_outcome == "failed"
        assert display.last_phase_artifact_outcome != "completed"
    finally:
        set_last_commit_error(None)


def test_phase_display_honesty_on_commit_failure_never_reports_completed() -> None:
    from ralph.pipeline.commit_executor import set_last_commit_error
    from ralph.pipeline.events import PipelineEvent
    from ralph.pipeline.reducer import reduce
    from ralph.pipeline.state import PipelineState
    from ralph.policy.loader import load_policy

    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    policy = load_policy(defaults_dir).pipeline

    state = PipelineState(
        phase="development_commit",
        previous_phase="development_commit_review",
        last_error="GitOperationError: cannot lock ref",
        pending_cycle_outcome="completed",
        last_execution_result_status="completed",
    )
    set_last_commit_error("GitOperationError: cannot lock ref")

    try:
        next_state, _effects = reduce(state, PipelineEvent.COMMIT_FAILURE, policy)

        assert next_state.phase != "failed_terminal"
        assert next_state.phase == "development_commit_review"
        assert next_state.pending_cycle_outcome is None
        assert next_state.last_execution_result_status != "completed"
    finally:
        set_last_commit_error(None)


def test_complete_commit_rejects_missing_or_empty_sha() -> None:
    from ralph.pipeline._commit_completion_authority import complete_commit
    from ralph.pipeline.auto_integrate_record import IntegrationRecord

    record = IntegrationRecord(
        target="main",
        phase="integrating",
        pre_feature_sha="1" * 40,
        pre_target_sha="2" * 40,
    )

    with pytest.raises(
        ValueError,
        match="cannot promote integration record to integrated without a valid integrated_feature_sha",
    ):
        complete_commit(record, integrated_feature_sha=None)

    with pytest.raises(
        ValueError,
        match="cannot promote integration record to integrated without a valid integrated_feature_sha",
    ):
        complete_commit(record, integrated_feature_sha="   ")


def _run_failing_commit_route(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    *,
    rebuild_owner_per_attempt: bool = False,
) -> _CommitRouteRun:
    clock = FakeClock(start=0.0)
    agent_config = AgentConfig(cmd=_COMMIT_AGENT_NAME, output_flag="--json")
    agent_registry = AgentRegistry.from_config(
        UnifiedConfig(agents={_COMMIT_AGENT_NAME: agent_config})
    )
    general_config = UnifiedConfig(general=GeneralConfig(max_same_agent_retries=0))
    agent_invocations: list[str] = []
    attempt_controllers: list[RecoveryController] = []

    def failing_agent_invoker(*_args: object, **_kwargs: object) -> Iterator[str]:
        agent_invocations.append(_COMMIT_AGENT_NAME)

        def _agent_output() -> Iterator[str]:
            yield f"Session ID: {_COMMIT_SESSION_ID}"
            raise AgentInvocationError(
                _COMMIT_AGENT_NAME,
                1,
                "Model returned an empty response with no tool calls",
                parsed_output=["invalid Files section"],
            )

        return _agent_output()

    chain_config = commit_cli.CommitChainConfig(
        registry=agent_registry,
        agents=[_COMMIT_AGENT_NAME],
        verbose=False,
        agents_policy=AgentsPolicy(),
        general_config=general_config,
    )
    display_context = make_display_context()
    pipeline_deps = make_test_pipeline_deps(
        display_context,
        registry_factory=lambda _config: agent_registry,
        recovery_clock=clock,
    )
    benched_reinvocations: list[int] = []
    real_run_agent_attempt = commit_plumbing.run_agent_attempt
    real_attempt_controller = commit_plumbing._attempt_controller

    def observed_run_agent_attempt(
        entry_point: AgentEntryPoint,
        *,
        effect: object | None = None,
        invocation: object | None = None,
        config: UnifiedConfig,
        controller: RecoveryController,
        deps: AttemptRunnerDeps,
        state: object | None = None,
        **execute_kwargs: object,
    ) -> AttemptResult:
        if isinstance(controller.ledger.status(_COMMIT_PHASE, _COMMIT_AGENT_NAME), Cooldown):
            benched_reinvocations.append(len(attempt_controllers))
        attempt_controllers.append(controller)
        return real_run_agent_attempt(
            entry_point,
            effect=effect,
            invocation=invocation,
            config=config,
            controller=controller,
            deps=deps,
            state=state,
            **execute_kwargs,
        )

    def rebuilt_owner_run_agent_attempt(
        entry_point: AgentEntryPoint,
        *,
        effect: object | None = None,
        invocation: object | None = None,
        config: UnifiedConfig,
        controller: RecoveryController,
        deps: AttemptRunnerDeps,
        state: object | None = None,
        **execute_kwargs: object,
    ) -> AttemptResult:
        del controller
        return observed_run_agent_attempt(
            entry_point,
            effect=effect,
            invocation=invocation,
            config=config,
            controller=real_attempt_controller(config, pipeline_deps, deps),
            deps=deps,
            state=state,
            **execute_kwargs,
        )

    monkeypatch.setattr(commit_cli, "invoke_agent", failing_agent_invoker)
    monkeypatch.setattr(
        commit_plumbing,
        "run_agent_attempt",
        rebuilt_owner_run_agent_attempt
        if rebuild_owner_per_attempt
        else observed_run_agent_attempt,
    )

    result = commit_plumbing.run_commit_plumbing(
        diff="diff --git a/file.txt b/file.txt",
        repo_root=tmp_path,
        chain_config=chain_config,
        display_context=display_context,
        pipeline_deps=pipeline_deps,
    )

    return _CommitRouteRun(
        clock=clock,
        result=result,
        owners=attempt_controllers,
        agent_invocations=agent_invocations,
        benched_reinvocations=benched_reinvocations,
    )


def test_commit_outer_retry_regression_owner_controller_preserves_cooldown(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    run = _run_failing_commit_route(tmp_path, monkeypatch)

    outer_attempts = len(run.owners)
    assert run.result.outcome is commit_plumbing.CommitAgentOutcome.FAILED
    assert outer_attempts >= _MIN_OUTER_ATTEMPTS, (
        "the commit outer retry loop must run more than one attempt, otherwise "
        "the owner has no state to carry across the outer retries"
    )
    assert len(run.agent_invocations) == outer_attempts, (
        "each commit outer attempt must invoke the agent exactly once, so the "
        "owner is charged once per outer attempt and the cooldown can only come "
        "from charges accumulated across the outer retries"
    )
    assert len({id(owner) for owner in run.owners}) == 1, (
        "every commit outer retry must run through the same recovery owner "
        "instance, otherwise the bench state cannot survive the outer retry"
    )

    controller = run.owners[0]
    ledger_entries = controller.ledger.snapshot()["entries"]
    assert isinstance(ledger_entries, dict)
    ledger_entry = ledger_entries[_COMMIT_LEDGER_KEY]
    assert isinstance(ledger_entry, dict)
    assert ledger_entry["consecutive_unproductive"] == outer_attempts, (
        "the single owner must accumulate one unproductive charge per outer attempt, "
        "so the bench state survives the outer retries"
    )

    cooldown = controller.ledger.status(_COMMIT_PHASE, _COMMIT_AGENT_NAME)
    assert isinstance(cooldown, Cooldown), (
        "the single owner that served every commit outer retry must report "
        f"Cooldown once the outer retries are done, got {cooldown!r}"
    )
    assert cooldown.reason == _COMMIT_COOLDOWN_REASON
    now_ms = int(run.clock.monotonic() * 1000)
    assert cooldown.until_ms - now_ms == int(controller.ledger.base_cooldown_seconds * 1000)
    assert controller.agent_eligibility(_COMMIT_PHASE, _COMMIT_AGENT_NAME).is_eligible is False
    assert run.benched_reinvocations == [], (
        "the commit route must not start an outer attempt while the owner has the "
        f"agent benched, got attempts {run.benched_reinvocations}"
    )


def test_commit_outer_retry_owner_rebuilt_per_attempt_loses_the_bench_state(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    run = _run_failing_commit_route(
        tmp_path,
        monkeypatch,
        rebuild_owner_per_attempt=True,
    )

    outer_attempts = len(run.owners)
    assert outer_attempts >= _MIN_OUTER_ATTEMPTS
    assert len(run.agent_invocations) == outer_attempts
    assert len({id(owner) for owner in run.owners}) == outer_attempts, (
        "the mutation must hand every outer attempt its own recovery owner"
    )
    assert all(
        not isinstance(owner.ledger.status(_COMMIT_PHASE, _COMMIT_AGENT_NAME), Cooldown)
        for owner in run.owners
    ), (
        "an owner rebuilt per attempt cannot accumulate the bench state, so the "
        "cooldown the commit route depends on is lost"
    )
