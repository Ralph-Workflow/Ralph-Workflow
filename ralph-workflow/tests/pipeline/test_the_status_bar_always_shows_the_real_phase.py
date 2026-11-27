from __future__ import annotations

import io
import re
import tempfile
from pathlib import Path

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.status_bar import StatusBarModel
from ralph.pipeline.conflict_resolution.status import (
    PHASE_LABEL,
    conflict_status_bar_session,
    push_conflict_status_bar,
)
from ralph.pipeline.run_loop import build_status_bar_model, push_status_bar_if_changed
from ralph.pipeline.state import PipelineState
from ralph.pipeline.status_bar_authority import running_agent_status, show_phase_status
from ralph.policy.loader import load_policy

_RALPH = Path(__file__).resolve().parents[2] / "ralph"
_OWNER = _RALPH / "pipeline" / "status_bar_authority.py"
_DISPLAY_PACKAGE = _RALPH / "display"


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def _display(root: Path) -> ParallelDisplay:
    console = Console(file=_Tty(), force_terminal=True, width=120)
    return ParallelDisplay(make_display_context(console=console, env={}), workspace_root=root)


def _shown(display: ParallelDisplay) -> StatusBarModel:
    model = display.status_bar.last_model
    assert isinstance(model, StatusBarModel)
    return model


def test_the_phase_returns_when_a_resolution_ends_without_any_snapshot() -> None:
    root = Path(tempfile.mkdtemp())
    display = _display(root)
    policy = load_policy(root / ".agent")
    state = PipelineState(phase="development_analysis")
    sig = push_status_bar_if_changed(display, state, policy, root, None)

    with conflict_status_bar_session(display, root):
        push_conflict_status_bar(display, root, target="main", round_index=1, round_cap=3)
        assert _shown(display).phase_label == PHASE_LABEL
    push_status_bar_if_changed(display, state, policy, root, sig)

    assert _shown(display).phase_label == build_status_bar_model(state, policy, root).phase_label


def test_a_resolver_bar_can_never_outlive_its_resolution() -> None:
    root = Path(tempfile.mkdtemp())
    display = _display(root)
    policy = load_policy(root / ".agent")
    state = PipelineState(phase="development_analysis")
    push_status_bar_if_changed(display, state, policy, root, None)

    push_conflict_status_bar(display, root, target="main", round_index=1, round_cap=3)

    assert _shown(display).phase_label != PHASE_LABEL


def test_a_stray_phase_write_is_corrected_on_the_next_step() -> None:
    root = Path(tempfile.mkdtemp())
    display = _display(root)
    policy = load_policy(root / ".agent")
    state = PipelineState(phase="development")
    sig = push_status_bar_if_changed(display, state, policy, root, None)

    show_phase_status(
        display, StatusBarModel(workspace_root=str(root), phase_label="Bogus", phase_style="")
    )
    push_status_bar_if_changed(display, state, policy, root, sig)

    assert _shown(display).phase_label == build_status_bar_model(state, policy, root).phase_label


def test_an_unchanged_phase_is_not_repainted() -> None:
    root = Path(tempfile.mkdtemp())
    display = _display(root)
    policy = load_policy(root / ".agent")
    state = PipelineState(phase="development")
    sig = push_status_bar_if_changed(display, state, policy, root, None)
    first = display.status_bar.last_model

    push_status_bar_if_changed(display, state, policy, root, sig)

    assert display.status_bar.last_model is first


def test_only_the_status_bar_owner_writes_to_the_status_bar() -> None:
    writers = [
        str(path.relative_to(_RALPH.parent))
        for path in _RALPH.rglob("*.py")
        if path != _OWNER
        and _DISPLAY_PACKAGE not in path.parents
        and re.search(r"\bupdate_status_bar\b", path.read_text(encoding="utf-8"))
    ]

    assert writers == []


def test_an_agent_run_inside_another_phase_shows_its_own_phase_and_agent() -> None:
    root = Path(tempfile.mkdtemp())
    display = _display(root)
    policy = load_policy(root / ".agent")
    state = PipelineState(phase="planning")
    push_status_bar_if_changed(display, state, policy, root, None)
    planning = _shown(display)

    with running_agent_status("development_commit_review", "codex/gpt"):
        shown = _shown(display)
        assert shown.phase_label == "Development Commit Review"
        assert shown.agent_name == "codex/gpt"

    assert _shown(display) is planning


def test_a_resolver_agent_keeps_the_resolution_label_and_names_the_real_agent() -> None:
    root = Path(tempfile.mkdtemp())
    display = _display(root)
    policy = load_policy(root / ".agent")
    push_status_bar_if_changed(display, PipelineState(phase="development"), policy, root, None)

    with conflict_status_bar_session(display, root):
        push_conflict_status_bar(display, root, target="main", round_index=1, round_cap=3)
        with running_agent_status("rebase_conflict_resolution", "pi/minimax"):
            shown = _shown(display)
            assert shown.phase_label == PHASE_LABEL
            assert shown.agent_name == "pi/minimax"
