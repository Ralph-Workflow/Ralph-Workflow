from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.cli.commands import run as run_module
from ralph.display.phase_status import format_exit_trigger
from ralph.display.snapshot import snapshot_from_state
from ralph.pipeline import checkpoint as ckpt
from ralph.pipeline.state import PipelineState
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pathlib import Path


def test_a_resumed_run_no_longer_reports_the_earlier_interrupt(tmp_path: Path) -> None:
    ckpt.save(
        PipelineState(phase="development", interrupted_by_user=True),
        ckpt.run_state_path(tmp_path),
    )

    resumed = run_module.load_resume_state(WorkspaceScope(tmp_path))

    assert resumed is not None
    assert resumed.interrupted_by_user is False
    assert format_exit_trigger(snapshot_from_state(resumed)) != "interrupted"
