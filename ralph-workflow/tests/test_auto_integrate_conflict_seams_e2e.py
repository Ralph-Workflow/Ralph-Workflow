"""Fast black-box coverage for startup and phase-transition conflict seams.

The deleted real-Git scenarios repeated endpoint resolution, marker rejection,
and repository cleanup already proved by ``test_auto_integrate_resolution.py``
and ``test_conflict_resolution_rebase_loop.py``.  This module preserves its
unique behaviour: startup and phase-transition callers must expose the exact
conflict outcome returned by the injected integration boundary and must thread
the production resolver objects into that boundary.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock

import pytest

from ralph.config.models import UnifiedConfig
from ralph.pipeline import run_loop, runner
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.conflict_resolution import RebaseStopResolver
    from ralph.pipeline.state import PipelineState

pytestmark = pytest.mark.subprocess_e2e


def _config() -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {"general": {"auto_integrate_enabled": True}}
    )


def test_phase_transition_returns_observable_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A boundary conflict is returned instead of being hidden."""
    outcome = RebaseState(
        last_action="conflict",
        last_target="main",
        last_reason="markers remain",
        fast_forwarded=False,
    )
    integrate = MagicMock(return_value=outcome)
    monkeypatch.setattr(runner, "auto_integrate_on_phase_transition", integrate)
    scope = WorkspaceScope(Path("/workspace"))

    actual = runner._maybe_auto_integrate(
        effect=object(),
        event=PipelineEvent.AGENT_SUCCESS,
        commit_phase_def=None,
        config=_config(),
        workspace_scope=scope,
        state=cast("PipelineState", SimpleNamespace(rebase=RebaseState())),
        display=MagicMock(),
    )

    assert actual is outcome
    assert actual.last_reason == "markers remain"


def test_startup_threads_both_resolvers_and_returns_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Startup uses both production resolver factories before phase one."""
    conflict_resolver = MagicMock(name="conflict-resolver")
    rebase_resolver = MagicMock(name="rebase-stop-resolver")
    outcome = RebaseState(
        last_action="conflict", last_target="main", fast_forwarded=False
    )
    received: list[tuple[ConflictResolver | None, RebaseStopResolver | None]] = []

    def _integrate(
        _config: UnifiedConfig,
        _scope: WorkspaceScope,
        _state: RebaseState,
        *,
        conflict_resolver: ConflictResolver | None = None,
        rebase_stop_resolver: RebaseStopResolver | None = None,
        display: ParallelDisplay | None = None,
        **_kwargs: object,
    ) -> RebaseState:
        received.append((conflict_resolver, rebase_stop_resolver))
        return outcome

    def _conflict_builder(**_kwargs: object) -> MagicMock:
        return conflict_resolver

    def _rebase_builder(**_kwargs: object) -> MagicMock:
        return rebase_resolver

    monkeypatch.setattr(
        run_loop, "build_agent_conflict_resolver", _conflict_builder
    )
    monkeypatch.setattr(
        run_loop, "build_agent_rebase_stop_resolver", _rebase_builder
    )
    integrate = MagicMock(side_effect=_integrate)
    monkeypatch.setattr(run_loop, "auto_integrate_on_phase_transition", integrate)
    monkeypatch.setattr(
        runner, "_log_auto_integrate_outcome", MagicMock(return_value=None)
    )
    ctx = MagicMock()
    ctx.config = _config()
    ctx.workspace_scope = WorkspaceScope(Path("/workspace"))

    actual = run_loop._run_startup_integration(ctx)

    assert actual is outcome
    assert received == [(conflict_resolver, rebase_resolver)]
