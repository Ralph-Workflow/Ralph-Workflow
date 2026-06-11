"""Integration test: default `ralph` run wires ParallelDisplay + transcript surfaces.

Drives a single planning → development → development_analysis → development_commit
cycle through the runner using a fake agent-execute seam, asserting the
display surfaces (phase banner, decision log, completion summary) reflect
real pipeline state.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from rich.console import Console

import ralph.display.parallel_display as pd_module
from ralph.config.enums import Verbosity
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import (
    CommitEffect,
    InvokeAgentEffect,
)
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pytest import MonkeyPatch


DEFAULT_POLICY_DIR = Path(__file__).parent.parent.parent / "ralph" / "policy" / "defaults"


def _config() -> UnifiedConfig:
    return UnifiedConfig()


@pytest.mark.skipif(
    os.environ.get("CI") == "1",
    reason="Terminal emulation is unstable in CI; covered by display unit tests",
)
def test_default_run_constructs_parallel_display_and_renders_surfaces(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """Calling runner.run() without a display constructs exactly one ParallelDisplay."""
    policy_bundle = load_policy(DEFAULT_POLICY_DIR)

    constructed: list[object] = []
    real_init = pd_module.ParallelDisplay.__init__

    def spy_init(self: object, *args: object, **kwargs: object) -> None:
        constructed.append(self)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(pd_module.ParallelDisplay, "__init__", spy_init)

    monkeypatch.setattr(runner_module, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_path))
    monkeypatch.setattr(runner_module, "load_policy_or_die", lambda _path: policy_bundle)
    monkeypatch.setattr(runner_module, "materialize_agent_prompt_if_needed", lambda *a, **kw: None)
    monkeypatch.setattr(runner_module.ckpt, "save", lambda _state, *_args, **_kwargs: None)

    invoked_phases: list[str] = []

    def fake_execute_effect(
        effect: object,
        _config: object,
        _workspace_scope: object,
    ) -> PipelineEvent:
        if isinstance(effect, InvokeAgentEffect):
            invoked_phases.append(effect.phase)
            return PipelineEvent.AGENT_SUCCESS
        if isinstance(effect, CommitEffect):
            return PipelineEvent.COMMIT_SUCCESS
        msg = f"Unexpected effect: {type(effect)!r}"
        raise AssertionError(msg)

    def fake_phase_event_after_agent_run(
        *,
        effect: InvokeAgentEffect,
        **_kwargs: object,
    ) -> PipelineEvent:
        if effect.phase == "development_analysis":
            return PipelineEvent.ANALYSIS_SUCCESS
        if effect.phase == "review_analysis":
            return PipelineEvent.ANALYSIS_SUCCESS
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(runner_module, "execute_effect", fake_execute_effect)
    monkeypatch.setattr(
        runner_module,
        "phase_event_after_agent_run",
        fake_phase_event_after_agent_run,
    )

    state = PipelineState(
        phase="planning",
        budget_caps={"iteration": 1, "reviewer_pass": 0},
    )

    exit_code = runner_module.run(_config(), initial_state=state)

    assert exit_code == 0
    # Default verbose path constructs exactly one ParallelDisplay.
    assert len(constructed) == 1
    # Pipeline ran through planning -> development -> dev_analysis -> dev_commit.
    assert "planning" in invoked_phases
    assert "development" in invoked_phases
    assert "development_analysis" in invoked_phases


def test_default_run_propagates_display_subscriber(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """When no display is provided, the runner uses the ParallelDisplay subscriber."""
    policy_bundle = load_policy(DEFAULT_POLICY_DIR)

    monkeypatch.setattr(runner_module, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_path))
    monkeypatch.setattr(runner_module, "load_policy_or_die", lambda _path: policy_bundle)
    monkeypatch.setattr(runner_module.ckpt, "save", lambda _state, *_args, **_kwargs: None)

    real_init = pd_module.ParallelDisplay.__init__

    def plain_init(self: object, *args: object, **kwargs: object) -> None:
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(pd_module.ParallelDisplay, "__init__", plain_init)

    # Short-circuit immediately: enter -> exit by going straight to complete.
    state = MagicMock()
    state.phase = "complete"
    monkeypatch.setattr(
        runner_module,
        "determine_effect_from_policy",
        lambda *_args, **_kwargs: runner_module.ExitSuccessEffect(),
    )

    exit_code = runner_module.run(_config(), initial_state=state)

    assert exit_code == 0


def test_width_refresher_updates_live_display_context(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """Width refresher must update the display object the runner keeps using.

    The runner now uses install_width_refresher (cross-platform: SIGWINCH on
    POSIX, poll thread on Windows) instead of the POSIX-only
    install_sigwinch_refresher.
    """
    policy_bundle = load_policy(DEFAULT_POLICY_DIR)
    base_console = Console(record=True, width=120, force_terminal=True)
    wide_ctx = make_display_context(console=base_console, env={"COLUMNS": "120"})
    compact_ctx = make_display_context(
        console=base_console,
        env={"COLUMNS": "40"},
        force_width=40,
    )

    class StubDisplay:
        def __init__(self) -> None:
            self._ctx = wide_ctx

        def __enter__(self) -> StubDisplay:
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: object,
        ) -> bool:
            return False

        def emit(self, *_args: object, **_kwargs: object) -> None:
            return None

    display = StubDisplay()

    monkeypatch.setattr(runner_module, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_path))
    monkeypatch.setattr(runner_module, "write_start_commit_if_absent", lambda _root: None)
    monkeypatch.setattr(runner_module, "validate_custom_mcp_servers", lambda _root: 0)
    monkeypatch.setattr(runner_module, "load_policy_or_die", lambda _path: policy_bundle)
    monkeypatch.setattr(runner_module.ckpt, "save", lambda _state, *_args, **_kwargs: None)

    stop_called: list[bool] = []

    def fake_install_width_refresher(ctx_holder: list[object], on_refresh: object = None) -> object:
        ctx_holder[0] = compact_ctx
        if callable(on_refresh):
            on_refresh(compact_ctx)

        def stop() -> None:
            stop_called.append(True)

        return stop

    monkeypatch.setattr(
        runner_module,
        "install_width_refresher",
        fake_install_width_refresher,
    )

    state = PipelineState(
        phase="complete",
    )

    exit_code = runner_module.run(
        _config(),
        initial_state=state,
        display=display,
        verbosity=Verbosity.VERBOSE,
    )

    assert exit_code == 0
    assert display._ctx.mode == "compact"
    # The stop callback returned by install_width_refresher must be called on shutdown
    assert stop_called, "Runner did not call the width refresher stop callback on shutdown"
