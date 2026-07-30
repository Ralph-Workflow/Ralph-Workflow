"""Black-box tests for ``ralph.pipeline.run_loop._cleanup_pipeline`` shutdown.

These tests pin the wt-024 Step 5 contract: the run-loop's
``_cleanup_pipeline`` finally MUST invoke a session-wide
``process_teardown`` callable (defaulting to
``get_process_manager().shutdown_all``) so non-phase-labeled
children are reaped on every exit path, not just the atexit net.

The teardown is injected via ``loop_ctx.process_teardown`` so tests
can drive the success and exception paths with a recording callable
and assert the call shape. Cleanup-step failures are swallowed so
sibling cleanup continues, but each failure MUST emit an actionable
diagnostic-log record naming the resource and reclamation stage.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Protocol
from unittest.mock import MagicMock

import pytest
from loguru import logger

from ralph.pipeline.run_loop import _cleanup_pipeline, _LoopContext
from ralph.pipeline.state import PipelineState
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.config.agent_config import AgentConfig


def _make_loop_ctx(
    *,
    process_teardown: Callable[[], None] | None = None,
) -> _LoopContext:
    """Build a ``_LoopContext`` with MagicMock placeholders + injected process_teardown."""
    return _LoopContext(
        policy_bundle=MagicMock(),
        workspace_scope=WorkspaceScope(root=Path(tempfile.gettempdir())),
        config=MagicMock(),
        active_display=MagicMock(),
        display_context=MagicMock(),
        effective_verbosity=MagicMock(),
        registry=MagicMock(),
        effective_pipeline_subscriber=None,
        controller=MagicMock(),
        config_path=None,
        cli_overrides={},
        monitor_stop=None,
        connectivity_monitor=MagicMock(),
        sleep=MagicMock(),
        is_quiet=False,
        heartbeat_client=None,
        pro_watcher=None,
        snapshot_registry=None,
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


def test_cleanup_pipeline_logs_teardown_failure_to_diagnostic_log() -> None:
    """A process_teardown failure must emit a diagnostic-log record.

    Swallowing alone is not enough: operators need an actionable record
    naming the resource identity and reclamation stage so a leaked
    process is a recorded defect rather than a silent one.
    """

    def _bad_teardown() -> None:
        raise RuntimeError("teardown exploded")

    captured: list[str] = []
    sink_id = logger.add(captured.append, level="ERROR", format="{message}")
    try:
        state = PipelineState(phase="development")
        loop_ctx = _make_loop_ctx(process_teardown=_bad_teardown)
        _cleanup_pipeline(
            loop_ctx, _noop_unsubscribe, _noop_unsubscribe_display, _noop_display_stop, state
        )
    finally:
        logger.remove(sink_id)

    matching = [msg for msg in captured if "process_teardown" in msg and "teardown" in msg.lower()]
    assert matching, (
        "expected a diagnostic-log ERROR naming resource=process_teardown / "
        f"stage=teardown; got: {captured!r}"
    )


def test_cleanup_pipeline_logs_early_step_failure_and_still_runs_teardown() -> None:
    """An early cleanup failure is logged and does not skip process_teardown."""
    teardown_calls: list[None] = []

    def _record_teardown() -> None:
        teardown_calls.append(None)

    def _bad_unsubscribe() -> None:
        raise RuntimeError("unsubscribe exploded")

    captured: list[str] = []
    sink_id = logger.add(captured.append, level="ERROR", format="{message}")
    try:
        state = PipelineState(phase="development")
        loop_ctx = _make_loop_ctx(process_teardown=_record_teardown)
        _cleanup_pipeline(
            loop_ctx, _bad_unsubscribe, _noop_unsubscribe_display, _noop_display_stop, state
        )
    finally:
        logger.remove(sink_id)

    assert len(teardown_calls) == 1
    matching = [msg for msg in captured if "unsubscribe_bus" in msg]
    assert matching, (
        "expected a diagnostic-log ERROR naming resource=unsubscribe_bus; "
        f"got: {captured!r}"
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
