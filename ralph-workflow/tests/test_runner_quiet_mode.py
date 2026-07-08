"""Regression tests: --quiet suppresses dashboard surfaces but keeps completion summary."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console

import ralph.display.parallel_display as pd_module
from ralph.config.enums import Verbosity
from ralph.config.models import GeneralConfig, UnifiedConfig
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


DEFAULT_POLICY_DIR = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"


def _install_runner_display_context(monkeypatch: MonkeyPatch, console: Console) -> None:
    ctx = make_display_context(
        console=console,
        force_width=console.width,
    )
    monkeypatch.setattr(runner_module, "make_display_context", lambda **_kwargs: ctx)


def _config() -> UnifiedConfig:
    return UnifiedConfig(
        general=GeneralConfig(
            verbosity=0,
        )
    )


def _install_runner_stubs(
    monkeypatch: MonkeyPatch, policy_bundle: object, tmp_path: Path
) -> list[str]:
    invoked_phases: list[str] = []

    def fake_execute_effect(effect: object, _config: object, _workspace_scope: object) -> object:
        if isinstance(effect, InvokeAgentEffect):
            invoked_phases.append(effect.phase)
            return PipelineEvent.AGENT_SUCCESS
        if isinstance(effect, CommitEffect):
            return PipelineEvent.COMMIT_SUCCESS
        msg = f"Unexpected effect: {type(effect)!r}"
        raise AssertionError(msg)

    def fake_phase_event_after_agent_run(*, effect: object, **_kwargs: object) -> object:
        if effect.phase in {"development_analysis", "review_analysis"}:
            return PipelineEvent.ANALYSIS_SUCCESS
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(runner_module, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_path))
    monkeypatch.setattr(runner_module, "load_policy_or_die", lambda _path: policy_bundle)
    monkeypatch.setattr(runner_module, "materialize_agent_prompt_if_needed", lambda *a, **kw: None)
    monkeypatch.setattr(runner_module.ckpt, "save", lambda _state, *_args, **_kwargs: None)
    monkeypatch.setattr(runner_module, "execute_effect", fake_execute_effect)
    monkeypatch.setattr(
        runner_module, "phase_event_after_agent_run", fake_phase_event_after_agent_run
    )
    return invoked_phases


def test_quiet_mode_suppresses_dashboard_header_and_phase_banners(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """In quiet mode no dashboard header or phase-transition banner appears."""
    monkeypatch.setenv("CI", "1")
    policy_bundle = load_policy(DEFAULT_POLICY_DIR)

    captured_console = Console(record=True, force_terminal=False, width=120, color_system=None)
    _install_runner_display_context(monkeypatch, captured_console)

    constructed_displays: list[object] = []
    real_init = pd_module.ParallelDisplay.__init__

    def spy_init(self: object, *args: object, **kwargs: object) -> None:
        constructed_displays.append(self)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(pd_module.ParallelDisplay, "__init__", spy_init)

    _install_runner_stubs(monkeypatch, policy_bundle, tmp_path)

    state = PipelineState(
        phase="planning",
        budget_caps={"iteration": 1, "reviewer_pass": 0},
    )

    exit_code = runner_module.run(_config(), initial_state=state, verbosity=Verbosity.QUIET)
    assert exit_code == 0

    # ParallelDisplay is the only display; in quiet mode it is constructed
    # once with `is_quiet=True` so its dashboard surfaces stay silent.
    assert len(constructed_displays) == 1
    quiet_display = constructed_displays[0]
    assert getattr(quiet_display, "_is_quiet", False) is True

    out = captured_console.export_text()
    # Dashboard header and phase-transition banner text should be absent.
    assert "Ralph Workflow" not in out
    assert "Planning → Development" not in out

    # The completion summary panel still renders.
    assert ("Pipeline Complete" in out) or ("Pipeline Failed" in out)


def test_quiet_mode_renders_completion_summary_on_failure(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """When the pipeline ends in 'failed' phase, quiet mode still renders a Failed panel."""
    monkeypatch.setenv("CI", "1")
    policy_bundle = load_policy(DEFAULT_POLICY_DIR)

    captured_console = Console(record=True, force_terminal=False, width=120, color_system=None)
    _install_runner_display_context(monkeypatch, captured_console)

    monkeypatch.setattr(runner_module, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_path))
    monkeypatch.setattr(runner_module, "load_policy_or_die", lambda _path: policy_bundle)
    monkeypatch.setattr(runner_module.ckpt, "save", lambda _state: None)

    failed_state = PipelineState(
        phase="failed",
        previous_phase="planning",
        last_error="kaboom",
    )
    effects = iter(
        [
            runner_module.PreparePromptEffect(phase="planning", iteration=0),
            runner_module.ExitSuccessEffect(),
        ]
    )

    monkeypatch.setattr(
        runner_module,
        "call_determine_effect_from_policy",
        lambda *_a, **_kw: next(effects),
    )
    monkeypatch.setattr(
        runner_module,
        "materialize_prepared_prompt",
        lambda *_a, **_kw: None,
    )
    monkeypatch.setattr(
        runner_module,
        "emit_phase_transition_if_changed",
        lambda *args, **kwargs: args[1],
    )

    exit_code = runner_module.run(_config(), initial_state=failed_state, verbosity=Verbosity.QUIET)
    assert exit_code == 0

    out = captured_console.export_text()
    assert "Pipeline Failed" not in out
