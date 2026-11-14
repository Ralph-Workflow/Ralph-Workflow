from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ralph.policy.models import PolicyBundle

from ralph.agents.clock import Clock
from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.pipeline.conflict_resolution import driver as driver_module
from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)
from ralph.pipeline.conflict_resolution.chain_candidates import skipped_candidates
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.pipeline.conflict_resolution.session import (
    ResolutionSession,
    classify_failed_resolution_attempt,
)
from ralph.pipeline.conflict_resolution.session import (
    invoke_resolution_agent as _orig_invoke_resolution_agent,
)
from ralph.pipeline.integration_actions import install_executors, reset_executors
from ralph.pipeline.resolution_outcome import (
    ResolvedOutcome,
    RetryLaterOutcome,
    StopResolutionOutcome,
)
from ralph.recovery import dead_agent_probe
from ralph.recovery.agent_failure_ledger import AgentFailureLedger, AttemptFact
from ralph.recovery.canary_probe import ProbeOutcome, ProbeResult
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from tests._conflict_resolution_phase_parity_seams import (
    _CONFLICTED,
    _config,
    _install_seams,
    _policy_bundle,
)

_orig_run_conflict_resolution_pipeline = driver_module.run_conflict_resolution_pipeline


def _ensure_parity_session_controller(
    session: ResolutionSession,
    config: UnifiedConfig,
    policy_bundle: object,
) -> None:
    from ralph.pipeline.integration_actions import current_clock
    from ralph.recovery.dead_agent_probe import production_dead_agent_probe_runner

    if not isinstance(session.recovery_controller, RecoveryController):

        class _ClockWrapper(Clock):
            def __init__(self, inner: object) -> None:
                self._inner = inner

            def monotonic(self) -> float:
                return getattr(self._inner, "monotonic", time.monotonic)()

            def epoch(self) -> float:
                return getattr(self._inner, "epoch", getattr(self._inner, "monotonic", time.time))()

            def sleep(self, seconds: float) -> None:
                getattr(self._inner, "sleep", time.sleep)(seconds)

            def now_utc(self) -> datetime:
                return datetime.now(UTC)

            def wait_for_event(self, event: threading.Event, seconds: float) -> bool:
                return event.wait(seconds)

        cw = _ClockWrapper(current_clock())
        probe_runner = production_dead_agent_probe_runner(config)
        controller = RecoveryController(
            options=RecoveryControllerOptions(
                clock=cw,
                policy_bundle=policy_bundle,
                dead_agent_probe_runner=probe_runner,
            ),
            ledger=session.ledger or AgentFailureLedger(clock=cw),
        )
        session.recovery_controller = controller
        session.ledger = controller.ledger


_orig_classify_failed_resolution_attempt = classify_failed_resolution_attempt


def classify_failed_resolution_attempt(
    session: ResolutionSession | None,
    agent_name: str,
    raw_failure: BaseException | str | None = None,
    *,
    candidates: Sequence[str] = (),
    failed_index: int = 0,
    policy_bundle: PolicyBundle | None = None,
    **kwargs: object,
) -> None:
    if session is not None:
        _ensure_parity_session_controller(session, _config(), policy_bundle)
    res = _orig_classify_failed_resolution_attempt(
        session,
        agent_name,
        raw_failure,
        candidates=candidates,
        failed_index=failed_index,
        policy_bundle=policy_bundle,
        **kwargs,
    )
    if session is not None and session.skip_same_agent_retries:
        session.skip_same_agent_retries = False
    return res


def run_conflict_resolution_pipeline(
    *,
    root: Path,
    target: str,
    config: UnifiedConfig,
    pipeline_deps: object = None,
    workspace_scope: object = None,
    policy_bundle: object = None,
    display: object = None,
    display_context: object = None,
    invoke: object = None,
    clock: object = None,
    session: ResolutionSession | None = None,
    **kwargs: object,
) -> bool:
    from ralph.pipeline.conflict_resolution import resolution_session_from_config

    active_session = session if session is not None else resolution_session_from_config(config)
    _ensure_parity_session_controller(active_session, config, policy_bundle)

    adapted_invoke = None
    if invoke is not None:

        def _invoke_adapter(*args: object, **kw: object) -> StopResolutionOutcome:
            res = invoke(*args, **kw)
            if isinstance(res, bool):
                return ResolvedOutcome() if res else RetryLaterOutcome(on_change="unresolved")
            if isinstance(res, StopResolutionOutcome):
                return res
            raise TypeError(f"Unexpected invoke return type: {type(res)}")

        adapted_invoke = _invoke_adapter

    res = _orig_run_conflict_resolution_pipeline(
        root=root,
        target=target,
        config=config,
        pipeline_deps=pipeline_deps,
        workspace_scope=workspace_scope,
        policy_bundle=policy_bundle,
        display=display,
        display_context=display_context,
        invoke=adapted_invoke,
        clock=clock,
        session=active_session,
        **kwargs,
    )
    return bool(res)


def invoke_resolution_agent(
    *,
    agent_name: str,
    prompt_path: Path,
    config: UnifiedConfig,
    pipeline_deps: object = None,
    workspace_scope: object = None,
    policy_bundle: object = None,
    display: object = None,
    display_context: object = None,
    session: ResolutionSession | None = None,
    **kwargs: object,
) -> bool:
    from ralph.pipeline.conflict_resolution import resolution_session_from_config

    active_session = session if session is not None else resolution_session_from_config(config)
    _ensure_parity_session_controller(active_session, config, policy_bundle)

    res = _orig_invoke_resolution_agent(
        agent_name=agent_name,
        prompt_path=prompt_path,
        config=config,
        pipeline_deps=pipeline_deps,
        workspace_scope=workspace_scope,
        policy_bundle=policy_bundle,
        display=display,
        display_context=display_context,
        session=active_session,
        **kwargs,
    )
    return bool(res)


if TYPE_CHECKING:
    import pytest


def _stub_canary_probe_boundary(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    probed: list[str] = []

    def fake_run_canary_probe(config: AgentConfig, **_kwargs: object) -> ProbeResult:
        probed.append(config.cmd)
        return ProbeResult(outcome=ProbeOutcome.INCONCLUSIVE, detail="scripted")

    monkeypatch.setattr(dead_agent_probe, "run_canary_probe", fake_run_canary_probe)
    return probed


def test_four_agent_chain_tries_every_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        driver_module, "resolution_chain_agents", lambda _bundle: ("one", "two", "three", "four")
    )
    clock = FakeClock()

    class _FakeClockAdapter:
        def now(self) -> float:
            return clock.monotonic()

        def sleep(self, seconds: float) -> None:
            clock.sleep(seconds)

    token = install_executors(clock=_FakeClockAdapter())
    try:
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
                clock=clock.monotonic,
            )
            is False
        )
        seen: list[str] = []
        for name in called:
            if not seen or seen[-1] != name:
                seen.append(name)
        assert seen[:4] == ["one", "two", "three", "four"]
    finally:
        reset_executors(token)


def test_transient_failure_retries_the_same_agent_using_recovery_controller_handle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ("one", "two"))
    monkeypatch.setattr(
        "ralph.pipeline.conflict_resolution.driver._sleep_seconds", lambda _seconds: None
    )
    _install_seams(monkeypatch, surviving_per_round=[_CONFLICTED, _CONFLICTED, _CONFLICTED])
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
    sleeps: list[float] = []
    monkeypatch.setattr("ralph.pipeline.conflict_resolution.driver._sleep_seconds", sleeps.append)
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ("one", "two"))
    _install_seams(monkeypatch, surviving_per_round=[_CONFLICTED, _CONFLICTED, _CONFLICTED])

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
    from ralph.recovery.controller import RecoveryController

    handled: list[str] = []
    original = RecoveryController.finalize_attempt

    def _spy(
        self: RecoveryController,
        phase: str,
        agent: str | None,
        *,
        facts: object,
        attempt_failed: bool,
        exc: BaseException | None = None,
        **kw: object,
    ) -> object:
        if exc is not None:
            handled.append(str(exc))
        return original(
            self, phase, agent, facts=facts, attempt_failed=attempt_failed, exc=exc, **kw
        )

    monkeypatch.setattr(RecoveryController, "finalize_attempt", _spy)
    monkeypatch.setattr(driver_module, "_sleep_seconds", lambda _s: None)
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ("one", "two"))
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


def test_next_round_keeps_the_successful_highest_priority_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        driver_module, "resolution_chain_agents", lambda _bundle: ("one", "two", "three", "four")
    )
    _install_seams(
        monkeypatch,
        unmerged=["src/alpha.py", "src/beta.py"],
        surviving_per_round=[["src/alpha.py"], ["src/alpha.py"], []],
    )
    called: list[tuple[str, int]] = []
    session = ResolutionSession()

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        called.append((agent_name, round_index))
        if round_index == 2:
            session.recovery_controller.ledger.mark_unavailable(
                PHASE_RESOLUTION, "one", reason="unproductive"
            )
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
    assert called[0] == ("one", 1)
    assert called[1] == ("one", 2)
    assert called[2] == ("two", 3)


def test_invoke_resolution_agent_keeps_chain_retry_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: list[int] = []
    probed = _stub_canary_probe_boundary(monkeypatch)

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
    assert probed, "the failed resolution attempt must still reach the dead-agent probe boundary"


def test_launch_failure_is_not_collapsed_to_candidate_declined(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(driver_module, "_sleep_seconds", lambda _s: None)
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


def test_failed_invoke_routes_launch_provider_and_decline_through_shared_finalizer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from ralph.recovery.controller import RecoveryController

    seen: list[str] = []

    original = RecoveryController.finalize_attempt

    def _spy(
        self: RecoveryController,
        phase: str,
        agent: str | None,
        **kwargs: object,
    ) -> object:
        exc = kwargs.get("exc")
        text = str(exc).lower() if exc is not None else ""
        if "launch" in text:
            seen.append("launch")
        elif "provider" in text:
            seen.append("provider")
        else:
            seen.append("decline")
        return original(self, phase, agent, **kwargs)

    monkeypatch.setattr(
        RecoveryController,
        "finalize_attempt",
        _spy,
        raising=False,
    )
    monkeypatch.setattr(
        driver_module, "resolution_chain_agents", lambda _bundle: ("one", "two", "three")
    )
    monkeypatch.setattr(
        "ralph.pipeline.conflict_resolution.driver._sleep_seconds", lambda _seconds: None
    )
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
    monkeypatch.setattr(
        driver_module, "resolution_chain_agents", lambda _bundle: ("one", "two", "three", "four")
    )
    clock = FakeClock()

    class _FakeClockAdapter:
        def now(self) -> float:
            return clock.monotonic()

        def sleep(self, seconds: float) -> None:
            clock.sleep(seconds)

    token = install_executors(clock=_FakeClockAdapter())
    try:
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
                clock=clock.monotonic,
                session=session,
            )
            is False
        )
        seen: list[str] = []
        for name in called:
            if not seen or seen[-1] != name:
                seen.append(name)
        assert seen[:4] == ["one", "two", "three", "four"]
    finally:
        reset_executors(token)


def test_round_after_a_spent_chain_still_invokes_a_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ("one", "two"))
    _install_seams(monkeypatch, surviving_per_round=[_CONFLICTED, _CONFLICTED, _CONFLICTED])
    clock = FakeClock()
    session = ResolutionSession(ledger=AgentFailureLedger(clock=clock))
    called: list[tuple[int, str]] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        called.append((round_index, agent_name))
        return agent_name == "two"

    config = UnifiedConfig.model_validate(
        {
            "conflict_resolution": {"max_rounds_per_stop": 3},
        }
    )

    class _FakeClockAdapter:
        def now(self) -> float:
            return clock.monotonic()

        def sleep(self, seconds: float) -> None:
            clock.sleep(seconds)

    token = install_executors(clock=_FakeClockAdapter())
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
                clock=clock.monotonic,
                session=session,
            )
            is False
        )
    finally:
        reset_executors(token)

    cap = config.conflict_resolution.max_rounds_per_stop
    invoked_rounds = sorted({round_index for round_index, _ in called})
    assert invoked_rounds == list(range(1, cap + 1))
    assert len(called) > len(invoked_rounds)


def test_a_new_stop_restarts_the_chain_on_a_reused_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from ralph.recovery.agent_failure_ledger import AgentFailureLedger

    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ("one", "two"))
    clock = FakeClock()
    session = ResolutionSession(ledger=AgentFailureLedger(clock=clock))
    _install_seams(monkeypatch, surviving_per_round=[_CONFLICTED, _CONFLICTED, _CONFLICTED, []])

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
            clock=clock.monotonic,
            session=session,
        )
        is True
    )
    clock.advance(60.0)
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
            clock=clock.monotonic,
            session=session,
        )
        is True
    )
    assert second_stop == ["one"]


def test_a_round_that_invokes_nobody_is_never_reported_as_a_failed_attempt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ("one", "two"))
    _install_seams(monkeypatch, surviving_per_round=[_CONFLICTED, _CONFLICTED, _CONFLICTED])
    clock = FakeClock()
    ledger = AgentFailureLedger(clock=clock, total_limit=1)
    for agent in ("one", "two"):
        ledger.record(
            AttemptFact(
                phase=PHASE_RESOLUTION,
                agent=agent,
                artifact_valid=False,
            )
        )
    session = ResolutionSession(ledger=ledger)
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
        line.startswith(ResolutionTerminationReason.CANDIDATE_EXHAUSTED.value) for line in lines
    )


def test_a_dead_chain_stops_burning_rounds(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ("one",))
    _install_seams(monkeypatch, surviving_per_round=[_CONFLICTED, _CONFLICTED, _CONFLICTED])
    clock = FakeClock()
    ledger = AgentFailureLedger(clock=clock, total_limit=1)
    ledger.record(
        AttemptFact(
            phase=PHASE_RESOLUTION,
            agent="one",
            artifact_valid=False,
        )
    )
    session = ResolutionSession(ledger=ledger)
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
    assert effect_executor_module.pop_last_captured_retry_intent().failure_reason == ""


def test_a_resolver_that_ran_and_came_back_unsuccessful_is_a_failed_attempt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from ralph.pipeline.events import PipelineEvent

    def _ran_then_refused(*args: object, **kwargs: object) -> object:
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
    from ralph.pipeline import effect_executor as effect_executor_module
    from ralph.pipeline.agent_retry_intent import AgentRetryIntent
    from ralph.pipeline.events import PipelineEvent

    def _agent_not_found(*args: object, **kwargs: object) -> object:
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
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ("pi", "claude"))
    monkeypatch.setattr(driver_module, "_sleep_seconds", lambda _seconds: None)
    _install_seams(monkeypatch, surviving_per_round=[_CONFLICTED, []])
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
    assert "pi" in skipped_candidates(session, ("pi", "claude"))
    assert "claude" not in skipped_candidates(session, ("pi", "claude"))


def test_skip_same_agent_retries_advances_the_chain_instead_of_retrying(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
    assert session.skip_same_agent_retries is False
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


def test_a_live_candidate_behind_the_cursor_is_still_invoked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        driver_module, "resolution_chain_agents", lambda _bundle: ("alpha", "beta", "gamma")
    )
    monkeypatch.setattr(driver_module, "_sleep_seconds", lambda _seconds: None)
    _install_seams(monkeypatch, surviving_per_round=[_CONFLICTED, _CONFLICTED, _CONFLICTED])
    clock = FakeClock()
    ledger = AgentFailureLedger(clock=clock, total_limit=1)
    for agent in ("beta", "gamma"):
        ledger.record(
            AttemptFact(
                phase=PHASE_RESOLUTION,
                agent=agent,
                artifact_valid=False,
            )
        )
    session = ResolutionSession(chain_cursor=1, ledger=ledger)
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
    assert skipped_candidates(session, ("one", "two")) == frozenset()
    assert session.terminal_reason is ResolutionTerminationReason.TRANSPORT_LOOP_DETECTED


def test_conflict_invocations_skip_cooldown_and_restore_priority(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from ralph.agents.timeout_clock import FakeClock
    from ralph.recovery.agent_unavailability_tracker import UnavailabilityEntry
    from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
    from ralph.recovery.unavailability_reason import UnavailabilityReason

    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            unavailability_entries={
                "one": UnavailabilityEntry(
                    unavailable_until_ms=5000,
                    reason=UnavailabilityReason.NO_OUTPUT_AT_START,
                    attempt=0,
                    base_backoff_ms=5000,
                    max_backoff_ms=18000000,
                )
            },
        )
    )
    session = ResolutionSession(chain_cursor=1, recovery_controller=controller)
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ("one", "two"))
    _install_seams(monkeypatch, surviving_per_round=[_CONFLICTED, _CONFLICTED, []])
    called: list[tuple[str, float]] = []

    def invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        called.append((agent_name, clock.monotonic()))
        clock.advance(6.0)
        return True

    class _FakeClockAdapter:
        def now(self) -> float:
            return clock.monotonic()

        def sleep(self, seconds: float) -> None:
            clock.sleep(seconds)

    token = install_executors(clock=_FakeClockAdapter())
    try:
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

    assert called[0][0] == "two"
    restored_priority_calls = [at for agent, at in called if agent == "one"]
    assert restored_priority_calls
    assert all(at >= 5.0 for at in restored_priority_calls)
