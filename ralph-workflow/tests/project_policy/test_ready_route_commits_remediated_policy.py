from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline.state import PipelineState
from ralph.project_policy import analysis as policy_analysis
from ralph.project_policy import cli_integration, markers
from ralph.project_policy.models import ReadinessResult, ReadinessStatus
from ralph.project_policy.pipeline_graph import PHASE_REMEDIATION
from ralph.workspace.memory import MemoryWorkspace
from ralph.workspace.scope import WorkspaceScope
from tests.project_policy.policy_corpus import remediation_required_result, seed_complete_corpus

if TYPE_CHECKING:
    import pytest

    from ralph.project_policy.analysis import InvokePolicyAgent
    from ralph.workspace.protocol import Workspace

_APPROVAL = (
    "---\n"
    "type: policy_remediation_analysis_decision\n"
    "status: completed\n"
    "---\n\n"
    "## Summary\n\n"
    "- [SUM-1] Policy is sound; every committed criterion is met.\n\n"
    "## Criterion Verdicts\n\n"
    "- [PR-001] Criterion: the declared verification command resolves. "
    "Expected observation: `make verify` invokes a target. "
    "Verdict: met. Evidence: make reports the rule. "
    "Location: verification-policy.md RALPH-COMMAND.\n"
)


def _fixing_agent(workspace: Workspace) -> InvokePolicyAgent:
    def invoke(*, phase: str, prompt_path: str, progress_check: object = None) -> bool:
        del prompt_path, progress_check
        if phase == PHASE_REMEDIATION:
            seed_complete_corpus(workspace)
        else:
            workspace.write(policy_analysis.ANALYSIS_ARTIFACT_REL_PATH, _APPROVAL)
        return True

    return invoke


def test_a_remediation_that_ends_ready_commits_the_policy_it_rewrote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = MemoryWorkspace()
    commits: list[tuple[str, frozenset[str] | None]] = []
    emitted: list[str] = []
    root = "/test/ready-commits"
    monkeypatch.setattr(
        cli_integration,
        "run_policy_readiness_preflight",
        lambda *_args, **_kwargs: remediation_required_result(),
    )

    cli_integration.run_project_policy_readiness(
        load_result=cli_integration.LoadResult(
            config=UnifiedConfig(),
            workspace_scope=WorkspaceScope(root=root, allowed_roots=[root]),
            initial_state=PipelineState(phase="planning", policy_entry_phase="planning"),
            policy_bundle=None,
            run_id="test-ready-commit",
        ),
        display_context=make_display_context(),
        workspace_factory=lambda: workspace,
        emit_factory=emitted.append,
        invoke_remediation_agent_factory=_fixing_agent,
        is_tty=lambda: False,
        working_tree_snapshot=lambda _scope: frozenset(),
        commit_policy_updates=lambda scope, dirty: commits.append((str(scope.root), dirty)),
    )

    assert any("ready" in line.lower() for line in emitted), emitted
    assert commits == [(root, frozenset())]


def _run(
    monkeypatch: pytest.MonkeyPatch,
    workspace: MemoryWorkspace,
    snapshot: frozenset[str],
    commits: list[tuple[str, frozenset[str] | None]],
) -> None:
    root = "/test/ready-commits"
    monkeypatch.setattr(
        cli_integration,
        "run_policy_readiness_preflight",
        lambda *_args, **_kwargs: remediation_required_result(),
    )
    cli_integration.run_project_policy_readiness(
        load_result=cli_integration.LoadResult(
            config=UnifiedConfig(),
            workspace_scope=WorkspaceScope(root=root, allowed_roots=[root]),
            initial_state=PipelineState(phase="planning", policy_entry_phase="planning"),
            policy_bundle=None,
            run_id="test-ready-commit",
        ),
        display_context=make_display_context(),
        workspace_factory=lambda: workspace,
        emit_factory=lambda _line: None,
        invoke_remediation_agent_factory=_fixing_agent,
        is_tty=lambda: False,
        working_tree_snapshot=lambda _scope: snapshot,
        commit_policy_updates=lambda _scope, dirty: commits.append(("", dirty)),
    )


def test_policy_files_an_interrupted_remediation_left_behind_are_committed_on_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ralph_written = "docs/ralph-workflow-policy/testing-policy.md"
    user_edited = "docs/ralph-workflow-policy/linting-policy.md"
    first = MemoryWorkspace()
    commits: list[tuple[str, frozenset[str] | None]] = []
    _run(monkeypatch, first, frozenset(), commits)
    assert first.exists(markers.POLICY_REMEDIATION_WRITES_REL_PATH)

    resumed = MemoryWorkspace()
    seed_complete_corpus(resumed)
    resumed.write(
        markers.POLICY_REMEDIATION_WRITES_REL_PATH,
        first.read(markers.POLICY_REMEDIATION_WRITES_REL_PATH),
    )
    resumed.write(user_edited, resumed.read(user_edited) + "\nmy draft\n")
    commits.clear()

    _run(monkeypatch, resumed, frozenset({ralph_written, user_edited}), commits)

    assert commits
    excluded = commits[-1][1] or frozenset()
    assert ralph_written not in excluded
    assert user_edited in excluded


def test_a_policy_already_ready_at_start_still_commits_what_ralph_left_uncommitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = MemoryWorkspace()
    seed_complete_corpus(workspace)
    commits: list[tuple[str, frozenset[str] | None]] = []
    root = "/test/ready-at-start"
    monkeypatch.setattr(
        cli_integration,
        "run_policy_readiness_preflight",
        lambda *_args, **_kwargs: ReadinessResult(status=ReadinessStatus.READY, findings=[]),
    )

    cli_integration.run_project_policy_readiness(
        load_result=cli_integration.LoadResult(
            config=UnifiedConfig(),
            workspace_scope=WorkspaceScope(root=root, allowed_roots=[root]),
            initial_state=PipelineState(phase="planning", policy_entry_phase="planning"),
            policy_bundle=None,
            run_id="test-ready-at-start",
        ),
        display_context=make_display_context(),
        workspace_factory=lambda: workspace,
        emit_factory=lambda _line: None,
        invoke_remediation_agent_factory=_fixing_agent,
        is_tty=lambda: False,
        working_tree_snapshot=lambda _scope: frozenset(),
        commit_policy_updates=lambda _scope, dirty: commits.append(("", dirty)),
    )

    assert commits == [("", frozenset())]
