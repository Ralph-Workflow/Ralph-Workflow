"""Fast black-box coverage for the post-commit conflict seam.

The former tests rebuilt a conflicting repository twice and reran the complete
Git resolution chain.  That duplicated the real endpoint-merge proof in
``test_auto_integrate_resolution.py`` and the resolver-factory contract in
``test_auto_integrate_agent_resolver.py``.  Those behaviours remain covered
there.  This file now owns the distinct seam contract: the runner constructs
both resolvers, passes them to auto-integration, and returns its observable
conflict or resolved outcome.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock

import pytest

from ralph.config.models import UnifiedConfig
from ralph.pipeline import runner
from ralph.pipeline.effects import CommitEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.conflict_resolution import RebaseStopResolver
    from ralph.pipeline.factory import PipelineDeps
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import PhaseDefinition

pytestmark = pytest.mark.subprocess_e2e


def _config() -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {"general": {"auto_integrate_enabled": True}}
    )


@pytest.mark.parametrize(
    ("action", "fast_forwarded"),
    [("conflict", False), ("rebased", True)],
)
def test_commit_seam_returns_the_injected_integration_outcome(
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    fast_forwarded: bool,
) -> None:
    """Conflict and successful resolution remain distinct to the caller."""
    conflict_resolver = MagicMock(name="conflict-resolver")
    rebase_resolver = MagicMock(name="rebase-stop-resolver")
    outcome = RebaseState(
        last_action=action,
        last_target="main",
        fast_forwarded=fast_forwarded,
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
    ) -> RebaseState:
        received.append((conflict_resolver, rebase_stop_resolver))
        return outcome

    def _conflict_builder(**_kwargs: object) -> MagicMock:
        return conflict_resolver

    def _rebase_builder(**_kwargs: object) -> MagicMock:
        return rebase_resolver

    monkeypatch.setattr(runner, "_build_seam_conflict_resolver", _conflict_builder)
    monkeypatch.setattr(runner, "_build_seam_rebase_stop_resolver", _rebase_builder)
    integrate = MagicMock(side_effect=_integrate)
    monkeypatch.setattr(runner, "auto_integrate_after_commit", integrate)
    monkeypatch.setattr(
        runner, "clear_cycle_baseline", MagicMock(return_value=None)
    )
    scope = WorkspaceScope(Path("/workspace"))
    state = cast("PipelineState", SimpleNamespace(rebase=RebaseState()))
    phase = cast("PhaseDefinition", SimpleNamespace(role="commit"))

    actual = runner._maybe_auto_integrate(
        effect=CommitEffect(message_file="unused"),
        event=PipelineEvent.COMMIT_SUCCESS,
        commit_phase_def=phase,
        config=_config(),
        workspace_scope=scope,
        state=state,
        display=MagicMock(),
        policy_bundle=MagicMock(),
        registry=MagicMock(),
        pipeline_deps=cast(
            "PipelineDeps", MagicMock(auto_integrate_resolver=None)
        ),
    )

    assert actual is outcome
    assert received == [(conflict_resolver, rebase_resolver)]
