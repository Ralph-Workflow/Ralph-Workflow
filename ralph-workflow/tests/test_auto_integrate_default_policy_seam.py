"""Default-policy contracts for the post-commit auto-integration seam."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from ralph.config.models import UnifiedConfig
from ralph.pipeline import runner as runner_module
from ralph.pipeline.commit_state import CommitState
from ralph.pipeline.effect_router import determine_effect_from_policy
from ralph.pipeline.effects import CommitEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pytest import MonkeyPatch

    from ralph.policy.models import PolicyBundle


@lru_cache(maxsize=1)
def _load_default_policy_bundle() -> PolicyBundle:
    return load_policy(Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults")


def test_default_commit_phase_returns_commit_effect_after_message_agent() -> None:
    """Plan step 3 / AC-02: the real two-stage commit phase reaches CommitEffect."""
    bundle = _load_default_policy_bundle()
    state = PipelineState(phase="development_commit", commit=CommitState(agent_invoked=True))

    effect = determine_effect_from_policy(
        state,
        bundle,
        WorkspaceScope(Path("/workspace")),
        config=UnifiedConfig(),
    )

    assert bundle.pipeline.phases["development_commit"].role == "commit"
    assert isinstance(effect, CommitEffect)


def test_default_commit_success_calls_auto_integrate_but_skipped_does_not(
    monkeypatch: MonkeyPatch,
) -> None:
    """Plan step 3 / AC-02: success triggers commit integration; skip uses only boundary hook."""
    bundle = _load_default_policy_bundle()
    commit_def = bundle.pipeline.phases["development_commit"]
    integration = MagicMock(return_value=RebaseState(last_action="rebased"))
    transition = MagicMock(return_value=None)
    monkeypatch.setattr(runner_module, "auto_integrate_after_commit", integration)
    monkeypatch.setattr(runner_module, "auto_integrate_on_phase_transition", transition)
    monkeypatch.setattr(runner_module, "clear_cycle_baseline", lambda _root: None)
    state = PipelineState(phase="development_commit")
    config = UnifiedConfig()
    scope = WorkspaceScope(Path("/workspace"))

    outcome = runner_module._maybe_auto_integrate(
        effect=CommitEffect(message_file="message"),
        event=PipelineEvent.COMMIT_SUCCESS,
        commit_phase_def=commit_def,
        config=config,
        workspace_scope=scope,
        state=state,
        display=MagicMock(),
    )

    assert outcome is not None
    integration.assert_called_once()
    transition.assert_not_called()

    runner_module._maybe_auto_integrate(
        effect=CommitEffect(message_file="message"),
        event=PipelineEvent.COMMIT_SKIPPED,
        commit_phase_def=commit_def,
        config=config,
        workspace_scope=scope,
        state=state,
        display=MagicMock(),
    )

    integration.assert_called_once()
    transition.assert_called_once()
