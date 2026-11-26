from __future__ import annotations

from pathlib import Path

from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline.state import PipelineState
from ralph.project_policy.cli_integration import LoadResult, run_project_policy_readiness
from ralph.workspace.memory import MemoryWorkspace
from ralph.workspace.scope import WorkspaceScope


class _Worker:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def stop(self) -> None:
        self._events.append("catch-up stopped")


def _load_result(root: str) -> LoadResult:
    return LoadResult(
        config=UnifiedConfig(),
        workspace_scope=WorkspaceScope(root=root, allowed_roots=[root]),
        initial_state=PipelineState(phase="planning", policy_entry_phase="planning"),
        policy_bundle=None,
        run_id="catchup-during-readiness",
    )


def test_the_background_catchup_runs_while_policy_readiness_and_remediation_run() -> None:
    events: list[str] = []
    roots: list[Path] = []

    def start_catchup(_config: UnifiedConfig, root: Path) -> _Worker:
        roots.append(root)
        events.append("catch-up started")
        return _Worker(events)

    def workspace() -> MemoryWorkspace:
        events.append("readiness reads the workspace")
        return MemoryWorkspace()

    run_project_policy_readiness(
        load_result=_load_result("/test/catchup-during-readiness"),
        display_context=make_display_context(),
        workspace_factory=workspace,
        emit_factory=lambda _m: None,
        is_tty=lambda: False,
        catchup_factory=start_catchup,
    )

    assert events[0] == "catch-up started"
    assert "readiness reads the workspace" in events
    assert events[-1] == "catch-up stopped"
    assert roots == [Path("/test/catchup-during-readiness")]
