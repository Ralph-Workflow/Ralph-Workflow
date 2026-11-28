from __future__ import annotations

import io
import tempfile
from pathlib import Path

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.status_bar import StatusBarModel
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.pipeline.conflict_resolution.status import (
    DIAGNOSIS_PHASE_LABEL,
    PHASE_LABEL,
    conflict_status_bar_session,
    diagnostic_status_bar_session,
    push_conflict_status_bar,
)
from ralph.pipeline.run_loop import push_status_bar_if_changed
from ralph.pipeline.state import PipelineState
from ralph.pipeline.status_bar_authority import running_agent_status
from ralph.policy.loader import load_policy


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


def test_the_diagnostic_agent_is_labelled_as_a_diagnosis_inside_a_resolution() -> None:
    root = Path(tempfile.mkdtemp())
    display = _display(root)
    push_status_bar_if_changed(
        display, PipelineState(phase="development"), load_policy(root / ".agent"), root, None
    )

    with conflict_status_bar_session(display, root):
        push_conflict_status_bar(display, root, target="main", round_index=1, round_cap=3)
        with (
            diagnostic_status_bar_session(
                display, root, target="main", attempt=1, attempt_cap=5, agent_name="codex"
            ),
            running_agent_status(PHASE_RESOLUTION, "codex"),
        ):
            shown = _shown(display)
            assert shown.phase_label.startswith(DIAGNOSIS_PHASE_LABEL)
            assert shown.agent_name == "codex"
        assert _shown(display).phase_label.startswith(PHASE_LABEL)
