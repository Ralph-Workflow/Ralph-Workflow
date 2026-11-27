from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.git.scoped_auto_commit import list_dirty_paths
from ralph.pipeline.state import PipelineState
from ralph.project_policy import round_landing
from ralph.project_policy.cli_integration import LoadResult, run_project_policy_readiness
from ralph.workspace.memory import MemoryWorkspace
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStop
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.pipeline.resolution_outcome import StopResolutionOutcome

_ROOT = "/test/remediation-rounds-are-landed"


def test_each_remediation_round_is_committed_and_landed_as_it_ends() -> None:
    landed: list[str] = []
    rounds: list[str] = []

    def invoke(_ws: object):
        def _agent(*, phase: str, prompt_path: str, progress_check: object = None) -> bool:
            del prompt_path, progress_check
            rounds.append(phase)
            return True

        return _agent

    def land(scope: WorkspaceScope, _config: UnifiedConfig, _dirty: frozenset[str] | None) -> None:
        landed.append(str(scope.root))

    run_project_policy_readiness(
        load_result=LoadResult(
            config=UnifiedConfig(),
            workspace_scope=WorkspaceScope(root=_ROOT, allowed_roots=[_ROOT]),
            initial_state=PipelineState(phase="planning", policy_entry_phase="planning"),
            policy_bundle=None,
            run_id="remediation-rounds-are-landed",
        ),
        display_context=make_display_context(),
        workspace_factory=MemoryWorkspace,
        emit_factory=lambda _m: None,
        is_tty=lambda: False,
        invoke_remediation_agent_factory=invoke,
        catchup_factory=lambda _c, _r: None,
        recover_integration=lambda _s, _c: None,
        working_tree_snapshot=lambda _s: frozenset(),
        commit_policy_updates=lambda _s, _d: None,
        land_policy_round=land,
    )

    remediation_rounds = rounds.count("policy_remediation")
    assert remediation_rounds >= 1
    assert landed == [_ROOT] * remediation_rounds


def test_the_readiness_commit_is_landed_when_readiness_ends() -> None:
    events: list[str] = []

    run_project_policy_readiness(
        load_result=LoadResult(
            config=UnifiedConfig(),
            workspace_scope=WorkspaceScope(root=_ROOT, allowed_roots=[_ROOT]),
            initial_state=PipelineState(phase="planning", policy_entry_phase="planning"),
            policy_bundle=None,
            run_id="readiness-commit-is-landed",
        ),
        display_context=make_display_context(),
        workspace_factory=MemoryWorkspace,
        emit_factory=lambda _m: None,
        is_tty=lambda: False,
        invoke_remediation_agent_factory=lambda _ws: lambda **_kw: True,
        catchup_factory=lambda _c, _r: None,
        recover_integration=lambda _s, _c: None,
        working_tree_snapshot=lambda _s: frozenset(),
        commit_policy_updates=lambda _s, _d: events.append("commit"),
        land_policy_round=lambda _s, _c, _d: events.append("round"),
        land_readiness_commits=lambda scope, _c: events.append(f"land {scope.root}"),
    )

    assert events, "readiness must commit its policy updates"
    assert events[-1] == f"land {_ROOT}", "the readiness commit must be landed last"
    assert events.count(f"land {_ROOT}") == 1


def _conflict_resolver(_root: Path, _branch: str) -> StopResolutionOutcome:
    raise AssertionError("only passed through, never called")


def _rebase_stop_resolver(_root: Path, _branch: str, _stop: RebaseStop) -> bool:
    raise AssertionError("only passed through, never called")


def test_a_round_landing_resolves_conflicts_with_the_given_resolvers(
    monkeypatch: pytest.MonkeyPatch, tmp_git_repo: Path
) -> None:
    integrated: list[dict[str, object]] = []

    def _integrate(
        _config: UnifiedConfig, _scope: WorkspaceScope, _state: RebaseState, **kw: object
    ) -> None:
        integrated.append(kw)

    monkeypatch.setattr(round_landing, "auto_integrate_after_commit", _integrate)
    scope = WorkspaceScope(root=str(tmp_git_repo), allowed_roots=[str(tmp_git_repo)])

    landing = round_landing.round_landing(
        conflict_resolver=_conflict_resolver, rebase_stop_resolver=_rebase_stop_resolver
    )
    landing(scope, UnifiedConfig(), None)

    assert integrated == [
        {"conflict_resolver": _conflict_resolver, "rebase_stop_resolver": _rebase_stop_resolver}
    ]


def test_a_round_lands_on_a_clean_tree_and_gives_development_its_work_back(
    monkeypatch: pytest.MonkeyPatch, tmp_git_repo: Path
) -> None:
    unfinished = tmp_git_repo / "app.rb"
    unfinished.write_text("development work in progress\n", encoding="utf-8")
    dirty_while_landing: list[frozenset[str]] = []

    def _integrate(
        _config: UnifiedConfig, scope: WorkspaceScope, _state: RebaseState, **_kw: object
    ) -> None:
        dirty_while_landing.append(list_dirty_paths(scope.root))

    monkeypatch.setattr(round_landing, "auto_integrate_after_commit", _integrate)
    scope = WorkspaceScope(root=str(tmp_git_repo), allowed_roots=[str(tmp_git_repo)])

    landing = round_landing.round_landing(conflict_resolver=None, rebase_stop_resolver=None)
    landing(scope, UnifiedConfig(), frozenset({"app.rb"}))

    assert dirty_while_landing == [frozenset()]
    assert unfinished.read_text(encoding="utf-8") == "development work in progress\n"
