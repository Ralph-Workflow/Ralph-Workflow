"""Integration test: full transcript ordering from run-start through completion.

Drives a stubbed runner through planning → development → development_analysis →
development_commit → review → review_analysis → review_commit → complete and asserts
the captured transcript contains the expected ordered sequence:
run-start → phase transitions → streaming content → phase-close → completion.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console

import ralph.display.parallel_display as pd_module
from ralph.config.enums import Verbosity
from ralph.config.models import UnifiedConfig
from ralph.display.activity_model import ActivityEventKind
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


def _install_runner_stubs(
    monkeypatch: MonkeyPatch,
    policy_bundle,
    tmp_path: Path,
) -> tuple[list[str], Console, list]:
    """Set up stubs for runner.run(); return (invoked_phases, console, displays)."""
    invoked_phases: list[str] = []
    # Use a list to capture display instances (list is mutable, survives monkeypatch)
    captured_displays: list = []

    def fake_execute_effect(effect, _config, _workspace_scope):
        if isinstance(effect, InvokeAgentEffect):
            invoked_phases.append(effect.phase)
            # Emit sample streaming content through the display so transcript has content markers
            # Iterate over captured displays and emit on each (normally there's just one)
            for display in captured_displays:
                try:
                    unit_id = "dev-1"
                    # Emit text content that will be grouped into a streaming block.
                    display._emit_activity_event(
                        unit_id,
                        ActivityEventKind.TEXT,
                        "Sample development output line 1",
                        None,
                    )
                    display._emit_activity_event(
                        unit_id,
                        ActivityEventKind.TEXT,
                        "Sample development output line 2",
                        None,
                    )
                except Exception:
                    # Emit through plain renderer directly as fallback
                    try:
                        display._plain_renderer.emit_activity_line(
                            "dev-1", "text", "Sample development output line 1"
                        )
                        display._plain_renderer.emit_activity_line(
                            "dev-1", "text", "Sample development output line 2"
                        )
                    except Exception:
                        pass
            return PipelineEvent.AGENT_SUCCESS
        if isinstance(effect, CommitEffect):
            return PipelineEvent.COMMIT_SUCCESS
        msg = f"Unexpected effect: {type(effect)!r}"
        raise AssertionError(msg)

    def fake_phase_event_after_agent_run(*, effect, display=None, **_kwargs):
        if effect.phase in {"development_analysis", "review_analysis"}:
            return PipelineEvent.ANALYSIS_SUCCESS
        if display is not None and hasattr(display, "emit_phase_close"):
            with contextlib.suppress(Exception):
                display.emit_phase_close(effect.phase, f"{effect.phase}: done")
        return PipelineEvent.AGENT_SUCCESS

    captured_console = Console(
        record=True, force_terminal=False, width=300, color_system=None
    )
    monkeypatch.setattr(
        runner_module,
        "make_display_context",
        lambda **_kwargs: make_display_context(
            console=captured_console, force_width=300, force_mode="wide"
        ),
    )
    monkeypatch.setattr(runner_module, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_path))
    monkeypatch.setattr(runner_module, "load_policy_or_die", lambda _path: policy_bundle)
    monkeypatch.setattr(runner_module, "_materialize_agent_prompt_if_needed", lambda *a, **kw: None)
    monkeypatch.setattr(runner_module.ckpt, "save", lambda _state: None)
    monkeypatch.setattr(runner_module, "_execute_effect", fake_execute_effect)
    monkeypatch.setattr(
        runner_module, "_phase_event_after_agent_run", fake_phase_event_after_agent_run
    )

    # Spy on ParallelDisplay construction to capture the display instance
    real_init = pd_module.ParallelDisplay.__init__

    def spy_init(self: object, *args: object, **kwargs: object) -> None:
        real_init(self, *args, **kwargs)
        captured_displays.append(self)

    monkeypatch.setattr(pd_module.ParallelDisplay, "__init__", spy_init)

    return invoked_phases, captured_console, captured_displays


def test_transcript_ordering_run_start_phase_transitions_streaming_phase_close_completion(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Transcript contains run-start → phase → content → phase-close → completion in order."""
    monkeypatch.setenv("CI", "1")
    policy_bundle = load_policy(DEFAULT_POLICY_DIR)

    # Write a minimal plan.json so the subscriber sees plan_present=True
    artifacts_dir = tmp_path / ".agent" / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    plan_data = {
        "summary": {
            "context": "Test plan",
            "scope_items": [
                {"text": "step 1"},
                {"text": "step 2"},
                {"text": "step 3"},
            ],
        },
        "steps": [{"number": 1, "title": "Validate", "content": "Do the work"}],
        "critical_files": {
            "primary_files": [{"path": "ralph/pipeline/runner.py", "action": "modify"}],
            "reference_files": [],
        },
        "risks_mitigations": [
            {"risk": "Risk A", "mitigation": "Mitigation A"},
        ],
        "verification_strategy": [
            {"method": "pytest", "expected_outcome": "passes"},
        ],
    }
    (artifacts_dir / "plan.json").write_text(json.dumps(plan_data), encoding="utf-8")

    _invoked_phases, captured_console, _captured_displays = _install_runner_stubs(
        monkeypatch, policy_bundle, tmp_path
    )

    state = PipelineState(
        phase="planning",
        budget_caps={"iteration": 1, "reviewer_pass": 1},
        budget_remaining={"iteration": 1, "reviewer_pass": 1},
    )

    exit_code = runner_module.run(_config(), initial_state=state)
    assert exit_code == 0

    out = captured_console.export_text()

    # --- Assert phase ordering ---
    assert "[phase] ◆ planning" in out
    assert "[phase] ◆ development" in out
    assert "[phase] ◆ review" in out

    planning_idx = out.index("[phase] ◆ planning")
    development_idx = out.index("[phase] ◆ development")
    review_idx = out.index("[phase] ◆ review")

    assert planning_idx < development_idx < review_idx, (
        "Phase transitions should appear in order: planning → development → review"
    )

    # --- Assert run-start comes before first phase ---
    run_start_idx = out.index("MILESTONE META [run-start]")
    assert run_start_idx < planning_idx, "run-start should appear before the first phase transition"

    # --- Assert streaming content block structure ---
    # Content is emitted during the stubbed agent runs, which may span multiple phases.
    # We check for the overall streaming block structure rather than assuming content starts
    # in any particular phase.
    assert "[content-start]" in out, "Transcript should contain a content-start marker"
    # Require proper streaming sequence: content-start → content-continue#N → content-end
    assert "[content-continue" in out, "Transcript should contain content-continue markers"
    assert "[content-end]" in out, "Transcript should contain a content-end marker"

    # --- Assert [phase-close] contains elapsed= and content_blocks= ---
    phase_close_lines = [ln for ln in out.splitlines() if "[phase-close]" in ln]
    assert phase_close_lines, "Transcript should contain at least one [phase-close] line"
    for pc_line in phase_close_lines:
        assert "elapsed=" in pc_line, f"[phase-close] missing elapsed=: {pc_line}"
        assert "content_blocks=" in pc_line, f"[phase-close] missing content_blocks=: {pc_line}"

    # --- Assert [run-end] appears after last [phase-close] and before completion ---
    assert "MILESTONE META [run-end]" in out, "Transcript should contain [run-end] MILESTONE"
    run_end_idx = out.index("MILESTONE META [run-end]")
    last_phase_close_idx = out.rindex("[phase-close]")
    assert last_phase_close_idx < run_end_idx, "[run-end] should appear after last [phase-close]"
    # Completion summary should appear after run-end
    completion_idx = out.index("Pipeline Complete")
    assert run_end_idx < completion_idx, "[run-end] should appear before completion summary"

    # --- Assert [run-end] block contains required fields ---
    run_end_block = out[run_end_idx:]
    assert any("phase=" in ln for ln in run_end_block.splitlines()), "[run-end] missing phase="
    assert any("elapsed=" in ln for ln in run_end_block.splitlines()), "[run-end] missing elapsed="
    assert any(
        "content_blocks=" in ln for ln in run_end_block.splitlines()
    ), "[run-end] missing content_blocks="

    # --- Assert completion summary ---
    assert "Pipeline Complete" in out or "Pipeline Failed" in out


def test_quiet_mode_suppresses_run_start_and_phase_close(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """In quiet mode, run-start, phase-close, and run-end lines are not emitted."""
    monkeypatch.setenv("CI", "1")
    policy_bundle = load_policy(DEFAULT_POLICY_DIR)

    _invoked_phases, captured_console, _captured_displays = _install_runner_stubs(
        monkeypatch, policy_bundle, tmp_path
    )

    state = PipelineState(
        phase="planning",
        budget_caps={"iteration": 1, "reviewer_pass": 0},
        budget_remaining={"iteration": 1, "reviewer_pass": 0},
    )

    # Run with Verbosity.QUIET
    quiet_config = UnifiedConfig()
    exit_code = runner_module.run(quiet_config, initial_state=state, verbosity=Verbosity.QUIET)
    assert exit_code == 0

    out = captured_console.export_text()

    # run-start, phase-close, and run-end should NOT appear in quiet mode
    assert "[run-start]" not in out, "run-start should be suppressed in quiet mode"
    assert "[phase-close]" not in out, "phase-close should be suppressed in quiet mode"
    assert "[run-end]" not in out, "run-end should be suppressed in quiet mode"

    # But completion summary still renders
    assert ("Pipeline Complete" in out) or ("Pipeline Failed" in out)
