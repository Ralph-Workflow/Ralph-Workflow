"""Black-box tests for ``ralph.pipeline.run_loop._cleanup_pipeline`` shutdown.

These tests pin the wt-024 Step 5 contract: the run-loop's
``_cleanup_pipeline`` finally MUST invoke a session-wide
``process_teardown`` callable (defaulting to
``get_process_manager().shutdown_all``) so non-phase-labeled
children are reaped on every exit path, not just the atexit net.

The teardown is injected via ``loop_ctx.process_teardown`` so tests
can drive the success and exception paths with a recording callable
and assert the call shape. The injected callable is fired inside a
``suppress(Exception)`` so a teardown failure cannot prevent other
cleanup steps from running.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast
from unittest.mock import MagicMock

import pytest

from ralph.pipeline.run_loop import _cleanup_pipeline, _LoopContext
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


def _make_loop_ctx(
    *,
    process_teardown: Callable[[], None] | None = None,
) -> _LoopContext:
    """Build a ``_LoopContext`` with MagicMock placeholders + injected process_teardown."""
    return _LoopContext(
        policy_bundle=cast("PolicyBundle", MagicMock()),
        workspace_scope=WorkspaceScope(root=Path(tempfile.gettempdir())),
        config=cast("UnifiedConfig", MagicMock()),
        active_display=cast("ParallelDisplay", MagicMock()),
        display_context=cast("DisplayContext", MagicMock()),
        effective_verbosity=cast("Verbosity", MagicMock()),
        registry=cast("_RegistryLike", MagicMock()),
        effective_pipeline_subscriber=None,
        controller=cast("RecoveryController", MagicMock()),
        config_path=None,
        cli_overrides={},
        monitor_stop=None,
        connectivity_monitor=cast("_MonitorLike", MagicMock()),
        sleep=cast("Callable[[float], None]", MagicMock()),
        is_quiet=False,
        heartbeat_client=cast("ProHeartbeatClient | None", None),
        pro_watcher=cast("ProMarkerWatcher | None", None),
        snapshot_registry=cast("SnapshotRegistry | None", None),
        process_teardown=process_teardown,
    )


def _noop_unsubscribe() -> None:
    return None


def _noop_unsubscribe_display() -> None:
    return None


def _noop_display_stop() -> None:
    return None


def test_cleanup_pipeline_invokes_process_teardown_on_normal_exit() -> None:
    """The injected process_teardown is called on a normal cleanup path."""
    teardown_calls: list[None] = []

    def _record_teardown() -> None:
        teardown_calls.append(None)

    state = PipelineState(phase="development")
    loop_ctx = _make_loop_ctx(process_teardown=_record_teardown)
    _cleanup_pipeline(
        loop_ctx, _noop_unsubscribe, _noop_unsubscribe_display, _noop_display_stop, state
    )
    assert len(teardown_calls) == 1


def test_cleanup_pipeline_invokes_process_teardown_even_when_other_steps_fail() -> None:
    """The teardown MUST run even when an earlier cleanup step raises."""
    teardown_calls: list[None] = []

    def _record_teardown() -> None:
        teardown_calls.append(None)

    def _bad_unsubscribe() -> None:
        raise RuntimeError("unsubscribe exploded")

    state = PipelineState(phase="development")
    loop_ctx = _make_loop_ctx(process_teardown=_record_teardown)
    _cleanup_pipeline(
        loop_ctx, _bad_unsubscribe, _noop_unsubscribe_display, _noop_display_stop, state
    )
    assert len(teardown_calls) == 1


def test_cleanup_pipeline_swallows_teardown_exceptions() -> None:
    """A teardown failure must NOT propagate out of _cleanup_pipeline."""

    def _bad_teardown() -> None:
        raise RuntimeError("teardown exploded")

    state = PipelineState(phase="development")
    loop_ctx = _make_loop_ctx(process_teardown=_bad_teardown)
    # Must not raise
    _cleanup_pipeline(
        loop_ctx, _noop_unsubscribe, _noop_unsubscribe_display, _noop_display_stop, state
    )


def test_cleanup_pipeline_skips_teardown_when_none_injected() -> None:
    """When no process_teardown is injected (loop_ctx.process_teardown is None),
    the cleanup still completes without raising."""
    state = PipelineState(phase="development")
    loop_ctx = _make_loop_ctx(process_teardown=None)
    # Must not raise
    _cleanup_pipeline(
        loop_ctx, _noop_unsubscribe, _noop_unsubscribe_display, _noop_display_stop, state
    )


class _RegistryLike(Protocol):
    def get(self, name: str) -> AgentConfig | None: ...


class _MonitorLike(Protocol):
    @property
    def current_state(self) -> str: ...

    def add_listener(self, cb: Callable[[object], object]) -> Callable[[], object]: ...


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
