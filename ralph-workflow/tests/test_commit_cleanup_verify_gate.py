"""Verify-gated black-box proofs for commit-cleanup leftover, routing, and rules."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from git import Repo

from ralph.agents.chain import ChainManager
from ralph.mcp.protocol._session_drain import SessionDrain
from ralph.phases import PhaseContext
from ralph.phases._commit_cleanup_catalog import render_delete_decision_rules_markdown
from ralph.phases.commit_cleanup import handle_commit_cleanup_phase
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PhaseFailureEvent, PipelineEvent
from ralph.pipeline.handoffs import resolve_next_phase
from ralph.policy.loader import load_policy
from ralph.policy.models import AgentChainConfig, AgentDrainConfig, AgentsPolicy
from ralph.prompts.commit_cleanup import render_commit_cleanup_prompt
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.types import SessionCapabilities
from ralph.recovery.classifier import FailureCategory
from ralph.test_suites import REQUIRED_AUTO_INTEGRATE_E2E_FILES
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    import pytest

COMMIT_CLEANUP_ARTIFACT_PATH = ".agent/artifacts/commit_cleanup.md"
_INVARIANTS_FILE = "tests/test_commit_cleanup_invariants.py"
_DEFAULT_POLICY_DIR = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"


def _write_commit_cleanup_artifact(
    workspace: FsWorkspace,
    content: dict[str, object],
) -> None:
    analysis_complete = str(content["analysis_complete"]).lower()
    lines = [
        "---",
        "type: commit_cleanup",
        f"analysis_complete: {analysis_complete}",
        "---",
        "## Actions",
    ]
    actions = content.get("actions", [])
    assert isinstance(actions, list)
    for index, action in enumerate(actions, start=1):
        assert isinstance(action, dict)
        action_name = action["action"]
        value = action.get("path", action.get("pattern"))
        lines.append(f"- [A{index}] {action_name} | {value}")
    path = Path(workspace.root) / COMMIT_CLEANUP_ARTIFACT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _ctx(
    workspace: FsWorkspace,
    *,
    chain_manager: object | None = None,
) -> PhaseContext:
    return PhaseContext.construct(
        workspace=workspace,
        registry=object(),
        chain_manager=object() if chain_manager is None else chain_manager,
        pipeline_policy=object(),
        artifacts_policy=object(),
        agents_policy=object(),
    )


def _invoke(phase: str = "development_commit_cleanup") -> InvokeAgentEffect:
    return InvokeAgentEffect(agent_name="dev", phase=phase, prompt_file="cleanup.txt")


def _run(
    workspace: FsWorkspace,
    phase: str = "development_commit_cleanup",
    *,
    chain_manager: object | None = None,
) -> list[object]:
    return handle_commit_cleanup_phase(
        _invoke(phase),
        _ctx(workspace, chain_manager=chain_manager),
    )


def _render_prompt(tmp_git_repo: Path, phase: str = "development_commit_cleanup") -> str:
    return render_commit_cleanup_prompt(
        phase=phase,
        workspace_root=tmp_git_repo,
        worker_namespace=None,
        prompt_content="criteria",
        product_criteria_path=str(tmp_git_repo / ".agent" / "PRODUCT_CRITERIA.md"),
        template_name="commit_cleanup.jinja",
        tmpl_ctx=TemplateContext.default(tmp_git_repo),
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.COMMIT),
    )


def test_well_formed_leftover_is_missing_on_second_handle_without_resubmit(
    tmp_git_repo: Path,
) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    junk = tmp_git_repo / "session-output.txt"
    junk.write_text("tmp\n", encoding="utf-8")
    _write_commit_cleanup_artifact(
        workspace,
        {
            "analysis_complete": True,
            "actions": [{"action": "delete_file", "path": "session-output.txt"}],
        },
    )
    first = _run(workspace)
    assert first == [PipelineEvent.AGENT_SUCCESS]
    assert not junk.exists()
    second = _run(workspace)
    assert isinstance(second[0], PhaseFailureEvent)
    assert second[0].failure_category is FailureCategory.ARTIFACT_VALIDATION


def test_resubmitted_duplicate_batch_remains_idempotent(tmp_git_repo: Path) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    junk = tmp_git_repo / "session-output.txt"
    junk.write_text("tmp\n", encoding="utf-8")
    payload: dict[str, object] = {
        "analysis_complete": True,
        "actions": [
            {"action": "delete_file", "path": "session-output.txt"},
            {"action": "add_to_gitignore", "pattern": "*.exe"},
        ],
    }
    _write_commit_cleanup_artifact(workspace, payload)
    first = _run(workspace)
    _write_commit_cleanup_artifact(workspace, payload)
    second = _run(workspace)
    assert first == [PipelineEvent.AGENT_SUCCESS]
    assert second == [PipelineEvent.AGENT_SUCCESS]
    assert not junk.exists()
    assert "*.exe" in (tmp_git_repo / ".gitignore").read_text(encoding="utf-8")


def test_missing_artifact_bound_survives_highest_priority_reselection(
    tmp_git_repo: Path,
) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    agents_policy = AgentsPolicy(
        agent_chains={
            "commit": AgentChainConfig(agents=["sonnet", "fallback"]),
        },
        agent_drains={
            "commit": AgentDrainConfig(chain="commit"),
        },
    )
    chain_manager = ChainManager(agents_policy)
    events: list[object] = []
    for _ in range(4):
        chain = chain_manager.chain_for_drain("commit")
        assert chain.agents[0] == "sonnet"
        events.append(_run(workspace, chain_manager=chain_manager)[0])
    assert isinstance(events[0], PhaseFailureEvent)
    assert events[0].failure_category is FailureCategory.ARTIFACT_VALIDATION
    assert events[-1] is PipelineEvent.AGENT_SUCCESS


def test_cleanup_failure_routes_to_commit_not_failed_terminal() -> None:
    pipeline = load_policy(_DEFAULT_POLICY_DIR).pipeline
    assert resolve_next_phase("development_commit_cleanup", "failure", pipeline) == (
        "development_commit"
    )
    assert resolve_next_phase("development_final_commit_cleanup", "failure", pipeline) == (
        "development_final_commit"
    )


def test_invariants_module_is_required_auto_integrate() -> None:
    assert _INVARIANTS_FILE in REQUIRED_AUTO_INTEGRATE_E2E_FILES


def test_next_prompt_contains_declined_and_apply_failed_paths(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    (tmp_git_repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    _write_commit_cleanup_artifact(
        workspace,
        {
            "analysis_complete": True,
            "actions": [
                {"action": "delete_file", "path": "app.py"},
                {"action": "add_to_gitignore", "pattern": "*.bin"},
            ],
        },
    )

    def _boom(_root: Path, _patterns: list[str]) -> None:
        raise OSError("append failed")

    monkeypatch.setattr("ralph.phases.commit_cleanup.append_to_gitignore", _boom)
    _run(workspace, phase="development_final_commit_cleanup")
    rendered = _render_prompt(tmp_git_repo, phase="development_final_commit_cleanup")
    assert "app.py" in rendered
    assert "*.bin" in rendered
    lowered = rendered.lower()
    assert "declined" in lowered or "rejected" in lowered
    assert "failed" in lowered


def test_mixed_declined_delete_and_throwing_ignore_is_not_applied_work(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    (tmp_git_repo / "lib.py").write_text("pass\n", encoding="utf-8")
    _write_commit_cleanup_artifact(
        workspace,
        {
            "analysis_complete": True,
            "actions": [
                {"action": "delete_file", "path": "lib.py"},
                {"action": "add_to_gitignore", "pattern": "*.o"},
            ],
        },
    )

    def _boom(_root: Path, _patterns: list[str]) -> None:
        raise OSError("ignore failed")

    monkeypatch.setattr("ralph.phases.commit_cleanup.append_to_gitignore", _boom)
    result = _run(workspace)
    gitignore = tmp_git_repo / ".gitignore"
    ignore_text = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    assert "*.o" not in ignore_text
    assert (tmp_git_repo / "lib.py").exists()
    assert result == [PipelineEvent.AGENT_SUCCESS] or isinstance(result[0], PhaseFailureEvent)


def test_untracked_completion_report_md_deletes_and_tracked_is_declined(
    tmp_git_repo: Path,
) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    report = tmp_git_repo / "completion_report.md"
    report.write_text("# generated\n", encoding="utf-8")
    _write_commit_cleanup_artifact(
        workspace,
        {
            "analysis_complete": True,
            "actions": [{"action": "delete_file", "path": "completion_report.md"}],
        },
    )
    first = _run(workspace)
    assert first == [PipelineEvent.AGENT_SUCCESS]
    assert not report.exists()

    report.write_text("# tracked\n", encoding="utf-8")
    repo = Repo(tmp_git_repo)
    try:
        repo.index.add(["completion_report.md"])
        repo.index.commit("track completion report")
    finally:
        repo.close()
    _write_commit_cleanup_artifact(
        workspace,
        {
            "analysis_complete": True,
            "actions": [{"action": "delete_file", "path": "completion_report.md"}],
        },
    )
    second = _run(workspace)
    assert second == [PipelineEvent.AGENT_SUCCESS]
    assert report.exists()


def test_prompt_named_deletable_classes_match_engine_boundary(tmp_git_repo: Path) -> None:
    rules = render_delete_decision_rules_markdown()
    assert "`delete_file`" in rules
    lowered = rules.lower()
    assert "completion" in lowered or "report" in lowered
    assert "lockfile" in lowered or "package-lock.json" in rules
    jinja = (
        Path(__file__).resolve().parents[1]
        / "ralph"
        / "prompts"
        / "templates"
        / "commit_cleanup.jinja"
    ).read_text(encoding="utf-8")
    assert "DELETE_DECISION_RULES" in jinja
    assert "LAST_RETRY_ERROR" in jinja
    workspace = FsWorkspace(tmp_git_repo)
    (tmp_git_repo / "scratch.tmp").write_text("x\n", encoding="utf-8")
    _write_commit_cleanup_artifact(
        workspace,
        {
            "analysis_complete": True,
            "actions": [
                {"action": "delete_file", "path": "scratch.tmp"},
                {"action": "delete_file", "path": "app.py"},
                {"action": "delete_file", "path": "package-lock.json"},
                {"action": "delete_file", "path": "../outside.txt"},
            ],
        },
    )
    (tmp_git_repo / "app.py").write_text("print(1)\n", encoding="utf-8")
    (tmp_git_repo / "package-lock.json").write_text("{}", encoding="utf-8")
    result = _run(workspace)
    assert result == [PipelineEvent.AGENT_SUCCESS]
    assert not (tmp_git_repo / "scratch.tmp").exists()
    assert (tmp_git_repo / "app.py").exists()
    assert (tmp_git_repo / "package-lock.json").exists()
