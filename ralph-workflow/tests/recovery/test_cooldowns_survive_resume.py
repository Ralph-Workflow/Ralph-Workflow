from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline import checkpoint as ckpt
from ralph.pipeline import run_loop
from ralph.pipeline.runner import checkpoint_path_for_root
from ralph.pipeline.state import PipelineState
from ralph.recovery.agent_failure_ledger import Cooldown, Eligible
from ralph.recovery.unavailability_reason import UnavailabilityReason
from tests.recovery.test_cooldown_real_invocation_main_phases import _MainPhaseHarness

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.recovery.controller import RecoveryController

_PHASE = "development_analysis"
_REASON = UnavailabilityReason.PROVIDER_UNAVAILABLE
_ESCALATIONS = 6
_ESCALATED_MS = 60_000 << (_ESCALATIONS - 1)
_ELAPSED_BEFORE_RESTART_MS = 5 * 60_000
_REMAINING_MS = 27 * 60_000


def _escalated_harness(root: Path) -> _MainPhaseHarness:
    harness = _MainPhaseHarness(_PHASE, root)
    fallback = harness.agents[1]
    store = harness.controller.unavailability_store
    for _ in range(_ESCALATIONS):
        store.mark_unavailable(_PHASE, fallback, _REASON)
    harness.clock.advance(_ELAPSED_BEFORE_RESTART_MS / 1000)
    stepped = harness.run_step("unproductive")
    assert isinstance(stepped, PipelineState)
    harness.state = stepped
    return harness


def _resumed(harness: _MainPhaseHarness, root: Path, *, idle_ms: int = 0) -> RecoveryController:
    path = checkpoint_path_for_root(root)
    ckpt.save(harness.state, path)
    loaded = ckpt.load(path)
    assert loaded is not None
    restarted_clock = FakeClock(start=0.0, epoch_start=harness.clock.epoch() + idle_ms / 1000)
    controller, _cap = run_loop.build_recovery_controller(
        loaded, harness.bundle, harness.config, clock=restarted_clock
    )
    controller.register_phase_chain(_PHASE, harness.agents)
    return controller


def _remaining_ms(controller: RecoveryController, agent: str) -> int:
    status = controller.agent_eligibility(_PHASE, agent)
    assert isinstance(status, Cooldown), status
    return status.until_ms - int(controller.clock.monotonic() * 1000)


def test_a_provider_cooldown_with_27_minutes_left_survives_a_checkpoint_resume(
    tmp_git_repo: Path,
) -> None:
    harness = _escalated_harness(tmp_git_repo)
    fallback = harness.agents[1]

    assert _ESCALATED_MS - _ELAPSED_BEFORE_RESTART_MS == _REMAINING_MS
    controller = _resumed(harness, tmp_git_repo)

    assert _remaining_ms(controller, fallback) == _REMAINING_MS
    status = controller.agent_eligibility(_PHASE, fallback)
    assert isinstance(status, Cooldown)
    assert status.reason == str(_REASON)
    assert fallback not in controller.agents_now_available(_PHASE, list(harness.agents))

    controller.clock.advance(_REMAINING_MS / 1000)

    assert isinstance(controller.agent_eligibility(_PHASE, fallback), Eligible)
    assert fallback in controller.agents_now_available(_PHASE, list(harness.agents))


def test_the_backoff_keeps_escalating_after_a_resume(tmp_git_repo: Path) -> None:
    harness = _escalated_harness(tmp_git_repo)
    fallback = harness.agents[1]
    controller = _resumed(harness, tmp_git_repo)
    controller.clock.advance(_REMAINING_MS / 1000)

    controller.unavailability_store.mark_unavailable(_PHASE, fallback, _REASON)

    assert _remaining_ms(controller, fallback) == _ESCALATED_MS * 2


def test_the_remaining_window_counts_the_time_the_engine_was_down(tmp_git_repo: Path) -> None:
    harness = _escalated_harness(tmp_git_repo)
    fallback = harness.agents[1]

    controller = _resumed(harness, tmp_git_repo, idle_ms=_REMAINING_MS - 60_000)

    assert _remaining_ms(controller, fallback) == 60_000


def test_a_window_that_expired_while_the_engine_was_down_is_dropped(tmp_git_repo: Path) -> None:
    harness = _escalated_harness(tmp_git_repo)
    fallback = harness.agents[1]

    controller = _resumed(harness, tmp_git_repo, idle_ms=_REMAINING_MS + 1)

    assert isinstance(controller.agent_eligibility(_PHASE, fallback), Eligible)
    assert fallback not in controller.cooldown_snapshot()["unavailable_until_epoch_ms"]


def test_a_checkpoint_written_before_cooldowns_were_persisted_still_resumes(
    tmp_git_repo: Path,
) -> None:
    harness = _MainPhaseHarness(_PHASE, tmp_git_repo)
    path = checkpoint_path_for_root(tmp_git_repo)
    ckpt.save(harness.state, path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.pop("agent_cooldowns", None)
    path.write_text(json.dumps(raw), encoding="utf-8")

    loaded = ckpt.load(path)

    assert isinstance(loaded, PipelineState)
    assert loaded.agent_cooldowns is None
    controller, _cap = run_loop.build_recovery_controller(
        loaded, harness.bundle, harness.config, clock=FakeClock(start=0.0)
    )
    for agent in harness.agents:
        assert isinstance(controller.agent_eligibility(_PHASE, agent), Eligible)
