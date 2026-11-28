from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from ralph.pipeline import run_loop
from ralph.pipeline.auto_integrate_record import read_record
from ralph.pipeline.retry_diagnostic import RetryDiagnosticHooks
from ralph.pipeline.state import PipelineState
from ralph.workspace.scope import WorkspaceScope
from tests.test_a_feature_landed_as_a_rebased_copy_is_adopted import (
    _head,
    _landed_elsewhere,
    _record,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.pipeline.run_loop import LoopContext

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]


def test_work_already_on_the_target_is_released_before_recovery_runs_an_agent(
    tmp_path: Path,
) -> None:
    root, feature, landed = _landed_elsewhere(tmp_path)
    _record(root, feature, landed)
    recovery_steps: list[str] = []
    hooks = RetryDiagnosticHooks(
        save_checkpoint=lambda *_args: None,
        handoff_retained_failure=lambda *_args: recovery_steps.append("diagnosis"),
        conflict_resolution=lambda *_args: recovery_steps.append("resolver") is None,
    )
    ctx = cast(
        "LoopContext",
        SimpleNamespace(
            workspace_scope=WorkspaceScope(root), retained_hold_backoff=None, hooks=hooks
        ),
    )

    _state, blocked = run_loop.handle_retained_integration(
        PipelineState(phase="development"), ctx, "development"
    )

    assert blocked is None
    assert recovery_steps == []
    assert read_record(root) is None
    assert _head(root) == landed
