"""Regression coverage for auto-integration after a fan-out join."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from ralph.config.models import UnifiedConfig
from ralph.pipeline import runner as runner_module
from ralph.pipeline.parallel import worker_runtime
from ralph.pipeline.rebase_state import RebaseState

if TYPE_CHECKING:
    from pytest import MonkeyPatch


def _config(*, enabled: bool) -> UnifiedConfig:
    return UnifiedConfig.model_validate({"general": {"auto_integrate_enabled": enabled}})


def test_fan_out_join_invokes_auto_integrate_when_enabled(
    monkeypatch: MonkeyPatch,
) -> None:
    """Plan step 3: an enabled fan-out join integrates at the coordinator seam."""
    hook = MagicMock(
        return_value=RebaseState(last_action="rebased", last_target="main", fast_forwarded=True)
    )
    monkeypatch.setattr(runner_module, "auto_integrate_on_phase_transition", hook)
    rebase = RebaseState()
    state = MagicMock()
    state.rebase = rebase
    state.copy_with.return_value = state
    config = _config(enabled=True)
    workspace_scope = MagicMock()
    display = MagicMock()

    result = runner_module._integrate_after_fan_out(
        state=state,
        config=config,
        workspace_scope=workspace_scope,
        display=display,
        policy_bundle=None,
        registry=None,
    )

    # No policy bundle and no registry, so BOTH resolvers decline to build;
    # the display is threaded through so the resolution loop can own the
    # status-bar footer for its whole duration.
    hook.assert_called_once_with(
        config,
        workspace_scope,
        rebase,
        conflict_resolver=None,
        rebase_stop_resolver=None,
        display=display,
    )
    assert result is state
    state.copy_with.assert_called_once_with(rebase=hook.return_value)


def test_fan_out_join_skips_auto_integrate_when_disabled(
    monkeypatch: MonkeyPatch,
) -> None:
    """Plan step 3: disabled auto-integration does not call the boundary hook."""
    hook = MagicMock()
    monkeypatch.setattr(runner_module, "auto_integrate_on_phase_transition", hook)
    state = MagicMock()
    state.rebase = RebaseState()

    result = runner_module._integrate_after_fan_out(
        state=state,
        config=_config(enabled=False),
        workspace_scope=MagicMock(),
        display=MagicMock(),
        policy_bundle=None,
        registry=None,
    )

    hook.assert_not_called()
    assert result is state
    state.copy_with.assert_not_called()


def test_fan_out_join_skip_emits_warn_line_with_reason(monkeypatch: MonkeyPatch) -> None:
    """Plan step 5: fan-out skip outcomes retain their operator-visible reason."""
    reason = "feature worktree is dirty"
    hook = MagicMock(
        return_value=RebaseState(last_action="skipped", last_reason=reason, last_target="main")
    )
    monkeypatch.setattr(runner_module, "auto_integrate_on_phase_transition", hook)
    state = MagicMock()
    state.rebase = RebaseState()
    display = MagicMock()

    runner_module._integrate_after_fan_out(
        state=state,
        config=_config(enabled=True),
        workspace_scope=MagicMock(),
        display=display,
        policy_bundle=None,
        registry=None,
    )

    display.emit_warn_line.assert_called_once()
    assert reason in display.emit_warn_line.call_args.args[2]


class _RecordingDisplay:
    """Minimal display that records the operator lines pushed at it."""

    def __init__(self) -> None:
        self.warn_lines: list[tuple[str, str, str]] = []

    def emit_warn_line(self, unit_id: str, tag: str, message: str) -> None:
        self.warn_lines.append((unit_id, tag, message))


def test_worker_without_resolution_dependencies_warns_the_operator(
    monkeypatch: MonkeyPatch,
) -> None:
    """A worker integrating with NO conflict resolution must say so.

    This was the quietest of the decline branches -- a ``logger.debug``
    -- yet it is the one that silently turns auto-integration into
    "aborts on the first conflict" for every parallel worker.
    """
    display = _RecordingDisplay()
    monkeypatch.setattr(
        worker_runtime, "resolve_active_display", lambda _d, _ctx: display
    )

    resolvers = worker_runtime._worker_integration_resolvers(
        config=_config(enabled=True),
        workspace_scope=MagicMock(),
        policy_bundle=None,
        registry=None,
        pipeline_deps=None,
        display_context=MagicMock(),
    )

    assert resolvers == (None, None, None)
    assert display.warn_lines[0][0] == "run"
    assert display.warn_lines[0][1] == "auto-integrate"
    assert (
        "parallel worker has no resolution dependencies" in display.warn_lines[0][2]
    )


def test_worker_without_a_display_context_declines_without_raising() -> None:
    """The missing dependency may BE the display; that must stay a no-op."""
    resolvers = worker_runtime._worker_integration_resolvers(
        config=_config(enabled=True),
        workspace_scope=MagicMock(),
        policy_bundle=None,
        registry=None,
        pipeline_deps=None,
        display_context=None,
    )

    assert resolvers == (None, None, None)
