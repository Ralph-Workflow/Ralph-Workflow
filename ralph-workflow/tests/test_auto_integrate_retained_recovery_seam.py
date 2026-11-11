from __future__ import annotations

import importlib
import io
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from rich.console import Console

from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline import IntegrationAction
from ralph.pipeline.auto_integrate import recovery_retained_record
from ralph.pipeline.auto_integrate_record import IntegrationRecord
from ralph.pipeline.integration_resolution import (
    IntegrationResolutionStatus,
    IntegrationResolutionVerdict,
)
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState
from ralph.workspace.scope import WorkspaceScope
from tests.test_run_loop_failed_ladder_terminal import run_recovery_scenario

if TYPE_CHECKING:
    from pytest import MonkeyPatch

    from ralph.display.context import DisplayContext

_CONFIG = UnifiedConfig.model_validate(
    {"general": {"auto_integrate_enabled": True, "auto_integrate_target": "main"}}
)


class _RecordingDisplay:
    def __init__(self) -> None:
        self.warn_lines: list[str] = []

    def emit_warn_line(self, scope: str, channel: str, message: str) -> None:
        del scope, channel
        self.warn_lines.append(message)


def _config() -> UnifiedConfig:
    return _CONFIG


def _retained() -> RebaseState:
    return RebaseState(
        last_action="skipped",
        last_reason=("recovery: feature branch not restored, record retained for retry"),
        last_target="main",
        recovery_record_retained=True,
    )


def _reconciled() -> RebaseState:
    return RebaseState(
        last_action="recovered",
        last_reason="restored feature branch after interrupted rebase",
        last_target="main",
    )


def test_the_retention_predicate_reads_the_flag_not_the_reason_text() -> None:
    assert recovery_retained_record(_retained()) is True
    assert recovery_retained_record(_reconciled()) is False
    assert recovery_retained_record(None) is False
    assert (
        recovery_retained_record(
            RebaseState(
                last_action="skipped",
                last_reason="recovery: record retained for retry",
            )
        )
        is False
    ), "reason text alone must never make an outcome read as retained"


def _recovery_module() -> ModuleType:
    return importlib.import_module("ralph.pipeline.auto_integrate_recovery")


def _landing_module() -> ModuleType:
    return importlib.import_module("ralph.pipeline._recovery_fast_forward_landing")


def _fake_record(phase: str, *, integrated_sha: str | None = None) -> IntegrationRecord:
    return IntegrationRecord.model_validate(
        {
            "phase": phase,
            "target": "main",
            "pre_feature_sha": "a" * 40,
            "pre_target_sha": None,
            "integrated_feature_sha": integrated_sha,
        }
    )


def _stub_clean_git(recovery: ModuleType, monkeypatch: MonkeyPatch, record: object) -> None:
    monkeypatch.setattr(recovery, "_read_record", lambda _root, **_kwargs: record)
    monkeypatch.setattr(recovery, "_clear_record", lambda _root: None)
    monkeypatch.setattr(_landing_module(), "_clear_record", lambda _root: None)
    monkeypatch.setattr(recovery, "rebase_in_progress", lambda _root: False)
    monkeypatch.setattr(recovery, "abort_rebase_discarding_progress", lambda _root: None)
    monkeypatch.setattr(recovery, "merge_state", lambda _root: recovery.MERGE_STATE_NONE)
    monkeypatch.setattr(recovery, "abort_merge", lambda _root: True)
    landing = _landing_module()
    monkeypatch.setattr(landing, "post_attempt_verify", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(landing, "_delete_rebase_backup_refs", lambda _root, _ref: None)
    monkeypatch.setattr(landing, "_clear_record", lambda _root: None)
    monkeypatch.setattr(recovery, "_head_matches_sha", lambda _root, _sha: True)


def _boom(*_args: object, **_kwargs: object) -> None:
    raise RuntimeError("simulated failure")


def test_target_reconcile_recovery_never_resets_a_target_that_moved(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    recovery = _recovery_module()
    owner = tmp_path / "target-owner"
    record = recovery.IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="before",
        pre_target_sha="before",
        operation_kind="target_reconcile",
        owning_worktree=str(owner),
    )
    _stub_clean_git(recovery, monkeypatch, record)
    monkeypatch.setattr(recovery, "branch_sha", lambda _root, _target: "moved")
    cleared: list[Path] = []

    monkeypatch.setattr(recovery, "_clear_record", cleared.append)
    monkeypatch.setattr(_landing_module(), "_clear_record", cleared.append)

    outcome = recovery.recover_incomplete_integration(WorkspaceScope(tmp_path))

    assert recovery_retained_record(outcome) is True
    assert cleared == []


def test_unconfigured_pending_landing_retains_the_record(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    recovery = _recovery_module()
    _stub_clean_git(recovery, monkeypatch, _fake_record("integrating"))

    outcome = recovery.recover_incomplete_integration(WorkspaceScope(tmp_path))

    assert recovery_retained_record(outcome), (
        f"pending landing without configuration retains the record; got {outcome!r}"
    )


def test_an_unprovable_abort_retains_the_record_and_says_so_structurally(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    recovery = _recovery_module()
    _stub_clean_git(recovery, monkeypatch, _fake_record("integrated", integrated_sha="b" * 40))
    monkeypatch.setattr(recovery, "rebase_in_progress", lambda _root: True)
    monkeypatch.setattr(recovery, "abort_rebase_discarding_progress", _boom)

    outcome = recovery.recover_incomplete_integration(WorkspaceScope(tmp_path))

    assert recovery_retained_record(outcome), (
        f"an unprovable abort retains the record; got {outcome!r}"
    )


def test_an_unrefreshable_pointer_retains_the_record_and_says_so_structurally(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    recovery = _recovery_module()
    _stub_clean_git(recovery, monkeypatch, _fake_record("integrated", integrated_sha="b" * 40))
    monkeypatch.setattr(
        recovery,
        "_refresh_target",
        lambda _config, _root, _target: recovery.REFRESH_UNREACHABLE,
    )

    outcome = recovery.recover_incomplete_integration(WorkspaceScope(tmp_path), config=_config())

    assert recovery_retained_record(outcome), (
        f"an unrefreshable pointer retains the record; got {outcome!r}"
    )


def test_a_transient_fast_forward_failure_retains_the_record_structurally(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    recovery = _recovery_module()
    _stub_clean_git(recovery, monkeypatch, _fake_record("integrated", integrated_sha="b" * 40))
    monkeypatch.setattr(recovery, "branch_sha", lambda _root, _target: "c" * 40)
    monkeypatch.setattr(recovery, "ancestry_state", lambda _root, _target, _sha: True)
    monkeypatch.setattr(_landing_module(), "fast_forward_target", _boom)

    outcome = recovery.recover_incomplete_integration(WorkspaceScope(tmp_path))

    assert recovery_retained_record(outcome), (
        f"a fast-forward that raised retains the record; got {outcome!r}"
    )


@pytest.mark.parametrize(
    ("ff_result", "retained"),
    [
        ((True, ""), False),
        (
            (False, "target advanced concurrently"),
            True,
        ),
    ],
)
def test_landing_outcome_reports_whether_recovery_remains_owned(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    ff_result: tuple[bool, str],
    retained: bool,
) -> None:
    recovery = _recovery_module()
    _stub_clean_git(recovery, monkeypatch, _fake_record("integrated", integrated_sha="b" * 40))
    monkeypatch.setattr(recovery, "branch_sha", lambda _root, _target: "c" * 40)
    monkeypatch.setattr(recovery, "ancestry_state", lambda _root, _target, _sha: True)
    monkeypatch.setattr(
        _landing_module(),
        "fast_forward_target",
        lambda _root, _target, _sha, **_kwargs: ff_result,
    )

    outcome = recovery.recover_incomplete_integration(WorkspaceScope(tmp_path))

    assert recovery_retained_record(outcome) is retained


def test_an_unexpected_recovery_crash_keeps_integration_blocked(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    recovery = _recovery_module()
    monkeypatch.setattr(recovery, "_read_record", _boom)

    outcome = recovery.recover_incomplete_integration(WorkspaceScope(tmp_path))

    assert outcome is not None
    assert recovery_retained_record(outcome) is True


def _route_record(**overrides: object) -> IntegrationRecord:
    data: dict[str, object] = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
    }
    data.update(overrides)
    return IntegrationRecord.model_validate(data)


def test_the_startup_route_is_consumed_from_the_owner_verdict() -> None:
    recovery = _recovery_module()
    route = recovery._startup_recovery_route
    route_kind = recovery._StartupRecoveryRoute

    plain = _route_record()
    resolving = _route_record(resolving_rebase=True)
    reconcile = _route_record(operation_kind="target_reconcile")
    reintegrate = _route_record(reintegrate_pending=True)
    integrated = _route_record(phase="integrated", integrated_feature_sha="c" * 40)

    assert route(IntegrationAction.WAIT_FOR_BOUNDED_RETRY, resolving) is (
        route_kind.RECOVERY_OWNS_OPERATION
    )
    assert route(IntegrationAction.REQUIRE_INTERVENTION, reconcile) is (
        route_kind.RECOVERY_OWNS_OPERATION
    )
    assert route(IntegrationAction.WAIT_FOR_BOUNDED_RETRY, reintegrate) is (
        route_kind.RECOVERY_OWNS_OPERATION
    )
    assert route(IntegrationAction.REINTEGRATE_AGAINST_TARGET, integrated) is (
        route_kind.LANDING_CONTINUATION
    )
    assert route(IntegrationAction.LAND_TARGET, integrated) is (route_kind.LANDING_CONTINUATION)
    assert route(IntegrationAction.REQUIRE_INTERVENTION, plain) is route_kind.DEFER_INTERVENTION
    assert route(IntegrationAction.WAIT_FOR_BOUNDED_RETRY, plain) is route_kind.DEFER_RETRY
    for verdict in (
        IntegrationAction.RESOLVE_ACTIVE_OPERATION,
        IntegrationAction.CONTINUE_GIT_OPERATION,
        IntegrationAction.REINTEGRATE_AGAINST_TARGET,
        IntegrationAction.LAND_TARGET,
    ):
        assert route(verdict, plain) is route_kind.RECOVERY_OWNS_OPERATION


def test_startup_recovery_asks_the_owner_before_it_routes(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    recovery = _recovery_module()
    asked: list[bool] = []

    def fake_seam(root: object, **kwargs: object) -> IntegrationAction:
        del root, kwargs
        asked.append(True)
        return IntegrationAction.WAIT_FOR_BOUNDED_RETRY

    monkeypatch.setattr(recovery, "decide_seam_action", fake_seam)
    _stub_clean_git(recovery, monkeypatch, _route_record())

    outcome = recovery.recover_incomplete_integration(WorkspaceScope(tmp_path))

    assert asked, "the startup boundary must ask the single owner before routing"
    assert recovery.recovery_retained_record(outcome)


def _run_loop_module() -> ModuleType:
    return importlib.import_module("ralph.pipeline.run_loop")


def _install_run_loop_seams(
    module: ModuleType, monkeypatch: MonkeyPatch, recovered: RebaseState | None
) -> tuple[list[str], list[PipelineState]]:
    events: list[str] = []
    saves: list[PipelineState] = []

    def _fake_recover(
        workspace_scope: object, config: object = None, **_kwargs: object
    ) -> RebaseState | None:
        del workspace_scope, config
        events.append("recover")
        return recovered

    def _fake_startup(ctx: object, state: RebaseState | None = None) -> RebaseState | None:
        del ctx, state
        events.append("integrate")
        return RebaseState(last_action="fast_forwarded", last_target="main")

    def _fake_save(state: PipelineState, ctx: object) -> None:
        del ctx
        events.append("save")
        saves.append(state)

    monkeypatch.setattr(module, "_run_auto_integrate_recovery_preamble", _fake_recover)
    monkeypatch.setattr(module, "_run_startup_integration", _fake_startup)
    monkeypatch.setattr(module, "_save_recovered_rebase_checkpoint", _fake_save)
    return events, saves


def _loop_ctx(tmp_path: Path, display: object) -> object:
    return SimpleNamespace(
        workspace_scope=WorkspaceScope(tmp_path),
        config=_config(),
        active_display=display,
    )


def test_run_loop_does_not_integrate_over_a_retained_recovery_record(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    module = _run_loop_module()
    events, _saves = _install_run_loop_seams(module, monkeypatch, _retained())
    display = _RecordingDisplay()

    state = module._apply_startup_rebase_outcomes(
        PipelineState(phase="planning"), _loop_ctx(tmp_path, display)
    )

    assert "integrate" not in events, (
        "startup integration writes its own durable record before mutating"
        f" git, so it must not run after a retained recovery; got {events!r}"
    )
    assert state.rebase.recovery_record_retained is True, (
        "the retained recovery outcome must survive into the checkpointed"
        " state so the next startup sees it"
    )


def test_run_loop_persists_and_announces_the_deferred_catch_up(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    module = _run_loop_module()
    events, saves = _install_run_loop_seams(module, monkeypatch, _retained())
    display = _RecordingDisplay()

    module._apply_startup_rebase_outcomes(
        PipelineState(phase="planning"), _loop_ctx(tmp_path, display)
    )

    assert "save" in events, "the retained outcome must still be checkpointed"
    assert saves and saves[0].rebase.recovery_record_retained is True
    assert any("deferred" in line for line in display.warn_lines), (
        f"expected an operator-visible deferral line, got {display.warn_lines!r}"
    )


def test_a_display_that_cannot_take_the_line_never_aborts_startup(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    module = _run_loop_module()
    _events, _saves = _install_run_loop_seams(module, monkeypatch, _retained())

    class _HostileDisplay:
        def emit_warn_line(self, *_args: object) -> None:
            raise RuntimeError("simulated display failure")

    state = module._apply_startup_rebase_outcomes(
        PipelineState(phase="planning"), _loop_ctx(tmp_path, _HostileDisplay())
    )

    assert state.rebase.recovery_record_retained is True


def test_persisted_legacy_conflict_blocks_shared_loop_before_dispatch(
    monkeypatch: MonkeyPatch,
) -> None:
    state = PipelineState(
        phase="planning",
        rebase=RebaseState(
            last_action="conflict", legacy_checkpoint_blocked=True, last_reason="legacy conflict"
        ),
    )

    observed = run_recovery_scenario(monkeypatch, state, startup=True)

    assert observed.exit_code == 0
    assert observed.waits and all(delay > 0 for delay in observed.waits)
    assert observed.saved[0].rebase == state.rebase
    assert [dispatched.phase for dispatched in observed.dispatched] == ["planning"]
    assert any("CRITICAL" in warning for warning in observed.warnings)
    assert observed.result.rebase.integration_unresolved is False


def test_run_loop_checkpointed_conflict_blocks_first_phase_dispatch(
    monkeypatch: MonkeyPatch,
) -> None:
    state = PipelineState(
        phase="planning",
        rebase=RebaseState(last_action="conflict", last_reason="resolver exhausted"),
    )

    observed = run_recovery_scenario(monkeypatch, state, startup=True)

    assert observed.exit_code == 0
    assert observed.waits and all(delay > 0 for delay in observed.waits)
    assert observed.saved[0].rebase == state.rebase
    assert [dispatched.phase for dispatched in observed.dispatched] == ["planning"]
    assert any("CRITICAL" in warning for warning in observed.warnings)
    assert observed.result.rebase.integration_unresolved is False


def test_run_loop_still_catches_up_after_a_reconciled_recovery(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    module = _run_loop_module()
    events, _saves = _install_run_loop_seams(module, monkeypatch, _reconciled())
    display = _RecordingDisplay()

    state = module._apply_startup_rebase_outcomes(
        PipelineState(phase="planning"), _loop_ctx(tmp_path, display)
    )

    assert "integrate" in events, f"a reconciled recovery must still catch up, got {events!r}"
    assert state.rebase.last_action == "fast_forwarded"
    assert display.warn_lines == []


def test_run_loop_still_catches_up_when_there_was_nothing_to_recover(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    module = _run_loop_module()
    events, _saves = _install_run_loop_seams(module, monkeypatch, None)

    module._apply_startup_rebase_outcomes(
        PipelineState(phase="planning"), _loop_ctx(tmp_path, _RecordingDisplay())
    )

    assert events == ["recover", "integrate", "save"], (
        f"expected the ordinary startup sequence, got {events!r}"
    )


def _worker_module() -> ModuleType:
    return importlib.import_module("ralph.pipeline.parallel.worker_runtime")


def _git_workspace(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir(exist_ok=True)
    return tmp_path


def _install_worker_seams(
    module: ModuleType, monkeypatch: MonkeyPatch, recovered: RebaseState | None
) -> list[str]:
    events: list[str] = []

    def _fake_recover(
        workspace_scope: object, *, config: object = None, **_kwargs: object
    ) -> RebaseState | None:
        del workspace_scope, config
        events.append("recover")
        return recovered

    def _fake_integrate(*args: object, **kwargs: object) -> RebaseState | None:
        del args, kwargs
        events.append("integrate")
        return RebaseState(last_action="fast_forwarded", last_target="main")

    monkeypatch.setattr(module, "recover_incomplete_integration", _fake_recover, raising=False)
    monkeypatch.setattr(
        module, "auto_integrate_on_phase_transition", _fake_integrate, raising=False
    )
    monkeypatch.setattr(
        module,
        "inspect_integration_resolution",
        lambda _root, state: IntegrationResolutionVerdict(
            IntegrationResolutionStatus.RECOVERABLE
            if state.recovery_record_retained or state.last_action == "conflict"
            else IntegrationResolutionStatus.RESOLVED,
        ),
    )
    return events


def _run_worker_seam(
    module: ModuleType,
    tmp_path: Path,
    *,
    recover_first: bool = True,
    display_context: DisplayContext | None = None,
    state: RebaseState | None = None,
) -> RebaseState | None:
    return module.run_worker_auto_integration(
        config=_config(),
        workspace_scope=WorkspaceScope(_git_workspace(tmp_path)),
        policy_bundle=None,
        registry=None,
        pipeline_deps=None,
        display_context=display_context,
        recover_first=recover_first,
        state=state,
    )


def test_worker_does_not_integrate_over_a_retained_recovery_record(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    module = _worker_module()
    events = _install_worker_seams(module, monkeypatch, _retained())

    outcome = _run_worker_seam(module, tmp_path)

    assert events == ["recover"], (
        f"a worker must not integrate over a record recovery still owns; got {events!r}"
    )
    assert outcome is not None
    assert outcome.recovery_record_retained is True, (
        "the retained recovery verdict must reach the worker's caller, not"
        " be swallowed as a silent no-op"
    )


def test_the_worker_deferral_reaches_a_real_operator_console(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    module = _worker_module()
    events = _install_worker_seams(module, monkeypatch, _retained())
    buffer = io.StringIO()
    display_context = make_display_context(
        env={}, console=Console(file=buffer, width=200), force_width=200
    )

    outcome = _run_worker_seam(module, tmp_path, display_context=display_context)

    assert events == ["recover"]
    assert outcome is not None
    rendered = buffer.getvalue()
    assert "deferred" in rendered, (
        f"expected the deferral on the operator console, got {rendered!r}"
    )
    assert "auto-integrate" in rendered, (
        f"the line must be attributed to the auto-integrate channel, got {rendered!r}"
    )


def test_worker_still_integrates_after_a_reconciled_recovery(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    module = _worker_module()
    events = _install_worker_seams(module, monkeypatch, _reconciled())

    outcome = _run_worker_seam(module, tmp_path)

    assert events == ["recover", "integrate"], (
        f"a reconciled recovery must still catch up, got {events!r}"
    )
    assert outcome is not None
    assert outcome.last_action == "fast_forwarded"


@pytest.mark.parametrize(
    "unresolved",
    [
        RebaseState(last_action="conflict", last_reason="resolver exhausted"),
        _retained(),
    ],
)
def test_worker_boundary_does_not_integrate_over_an_unresolved_outcome(
    monkeypatch: MonkeyPatch, tmp_path: Path, unresolved: RebaseState
) -> None:
    module = _worker_module()
    events = _install_worker_seams(module, monkeypatch, _retained())

    outcome = _run_worker_seam(module, tmp_path, recover_first=False, state=unresolved)

    assert events == ["recover"], f"only owned recovery is allowed, got {events!r}"
    assert outcome is not None and outcome.recovery_record_retained


def test_run_loop_retained_recovery_blocks_first_phase_dispatch(monkeypatch: MonkeyPatch) -> None:
    state = PipelineState(phase="planning", rebase=_retained())

    observed = run_recovery_scenario(monkeypatch, state, startup=True)

    assert observed.exit_code == 0
    assert observed.waits and all(delay > 0 for delay in observed.waits)
    assert observed.saved[0].rebase == state.rebase
    assert [dispatched.phase for dispatched in observed.dispatched] == ["planning"]
    assert any("CRITICAL" in warning for warning in observed.warnings)
    assert observed.result.rebase.integration_unresolved is False
