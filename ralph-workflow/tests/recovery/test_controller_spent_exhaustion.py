from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import UnifiedConfig
from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline import (
    RebaseResolutionResult,
    StopResolutionTrigger,
    attempt_stop_resolution,
)
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
    from ralph.git.conflict_stage_entries import ConflictStageEntries
    from ralph.pipeline.conflict_resolution import sight as sight_module

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
    monkeypatch.setattr(
        sight_module,
        "conflict_stage_entries",
        lambda _root, _paths: ConflictStageEntries({}),
    )


class _TestSessionRunner:
    def __init__(self, persistent_session: ResolutionSession, invoke: Any = None) -> None:
        self.persistent_session = persistent_session
        self.invoke = invoke

    def resolve_rebase(
        self,
        root: Path,
        target: str,
        resolver: Any,
        *,
        session: ResolutionSession,
    ) -> bool:
        session.__dict__ = self.persistent_session.__dict__
        return run_conflict_resolution_pipeline(
            root=root,
            target=target,
            config=_CONFIG,
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=_policy_bundle(),
            display=None,
            display_context=None,
            invoke=self.invoke,
            session=session,
        )


def drive_conflict_resolution_session(
    root: Path,
    session: ResolutionSession,
    *,
    invoke: Any = None,
) -> RebaseResolutionResult:
    runner = _TestSessionRunner(session, invoke=invoke)
    result = attempt_stop_resolution(
        None,
        StopResolutionTrigger(
            name="rebase_stop",
            root=root,
            target="main",
            resolver=lambda *args: False,
            agent_runner=runner,
        ),
    )
    assert isinstance(result, RebaseResolutionResult)
    return result


def test_prior_dead_launch_records_tool_surface_dead(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
    _install_seams(monkeypatch)
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            policy_bundle=_policy_bundle(),
            event_bus=FailureEventBus(),
        )
    )
    controller.note_retry_exhaustion(PHASE_RESOLUTION, "primary")
    controller.note_retry_exhaustion(PHASE_RESOLUTION, "fallback")

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

    result = attempt_stop_resolution(
        None,
        StopResolutionTrigger(
            name="rebase_stop",
            root=tmp_path,
            target="main",
            resolver=lambda *args: False,
        ),
    )

    assert isinstance(result, RebaseResolutionResult)
    assert result.succeeded is False
    assert result.reason is not None
    assert "spent/unavailable" in result.reason
    assert "git add -- a.py b.py" in result.reason
    assert result.failure_cause == ResolutionFailureCause.AGENT_FAILURE
    assert captured_session[0].terminal_reason is ResolutionTerminationReason.CANDIDATE_EXHAUSTED
    assert captured_session[0].terminal_reason not in RESOLVER_NOT_SPENT_TERMINATION_REASONS


def test_mixed_spent_and_cooldown_yields_candidate_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_seams(monkeypatch)
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            policy_bundle=_policy_bundle(),
            event_bus=FailureEventBus(),
        )
    )
    controller.note_retry_exhaustion(PHASE_RESOLUTION, "primary")
    controller.unavailability_store.mark_unavailable(
        PHASE_RESOLUTION, "fallback", UnavailabilityReason.NO_OUTPUT_AT_START
    )

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
    assert session.terminal_reason in RESOLVER_NOT_SPENT_TERMINATION_REASONS


def test_continuation_gate_key_changes_on_spent_transition_and_admits_one_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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

    avail_initial = controller.agents_now_available(
        "rebase_conflict_resolution", ["primary", "fallback"]
    )
    assert avail_initial == ["primary", "fallback"]

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
    assert key_spent != key_reset

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
    (rebase_dir / "git-rebase-todo").write_text("pick stop-1 subject\n", encoding="utf-8")

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
    assert resolver_invocations[0] == 1


def test_sequential_dead_launch_to_spent_repeat_exhaustion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
    invoked: list[str] = []

    def _dead_invoke(agent_name: str, prompt_path: Path, round_index: int) -> ResolutionOutcome:
        invoked.append(agent_name)
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

    result1 = drive_conflict_resolution_session(tmp_path, session, invoke=_dead_invoke)
    assert result1.succeeded is False
    assert invoked == ["primary", "fallback"]
    assert session.terminal_reason == ResolutionTerminationReason.TOOL_SURFACE_DEAD
    assert session.terminal_reason in RESOLVER_NOT_SPENT_TERMINATION_REASONS
    assert result1.reason is None

    controller.note_retry_exhaustion(PHASE_RESOLUTION, "primary")
    controller.note_retry_exhaustion(PHASE_RESOLUTION, "fallback")

    result2 = drive_conflict_resolution_session(tmp_path, session)
    assert result2.succeeded is False
    assert session.terminal_reason == ResolutionTerminationReason.CANDIDATE_EXHAUSTED
    assert session.terminal_reason not in RESOLVER_NOT_SPENT_TERMINATION_REASONS
    assert result2.failure_cause == ResolutionFailureCause.AGENT_FAILURE
    assert result2.reason is not None
    assert "spent/unavailable" in result2.reason
    assert "git add -- a.py b.py" in result2.reason


def test_stale_cooldown_overlap_with_controller_spent() -> None:
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            policy_bundle=_policy_bundle(),
            event_bus=FailureEventBus(),
        )
    )

    controller.unavailability_store.mark_unavailable(
        PHASE_RESOLUTION, "primary", UnavailabilityReason.NO_OUTPUT_AT_START
    )
    controller.note_retry_exhaustion(PHASE_RESOLUTION, "primary")

    assert controller.earliest_available_wait_ms(PHASE_RESOLUTION, ["primary"]) == 0
    assert controller.all_exhausted(PHASE_RESOLUTION, ["primary"]) is True

    controller.unavailability_store.mark_unavailable(
        PHASE_RESOLUTION, "fallback", UnavailabilityReason.NO_OUTPUT_AT_START
    )
    controller.note_retry_exhaustion(PHASE_RESOLUTION, "fallback")

    assert controller.earliest_available_wait_ms(PHASE_RESOLUTION, ["primary", "fallback"]) == 0
    assert controller.all_exhausted(PHASE_RESOLUTION, ["primary", "fallback"]) is True

    controller.reset_backoff(PHASE_RESOLUTION, "fallback")
    controller.unavailability_store.mark_unavailable(
        PHASE_RESOLUTION, "fallback", UnavailabilityReason.NO_OUTPUT_AT_START
    )

    wait_ms = controller.earliest_available_wait_ms(PHASE_RESOLUTION, ["primary", "fallback"])
    assert wait_ms > 0
    assert controller.all_exhausted(PHASE_RESOLUTION, ["primary", "fallback"]) is False
