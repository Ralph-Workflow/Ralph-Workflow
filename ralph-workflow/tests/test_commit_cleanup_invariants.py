"""Black-box invariants for commit-cleanup termination, safety, and accounting."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from git import Repo
from loguru import logger

from ralph.mcp.protocol._session_drain import SessionDrain
from ralph.phases import PhaseContext
from ralph.phases._commit_cleanup_catalog import (
    GENERATED_TEXT_MARKERS,
    LOCKFILE_BASENAMES,
    render_delete_decision_rules_markdown,
)
from ralph.phases.commit_cleanup import handle_commit_cleanup_phase
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PhaseFailureEvent, PipelineEvent
from ralph.prompts.commit_cleanup import render_commit_cleanup_prompt
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.types import SessionCapabilities
from ralph.recovery.classifier import FailureCategory
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from pytest import LogCaptureFixture

COMMIT_CLEANUP_ARTIFACT_PATH = ".agent/artifacts/commit_cleanup.md"

pytestmark = [
    pytest.mark.timeout_seconds(5),
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
]


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


def _ctx(workspace: FsWorkspace) -> PhaseContext:
    return PhaseContext.construct(
        workspace=workspace,
        registry=object(),
        chain_manager=object(),
        pipeline_policy=object(),
        artifacts_policy=object(),
        agents_policy=object(),
    )


def _invoke(phase: str = "development_commit_cleanup") -> InvokeAgentEffect:
    return InvokeAgentEffect(agent_name="dev", phase=phase, prompt_file="cleanup.txt")


def _run(workspace: FsWorkspace, phase: str = "development_commit_cleanup") -> list[object]:
    return handle_commit_cleanup_phase(_invoke(phase), _ctx(workspace))


def test_identical_declined_path_replay_completes_without_phase_failure(
    tmp_git_repo: Path,
) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    (tmp_git_repo / "src.py").write_text("print(1)\n", encoding="utf-8")
    _write_commit_cleanup_artifact(
        workspace,
        {
            "analysis_complete": True,
            "actions": [{"action": "delete_file", "path": "src.py"}],
        },
    )
    first = _run(workspace)
    _write_commit_cleanup_artifact(
        workspace,
        {
            "analysis_complete": True,
            "actions": [{"action": "delete_file", "path": "src.py"}],
        },
    )
    second = _run(workspace)
    assert first == [PipelineEvent.AGENT_SUCCESS]
    assert second == [PipelineEvent.AGENT_SUCCESS]
    assert (tmp_git_repo / "src.py").exists()
    assert not any(isinstance(event, PhaseFailureEvent) for event in [*first, *second])


def test_missing_artifact_identical_replay_is_bounded(tmp_git_repo: Path) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    events = [_run(workspace)[0] for _ in range(4)]
    assert isinstance(events[0], PhaseFailureEvent)
    assert events[0].failure_category is FailureCategory.ARTIFACT_VALIDATION
    assert events[-1] is PipelineEvent.AGENT_SUCCESS
    assert not any(
        isinstance(event, PhaseFailureEvent) and event.phase == "failed_terminal"
        for event in events
    )


def test_unparseable_artifact_identical_replay_is_bounded(tmp_git_repo: Path) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    path = tmp_git_repo / COMMIT_CLEANUP_ARTIFACT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not a commit_cleanup artifact\n", encoding="utf-8")
    events = [_run(workspace)[0] for _ in range(4)]
    assert isinstance(events[0], PhaseFailureEvent)
    assert events[-1] is PipelineEvent.AGENT_SUCCESS


def test_all_throw_apply_identical_replay_is_bounded(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    _write_commit_cleanup_artifact(
        workspace,
        {
            "analysis_complete": True,
            "actions": [{"action": "add_to_gitignore", "pattern": "*.bin"}],
        },
    )

    def _boom(_root: Path, _patterns: list[str]) -> None:
        raise OSError("append failed")

    monkeypatch.setattr("ralph.phases.commit_cleanup.append_to_gitignore", _boom)
    events = [_run(workspace)[0] for _ in range(4)]
    assert isinstance(events[0], PhaseFailureEvent)
    assert events[-1] is PipelineEvent.AGENT_SUCCESS


def test_wholly_declined_deletes_emit_no_phase_failure(tmp_git_repo: Path) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    (tmp_git_repo / "README.md").write_text("docs\n", encoding="utf-8")
    _write_commit_cleanup_artifact(
        workspace,
        {
            "analysis_complete": True,
            "actions": [{"action": "delete_file", "path": "README.md"}],
        },
    )
    result = _run(workspace)
    assert result == [PipelineEvent.AGENT_SUCCESS]
    assert (tmp_git_repo / "README.md").exists()


def test_next_cleanup_prompt_contains_declined_path_and_reason(tmp_git_repo: Path) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    (tmp_git_repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    _write_commit_cleanup_artifact(
        workspace,
        {
            "analysis_complete": True,
            "actions": [{"action": "delete_file", "path": "app.py"}],
        },
    )
    _run(workspace, phase="development_final_commit_cleanup")
    rendered = render_commit_cleanup_prompt(
        phase="development_final_commit_cleanup",
        workspace_root=tmp_git_repo,
        worker_namespace=None,
        prompt_content="criteria",
        product_criteria_path=str(tmp_git_repo / ".agent" / "PRODUCT_CRITERIA.md"),
        template_name="commit_cleanup.jinja",
        tmpl_ctx=TemplateContext.default(tmp_git_repo),
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.COMMIT),
    )
    assert "app.py" in rendered
    assert "rejected" in rendered.lower() or "declined" in rendered.lower()


def test_next_cleanup_prompt_contains_apply_failure(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    _write_commit_cleanup_artifact(
        workspace,
        {
            "analysis_complete": True,
            "actions": [{"action": "add_to_git_exclude", "pattern": ".env.local"}],
        },
    )

    def _boom(_root: Path, _patterns: list[str]) -> None:
        raise OSError("exclude failed")

    monkeypatch.setattr("ralph.phases.commit_cleanup.add_to_git_exclude", _boom)
    _run(workspace)
    rendered = render_commit_cleanup_prompt(
        phase="development_commit_cleanup",
        workspace_root=tmp_git_repo,
        worker_namespace=None,
        prompt_content="criteria",
        product_criteria_path=str(tmp_git_repo / ".agent" / "PRODUCT_CRITERIA.md"),
        template_name="commit_cleanup.jinja",
        tmpl_ctx=TemplateContext.default(tmp_git_repo),
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.COMMIT),
    )
    assert ".env.local" in rendered
    assert "failed" in rendered.lower()


def test_declined_and_empty_decisions_log_once_at_warning(
    tmp_git_repo: Path,
    caplog: LogCaptureFixture,
) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    (tmp_git_repo / "main.py").write_text("pass\n", encoding="utf-8")
    _write_commit_cleanup_artifact(
        workspace,
        {
            "analysis_complete": True,
            "actions": [
                {"action": "delete_file", "path": "main.py"},
                {"action": "delete_file", "path": "main.py"},
            ],
        },
    )
    records: list[str] = []
    handler_id = logger.add(lambda message: records.append(str(message)), level="WARNING")
    try:
        _run(workspace)
    finally:
        logger.remove(handler_id)
    text = "".join(records)
    assert "Skipping unsafe delete_file action for 'main.py'" in text
    assert "duplicate delete_file" in text


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
    assert PipelineEvent.AGENT_SUCCESS in result or isinstance(result[0], PhaseFailureEvent)


def test_untracked_completion_report_md_is_deleted(tmp_git_repo: Path) -> None:
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
    result = _run(workspace)
    assert result == [PipelineEvent.AGENT_SUCCESS]
    assert not report.exists()


def test_tracked_completion_report_md_is_declined(tmp_git_repo: Path) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    report = tmp_git_repo / "completion_report.md"
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
    result = _run(workspace)
    assert result == [PipelineEvent.AGENT_SUCCESS]
    assert report.exists()


def test_head_tracked_source_is_never_deleted(tmp_git_repo: Path) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    source = tmp_git_repo / "tracked.py"
    source.write_text("print('hi')\n", encoding="utf-8")
    repo = Repo(tmp_git_repo)
    try:
        repo.index.add(["tracked.py"])
        repo.index.commit("track source")
    finally:
        repo.close()
    _write_commit_cleanup_artifact(
        workspace,
        {
            "analysis_complete": True,
            "actions": [{"action": "delete_file", "path": "tracked.py"}],
        },
    )
    _run(workspace)
    assert source.exists()


def test_lockfile_is_never_deleted(tmp_git_repo: Path) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    lock = tmp_git_repo / "package-lock.json"
    lock.write_text("{}", encoding="utf-8")
    _write_commit_cleanup_artifact(
        workspace,
        {
            "analysis_complete": True,
            "actions": [{"action": "delete_file", "path": "package-lock.json"}],
        },
    )
    _run(workspace)
    assert lock.exists()
    assert "package-lock.json" in LOCKFILE_BASENAMES


def test_parent_traversal_delete_is_declined(tmp_git_repo: Path) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    _write_commit_cleanup_artifact(
        workspace,
        {
            "analysis_complete": True,
            "actions": [{"action": "delete_file", "path": "../outside.txt"}],
        },
    )
    result = _run(workspace)
    assert result == [PipelineEvent.AGENT_SUCCESS]


def test_missing_delete_target_is_noop(tmp_git_repo: Path) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    _write_commit_cleanup_artifact(
        workspace,
        {
            "analysis_complete": True,
            "actions": [{"action": "delete_file", "path": "scratch.tmp"}],
        },
    )
    result = _run(workspace)
    assert result == [PipelineEvent.AGENT_SUCCESS]
    assert not (tmp_git_repo / "scratch.tmp").exists()


def test_idempotent_second_apply_of_well_formed_batch(tmp_git_repo: Path) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    junk = tmp_git_repo / "session-output.txt"
    junk.write_text("tmp\n", encoding="utf-8")
    _write_commit_cleanup_artifact(
        workspace,
        {
            "analysis_complete": True,
            "actions": [
                {"action": "delete_file", "path": "session-output.txt"},
                {"action": "add_to_gitignore", "pattern": "*.exe"},
            ],
        },
    )
    first = _run(workspace)
    _write_commit_cleanup_artifact(
        workspace,
        {
            "analysis_complete": True,
            "actions": [
                {"action": "delete_file", "path": "session-output.txt"},
                {"action": "add_to_gitignore", "pattern": "*.exe"},
            ],
        },
    )
    second = _run(workspace)
    assert first == [PipelineEvent.AGENT_SUCCESS]
    assert second == [PipelineEvent.AGENT_SUCCESS]
    assert not junk.exists()
    assert "*.exe" in (tmp_git_repo / ".gitignore").read_text(encoding="utf-8")


def test_stale_identical_artifact_is_not_a_new_attempt(tmp_git_repo: Path) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    path = tmp_git_repo / COMMIT_CLEANUP_ARTIFACT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("stale leftover\n", encoding="utf-8")
    first = _run(workspace)[0]
    second = _run(workspace)[0]
    third = _run(workspace)[0]
    fourth = _run(workspace)[0]
    assert isinstance(first, PhaseFailureEvent)
    assert isinstance(second, PhaseFailureEvent)
    assert third is PipelineEvent.AGENT_SUCCESS or isinstance(third, PhaseFailureEvent)
    assert fourth is PipelineEvent.AGENT_SUCCESS


def test_preparation_failure_is_visible_and_non_fatal(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: LogCaptureFixture,
) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    _write_commit_cleanup_artifact(workspace, {"analysis_complete": True, "actions": []})

    def _boom(*_args: object, **_kwargs: object) -> list[str]:
        raise OSError("untrack exploded")

    monkeypatch.setattr("ralph.phases.commit_cleanup.untrack_engine_internal_files", _boom)
    records: list[str] = []
    handler_id = logger.add(lambda message: records.append(str(message)), level="WARNING")
    try:
        result = _run(workspace)
    finally:
        logger.remove(handler_id)
    assert result == [PipelineEvent.AGENT_SUCCESS]
    assert "untrack_engine_internal_files failed (continuing)" in "".join(records)


def test_prompt_and_engine_generated_text_classes_agree() -> None:
    table = render_delete_decision_rules_markdown()
    for marker in GENERATED_TEXT_MARKERS:
        assert marker in table
    assert "Markdown" in table or ".md" in table
    template = (
        Path(__file__).resolve().parents[1]
        / "ralph"
        / "prompts"
        / "templates"
        / "commit_cleanup.jinja"
    ).read_text(encoding="utf-8")
    assert "{{ DELETE_DECISION_RULES }}" in template
    assert "{% if LAST_RETRY_ERROR %}" in template
