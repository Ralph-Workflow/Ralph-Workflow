"""Deterministic runner-seam regressions for default auto-integration."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from ralph.config.models import UnifiedConfig
from ralph.pipeline import runner
from ralph.pipeline.effects import CommitEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.rebase_state import RebaseState


def _default_config() -> UnifiedConfig:
    return UnifiedConfig.model_validate({"general": {"auto_integrate_enabled": True}})


def test_commit_seam_invokes_auto_integrate(monkeypatch) -> None:
    """Plan step 2: a successful commit uses the unset-target config path."""
    config = _default_config()
    outcome = RebaseState(last_action="rebased", last_target="main", fast_forwarded=True)
    integrate = MagicMock(return_value=outcome)
    monkeypatch.setattr(runner, "auto_integrate_after_commit", integrate)
    workspace_scope = MagicMock()
    state = SimpleNamespace(rebase=RebaseState())

    actual = runner._maybe_auto_integrate(
        effect=CommitEffect(message_file="message"),
        event=PipelineEvent.COMMIT_SUCCESS,
        commit_phase_def=SimpleNamespace(role="commit"),
        config=config,
        workspace_scope=workspace_scope,
        state=state,
        display=MagicMock(),
    )

    assert actual is outcome
    assert config.general.auto_integrate_target is None
    assert integrate.call_args.args == (config, workspace_scope, state.rebase)


def test_phase_transition_seam_invokes_auto_integrate(monkeypatch) -> None:
    """Plan step 2: a successful phase transition uses the unset-target path."""
    config = _default_config()
    outcome = RebaseState(last_action="rebased", last_target="main", fast_forwarded=True)
    integrate = MagicMock(return_value=outcome)
    monkeypatch.setattr(runner, "auto_integrate_on_phase_transition", integrate)
    workspace_scope = MagicMock()
    state = SimpleNamespace(rebase=RebaseState())

    actual = runner._maybe_auto_integrate(
        effect=object(),
        event=PipelineEvent.AGENT_SUCCESS,
        commit_phase_def=None,
        config=config,
        workspace_scope=workspace_scope,
        state=state,
        display=MagicMock(),
    )

    assert actual is outcome
    assert config.general.auto_integrate_target is None
    assert integrate.call_args.args == (config, workspace_scope, state.rebase)
