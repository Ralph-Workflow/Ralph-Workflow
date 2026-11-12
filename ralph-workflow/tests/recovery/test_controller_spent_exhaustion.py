"""Tests for spent-aware candidate chain exhaustion in RecoveryController.

Proves:
(1) Prior dead launch records TOOL_SURFACE_DEAD;
(2) When all candidates are controller-spent via note_retry_exhaustion,
    repeat through driver yields CANDIDATE_EXHAUSTED, exhaustion recorded
    by resolve_rebase_with_config, operator message names "spent/unavailable" + command;
(3) Mixed spent/cooldown: wait > 0, CANDIDATE_UNAVAILABLE, no exhaustion;
(4) Continuation gate key changes when agent transitions spent -> reset and admits exactly one retry.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import UnifiedConfig
from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline import resolve_rebase_with_config
from ralph.pipeline.auto_integrate_record import IntegrationRecord, read_record, write_record
from ralph.pipeline.auto_integrate_recovery_record import (
    compute_continuation_retry_gate_key,
    continue_retained_resolution,
)
from ralph.pipeline.conflict_resolution import (
    ResolutionTerminationReason,
)
from ralph.pipeline.conflict_resolution import (
    driver as driver_module,
)
from ralph.pipeline.conflict_resolution.attempt_fault import (
    RESOLVER_NOT_SPENT_TERMINATION_REASONS,
)
from ralph.pipeline.conflict_resolution.driver import run_conflict_resolution_pipeline
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.pipeline.conflict_resolution.session import ResolutionSession
from ralph.pipeline.integration_actions import install_executors, reset_executors
from ralph.policy.loader import load_policy
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.events import FailureEventBus
from ralph.recovery.unavailability_reason import UnavailabilityReason

if TYPE_CHECKING:
    from ralph.policy.models import PolicyBundle

from ralph.pipeline.conflict_resolution.resolution_outcome import ResolutionOutcome

_CONFLICTED = ["a.py", "b.py"]
_POLICY_BUNDLE = load_policy(Path(__file__).resolve().parents[2] / "ralph" / "policy" / "defaults")
_CONFIG = UnifiedConfig.model_validate({"general": {}})

pytestmark = pytest.mark.timeout_seconds(10)


def _policy_bundle() -> PolicyBundle:
    return _POLICY_BUNDLE


def _install_seams(
    monkeypatch: pytest.MonkeyPatch,
    *,
    unmerged: Sequence[str] = _CONFLICTED,
) -> None:
    monkeypatch.setattr(driver_module, "unmerged_paths", lambda _root: list(unmerged))
    monkeypatch.setattr(
        driver_module,
        "paths_with_conflict_markers",
        lambda _root, _paths: list(unmerged),
    )
    monkeypatch.setattr(
        driver_module, "resolution_chain_agents", lambda _bundle: ("primary", "fallback")
    )
    monkeypatch.setattr(
        driver_module,
        "render_conflict_prompt",
        lambda *, root, **_kwargs: root / "resolution-prompt.md",
    )


def test_prior_dead_launch_records_tool_surface_dead(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """(1) Prior dead launch records TOOL_SURFACE_DEAD."""
    _install_seams(monkeypatch)
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            policy_bundle=_policy_bundle(),
            event_bus=FailureEventBus(),
        )
    )
    session = ResolutionSession(recovery_controller=controller)
    invoked_agents: list[str] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> ResolutionOutcome:
        invoked_agents.append(agent_name)
        session.last_attempt_saw_activity = True
        return ResolutionOutcome(
            succeeded=False,
            reason=ResolutionTerminationReason.TOOL_SURFACE_DEAD,
            duration_seconds=1.0,
            last_activity_kind="process_exit",
            last_activity_at=clock.monotonic(),
            unresolved_paths=tuple(_CONFLICTED),
            failure_cause=ResolutionFailureCause.AGENT_FAILURE,
        )

    success = run_conflict_resolution_pipeline(
        root=tmp_path,
        target="main",
        config=_CONFIG,
        pipeline_deps=None,
        workspace_scope=None,
        policy_bundle=_policy_bundle(),
        display=None,
        display_context=None,
        invoke=_invoke,
        session=session,
    )
    assert success is False
    assert invoked_agents == ["primary", "fallback"]
    assert session.terminal_reason is ResolutionTerminationReason.TOOL_SURFACE_DEAD
    assert session.terminal_reason in RESOLVER_NOT_SPENT_TERMINATION_REASONS


def test_all_controller_spent_repeat_yields_candidate_exhausted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """(2) When all candidates are controller-spent via note_retry_exhaustion,

    repeat through driver yields CANDIDATE_EXHAUSTED, exhaustion is recorded
    by resolve_rebase_with_config, and operator message names 'spent/unavailable' + command.
    """
    _install_seams(monkeypatch)
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            policy_bundle=_policy_bundle(),
            event_bus=FailureEventBus(),
        )
    )
    # Mark every candidate controller-spent via note_retry_exhaustion
    controller.note_retry_exhaustion(PHASE_RESOLUTION, "primary")
    controller.note_retry_exhaustion(PHASE_RESOLUTION, "fallback")

    # Controller confirms exhaustion math
    assert controller.all_exhausted(PHASE_RESOLUTION, ["primary", "fallback"]) is True
    assert controller.earliest_available_wait_ms(PHASE_RESOLUTION, ["primary", "fallback"]) == 0

    from ralph.pipeline import auto_integrate_rebase_merge as merge_mod

    captured_session: list[ResolutionSession] = []

    def _fake_resolve_rebase(
        root: Path,
        target: str,
        _resolver: Any,
        *,
        session: ResolutionSession | None = None,
    ) -> bool:
        assert session is not None
        captured_session.append(session)
        session.recovery_controller = controller
        # Real prior launch was attempted in an earlier round/stop
        session.rounds_spent = 1
        session.charge_conflict_budget = True

        return run_conflict_resolution_pipeline(
            root=root,
            target=target,
            config=_CONFIG,
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=_policy_bundle(),
            display=None,
            display_context=None,
            session=session,
        )

    monkeypatch.setattr(merge_mod, "resolve_rebase_in_progress", _fake_resolve_rebase)

    result = resolve_rebase_with_config(
        tmp_path,
        "main",
        lambda *args: False,
        config=None,
    )

    assert result.succeeded is False
    assert result.reason is not None
    # Operator message names "spent/unavailable" and the actionable git command
    assert "spent/unavailable" in result.reason
    assert "git add -- a.py b.py" in result.reason
    # Exhaustion was recorded (result cause is AGENT_FAILURE, not dropped)
    assert result.failure_cause == ResolutionFailureCause.AGENT_FAILURE
    assert captured_session[0].terminal_reason is ResolutionTerminationReason.CANDIDATE_EXHAUSTED
    assert captured_session[0].terminal_reason not in RESOLVER_NOT_SPENT_TERMINATION_REASONS


def test_mixed_spent_and_cooldown_yields_candidate_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """(3) Mixed spent/cooldown: wait > 0, CANDIDATE_UNAVAILABLE, no exhaustion."""
    _install_seams(monkeypatch)
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            policy_bundle=_policy_bundle(),
            event_bus=FailureEventBus(),
        )
    )
    # primary is controller-spent
    controller.note_retry_exhaustion(PHASE_RESOLUTION, "primary")
    # fallback is on cooldown in the tracker
    controller.unavailability_store.mark_unavailable(
        PHASE_RESOLUTION, "fallback", UnavailabilityReason.NO_OUTPUT_AT_START
    )

    # Exhaustion math checks
    wait_ms = controller.earliest_available_wait_ms(PHASE_RESOLUTION, ["primary", "fallback"])
    assert wait_ms > 0
    assert controller.all_exhausted(PHASE_RESOLUTION, ["primary", "fallback"]) is False

    session = ResolutionSession(recovery_controller=controller)
    session.rounds_spent = 1
    session.charge_conflict_budget = True

    class _NoSleepClock:
        def monotonic(self) -> float:
            return 0.0

        def sleep(self, seconds: float) -> None:
            pass

    token = install_executors(clock=_NoSleepClock())
    try:
        success = run_conflict_resolution_pipeline(
            root=tmp_path,
            target="main",
            config=_CONFIG,
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=_policy_bundle(),
            display=None,
            display_context=None,
            session=session,
        )
    finally:
        reset_executors(token)

    assert success is False
    assert session.terminal_reason is ResolutionTerminationReason.CANDIDATE_UNAVAILABLE
    # CANDIDATE_UNAVAILABLE is in RESOLVER_NOT_SPENT_TERMINATION_REASONS -> no exhaustion recorded
    assert session.terminal_reason in RESOLVER_NOT_SPENT_TERMINATION_REASONS


def test_continuation_gate_key_changes_on_spent_transition_and_admits_one_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """(4) Continuation gate key changes when agent transitions spent -> reset and admits exactly one retry."""
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            policy_bundle=_policy_bundle(),
            event_bus=FailureEventBus(),
        )
    )

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="1111111111111111111111111111111111111111",
        pre_target_sha="2222222222222222222222222222222222222222",
        resolving_rebase=True,
    )
    write_record(tmp_path, record)

    # 1. Initially both agents are available
    avail_initial = controller.agents_now_available(
        "rebase_conflict_resolution", ["primary", "fallback"]
    )
    assert avail_initial == ["primary", "fallback"]

    # 2. Mark primary controller-spent via note_retry_exhaustion
    controller.note_retry_exhaustion("rebase_conflict_resolution", "primary")
    avail_spent = controller.agents_now_available(
        "rebase_conflict_resolution", ["primary", "fallback"]
    )
    assert avail_spent == ["fallback"]

    key_spent = compute_continuation_retry_gate_key(
        tmp_path,
        record,
        recovery_controller=controller,
        phase="rebase_conflict_resolution",
        chain_agents=["primary", "fallback"],
        stop_identity="stop-1",
    )
    assert key_spent[1] == ("fallback",)

    # 3. Reset backoff restores primary
    controller.reset_backoff("rebase_conflict_resolution", "primary")
    avail_reset = controller.agents_now_available(
        "rebase_conflict_resolution", ["primary", "fallback"]
    )
    assert avail_reset == ["primary", "fallback"]

    key_reset = compute_continuation_retry_gate_key(
        tmp_path,
        record,
        recovery_controller=controller,
        phase="rebase_conflict_resolution",
        chain_agents=["primary", "fallback"],
        stop_identity="stop-1",
    )
    assert key_reset[1] == ("fallback", "primary")
    # Gate key changed on spent -> reset transition
    assert key_spent != key_reset

    # 4. Continuation gate admits exactly one retry
    from ralph.pipeline import auto_integrate_rebase_merge as merge_mod

    git_dir = tmp_path / ".git"
    git_dir.mkdir(parents=True, exist_ok=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git_dir / "config").write_text("[core]\n\trepositoryformatversion = 0\n", encoding="utf-8")
    (git_dir / "objects").mkdir(parents=True, exist_ok=True)
    (git_dir / "refs" / "heads").mkdir(parents=True, exist_ok=True)
    (git_dir / "refs" / "heads" / "main").write_text("0" * 40 + "\n", encoding="utf-8")

    rebase_dir = git_dir / "rebase-merge"
    rebase_dir.mkdir(parents=True, exist_ok=True)
    (rebase_dir / "head-name").write_text("refs/heads/feature\n", encoding="utf-8")
    (rebase_dir / "orig-head").write_text(
        "1111111111111111111111111111111111111111\n", encoding="utf-8"
    )
    (rebase_dir / "onto").write_text("2222222222222222222222222222222222222222\n", encoding="utf-8")
    (rebase_dir / "stopped-sha").write_text("stop-1\n", encoding="utf-8")

    resolver_invocations = [0]

    def _fake_resolve_rebase_in_progress(
        root: Path,
        target: str,
        resolver: Any,
        *,
        session: ResolutionSession | None = None,
    ) -> bool:
        resolver_invocations[0] += 1
        return False

    monkeypatch.setattr(merge_mod, "resolve_rebase_in_progress", _fake_resolve_rebase_in_progress)

    # When record has key_spent and controller is still spent, gate is closed:
    controller.note_retry_exhaustion("rebase_conflict_resolution", "primary")
    record_spent = record.with_resolution_gate_key(key_spent)
    write_record(tmp_path, record_spent)

    status1 = continue_retained_resolution(
        tmp_path,
        record_spent,
        config=None,
        conflict_resolver=None,
        rebase_stop_resolver=lambda *args: False,
        recovery_controller=controller,
        phase="rebase_conflict_resolution",
        chain_agents=["primary", "fallback"],
        config_path=None,
        force_retry=False,
        stop_identity="stop-1",
    )
    assert isinstance(status1, str) and "WAITING" in status1
    assert resolver_invocations[0] == 0

    # Reset backoff opens the gate: exactly one retry is admitted
    controller.reset_backoff("rebase_conflict_resolution", "primary")

    status2 = continue_retained_resolution(
        tmp_path,
        read_record(tmp_path) or record_spent,
        config=None,
        conflict_resolver=None,
        rebase_stop_resolver=lambda *args: False,
        recovery_controller=controller,
        phase="rebase_conflict_resolution",
        chain_agents=["primary", "fallback"],
        config_path=None,
        force_retry=False,
        stop_identity="stop-1",
    )
    assert status2 is not None
    assert resolver_invocations[0] == 1

    # Immediate next loop without changes hits the gate again (no hot loop)
    status3 = continue_retained_resolution(
        tmp_path,
        read_record(tmp_path) or record_spent,
        config=None,
        conflict_resolver=None,
        rebase_stop_resolver=lambda *args: False,
        recovery_controller=controller,
        phase="rebase_conflict_resolution",
        chain_agents=["primary", "fallback"],
        config_path=None,
        force_retry=False,
        stop_identity="stop-1",
    )
    assert isinstance(status3, str) and "WAITING" in status3
    assert resolver_invocations[0] == 1  # not invoked again!
