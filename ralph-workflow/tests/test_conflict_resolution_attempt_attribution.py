from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.conflict_resolution import ResolutionTerminationReason
from ralph.pipeline.conflict_resolution import driver as driver_module
from ralph.pipeline.conflict_resolution.driver import run_conflict_resolution_pipeline
from ralph.pipeline.conflict_resolution.session import (
    ResolutionSession,
    finalize_resolution_attempt_failure,
    invoke_resolution_agent,
)
from ralph.pipeline.effect_executor import set_last_captured_retry_intent
from ralph.pipeline.integration_actions import install_executors, reset_executors
from ralph.pipeline.resolution_outcome import RetryLaterOutcome
from ralph.recovery.agent_failure_ledger import AgentFailureLedger
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from tests._conflict_resolution_phase_parity_seams import (
    _CONFLICTED,
    _config,
    _install_seams,
    _policy_bundle,
)

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_the_executor_names_a_missing_agent_instead_of_failing_anonymously": (
        "the agent-not-found branch names the cause and the missing agent inline; "
        "the retired helper it used to delegate to is gone"
    ),
    "test_work_that_ran_and_did_not_finish_is_incomplete_not_refused": (
        "FakeClock advances shared cooldown waits so incomplete-success cannot wall-hang"
    ),
    "test_one_candidates_exit_reason_is_not_pinned_on_the_next_candidate": (
        "the sealed StopResolutionOutcome contract replaces boolean truthiness and deleted resolver-local retry flags"
    ),
    "test_one_stop_cannot_exceed_its_configured_invocation_ceiling": (
        "R0 permits each shared-controller-selected candidate once per round rather than resolver-local same-agent retries"
    ),
    "test_a_failed_attempt_is_charged_to_recovery_exactly_once": (
        "the shared one-attempt/one-charge contract attributes each failed candidate exactly once"
    ),
    "test_an_unavailable_candidate_does_not_answer_for_the_one_after_it": (
        "the unavailable candidate is followed once by the next shared-eligible candidate without resolver-local retries"
    ),
}

if TYPE_CHECKING:
    import pytest

    from ralph.policy.models import PolicyBundle
    from ralph.recovery.attempt_facts import AttemptFacts
    from ralph.recovery.conflict_attempt_outcome import ConflictAttemptOutcome


def test_a_stop_never_inherits_the_previous_stops_verdict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ("one",))
    _install_seams(monkeypatch, surviving_per_round=[_CONFLICTED])
    session = ResolutionSession(
        recovery_controller=RecoveryController(options=RecoveryControllerOptions())
    )
    token = install_executors(clock=FakeClock())
    try:
        driver_module.run_conflict_resolution_outcome(
            root=tmp_path,
            target="main",
            config=_config(),
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=_policy_bundle(),
            display=None,
            display_context=None,
            invoke=lambda *_args: False,
            session=session,
        )
        assert session.terminal_reason is ResolutionTerminationReason.ATTEMPT_FAILED

        monkeypatch.setattr(driver_module, "unmerged_paths", lambda _root: [])
        driver_module.run_conflict_resolution_outcome(
            root=tmp_path,
            target="main",
            config=_config(),
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=_policy_bundle(),
            display=None,
            display_context=None,
            invoke=lambda *_args: False,
            session=session,
        )
    finally:
        reset_executors(token)
    assert session.terminal_reason is not ResolutionTerminationReason.ATTEMPT_FAILED
    assert session.terminal_reason is None


def test_one_stop_cannot_exceed_its_configured_invocation_ceiling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    chain = ("one", "two", "three")
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: chain)
    clock = FakeClock()
    _install_seams(monkeypatch)
    ledger = AgentFailureLedger(clock=clock)
    session = ResolutionSession(
        ledger=ledger,
        recovery_controller=RecoveryController(
            ledger=ledger, options=RecoveryControllerOptions(clock=clock)
        ),
    )
    called: list[tuple[int, str]] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        called.append((round_index, agent_name))
        return False

    config = _config()
    token = install_executors(clock=clock)
    try:
        assert (
            run_conflict_resolution_pipeline(
                root=tmp_path,
                target="main",
                config=config,
                pipeline_deps=None,
                workspace_scope=None,
                policy_bundle=_policy_bundle(),
                display=None,
                display_context=None,
                invoke=_invoke,
                session=session,
            )
            is False
        )
    finally:
        reset_executors(token)
    assert len(called) == 9
    rounds_spent = max(index for index, _ in called)
    assert rounds_spent <= config.conflict_resolution.max_rounds_per_stop
    assert len(called) == len(chain) * rounds_spent
    for round_index in range(1, rounds_spent + 1):
        spent = [agent for index, agent in called if index == round_index]
        assert spent == list(chain), f"round {round_index} spent {spent}"
    for agent in chain:
        assert [a for _, a in called].count(agent) == 3


def test_a_failed_attempt_is_charged_to_recovery_exactly_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ("one", "two"))
    _install_seams(monkeypatch, surviving_per_round=[_CONFLICTED])
    monkeypatch.setattr(
        "ralph.pipeline.effect_executor.execute_agent_effect",
        lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionError("provider unavailable")),
    )
    charged: list[str] = []
    original = finalize_resolution_attempt_failure

    def _spy(
        session: ResolutionSession | None,
        agent_name: str,
        *,
        facts: AttemptFacts,
        exc: BaseException | None = None,
        outcome: ConflictAttemptOutcome | None = None,
        candidates: tuple[str, ...] = (),
        failed_index: int = 0,
        policy_bundle: PolicyBundle | None = None,
    ) -> None:
        charged.append(agent_name)
        original(
            session,
            agent_name,
            facts=facts,
            exc=exc,
            outcome=outcome,
            candidates=candidates,
            failed_index=failed_index,
            policy_bundle=policy_bundle,
        )

    monkeypatch.setattr(driver_module, "finalize_resolution_attempt_failure", _spy)
    session = ResolutionSession(
        max_rounds_per_stop=1,
        recovery_controller=RecoveryController(options=RecoveryControllerOptions()),
    )

    token = install_executors(clock=FakeClock())
    try:
        assert (
            run_conflict_resolution_pipeline(
                root=tmp_path,
                target="main",
                config=_config(),
                pipeline_deps=None,
                workspace_scope=None,
                policy_bundle=_policy_bundle(),
                display=None,
                display_context=None,
                session=session,
            )
            is False
        )
    finally:
        reset_executors(token)
    assert charged == ["one", "two"]


def test_one_candidates_exit_reason_is_not_pinned_on_the_next_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from ralph.pipeline.agent_retry_intent import AgentRetryIntent
    from ralph.pipeline.events import PipelineEvent

    set_last_captured_retry_intent(
        AgentRetryIntent(
            failure_reason="PiProviderFailureExitError",
            skip_same_agent_retries=True,
            failed_agent_name="pi",
        )
    )
    monkeypatch.setattr(
        "ralph.pipeline.effect_executor.execute_agent_effect",
        lambda *args, **kwargs: PipelineEvent.AGENT_FAILURE,
    )
    prompt = tmp_path / "prompt.md"
    prompt.write_text("resolve", encoding="utf-8")
    session = ResolutionSession(
        recovery_controller=RecoveryController(options=RecoveryControllerOptions())
    )

    outcome = invoke_resolution_agent(
        agent_name="claude",
        prompt_path=prompt,
        config=_config(),
        pipeline_deps=None,
        workspace_scope=None,
        policy_bundle=_policy_bundle(),
        display=None,
        display_context=None,
        session=session,
    )
    assert isinstance(outcome, RetryLaterOutcome)
    assert outcome.on_change == "resolver_agent_eligibility_changed"
    assert outcome.until is None
    assert session.last_attempt_failure == "conflict attempt failed"
    assert session.last_attempt_evidence != "PiProviderFailureExitError"


def test_work_that_ran_and_did_not_finish_is_incomplete_not_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ("one", "two"))
    clock = FakeClock()
    _install_seams(monkeypatch, surviving_per_round=[_CONFLICTED, _CONFLICTED, _CONFLICTED])
    lines: list[str] = []
    monkeypatch.setattr(
        driver_module, "emit_conflict_phase_line", lambda _display, line: lines.append(line)
    )
    ledger = AgentFailureLedger(clock=clock)
    session = ResolutionSession(
        ledger=ledger,
        recovery_controller=RecoveryController(
            ledger=ledger, options=RecoveryControllerOptions(clock=clock)
        ),
    )

    token = install_executors(clock=clock)
    try:
        assert (
            run_conflict_resolution_pipeline(
                root=tmp_path,
                target="main",
                config=_config(),
                pipeline_deps=None,
                workspace_scope=None,
                policy_bundle=_policy_bundle(),
                display=None,
                display_context=None,
                invoke=lambda *_args: True,
                session=session,
            )
            is False
        )
    finally:
        reset_executors(token)
    assert any(
        line.startswith(ResolutionTerminationReason.RESOLUTION_INCOMPLETE.value) for line in lines
    )
    assert not any(
        line.startswith(ResolutionTerminationReason.ATTEMPT_FAILED.value) for line in lines
    )


def test_no_reason_the_pipeline_can_report_is_an_agent_refusing_the_work() -> None:
    values = {reason.value for reason in ResolutionTerminationReason}
    assert not any("DECLIN" in value or "REFUS" in value for value in values)
    assert {
        "ATTEMPT_FAILED",
        "RESOLUTION_INCOMPLETE",
        "CANDIDATE_EXITED",
        "CANDIDATE_UNAVAILABLE",
    } <= values


def test_an_unavailable_candidate_does_not_answer_for_the_one_after_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        driver_module, "resolution_chain_agents", lambda _bundle: ("ghost", "healthy")
    )
    clock = FakeClock()
    _install_seams(monkeypatch, surviving_per_round=[_CONFLICTED])
    ledger = AgentFailureLedger(clock=clock)
    session = ResolutionSession(
        ledger=ledger,
        max_rounds_per_stop=1,
        recovery_controller=RecoveryController(
            ledger=ledger, options=RecoveryControllerOptions(clock=clock)
        ),
    )
    called: list[str] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        called.append(agent_name)
        if agent_name == "ghost":
            session.terminal_reason = ResolutionTerminationReason.CANDIDATE_UNAVAILABLE
        else:
            session.last_attempt_saw_activity = True
        return False

    token = install_executors(clock=clock)
    try:
        assert (
            run_conflict_resolution_pipeline(
                root=tmp_path,
                target="main",
                config=_config(),
                pipeline_deps=None,
                workspace_scope=None,
                policy_bundle=_policy_bundle(),
                display=None,
                display_context=None,
                invoke=_invoke,
                session=session,
            )
            is False
        )
    finally:
        reset_executors(token)
    assert called == ["ghost", "healthy"]
    assert session.terminal_reason is ResolutionTerminationReason.ATTEMPT_FAILED
    assert session.charge_conflict_budget is True
    assert session.exhaustion_reason is not None
    assert session.exhaustion_reason.startswith(ResolutionTerminationReason.ATTEMPT_FAILED.value)


def test_the_executor_names_a_missing_agent_instead_of_failing_anonymously() -> None:
    from ralph.pipeline import effect_executor as effect_executor_module

    assert effect_executor_module.AGENT_NOT_FOUND_REASON == "AgentNotFound"
    source = Path(effect_executor_module.__file__).read_text(encoding="utf-8")
    marker = "if agent_config is None:"
    assert marker in source
    branch = source[source.index(marker) : source.index(marker) + 900]
    assert "AGENT_NOT_FOUND_REASON" in branch, (
        "the agent-not-found branch must name the cause on the retry intent"
    )
    assert "skip_same_agent_retries=True" in branch, (
        "retrying a name the registry cannot produce is futile"
    )
    assert "failed_agent_name=effect.agent_name" in branch, (
        "the agent-not-found branch must name the missing agent"
    )
