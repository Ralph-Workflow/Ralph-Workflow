from __future__ import annotations

import dataclasses
from io import StringIO
from pathlib import Path

import pytest
from loguru import logger
from rich.console import Console

from ralph.agents.timeout_clock import FakeClock
from ralph.display.context import make_display_context
from ralph.pipeline import run_loop
from ralph.pipeline.attempt_result import AttemptResult
from ralph.pipeline.conflict_resolution import _driver_rounds as rounds_module
from ralph.pipeline.conflict_resolution import driver as driver_module
from ralph.pipeline.conflict_resolution import session as conflict_session
from ralph.pipeline.conflict_resolution.driver import run_conflict_resolution_pipeline
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.pipeline.conflict_resolution.session import (
    ResolutionSession,
    ensure_session_controller,
    invoke_resolution_agent,
)
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.integration_actions import install_executors, reset_executors
from ralph.pipeline.resolution_outcome import RetryLaterOutcome, WaitingOutcome
from ralph.pipeline.state import WaitReason
from ralph.recovery.agent_failure_ledger import AgentFailureLedger
from ralph.recovery.agent_unavailability_tracker import UnavailabilityEntry
from ralph.recovery.attempt_facts import AttemptFacts
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.unavailability_reason import UnavailabilityReason
from ralph.workspace.scope import WorkspaceScope
from tests._conflict_resolution_phase_parity_seams import (
    _config,
    _install_seams,
    _policy_bundle,
)
from tests._pipeline_deps_factory import make_test_pipeline_deps

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_wait_for_eligible_agent_applies_unavailable_wait_before_cooldown_sleep": (
        "resolver eligibility wait must enter ALL_AGENTS_UNAVAILABLE waiting state "
        "before shared handle_cooldown_delay so WAITING/RESUMED and wait token fire; "
        "driven through run_conflict_resolution_pipeline instead of the private "
        "_wait_for_eligible_agent, so the launch time replaces the returned wait"
    ),
    "test_revive_cooling_candidates_applies_unavailable_wait_before_cooldown_sleep": (
        "removed: it called the private _driver_rounds._revive_cooling_candidates, which "
        "the frozen white-box baseline rejects for a new file; the apply-then-sleep order "
        "is asserted through the public pipeline by the eligibility-wait test"
    ),
    "test_handle_candidate_failure_cools_peer_identical_eligible_agent": (
        "after shared finalize leaves the second peer-identical failure Eligible, "
        "resolver must bare-finalize like the ordinary reducer so durable shape is Cooldown"
    ),
    "test_marker_only_conflict_keeps_shared_completion_evidence": (
        "invariant 13: resolver must not clear requires_completion_evidence when "
        "decision_paths is empty; shared default True like ordinary phases"
    ),
    "test_waiting_and_failure_status_use_lifecycle_not_warn_channel": (
        "agent waiting/failure lines must use emit_agent_lifecycle_status / status_text, "
        "not warn-channel emit_conflict_phase_line"
    ),
    "test_ineligible_agent_refused_only_via_shared_run_agent_attempt": (
        "invariant 13 aspect-2: conflict_resolution must not short-circuit launch via a "
        "private eligibility gate; refusal goes through run_agent_attempt / effect_executor"
    ),
}


class _CooldownClock(FakeClock):
    def now(self) -> float:
        return self.monotonic()


def test_handle_candidate_failure_cools_peer_identical_eligible_agent() -> None:
    from tests.pipeline._auto_integrate_agent_failure_fixtures import (
        AgentMode,
        failure_exception_for_mode,
        resolution_policy_bundle,
    )

    candidates = ("first", "second")
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            ledger=AgentFailureLedger(clock=clock, consecutive_limit=1),
            policy_bundle=resolution_policy_bundle(candidates),
        )
    )
    for agent in candidates:
        ended = failure_exception_for_mode(AgentMode.NON_ZERO_EXIT, agent)
        ordinal = controller.next_attempt_ordinal(PHASE_RESOLUTION, agent)
        controller.finalize_attempt(
            PHASE_RESOLUTION,
            agent,
            facts=ended.facts,
            attempt_failed=True,
            attempt_key=f"shared:{PHASE_RESOLUTION}:{agent}:{ordinal}",
        )
    assert controller.agent_eligibility(PHASE_RESOLUTION, "first").is_cooldown
    assert controller.agent_eligibility(PHASE_RESOLUTION, "second").is_eligible

    session = ResolutionSession(recovery_controller=controller)
    session.last_attempt_result = AttemptResult(
        event=PipelineEvent.AGENT_FAILURE,
        facts=AttemptFacts(artifact_valid=False),
        outcome=None,
        attempt_outcomes=(),
    )
    session.unresolved_paths = ("shared.txt",)
    rounds_module._handle_candidate_failure(
        session,
        "second",
        RetryLaterOutcome(on_change="peers failed identically"),
        candidates,
        offset=1,
        policy_bundle=resolution_policy_bundle(candidates),
    )
    assert controller.agent_eligibility(PHASE_RESOLUTION, "second").is_cooldown
    assert controller.preferred_agent_index(PHASE_RESOLUTION, list(candidates)).agent is None


def test_marker_only_conflict_keeps_shared_completion_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: list[bool] = []

    class _Stop(BaseException):
        pass

    def _capture(**kwargs: object) -> object:
        captured.append(bool(kwargs.get("require_completion_evidence", True)))
        raise _Stop

    monkeypatch.setattr(driver_module, "invoke_resolution_agent", _capture)
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ("one",))
    _install_seams(monkeypatch, surviving_per_round=[["shared.txt"]])
    monkeypatch.setattr(
        driver_module,
        "declared_decision_paths",
        lambda *_a, **_k: (),
    )

    with pytest.raises(_Stop):
        run_conflict_resolution_pipeline(
            root=tmp_path,
            target="main",
            config=_config(),
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=_policy_bundle(),
            display=None,
            display_context=None,
            session=ResolutionSession(
                recovery_controller=RecoveryController(
                    options=RecoveryControllerOptions(clock=FakeClock())
                )
            ),
        )

    assert captured == [True]


def test_wait_for_eligible_agent_applies_unavailable_wait_before_cooldown_sleep(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    clock = _CooldownClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            unavailability_entries={
                "one": UnavailabilityEntry(
                    unavailable_until_ms=7000,
                    reason=UnavailabilityReason.NO_OUTPUT_AT_START,
                    attempt=1,
                    base_backoff_ms=5000,
                    max_backoff_ms=18000000,
                )
            },
        )
    )
    session = ResolutionSession(recovery_controller=controller)
    order: list[str] = []
    real_apply = RecoveryController.apply_all_agents_unavailable_wait
    real_handle = run_loop.handle_cooldown_delay

    def _spy_apply(self: RecoveryController, state: object, phase: str, **kwargs: object) -> object:
        order.append("apply")
        return real_apply(self, state, phase, **kwargs)

    def _spy_handle(state: object, ctx: object) -> object:
        order.append("sleep")
        assert getattr(state, "is_waiting_state", False) is True
        assert getattr(state, "wait_reason", None) is WaitReason.ALL_AGENTS_UNAVAILABLE
        return real_handle(state, ctx)

    monkeypatch.setattr(RecoveryController, "apply_all_agents_unavailable_wait", _spy_apply)
    monkeypatch.setattr(run_loop, "handle_cooldown_delay", _spy_handle)

    wait_lines: list[str] = []

    def _sink(message: object) -> None:
        record = getattr(message, "record", {})
        content = str(record.get("message", ""))
        if content.startswith("all agents unavailable"):
            wait_lines.append(content)

    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ("one",))
    called_at: list[float] = []

    def invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        del agent_name, prompt_path, round_index
        called_at.append(clock.monotonic())
        return True

    sink_id = logger.add(_sink, level="INFO", format="{message}")
    token = install_executors(clock=clock)
    try:
        _install_seams(monkeypatch, surviving_per_round=[[]])
        assert run_conflict_resolution_pipeline(
            root=tmp_path,
            target="main",
            config=_config(),
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=_policy_bundle(),
            display=None,
            display_context=None,
            invoke=invoke,
            session=session,
        )
    finally:
        reset_executors(token)
        logger.remove(sink_id)

    assert called_at == [7.0]
    assert order[:2] == ["apply", "sleep"]
    assert wait_lines
    assert clock.monotonic() == 7.0
    assert controller.agent_eligibility(PHASE_RESOLUTION, "one").is_eligible


def test_waiting_and_failure_status_use_lifecycle_not_warn_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle: list[tuple[str, str]] = []
    warn: list[str] = []

    def _lifecycle(_display: object, label: str, value: str, style: str = "cyan") -> None:
        del style
        lifecycle.append((label, value))

    def _warn(_display: object, message: str, **_kwargs: object) -> None:
        warn.append(message)

    monkeypatch.setattr(driver_module, "emit_agent_lifecycle_status", _lifecycle)
    monkeypatch.setattr(driver_module, "emit_conflict_phase_line", _warn)

    driver_module.emit_attempt_outcome(
        None,
        WaitingOutcome(external_condition="cooldown", resumes_when="eligible"),
    )
    driver_module.emit_attempt_outcome(
        None,
        RetryLaterOutcome(on_change="path changed"),
    )

    assert ("WAITING", "cooldown; resumes_when=eligible") in lifecycle
    assert ("WAITING", "retry later: on_change=path changed") in lifecycle
    assert warn == []


def test_ineligible_agent_refused_only_via_shared_run_agent_attempt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from ralph.pipeline.effect_executor import (
        install_agent_effect_dependencies,
        reset_agent_effect_dependencies,
    )

    prompt = tmp_path / "prompt.md"
    prompt.write_text("resolve", encoding="utf-8")
    clock = FakeClock()
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            ledger=AgentFailureLedger(clock=clock, consecutive_limit=3, total_limit=3),
            unavailability_entries={
                "cooling": UnavailabilityEntry(
                    unavailable_until_ms=60_000,
                    reason=UnavailabilityReason.NO_OUTPUT_AT_START,
                    attempt=1,
                    base_backoff_ms=5000,
                    max_backoff_ms=18000000,
                )
            },
        )
    )
    assert not controller.agent_eligibility(PHASE_RESOLUTION, "cooling").is_eligible
    context = make_display_context(console=Console(file=StringIO(), force_terminal=False))
    deps = dataclasses.replace(
        make_test_pipeline_deps(context, recovery_clock=clock),
        recovery_controller=controller,
        ledger=controller.ledger,
    )
    session = ResolutionSession()
    ensure_session_controller(session, _policy_bundle(), pipeline_deps=deps, config=_config())

    agent_work: list[str] = []
    shared_attempts: list[str] = []
    real_run = conflict_session.run_agent_attempt

    def _spy_run(*args: object, **kwargs: object) -> AttemptResult:
        effect = kwargs.get("effect")
        agent_name = getattr(effect, "agent_name", None)
        if isinstance(agent_name, str):
            shared_attempts.append(agent_name)
        return real_run(*args, **kwargs)

    def _stub_runner(effect: object, *args: object, **kwargs: object) -> PipelineEvent:
        del args, kwargs
        name = getattr(effect, "agent_name", None)
        if isinstance(name, str):
            agent_work.append(name)
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(conflict_session, "run_agent_attempt", _spy_run)
    token = install_agent_effect_dependencies(
        agent_runner=_stub_runner,
        recovery_controller=controller,
    )
    try:
        result = invoke_resolution_agent(
            agent_name="cooling",
            prompt_path=prompt,
            config=_config(),
            pipeline_deps=deps,
            workspace_scope=WorkspaceScope(tmp_path),
            policy_bundle=_policy_bundle(),
            display=None,
            display_context=context,
            session=session,
        )
    finally:
        reset_agent_effect_dependencies(token)

    assert shared_attempts == ["cooling"], (
        "ineligible launch must still enter shared run_agent_attempt; "
        f"got shared_attempts={shared_attempts!r}"
    )
    assert agent_work == [], "shared launch gate must refuse before agent work runs"
    assert isinstance(result, AttemptResult)
    assert result.event != PipelineEvent.AGENT_SUCCESS
    reason = result.outcome.reason if result.outcome is not None else ""
    assert "cannot be launched" not in reason, (
        "refusal must come from shared finalize / effect_executor, "
        f"not a private session pre-check; got reason={reason!r}"
    )
    assert result.attempt_outcomes, "shared run_agent_attempt must finalize the refusal"
