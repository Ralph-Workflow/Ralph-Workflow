"""Regression coverage for auto-integration after a fan-out join."""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from rich.console import Console

from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.pipeline import runner as runner_module
from ralph.pipeline.auto_integrate_record import IntegrationRecord
from ralph.pipeline.integration_resolution import (
    assert_non_resolution_dispatch_allowed,
    inspect_integration_resolution,
)
from ralph.pipeline.parallel import worker_runtime
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pytest import MonkeyPatch

    from ralph.pipeline.effects import Effect
    from ralph.pipeline.events import PhaseFailureEvent


def _config(*, enabled: bool) -> UnifiedConfig:
    return UnifiedConfig.model_validate({"general": {"auto_integrate_enabled": enabled}})


def _display(output: StringIO) -> ParallelDisplay:
    return ParallelDisplay(make_display_context(console=Console(file=output, width=180)))


def test_fan_out_join_invokes_auto_integrate_when_enabled(monkeypatch: MonkeyPatch) -> None:
    outcome = RebaseState(last_action="rebased", last_target="main", fast_forwarded=True)
    calls: list[tuple[object, ...]] = []
    options: list[dict[str, object]] = []

    def integrate(*args: object, **kwargs: object) -> RebaseState:
        calls.append(args)
        options.append(kwargs)
        return outcome

    monkeypatch.setattr(runner_module, "auto_integrate_on_phase_transition", integrate)
    state = PipelineState(phase="development")
    config = _config(enabled=True)
    scope = WorkspaceScope(Path("/workspace"))
    display = _display(StringIO())
    result = runner_module._integrate_after_fan_out(
        state=state,
        config=config,
        workspace_scope=scope,
        display=display,
        policy_bundle=None,
        registry=None,
    )
    assert calls == [(config, scope, state.rebase)]
    assert options == [
        {"conflict_resolver": None, "rebase_stop_resolver": None, "display": display}
    ]
    assert result.phase == state.phase and result.rebase == outcome


def test_fan_out_join_conflict_routes_through_recovery(monkeypatch: MonkeyPatch) -> None:
    conflict = RebaseState(last_action="conflict", last_reason="resolver exhausted")

    def integrate(*_args: object, **_kwargs: object) -> RebaseState:
        return conflict

    def reduce(
        state: PipelineState, event: PhaseFailureEvent, _policy: object
    ) -> tuple[PipelineState, list[Effect]]:
        return state.copy_with(last_error=event.reason), []

    monkeypatch.setattr(runner_module, "auto_integrate_on_phase_transition", integrate)
    monkeypatch.setattr(runner_module, "reducer_reduce", reduce)
    result = runner_module._integrate_after_fan_out(
        state=PipelineState(phase="development"),
        config=_config(enabled=True),
        workspace_scope=WorkspaceScope(Path("/workspace")),
        display=_display(StringIO()),
        policy_bundle=None,
        registry=None,
    )
    assert "integration conflict" in (result.last_error or "")
    assert result.rebase.integration_unresolved


def test_fan_out_join_skips_auto_integrate_when_disabled(monkeypatch: MonkeyPatch) -> None:
    def unexpected(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("disabled integration must not create a new transaction")

    monkeypatch.setattr(runner_module, "auto_integrate_on_phase_transition", unexpected)
    state = PipelineState(phase="development")
    result = runner_module._integrate_after_fan_out(
        state=state,
        config=_config(enabled=False),
        workspace_scope=WorkspaceScope(Path("/workspace")),
        display=_display(StringIO()),
        policy_bundle=None,
        registry=None,
    )
    assert result == state


def test_fan_out_join_skip_emits_warn_line_with_reason(monkeypatch: MonkeyPatch) -> None:
    reason = "feature worktree is dirty"

    def integrate(*_args: object, **_kwargs: object) -> RebaseState:
        return RebaseState(last_action="skipped", last_reason=reason, last_target="main")

    monkeypatch.setattr(runner_module, "auto_integrate_on_phase_transition", integrate)
    output = StringIO()
    runner_module._integrate_after_fan_out(
        state=PipelineState(phase="development"),
        config=_config(enabled=True),
        workspace_scope=WorkspaceScope(Path("/workspace")),
        display=_display(output),
        policy_bundle=None,
        registry=None,
    )
    assert reason in output.getvalue()


def test_worker_without_resolution_dependencies_warns_the_operator(
    monkeypatch: MonkeyPatch,
) -> None:
    output = StringIO()
    context = make_display_context(console=Console(file=output, width=180))
    display = ParallelDisplay(context)

    def active(*_args: object, **_kwargs: object) -> ParallelDisplay:
        return display

    monkeypatch.setattr(worker_runtime, "resolve_active_display", active)
    resolvers = worker_runtime._worker_integration_resolvers(
        config=_config(enabled=True),
        workspace_scope=WorkspaceScope(Path("/workspace")),
        policy_bundle=None,
        registry=None,
        pipeline_deps=None,
        display_context=context,
    )
    assert resolvers == (None, None, None)
    assert "parallel worker has no resolution dependencies" in output.getvalue()


def test_worker_without_a_display_context_declines_without_raising() -> None:
    resolvers = worker_runtime._worker_integration_resolvers(
        config=_config(enabled=True),
        workspace_scope=WorkspaceScope(Path("/workspace")),
        policy_bundle=None,
        registry=None,
        pipeline_deps=None,
        display_context=None,
    )
    assert resolvers == (None, None, None)


def test_fan_out_completed_phase_waits_for_retained_integration(monkeypatch: MonkeyPatch) -> None:
    retained = RebaseState(last_action="skipped", recovery_record_retained=True)
    record = IntegrationRecord(
        phase="integrated", target="main", pre_feature_sha="feature", pre_target_sha="main"
    )

    def read(_root: Path) -> IntegrationRecord:
        return record

    def integrate(*_args: object, **_kwargs: object) -> RebaseState:
        return retained

    monkeypatch.setattr("ralph.pipeline.auto_integrate_record.read_record", read)
    monkeypatch.setattr(runner_module, "auto_integrate_on_phase_transition", integrate)
    scope = WorkspaceScope(Path("/workspace"))
    state = PipelineState(phase="development")
    result = runner_module._integrate_after_fan_out(
        state=state,
        config=_config(enabled=True),
        workspace_scope=scope,
        display=_display(StringIO()),
        policy_bundle=None,
        registry=None,
    )
    assert result.phase == state.phase
    assert result.last_error == state.last_error
    assert result.rebase.recovery_record_retained
    verdict = inspect_integration_resolution(scope.root, result.rebase)
    with pytest.raises(RuntimeError, match="durable integration record retained"):
        assert_non_resolution_dispatch_allowed("development_commit", verdict)
