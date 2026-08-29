"""B1-B6: conflict resolution must follow the pipeline chain, not a local loop."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.config.models import UnifiedConfig
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
from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    import pytest

    from ralph.policy.models import PolicyBundle

_CONFLICTED = ["src/alpha.py"]


def _policy_bundle() -> PolicyBundle:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


def _config() -> UnifiedConfig:
    return UnifiedConfig.model_validate({"general": {}})


def _install_seams(
    monkeypatch: pytest.MonkeyPatch,
    *,
    unmerged: Sequence[str] = _CONFLICTED,
    surviving_per_round: Sequence[Sequence[str]] | None = None,
) -> None:
    monkeypatch.setattr(driver_module, "unmerged_paths", lambda root: list(unmerged))
    remaining = list(surviving_per_round) if surviving_per_round is not None else [list(unmerged)]

    def _fake_markers(root: Path, paths: Sequence[str]) -> list[str]:
        if remaining:
            return list(remaining.pop(0))
        return list(unmerged)

    monkeypatch.setattr(driver_module, "paths_with_conflict_markers", _fake_markers)


def test_four_agent_chain_tries_every_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        driver_module, "resolution_chain_agents", lambda _bundle: ("one", "two", "three", "four")
    )
    _install_seams(monkeypatch, surviving_per_round=[_CONFLICTED, _CONFLICTED, _CONFLICTED])
    called: list[str] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        called.append(agent_name)
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
        )
        is False
    )
    # Every candidate gets a turn, in chain order, before the round ends.
    # Later rounds wrap back to the head rather than invoking nobody, so
    # the chain -- not the exact call count -- is what this pins.
    assert called[:4] == ["one", "two", "three", "four"]


def test_transient_failure_retries_the_same_agent_using_recovery_controller_handle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A transient first-agent failure retries that agent before failover."""
    monkeypatch.setattr(
        driver_module, "resolution_chain_agents", lambda _bundle: ("one", "two")
    )
    monkeypatch.setattr("ralph.pipeline.conflict_resolution.driver._sleep_seconds", lambda _seconds: None)
    _install_seams(
        monkeypatch, surviving_per_round=[_CONFLICTED, _CONFLICTED, _CONFLICTED]
    )
    called: list[str] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        called.append(agent_name)
        if agent_name == "one":
            raise ConnectionError("provider unavailable")
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
        )
        is False
    )
    assert called[0] == "one"
    assert called.count("one") > 1
    assert "two" in called
    one_indexes = [index for index, name in enumerate(called) if name == "one"]
    assert one_indexes[1] == one_indexes[0] + 1


def test_conflict_retry_honors_chain_retry_delay_ms_from_recovery_controller(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Retry backoff is the chain's retry_delay_ms decided by RecoveryController.handle."""
    sleeps: list[float] = []
    monkeypatch.setattr(
        "ralph.pipeline.conflict_resolution.driver._sleep_seconds", sleeps.append
    )
    monkeypatch.setattr(
        driver_module, "resolution_chain_agents", lambda _bundle: ("one", "two")
    )
    _install_seams(
        monkeypatch, surviving_per_round=[_CONFLICTED, _CONFLICTED, _CONFLICTED]
    )

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        if agent_name == "one":
            raise ConnectionError("provider unavailable")
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
        )
        is False
    )
    chain = _policy_bundle().agents.agent_chains["rebase_conflict_resolution"]
    expected_seconds = chain.retry_delay_ms / 1000.0
    assert sleeps
    assert sleeps[0] == expected_seconds


def test_conflict_failures_call_recovery_controller_handle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """R1: candidate failures use RecoveryController.handle, not a private retry table."""
    from ralph.recovery.controller import RecoveryController

    handled: list[str] = []
    original = RecoveryController.handle

    def _spy(
        self: RecoveryController,
        state: object,
        raw_failure: BaseException | str,
        context: object,
    ) -> object:
        handled.append(str(raw_failure))
        return original(self, state, raw_failure, context)

    monkeypatch.setattr(RecoveryController, "handle", _spy)
    monkeypatch.setattr(
        driver_module, "resolution_chain_agents", lambda _bundle: ("one", "two")
    )
    _install_seams(monkeypatch, surviving_per_round=[_CONFLICTED, _CONFLICTED, _CONFLICTED])

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        raise RuntimeError(f"launch failed for {agent_name}")

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
        )
        is False
    )
    assert handled
    assert any("launch failed for one" in item for item in handled)


def test_next_round_starts_at_an_untried_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        driver_module, "resolution_chain_agents", lambda _bundle: ("one", "two", "three", "four")
    )
    _install_seams(monkeypatch, surviving_per_round=[_CONFLICTED, _CONFLICTED, []])
    called: list[tuple[str, int]] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        called.append((agent_name, round_index))
        return True

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
        )
        is True
    )
    assert called[0] == ("one", 1)
    assert called[1] == ("two", 2)


def test_invoke_resolution_agent_keeps_chain_retry_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: list[int] = []

    def _capture_execute(*args: object, **kwargs: object) -> object:
        config = args[1] if len(args) > 1 else kwargs.get("config")
        assert config is not None
        captured.append(config.general.max_same_agent_retries)
        raise RuntimeError("launch skipped")

    monkeypatch.setattr(
        "ralph.pipeline.conflict_resolution.session._effect_executor_module.execute_agent_effect",
        _capture_execute,
    )
    prompt = tmp_path / "prompt.md"
    prompt.write_text("resolve", encoding="utf-8")
    config = UnifiedConfig.model_validate({"general": {"max_same_agent_retries": 4}})
    assert (
        invoke_resolution_agent(
            agent_name="claude",
            prompt_path=prompt,
            config=config,
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=_policy_bundle(),
            display=None,
            display_context=None,
        )
        is False
    )
    assert captured == [4]


def test_launch_failure_is_not_collapsed_to_candidate_declined(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ("one",))
    _install_seams(monkeypatch, surviving_per_round=[_CONFLICTED, _CONFLICTED, _CONFLICTED])
    session = ResolutionSession()

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        raise RuntimeError("provider unreachable")

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
    assert session.terminal_reason is ResolutionTerminationReason.EXCEPTION


def test_failed_invoke_routes_launch_provider_and_decline_through_recovery_controller(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """After a failed invoke, RecoveryController sees launch vs provider vs decline."""
    from ralph.recovery.controller import RecoveryController
    from ralph.recovery.failure_classifier import FailureClassifier

    seen: list[str] = []

    def _spy(
        self: RecoveryController,
        raw_failure: BaseException | str,
        *,
        agent: str,
        phase: str = "rebase_conflict_resolution",
    ) -> object:
        text = str(raw_failure).lower()
        if "launch" in text:
            seen.append("launch")
        elif "provider" in text:
            seen.append("provider")
        else:
            seen.append("decline")
        return FailureClassifier().classify(raw_failure, phase=phase, agent=agent)

    monkeypatch.setattr(
        RecoveryController,
        "classify_conflict_attempt",
        _spy,
        raising=False,
    )
    monkeypatch.setattr(
        driver_module, "resolution_chain_agents", lambda _bundle: ("one", "two", "three")
    )
    monkeypatch.setattr("ralph.pipeline.conflict_resolution.driver._sleep_seconds", lambda _seconds: None)
    _install_seams(
        monkeypatch, surviving_per_round=[_CONFLICTED, _CONFLICTED, _CONFLICTED, _CONFLICTED]
    )
    outcomes: list[BaseException | bool] = [
        RuntimeError("launch failed: cannot spawn agent"),
        ConnectionError("provider unavailable"),
        ConnectionError("provider unavailable"),
        False,
    ]

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        item = outcomes.pop(0) if outcomes else False
        if isinstance(item, BaseException):
            raise item
        return item

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
        )
        is False
    )
    assert seen[0] == "launch"
    assert "provider" in seen
    assert "decline" in seen


def test_max_fallback_agents_does_not_truncate_a_four_agent_chain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """max_fallback_agents remains a non-truncating compatibility field."""
    monkeypatch.setattr(
        driver_module, "resolution_chain_agents", lambda _bundle: ("one", "two", "three", "four")
    )
    _install_seams(monkeypatch, surviving_per_round=[_CONFLICTED, _CONFLICTED, _CONFLICTED])
    called: list[str] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        called.append(agent_name)
        return False

    session = ResolutionSession(max_fallback_agents=2)
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
    assert called[:4] == ["one", "two", "three", "four"]


def test_round_after_a_spent_chain_still_invokes_a_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A round that starts past the end of the chain wraps instead of no-opping."""
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ("one", "two"))
    monkeypatch.setattr(driver_module, "_sleep_seconds", lambda _seconds: None)
    _install_seams(monkeypatch, surviving_per_round=[_CONFLICTED, _CONFLICTED, _CONFLICTED])
    called: list[tuple[int, str]] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        called.append((round_index, agent_name))
        return agent_name == "two"

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
        )
        is False
    )
    assert {round_index for round_index, _ in called} == {1, 2, 3}


def test_a_new_stop_restarts_the_chain_on_a_reused_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One session spans a rebase; a later stop must still spend the chain."""
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ("one", "two"))
    monkeypatch.setattr(driver_module, "_sleep_seconds", lambda _seconds: None)
    session = ResolutionSession()
    _install_seams(monkeypatch, surviving_per_round=[[]])

    def _first_stop(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        return agent_name == "two"

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
            invoke=_first_stop,
            session=session,
        )
        is True
    )
    _install_seams(monkeypatch, surviving_per_round=[[]])
    second_stop: list[str] = []

    def _second_stop(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        second_stop.append(agent_name)
        return True

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
            invoke=_second_stop,
            session=session,
        )
        is True
    )
    assert second_stop == ["one"]


def test_a_round_that_invokes_nobody_is_never_reported_as_a_failed_attempt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A chain nobody could spend has no attempt to report as failed."""
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ("one", "two"))
    _install_seams(monkeypatch, surviving_per_round=[_CONFLICTED, _CONFLICTED, _CONFLICTED])
    session = ResolutionSession(dead_tool_surfaces=("one", "two"))
    lines: list[str] = []
    monkeypatch.setattr(
        driver_module, "emit_conflict_phase_line", lambda _display, line: lines.append(line)
    )
    called: list[str] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        called.append(agent_name)
        return True

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
    assert called == []
    assert not any(
        line.startswith(ResolutionTerminationReason.ATTEMPT_FAILED.value) for line in lines
    )
    assert any(
        line.startswith(ResolutionTerminationReason.TOOL_SURFACE_DEAD.value) for line in lines
    )


def test_a_dead_chain_stops_burning_rounds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No live candidate means no later round can spend one, so stop asking."""
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ("one",))
    _install_seams(monkeypatch, surviving_per_round=[_CONFLICTED, _CONFLICTED, _CONFLICTED])
    session = ResolutionSession(dead_tool_surfaces=("one",))
    rounds: list[int] = []
    monkeypatch.setattr(
        driver_module,
        "render_conflict_prompt",
        lambda **kwargs: (
            rounds.append(int(kwargs["round_index"])),
            tmp_path / "prompt.md",
        )[1],
    )
    (tmp_path / "prompt.md").write_text("resolve", encoding="utf-8")

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
    assert rounds == [1]


def test_a_pi_context_exhaustion_is_not_reported_as_a_declined_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """pi exiting on an exhausted context is an exit, not a resolver's verdict."""
    from ralph.pipeline import effect_executor as effect_executor_module
    from ralph.pipeline.agent_retry_intent import AgentRetryIntent
    from ralph.pipeline.events import PipelineEvent

    def _exhausted_pi(*args: object, **kwargs: object) -> object:
        effect_executor_module._set_last_captured_retry_intent(
            AgentRetryIntent(
                failure_reason="PiContextExhaustedExitError",
                skip_same_agent_retries=True,
                failed_agent_name="pi",
            )
        )
        return PipelineEvent.AGENT_FAILURE

    monkeypatch.setattr(
        "ralph.pipeline.conflict_resolution.session._effect_executor_module.execute_agent_effect",
        _exhausted_pi,
    )
    prompt = tmp_path / "prompt.md"
    prompt.write_text("resolve", encoding="utf-8")
    session = ResolutionSession()

    assert (
        invoke_resolution_agent(
            agent_name="pi",
            prompt_path=prompt,
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
    assert session.last_attempt_failure == "PiContextExhaustedExitError"
    assert session.skip_same_agent_retries is True
    # The intent is consumed here, never left for the next phase to inherit.
    assert effect_executor_module.pop_last_captured_retry_intent().failure_reason == ""


def test_a_resolver_that_ran_and_came_back_unsuccessful_is_a_failed_attempt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An agent that ran and failed is a failed ATTEMPT, never a refusal.

    Nothing gives the resolver a way to refuse the conflict, so the only
    honest reading of a non-success from an agent that actually worked
    is that the attempt failed -- and the chain answers that by trying
    again, not by accepting it.
    """
    from ralph.pipeline.events import PipelineEvent

    def _ran_then_refused(*args: object, **kwargs: object) -> object:
        # A resolver that really declines has RUN, and running is visible.
        listener = kwargs.get("effect")
        activity = getattr(listener, "activity_status_listener", None)
        if callable(activity):
            activity("edited src/alpha.py")
        return PipelineEvent.AGENT_FAILURE

    monkeypatch.setattr(
        "ralph.pipeline.conflict_resolution.session._effect_executor_module.execute_agent_effect",
        lambda effect, *args, **kwargs: _ran_then_refused(effect=effect),
    )
    prompt = tmp_path / "prompt.md"
    prompt.write_text("resolve", encoding="utf-8")
    session = ResolutionSession()

    assert (
        invoke_resolution_agent(
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
        is False
    )
    assert session.last_attempt_failure == "conflict attempt failed"
    assert session.skip_same_agent_retries is False
    assert session.last_attempt_saw_activity is True
    assert session.terminal_reason is None


def test_a_candidate_that_never_ran_is_not_recorded_as_declining(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An instant non-success with no activity is an exit, not a verdict.

    The executor answers with the same event for failures that happen
    before any agent runs, which is how "invoking X" was followed
    immediately by X apparently reading the conflict and refusing it.
    """
    from ralph.pipeline.events import PipelineEvent

    monkeypatch.setattr(
        "ralph.pipeline.conflict_resolution.session._effect_executor_module.execute_agent_effect",
        lambda *args, **kwargs: PipelineEvent.AGENT_FAILURE,
    )
    prompt = tmp_path / "prompt.md"
    prompt.write_text("resolve", encoding="utf-8")
    session = ResolutionSession()

    assert (
        invoke_resolution_agent(
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
        is False
    )
    assert session.last_attempt_saw_activity is False
    assert session.terminal_reason is ResolutionTerminationReason.CANDIDATE_EXITED


def test_an_uninstalled_candidate_is_unavailable_not_declining(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A chain may name an agent this workspace does not have.

    The executor answers a missing binary with the same non-success
    event a real failure produces, so it read as the agent giving up on
    the conflict -- instantly, because nothing ran. It now names the
    cause on the retry-intent channel and the chain hands over.
    """
    from ralph.pipeline import effect_executor as effect_executor_module
    from ralph.pipeline.agent_retry_intent import AgentRetryIntent
    from ralph.pipeline.events import PipelineEvent

    def _agent_not_found(*args: object, **kwargs: object) -> object:
        # Exactly what execute_agent_effect does for a name the registry
        # cannot produce: name the cause on the retry-intent channel.
        effect_executor_module._set_last_captured_retry_intent(
            AgentRetryIntent(
                failure_reason=effect_executor_module.AGENT_NOT_FOUND_REASON,
                skip_same_agent_retries=True,
                failed_agent_name="pi",
            )
        )
        return PipelineEvent.AGENT_FAILURE

    monkeypatch.setattr(
        "ralph.pipeline.conflict_resolution.session._effect_executor_module.execute_agent_effect",
        _agent_not_found,
    )
    prompt = tmp_path / "prompt.md"
    prompt.write_text("resolve", encoding="utf-8")
    session = ResolutionSession()

    assert (
        invoke_resolution_agent(
            agent_name="pi",
            prompt_path=prompt,
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
    assert session.terminal_reason is ResolutionTerminationReason.CANDIDATE_UNAVAILABLE
    assert session.skip_same_agent_retries is True


def test_an_uninstalled_candidate_hands_over_to_the_next_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Being absent must cost the chain one candidate, not the whole round."""
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ("pi", "claude"))
    monkeypatch.setattr(driver_module, "_sleep_seconds", lambda _seconds: None)
    _install_seams(monkeypatch, surviving_per_round=[[]])
    session = ResolutionSession()
    called: list[str] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        called.append(agent_name)
        if agent_name == "pi":
            session.terminal_reason = ResolutionTerminationReason.CANDIDATE_UNAVAILABLE
            return False
        return True

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
        is True
    )
    assert called == ["pi", "claude"]
    assert "pi" in session.dead_tool_surfaces


def test_skip_same_agent_retries_advances_the_chain_instead_of_retrying(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A futile-to-retry exit must cost the next candidate, not the same one again."""
    session = ResolutionSession(skip_same_agent_retries=True)
    classify_failed_resolution_attempt(
        session,
        "pi",
        ConnectionError("provider unavailable"),
        candidates=("pi", "claude"),
        failed_index=0,
        policy_bundle=_policy_bundle(),
    )
    assert session.chain_cursor == 1
    assert session.current_agent_retries == 0
    assert session.skip_same_agent_retries is False
    # Contrast: without the flag the same environmental failure spends a
    # same-agent retry instead, which is what makes this assertion mean
    # something.
    retried = ResolutionSession()
    classify_failed_resolution_attempt(
        retried,
        "pi",
        ConnectionError("provider unavailable"),
        candidates=("pi", "claude"),
        failed_index=0,
        policy_bundle=_policy_bundle(),
    )
    assert retried.chain_cursor == 0
    assert retried.current_agent_retries == 1


def test_a_live_candidate_behind_the_cursor_is_still_invoked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Dead surfaces ahead of the cursor must not strand the live agent behind it."""
    monkeypatch.setattr(
        driver_module, "resolution_chain_agents", lambda _bundle: ("alpha", "beta", "gamma")
    )
    monkeypatch.setattr(driver_module, "_sleep_seconds", lambda _seconds: None)
    _install_seams(monkeypatch, surviving_per_round=[_CONFLICTED, _CONFLICTED, _CONFLICTED])
    session = ResolutionSession(chain_cursor=1, dead_tool_surfaces=("beta", "gamma"))
    called: list[str] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        called.append(agent_name)
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
    assert called
    assert set(called) == {"alpha"}


def test_one_candidates_infrastructure_fault_does_not_bury_the_next_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A dead tool surface is the agent that faulted, never the one after it."""
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ("one", "two"))
    monkeypatch.setattr(driver_module, "_sleep_seconds", lambda _seconds: None)
    _install_seams(monkeypatch, surviving_per_round=[_CONFLICTED, _CONFLICTED, _CONFLICTED])
    session = ResolutionSession()

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        if agent_name == "two":
            session.terminal_reason = ResolutionTerminationReason.TRANSPORT_LOOP_DETECTED
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
    # Barred for this stop -- Ralph's plumbing faulted, and the recovery
    # layer calls that retryable -- and never charged to the other agent.
    assert session.stop_dead_surfaces == ("two",)
    assert session.dead_tool_surfaces == ()


def test_a_stop_never_inherits_the_previous_stops_verdict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An early exit must not report a reason left behind by an earlier conflict."""
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ("one",))
    _install_seams(monkeypatch, surviving_per_round=[_CONFLICTED])
    session = ResolutionSession()
    outcome = driver_module.run_conflict_resolution_outcome(
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
    assert outcome.reason is ResolutionTerminationReason.ATTEMPT_FAILED

    # Second stop: nothing is conflicted, so no resolver runs at all.
    monkeypatch.setattr(driver_module, "unmerged_paths", lambda _root: [])
    quiet = driver_module.run_conflict_resolution_outcome(
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
    assert quiet.reason is not ResolutionTerminationReason.ATTEMPT_FAILED
    assert session.terminal_reason is None


def test_one_stop_cannot_exceed_its_configured_invocation_ceiling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Rounds re-spend the chain, so pin the ceiling that bounds the cost."""
    from ralph.pipeline.conflict_resolution.session import conflict_chain_max_retries

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
    # Declines earn no same-agent retry, so the count is exact: every
    # round spends the chain once and nothing spends it twice. WHICH
    # candidate leads a round rotates -- the next round starts after the
    # last one tried -- so the property is per-round coverage, not order.
    rounds = config.conflict_resolution.max_rounds_per_stop
    assert len(called) == rounds * len(chain)
    for round_index in range(1, rounds + 1):
        spent = [agent for index, agent in called if index == round_index]
        assert sorted(spent) == sorted(chain), f"round {round_index} spent {spent}"
    assert len(called) <= (
        rounds * len(chain) * max(1, conflict_chain_max_retries(_policy_bundle()))
    )


def test_a_failed_attempt_is_charged_to_recovery_exactly_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Two verdicts on one failure spent the same-agent retry budget instantly.

    The default invoker records the failure and the driver classifies
    it; classifying in both places charged RecoveryController twice for
    one attempt, so a transient provider error failed over to the next
    candidate rather than retrying the agent it just lost.
    """
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
    ) -> None:
        charged.append(agent_name)
        original(
            session,
            agent_name,
            raw_failure,
            candidates=candidates,
            failed_index=failed_index,
            policy_bundle=policy_bundle,
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
    # 'one' is retried on a transient failure before the chain falls over,
    # which only happens while each attempt is charged once.
    assert charged.count("one") > 1
    assert "two" in charged


def test_one_candidates_exit_reason_is_not_pinned_on_the_next_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The executor parks a retry intent on failure and clears it only on success.

    A candidate that failed through an exception path therefore leaves
    its intent behind; the next candidate's plain failure used to be
    reported with that reason and to inherit its skip_same_agent_retries,
    spending a healthy agent's retry budget on somebody else's exit.
    """
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

    assert (
        invoke_resolution_agent(
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
        is False
    )
    assert session.last_attempt_failure == "conflict attempt failed"
    assert session.last_attempt_evidence != "PiProviderFailureExitError"
    assert session.skip_same_agent_retries is False


def test_work_that_ran_and_did_not_finish_is_incomplete_not_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A successful invocation that leaves markers has not answered anything.

    No agent can refuse this work -- the session has no tool for it --
    so a round that came back successful with markers still on disk is
    unfinished work, and the next round hands the same agent's successor
    the paths that still carry markers.
    """
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
    """The enum must not offer a state that means "the resolver said no".

    There is no MCP tool with which a resolution session could refuse a
    conflict: ``declare_complete`` is its only completion signal. A
    reason named for a refusal therefore described something that cannot
    happen, while hiding what did -- an attempt that failed, a candidate
    that never started, or work that did not finish.
    """
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
    """A misconfigured candidate must not own the stop's verdict or its budget.

    A chain naming an agent this workspace does not have is ordinary. When
    the healthy candidate after it really ran and really failed, the round
    reported the GHOST's fault and left the stop unchargeable -- and an
    unchargeable stop records no exhaustion evidence at all, so the run
    could never escalate honestly.
    """
    monkeypatch.setattr(
        driver_module, "resolution_chain_agents", lambda _bundle: ("ghost", "healthy")
    )
    monkeypatch.setattr(driver_module, "_sleep_seconds", lambda _seconds: None)
    _install_seams(monkeypatch, surviving_per_round=[_CONFLICTED])
    session = ResolutionSession(max_rounds_per_stop=1)
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
    assert called == ["ghost", "healthy"]
    assert "ghost" in session.dead_tool_surfaces
    assert "healthy" not in session.dead_tool_surfaces
    assert session.terminal_reason is ResolutionTerminationReason.ATTEMPT_FAILED
    assert session.charge_conflict_budget is True
    assert session.exhaustion_reason is not None
    assert session.exhaustion_reason.startswith(
        ResolutionTerminationReason.ATTEMPT_FAILED.value
    )


def test_the_executor_names_a_missing_agent_instead_of_failing_anonymously(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The registry lookup lives in the executor; only it should do it.

    Duplicating the check in conflict resolution meant building an
    AgentRegistry per candidate, and building one spawns subprocesses --
    a process spawn in front of every resolution attempt. The executor
    already knows, so it says so on the channel callers already read.
    """
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
