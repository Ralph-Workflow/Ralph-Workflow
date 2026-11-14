from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.conflict_resolution import driver as driver_module
from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)
from ralph.pipeline.conflict_resolution.driver import run_conflict_resolution_pipeline
from ralph.pipeline.conflict_resolution.session import (
    ResolutionSession,
    classify_failed_resolution_attempt,
    invoke_resolution_agent,
)
from ralph.recovery.agent_failure_ledger import AgentFailureLedger
from tests._conflict_resolution_phase_parity_seams import (
    _CONFLICTED,
    _config,
    _install_seams,
    _policy_bundle,
)

if TYPE_CHECKING:
    import pytest

    from ralph.policy.models import PolicyBundle


def test_a_stop_never_inherits_the_previous_stops_verdict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ("one",))
    monkeypatch.setattr(driver_module, "_sleep_seconds", lambda _seconds: None)
    _install_seams(monkeypatch, surviving_per_round=[_CONFLICTED])
    session = ResolutionSession()
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
    assert session.terminal_reason is not ResolutionTerminationReason.ATTEMPT_FAILED
    assert session.terminal_reason is None


def test_one_stop_cannot_exceed_its_configured_invocation_ceiling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    chain = ("one", "two", "three")
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: chain)
    monkeypatch.setattr(driver_module, "_sleep_seconds", lambda _seconds: None)
    _install_seams(monkeypatch)
    called: list[tuple[int, str]] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        called.append((round_index, agent_name))
        return False

    config = _config()
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
        )
        is False
    )
    assert len(called) == 27
    rounds_spent = max(index for index, _ in called)
    for round_index in range(1, rounds_spent + 1):
        spent = [agent for index, agent in called if index == round_index]
        assert set(spent) == set(chain), f"round {round_index} spent {spent}"
    for agent in chain:
        assert [a for _, a in called].count(agent) == 9


def test_a_failed_attempt_is_charged_to_recovery_exactly_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ("one", "two"))
    monkeypatch.setattr(driver_module, "_sleep_seconds", lambda _seconds: None)
    _install_seams(monkeypatch, surviving_per_round=[_CONFLICTED])
    monkeypatch.setattr(
        "ralph.pipeline.conflict_resolution.session._effect_executor_module.execute_agent_effect",
        lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionError("provider unavailable")),
    )
    charged: list[str] = []
    original = classify_failed_resolution_attempt

    def _spy(
        session: ResolutionSession | None,
        agent_name: str,
        raw_failure: BaseException | str,
        *,
        candidates: tuple[str, ...] = (),
        failed_index: int = 0,
        policy_bundle: PolicyBundle | None = None,
        bench: bool = False,
        **kwargs: object,
    ) -> None:
        charged.append(agent_name)
        original(
            session,
            agent_name,
            raw_failure,
            candidates=candidates,
            failed_index=failed_index,
            policy_bundle=policy_bundle,
            bench=bench,
            **kwargs,
        )

    monkeypatch.setattr(driver_module, "classify_failed_resolution_attempt", _spy)
    session = ResolutionSession(max_rounds_per_stop=1)

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
    assert charged.count("one") > 1
    assert "two" in charged


def test_one_candidates_exit_reason_is_not_pinned_on_the_next_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from ralph.pipeline import effect_executor as effect_executor_module
    from ralph.pipeline.agent_retry_intent import AgentRetryIntent
    from ralph.pipeline.events import PipelineEvent

    effect_executor_module._set_last_captured_retry_intent(
        AgentRetryIntent(
            failure_reason="PiProviderFailureExitError",
            skip_same_agent_retries=True,
            failed_agent_name="pi",
        )
    )
    monkeypatch.setattr(
        "ralph.pipeline.conflict_resolution.session._effect_executor_module.execute_agent_effect",
        lambda *args, **kwargs: PipelineEvent.AGENT_FAILURE,
    )
    prompt = tmp_path / "prompt.md"
    prompt.write_text("resolve", encoding="utf-8")
    session = ResolutionSession()

    assert not invoke_resolution_agent(
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
    assert session.last_attempt_failure == "conflict attempt failed"
    assert session.last_attempt_evidence != "PiProviderFailureExitError"
    assert session.skip_same_agent_retries is False


def test_work_that_ran_and_did_not_finish_is_incomplete_not_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ("one", "two"))
    monkeypatch.setattr(driver_module, "_sleep_seconds", lambda _seconds: None)
    _install_seams(monkeypatch, surviving_per_round=[_CONFLICTED, _CONFLICTED, _CONFLICTED])
    lines: list[str] = []
    monkeypatch.setattr(
        driver_module, "emit_conflict_phase_line", lambda _display, line: lines.append(line)
    )
    session = ResolutionSession()

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
    monkeypatch.setattr(
        driver_module, "_sleep_seconds", lambda seconds: clock.advance(max(seconds, 65.0))
    )
    _install_seams(monkeypatch, surviving_per_round=[_CONFLICTED])
    ledger = AgentFailureLedger(clock=clock)
    session = ResolutionSession(ledger=ledger, max_rounds_per_stop=1)
    called: list[str] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        called.append(agent_name)
        if agent_name == "ghost":
            session.terminal_reason = ResolutionTerminationReason.CANDIDATE_UNAVAILABLE
        else:
            session.last_attempt_saw_activity = True
        return False

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
    assert called == ["ghost"] + ["healthy"] * 3
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
