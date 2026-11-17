from __future__ import annotations

from pathlib import Path

import pytest
from loguru import logger

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline import run_loop
from ralph.pipeline.conflict_resolution import driver as driver_module
from ralph.pipeline.conflict_resolution.driver import run_conflict_resolution_pipeline
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.pipeline.conflict_resolution.session import ResolutionSession
from ralph.pipeline.integration_actions import install_executors, reset_executors
from ralph.pipeline.resolution_outcome import RetryLaterOutcome, WaitingOutcome
from ralph.pipeline.state import WaitReason
from ralph.recovery.agent_unavailability_tracker import UnavailabilityEntry
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.unavailability_reason import UnavailabilityReason
from tests._conflict_resolution_phase_parity_seams import (
    _config,
    _install_seams,
    _policy_bundle,
)

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
    "test_marker_only_conflict_keeps_shared_completion_evidence": (
        "invariant 13: resolver must not clear requires_completion_evidence when "
        "decision_paths is empty; shared default True like ordinary phases"
    ),
    "test_waiting_and_failure_status_use_lifecycle_not_warn_channel": (
        "agent waiting/failure lines must use emit_agent_lifecycle_status / status_text, "
        "not warn-channel emit_conflict_phase_line"
    ),
}


class _CooldownClock(FakeClock):
    def now(self) -> float:
        return self.monotonic()


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
