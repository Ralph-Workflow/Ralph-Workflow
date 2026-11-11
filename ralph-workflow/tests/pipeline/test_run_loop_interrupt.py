from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import pytest

import ralph.pipeline.runner as _runner_module
from ralph.pipeline.run_loop import _handle_keyboard_interrupt, _LoopContext
from ralph.pipeline.state import PipelineState
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.config.agent_config import AgentConfig

    class _ActiveDisplayLike(Protocol):
        def emit(self, unit_id: object, line: str) -> object: ...

    class _RegistryLike(Protocol):
        def get(self, name: str) -> AgentConfig | None: ...

    class _MonitorLike(Protocol):
        @property
        def current_state(self) -> str: ...

        def add_listener(self, cb: Callable[[object], object]) -> Callable[[], object]: ...


_EXIT_CODE = 130


class _StubDisplay:
    def __init__(self) -> None:
        self.emit_calls: list[tuple[object, str]] = []

    def emit(self, unit_id: object, line: str) -> None:
        self.emit_calls.append((unit_id, line))


def _make_loop_ctx(
    *,
    active_display: _ActiveDisplayLike | None = None,
    monitor_stop: Callable[[], None] | None = None,
    workspace_scope: WorkspaceScope | None = None,
) -> _LoopContext:
    resolved_display = active_display if active_display is not None else _StubDisplay()
    resolved_scope = (
        workspace_scope
        if workspace_scope is not None
        else WorkspaceScope(root=Path(tempfile.gettempdir()))
    )
    return _LoopContext(
        policy_bundle=object(),
        workspace_scope=resolved_scope,
        config=object(),
        active_display=resolved_display,
        display_context=object(),
        effective_verbosity=object(),
        registry=object(),
        effective_pipeline_subscriber=None,
        controller=object(),
        config_path=None,
        cli_overrides={},
        monitor_stop=monitor_stop,
        connectivity_monitor=object(),
        sleep=lambda _seconds: None,
        is_quiet=False,
        heartbeat_client=None,
        pro_watcher=None,
        snapshot_registry=None,
    )


def _patch_runner_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[
    list[object],
    list[tuple[object, str, object]],
    list[tuple[WorkspaceScope, object]],
]:
    handle_keyboard_interrupt_calls: list[object] = []

    def _handle_keyboard_interrupt_stub(monitor_stop: object) -> None:
        handle_keyboard_interrupt_calls.append(monitor_stop)

    save_checkpoint_or_log_calls: list[tuple[object, str, object]] = []

    def _save_checkpoint_or_log_stub(
        state: object,
        *,
        message: str,
        path: object,
    ) -> None:
        save_checkpoint_or_log_calls.append((state, message, path))

    checkpoint_path_calls: list[tuple[WorkspaceScope, object]] = []

    def _checkpoint_path_stub(workspace_scope: WorkspaceScope) -> object:
        path = Path("/tmp/test-checkpoint.json")
        checkpoint_path_calls.append((workspace_scope, path))
        return path

    monkeypatch.setattr(
        _runner_module, "handle_keyboard_interrupt", _handle_keyboard_interrupt_stub
    )
    monkeypatch.setattr(_runner_module, "save_checkpoint_or_log", _save_checkpoint_or_log_stub)
    monkeypatch.setattr(_runner_module, "_checkpoint_path", _checkpoint_path_stub)
    return (
        handle_keyboard_interrupt_calls,
        save_checkpoint_or_log_calls,
        checkpoint_path_calls,
    )


def test_run_loop_handle_keyboard_interrupt_returns_130() -> None:
    state = PipelineState(phase="development")
    loop_ctx = _make_loop_ctx()
    result = _handle_keyboard_interrupt(state, loop_ctx)
    assert result == _EXIT_CODE
    assert isinstance(result, int)
    assert type(result) is int


def test_run_loop_handle_keyboard_interrupt_marks_state_interrupted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, save_calls, _ = _patch_runner_dependencies(monkeypatch)
    state = PipelineState(phase="development")
    loop_ctx = _make_loop_ctx()
    _handle_keyboard_interrupt(state, loop_ctx)
    assert len(save_calls) == 1
    saved_state, _message, _path = save_calls[0]
    saved_state_obj = saved_state
    assert saved_state_obj.interrupted_by_user is True


def test_run_loop_handle_keyboard_interrupt_clears_monitor_stop_on_loop_ctx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stop_calls: list[int] = []
    handle_calls: list[object] = []

    def _handle_stub(monitor_stop: object) -> None:
        handle_calls.append(monitor_stop)

    monkeypatch.setattr(_runner_module, "handle_keyboard_interrupt", _handle_stub)
    monkeypatch.setattr(_runner_module, "save_checkpoint_or_log", lambda *a, **k: None)
    monkeypatch.setattr(
        _runner_module, "_checkpoint_path", lambda ws: Path("/tmp/test-checkpoint.json")
    )
    loop_ctx = _make_loop_ctx(monitor_stop=lambda: stop_calls.append(1))
    assert loop_ctx.monitor_stop is not None
    original_monitor_stop = loop_ctx.monitor_stop
    state = PipelineState(phase="development")
    _handle_keyboard_interrupt(state, loop_ctx)
    assert loop_ctx.monitor_stop is None
    assert stop_calls == [], (
        f"monitor_stop was invoked {len(stop_calls)} times; the wrapper "
        f"must not call the old stop-callback before clearing it"
    )
    assert len(handle_calls) == 1
    assert handle_calls[0] is original_monitor_stop
    _ = original_monitor_stop


def test_run_loop_handle_keyboard_interrupt_saves_checkpoint_to_workspace_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, save_calls, checkpoint_path_calls = _patch_runner_dependencies(monkeypatch)
    ws_root = Path(tempfile.gettempdir()) / "test-workspace"
    workspace_scope = WorkspaceScope(root=ws_root)
    loop_ctx = _make_loop_ctx(workspace_scope=workspace_scope)
    state = PipelineState(phase="development")
    _handle_keyboard_interrupt(state, loop_ctx)
    assert len(checkpoint_path_calls) == 1
    called_scope, returned_path = checkpoint_path_calls[0]
    assert called_scope is loop_ctx.workspace_scope
    assert returned_path == Path("/tmp/test-checkpoint.json")
    assert len(save_calls) == 1
    saved_state, _message, saved_path = save_calls[0]
    saved_state_obj = saved_state
    assert saved_state_obj.interrupted_by_user is True
    assert saved_path == Path("/tmp/test-checkpoint.json")


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
