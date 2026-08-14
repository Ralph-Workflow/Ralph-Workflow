"""Tests for cycle-timebox warning injection in prompt materialization."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.policy.models import (
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
)
from ralph.prompts.materialize import (
    PromptPhaseContext,
    PromptPhaseOptions,
    _format_cycle_timebox_warning,
    materialize_prompt_for_phase,
)
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace

if TYPE_CHECKING:
    from pytest import MonkeyPatch


def test_cycle_timebox_warning_absent_when_option_is_none(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """No warning is appended when ``cycle_timebox_warning`` is not set."""
    ws = MemoryWorkspace(root=str(tmp_path))
    ws.write("PROMPT.md", "Base prompt.")
    ws.write(".agent/PLAN.md", "# Plan")
    caps = SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT)
    p = PipelinePolicy(
        phases={
            "development": PhaseDefinition(
                drain="development_result",
                role="execution",
                transitions=PhaseTransition(on_success="complete"),
            ),
            "complete": PhaseDefinition(
                drain="complete", role="terminal", terminal_outcome="success",
                transitions=PhaseTransition(on_success="complete"),
            ),
        },
        entry_phase="development", terminal_phase="complete",
    )
    ctx = PromptPhaseContext(
        phase="development", workspace=ws, pipeline_policy=p,
        session_caps=caps, workspace_root=tmp_path,
    )
    monkeypatch.setattr(
        "ralph.prompts.materialize._render_prompt_for_phase",
        lambda _ctx, _opts: "BASE PROMPT",
    )
    rendered = ws.read(materialize_prompt_for_phase(ctx, PromptPhaseOptions()))
    assert "Cycle Timebox Warning" not in rendered


def test_cycle_timebox_warning_present_when_option_set(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """The warning appendix appears when ``cycle_timebox_warning`` is provided."""
    ws = MemoryWorkspace(root=str(tmp_path))
    ws.write("PROMPT.md", "Base prompt.")
    ws.write(".agent/PLAN.md", "# Plan")
    caps = SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT)
    p = PipelinePolicy(
        phases={
            "development": PhaseDefinition(
                drain="development_result", role="execution",
                transitions=PhaseTransition(on_success="complete"),
            ),
            "complete": PhaseDefinition(
                drain="complete", role="terminal", terminal_outcome="success",
                transitions=PhaseTransition(on_success="complete"),
            ),
        },
        entry_phase="development", terminal_phase="complete",
    )
    ctx = PromptPhaseContext(
        phase="development", workspace=ws, pipeline_policy=p,
        session_caps=caps, workspace_root=tmp_path,
    )
    warning = {
        "elapsed_seconds": 5760.0,
        "remaining_seconds": 1440.0,
        "duration_seconds": 7200.0,
        "finalization_target": "development_final_commit_cleanup",
    }
    monkeypatch.setattr(
        "ralph.prompts.materialize._render_prompt_for_phase",
        lambda _ctx, _opts: "BASE PROMPT",
    )
    rendered = ws.read(
        materialize_prompt_for_phase(
            ctx, PromptPhaseOptions(cycle_timebox_warning=warning),
        )
    )
    assert "Cycle Timebox Warning" in rendered
    assert "96 minutes" in rendered
    assert "120-minute" in rendered
    assert "24 minutes" in rendered
    assert "development_final_commit_cleanup" in rendered
    # The warning is appended AFTER the base prompt, not replacing it.
    assert rendered.startswith("BASE PROMPT")


def test_format_warning_exact_threshold_24min_under_defaults() -> None:
    """At exactly 80% of the default 7200s budget, the wording shows 24 min remaining."""
    text = _format_cycle_timebox_warning({
        "elapsed_seconds": 5760.0,
        "remaining_seconds": 1440.0,
        "duration_seconds": 7200.0,
        "finalization_target": "development_final_commit_cleanup",
    })
    assert "96 minutes" in text
    assert "120-minute" in text
    assert "24 minutes" in text
    assert "development_final_commit_cleanup" in text
    # The cycle deadline is enforced at routing boundaries, so a running
    # session is never interrupted by it. The wording must say so outright:
    # read as a countdown on the agent's own session it causes exactly the
    # early exit this warning is not asking for. The session wrap-up nag
    # (`_session_wrapup.wrapup_notice`) is the only stop signal.
    assert "does not cut your session short" in text
    assert "session wrap-up notice" in text
    assert "declare_complete" in text
    # The redirect ends THIS cycle, not the run: under the bundled routes a
    # timed-out cycle is followed by another planning cycle while the budget
    # has room, so promising the agent it has no future iteration is false —
    # and false scarcity is the pressure the honesty guidance is removing.
    assert "LAST development session of this cycle" in text
    assert "remaining cycle budget" in text


def test_format_warning_updates_for_later_elapsed() -> None:
    """At 90% elapsed the remaining time is smaller than at 80%."""
    text_80 = _format_cycle_timebox_warning({
        "elapsed_seconds": 5760.0,
        "remaining_seconds": 1440.0,
        "duration_seconds": 7200.0,
        "finalization_target": "development_final_commit_cleanup",
    })
    text_90 = _format_cycle_timebox_warning({
        "elapsed_seconds": 6480.0,
        "remaining_seconds": 720.0,
        "duration_seconds": 7200.0,
        "finalization_target": "development_final_commit_cleanup",
    })
    assert "24 minutes" in text_80
    assert "12 minutes" in text_90  # 720s = 12 min
    assert "108 minutes" in text_90  # 6480s = 108 min


def test_format_warning_custom_finalization_target() -> None:
    """Custom phase names appear in the warning text."""
    text = _format_cycle_timebox_warning({
        "elapsed_seconds": 480.0,
        "remaining_seconds": 120.0,
        "duration_seconds": 600.0,
        "finalization_target": "my_custom_cleanup",
    })
    assert "my_custom_cleanup" in text
    assert "10-minute" in text  # 600s = 10 min
    assert "2 minutes" in text  # 120s = 2 min remaining
