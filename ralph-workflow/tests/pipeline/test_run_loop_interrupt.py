"""Black-box tests for ``ralph.pipeline.run_loop._handle_keyboard_interrupt``.

These tests pin the 4 contracts the run_loop interrupt wrapper must satisfy:

1. Return 130: the wrapper returns the canonical ``INTERRUPT_EXIT_CODE``
   (130) so the run loop exits with the correct code.
2. State marked interrupted: the wrapper calls
   ``state.copy_with(interrupted_by_user=True)`` before saving the
   checkpoint so a resumed run knows the previous run was
   user-interrupted.
3. monitor_stop cleared: the wrapper sets ``loop_ctx.monitor_stop = None``
   after calling the entry point so the cleanup phase does not
   double-stop the connectivity monitor.
4. Checkpoint saved: the wrapper calls ``save_checkpoint_or_log`` with
   the interrupted state and the path returned by
   ``_checkpoint_path(loop_ctx.workspace_scope)``.

The wrapper at ``ralph/pipeline/run_loop.py:446-469`` is the
synchronous path a real Ctrl+C reaches inside the pipeline loop. It
delegates to ``ralph.pipeline.runner.handle_keyboard_interrupt`` (the
re-exported entry point) and then mutates ``loop_ctx`` to clear the
monitor-stop callback and save a checkpoint.

All tests in this file patch ``ralph.pipeline.runner`` (imported as
``_runner_module``) so the wrapper's call sites are observable. The
patching is done via ``monkeypatch.setattr`` on the module attribute
(not on the local alias inside ``run_loop``) because ``run_loop.py:15``
imports the same module as ``_runner_module`` and the patch is visible
inside ``run_loop`` at the call site.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast
from unittest.mock import MagicMock

import pytest

import ralph.pipeline.runner as _runner_module
from ralph.pipeline.run_loop import _handle_keyboard_interrupt, _LoopContext
from ralph.pipeline.state import PipelineState
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.config.agent_config import AgentConfig
    from ralph.config.enums import Verbosity
    from ralph.config.models import UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.policy.models import PolicyBundle
    from ralph.pro_support.heartbeat import ProHeartbeatClient
    from ralph.pro_support.state_query import SnapshotRegistry
    from ralph.pro_support.watcher import ProMarkerWatcher
    from ralph.recovery.controller import RecoveryController

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
    """Minimal display stub whose ``emit`` records calls.

    The module-level ``emit_activity_line`` at
    ``ralph/display/parallel_display.py:3068-3095`` calls
    ``display.emit(unit_id, line)`` when display is not None. This
    stub satisfies that duck-typed surface and records every call for
    assertion.
    """

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
    """Build a ``_LoopContext`` populated with ``MagicMock`` placeholders.

    The wrapper at ``run_loop.py:446-469`` only reads
    ``loop_ctx.active_display``, ``loop_ctx.monitor_stop``, and
    ``loop_ctx.workspace_scope``. The other 15 fields are populated
    with ``MagicMock()`` placeholders so the dataclass construction
    succeeds without spinning up real policy / config / connectivity
    objects. The ``cast`` calls satisfy mypy strict mode.
    """
    resolved_display = active_display if active_display is not None else _StubDisplay()
    resolved_scope = (
        workspace_scope
        if workspace_scope is not None
        else WorkspaceScope(root=Path(tempfile.gettempdir()))
    )
    return _LoopContext(
        policy_bundle=cast("PolicyBundle", MagicMock()),
        workspace_scope=resolved_scope,
        config=cast("UnifiedConfig", MagicMock()),
        active_display=cast("ParallelDisplay", resolved_display),
        display_context=cast("DisplayContext", MagicMock()),
        effective_verbosity=cast("Verbosity", MagicMock()),
        registry=cast("_RegistryLike", MagicMock()),
        effective_pipeline_subscriber=None,
        controller=cast("RecoveryController", MagicMock()),
        config_path=None,
        cli_overrides={},
        monitor_stop=monitor_stop,
        connectivity_monitor=cast("_MonitorLike", MagicMock()),
        sleep=cast("Callable[[float], None]", MagicMock()),
        is_quiet=False,
        heartbeat_client=cast("ProHeartbeatClient | None", None),
        pro_watcher=cast("ProMarkerWatcher | None", None),
        snapshot_registry=cast("SnapshotRegistry | None", None),
    )


def _patch_runner_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[
    list[object],
    list[tuple[object, str, object]],
    list[tuple[WorkspaceScope, object]],
]:
    """Patch ``_runner_module`` call sites and return recording stubs.

    Returns three lists:
    - ``handle_keyboard_interrupt_calls``: arguments passed to
      ``_runner_module.handle_keyboard_interrupt``.
    - ``save_checkpoint_or_log_calls``: ``(state, message, path)``
      triples passed to ``_runner_module.save_checkpoint_or_log``.
    - ``checkpoint_path_calls``: ``(workspace_scope, return_value)``
      pairs from ``_runner_module._checkpoint_path``.

    All three patches are removed automatically when the
    ``monkeypatch`` fixture tears down.
    """
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
    """Pins contract 1: the wrapper returns the canonical exit code 130."""
    state = PipelineState(phase="development")
    loop_ctx = _make_loop_ctx()
    result = _handle_keyboard_interrupt(state, loop_ctx)
    assert result == _EXIT_CODE
    assert isinstance(result, int)
    assert type(result) is int


def test_run_loop_handle_keyboard_interrupt_marks_state_interrupted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pins contract 2: the wrapper passes a state whose
    ``interrupted_by_user`` is True to ``save_checkpoint_or_log``.
    """
    _, save_calls, _ = _patch_runner_dependencies(monkeypatch)
    state = PipelineState(phase="development")
    loop_ctx = _make_loop_ctx()
    _handle_keyboard_interrupt(state, loop_ctx)
    assert len(save_calls) == 1
    saved_state, _message, _path = save_calls[0]
    saved_state_obj = cast("PipelineState", saved_state)
    assert saved_state_obj.interrupted_by_user is True


def test_run_loop_handle_keyboard_interrupt_clears_monitor_stop_on_loop_ctx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pins contract 3: the wrapper sets ``loop_ctx.monitor_stop = None``
    after calling the entry point so the cleanup phase does not
    double-stop the connectivity monitor. The original ``monitor_stop``
    callable is passed to ``handle_keyboard_interrupt`` but is NOT
    invoked by the wrapper.
    """
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
    """Pins contract 4: the wrapper calls
    ``_runner_module._checkpoint_path(loop_ctx.workspace_scope)`` and
    forwards the result to ``save_checkpoint_or_log`` together with the
    interrupted state.
    """
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
    saved_state_obj = cast("PipelineState", saved_state)
    assert saved_state_obj.interrupted_by_user is True
    assert saved_path == Path("/tmp/test-checkpoint.json")


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
