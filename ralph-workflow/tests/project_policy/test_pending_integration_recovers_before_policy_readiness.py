from __future__ import annotations

from pathlib import Path

from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline.state import PipelineState
from ralph.project_policy.cli_integration import LoadResult, run_project_policy_readiness
from ralph.workspace.memory import MemoryWorkspace
from ralph.workspace.scope import WorkspaceScope

_ROOT = "/test/pending-integration-before-readiness"


def _load_result() -> LoadResult:
    return LoadResult(
        config=UnifiedConfig(),
        workspace_scope=WorkspaceScope(root=_ROOT, allowed_roots=[_ROOT]),
        initial_state=PipelineState(phase="planning", policy_entry_phase="planning"),
        policy_bundle=None,
        run_id="pending-integration-before-readiness",
    )


def test_a_retained_integration_is_recovered_before_policy_readiness_dispatches() -> None:
    events: list[str] = []
    recovered_roots: list[Path] = []

    def recover(scope: WorkspaceScope, _config: UnifiedConfig) -> None:
        recovered_roots.append(Path(scope.root))
        events.append("integration recovered")

    def workspace() -> MemoryWorkspace:
        events.append("readiness reads the workspace")
        return MemoryWorkspace()

    run_project_policy_readiness(
        load_result=_load_result(),
        display_context=make_display_context(),
        workspace_factory=workspace,
        emit_factory=lambda _m: None,
        is_tty=lambda: False,
        catchup_factory=lambda _c, _r: None,
        recover_integration=recover,
    )

    assert events[0] == "integration recovered"
    assert "readiness reads the workspace" in events
    assert recovered_roots == [Path(_ROOT)]
