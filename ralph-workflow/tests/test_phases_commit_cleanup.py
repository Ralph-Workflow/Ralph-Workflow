"""Unit tests for commit_cleanup phase handler."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ralph.phases import PhaseContext
from ralph.phases.commit_cleanup import handle_commit_cleanup_phase
from ralph.pipeline.effects import CommitEffect, InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.events import PhaseFailureEvent, PipelineEvent
from ralph.workspace.fs import FsWorkspace

COMMIT_CLEANUP_ARTIFACT_PATH = ".agent/artifacts/commit_cleanup.json"


def _write_commit_cleanup_artifact(
    workspace: FsWorkspace,
    content: dict,
) -> None:
    """Write a commit_cleanup artifact to the workspace."""
    artifact = {
        "name": "commit_cleanup",
        "type": "commit_cleanup",
        "content": content,
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:00:00Z",
    }
    path = Path(workspace.root) / COMMIT_CLEANUP_ARTIFACT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact), encoding="utf-8")


def test_prepare_prompt_returns_prompt_prepared() -> None:
    """Test that PreparePromptEffect returns PROMPT_PREPARED."""
    workspace = MagicMock()
    workspace.exists.return_value = False
    ctx = PhaseContext.construct(
        workspace=workspace,
        registry=object(),
        chain_manager=object(),
        pipeline_policy=object(),
        artifacts_policy=object(),
        agents_policy=object(),
    )
    effect = PreparePromptEffect(phase="development_commit_cleanup", iteration=1)
    result = handle_commit_cleanup_phase(effect, ctx)
    assert result == [PipelineEvent.PROMPT_PREPARED]


def test_non_agent_effect_returns_empty() -> None:
    """Test that non-InvokeAgent effects return empty list."""
    workspace = MagicMock()
    workspace.exists.return_value = False
    ctx = PhaseContext.construct(
        workspace=workspace,
        registry=object(),
        chain_manager=object(),
        pipeline_policy=object(),
        artifacts_policy=object(),
        agents_policy=object(),
    )
    effect = CommitEffect(message_file="/tmp/msg.txt")
    result = handle_commit_cleanup_phase(effect, ctx)
    assert result == []


def test_agent_success_when_analysis_complete(tmp_git_repo: Path) -> None:
    """Test that AGENT_SUCCESS is returned when analysis_complete=True."""
    workspace = FsWorkspace(tmp_git_repo)
    _write_commit_cleanup_artifact(workspace, {"analysis_complete": True, "actions": []})
    ctx = PhaseContext.construct(
        workspace=workspace,
        registry=object(),
        chain_manager=object(),
        pipeline_policy=object(),
        artifacts_policy=object(),
        agents_policy=object(),
    )
    effect = InvokeAgentEffect(
        agent_name="dev",
        phase="development_commit_cleanup",
        prompt_file="cleanup.txt",
    )
    result = handle_commit_cleanup_phase(effect, ctx)
    assert result == [PipelineEvent.AGENT_SUCCESS]


def test_phase_loopback_when_has_actions(tmp_git_repo: Path) -> None:
    """Test that PHASE_LOOPBACK is returned when actions remain."""
    workspace = FsWorkspace(tmp_git_repo)
    _write_commit_cleanup_artifact(
        workspace,
        {
            "analysis_complete": False,
            "actions": [{"action": "add_to_gitignore", "pattern": "*.exe"}],
        },
    )
    ctx = PhaseContext.construct(
        workspace=workspace,
        registry=object(),
        chain_manager=object(),
        pipeline_policy=object(),
        artifacts_policy=object(),
        agents_policy=object(),
    )
    effect = InvokeAgentEffect(
        agent_name="dev",
        phase="development_commit_cleanup",
        prompt_file="cleanup.txt",
    )
    result = handle_commit_cleanup_phase(effect, ctx)
    assert PipelineEvent.PHASE_LOOPBACK in result
    # Verify .gitignore was updated
    gitignore = tmp_git_repo / ".gitignore"
    assert gitignore.exists()
    assert "*.exe" in gitignore.read_text()


def test_delete_file_action_removes_file(tmp_git_repo: Path) -> None:
    """Test that delete_file action removes the specified file."""
    workspace = FsWorkspace(tmp_git_repo)
    binary = tmp_git_repo / "binary.exe"
    binary.write_text("binary content")
    assert binary.exists()

    _write_commit_cleanup_artifact(
        workspace,
        {"analysis_complete": False, "actions": [{"action": "delete_file", "path": "binary.exe"}]},
    )
    ctx = PhaseContext.construct(
        workspace=workspace,
        registry=object(),
        chain_manager=object(),
        pipeline_policy=object(),
        artifacts_policy=object(),
        agents_policy=object(),
    )
    effect = InvokeAgentEffect(
        agent_name="dev",
        phase="development_commit_cleanup",
        prompt_file="cleanup.txt",
    )
    result = handle_commit_cleanup_phase(effect, ctx)
    assert PipelineEvent.PHASE_LOOPBACK in result
    assert not binary.exists()


def test_git_exclude_action_adds_pattern(tmp_git_repo: Path) -> None:
    """Test that add_to_git_exclude action adds the pattern to .git/info/exclude."""
    workspace = FsWorkspace(tmp_git_repo)
    _write_commit_cleanup_artifact(
        workspace,
        {
            "analysis_complete": False,
            "actions": [{"action": "add_to_git_exclude", "pattern": ".env.local"}],
        },
    )
    ctx = PhaseContext.construct(
        workspace=workspace,
        registry=object(),
        chain_manager=object(),
        pipeline_policy=object(),
        artifacts_policy=object(),
        agents_policy=object(),
    )
    effect = InvokeAgentEffect(
        agent_name="dev",
        phase="development_commit_cleanup",
        prompt_file="cleanup.txt",
    )
    result = handle_commit_cleanup_phase(effect, ctx)
    assert PipelineEvent.PHASE_LOOPBACK in result
    exclude_path = tmp_git_repo / ".git" / "info" / "exclude"
    assert exclude_path.exists()
    assert ".env.local" in exclude_path.read_text()


def test_missing_artifact_returns_failure_event(tmp_git_repo: Path) -> None:
    """Test that missing artifact returns PhaseFailureEvent with recoverable=True."""
    workspace = FsWorkspace(tmp_git_repo)
    # Don't write any artifact
    ctx = PhaseContext.construct(
        workspace=workspace,
        registry=object(),
        chain_manager=object(),
        pipeline_policy=object(),
        artifacts_policy=object(),
        agents_policy=object(),
    )
    effect = InvokeAgentEffect(
        agent_name="dev",
        phase="development_commit_cleanup",
        prompt_file="cleanup.txt",
    )
    result = handle_commit_cleanup_phase(effect, ctx)
    assert len(result) == 1
    event = result[0]
    assert isinstance(event, PhaseFailureEvent)
    assert event.phase == "development_commit_cleanup"
    assert event.recoverable is True


def test_cleanup_actions_applied_when_analysis_complete_true(tmp_git_repo: Path) -> None:
    """Cleanup actions are applied even when analysis_complete=True."""
    workspace = FsWorkspace(tmp_git_repo)
    _write_commit_cleanup_artifact(
        workspace,
        {
            "analysis_complete": True,
            "actions": [{"action": "add_to_gitignore", "pattern": "*.exe"}],
        },
    )
    ctx = PhaseContext.construct(
        workspace=workspace,
        registry=object(),
        chain_manager=object(),
        pipeline_policy=object(),
        artifacts_policy=object(),
        agents_policy=object(),
    )
    effect = InvokeAgentEffect(
        agent_name="dev",
        phase="development_commit_cleanup",
        prompt_file="cleanup.txt",
    )
    result = handle_commit_cleanup_phase(effect, ctx)
    assert result == [PipelineEvent.AGENT_SUCCESS]
    gitignore = tmp_git_repo / ".gitignore"
    assert gitignore.exists()
    assert "*.exe" in gitignore.read_text()


def test_non_repo_directory_inits_git(tmp_path: Path) -> None:
    """Test that git is initialized if workspace is not a repo."""
    non_repo = tmp_path / "non_repo"
    non_repo.mkdir()
    workspace = FsWorkspace(non_repo)
    _write_commit_cleanup_artifact(workspace, {"analysis_complete": True, "actions": []})
    ctx = PhaseContext.construct(
        workspace=workspace,
        registry=object(),
        chain_manager=object(),
        pipeline_policy=object(),
        artifacts_policy=object(),
        agents_policy=object(),
    )
    effect = InvokeAgentEffect(
        agent_name="dev",
        phase="development_commit_cleanup",
        prompt_file="cleanup.txt",
    )
    result = handle_commit_cleanup_phase(effect, ctx)
    assert result == [PipelineEvent.AGENT_SUCCESS]
    # Verify git was initialized
    assert (non_repo / ".git").exists()


@pytest.mark.parametrize("file_path", [
    "ralph/models.py",     # Python source file
    "tests/test_foo.py",   # test file in tests/ directory
    "pyproject.toml",      # TOML configuration file
    "README.md",           # Markdown documentation file
    "config.json",         # JSON configuration file
    "NOTES.txt",           # text documentation file
])
def test_delete_unsafe_file_returns_failure_event(
    tmp_git_repo: Path,
    file_path: str,
) -> None:
    """Deleting any source, test, doc, or config file must return PhaseFailureEvent."""
    workspace = FsWorkspace(tmp_git_repo)
    target = tmp_git_repo / file_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("content")
    _write_commit_cleanup_artifact(
        workspace,
        {"analysis_complete": False, "actions": [{"action": "delete_file", "path": file_path}]},
    )
    ctx = PhaseContext.construct(
        workspace=workspace, registry=object(), chain_manager=object(),
        pipeline_policy=object(), artifacts_policy=object(), agents_policy=object(),
    )
    effect = InvokeAgentEffect(
        agent_name="dev", phase="development_commit_cleanup", prompt_file="cleanup.txt"
    )
    result = handle_commit_cleanup_phase(effect, ctx)
    assert len(result) == 1
    assert isinstance(result[0], PhaseFailureEvent)
    assert target.exists()  # file must NOT be deleted


def test_delete_file_with_parent_traversal_returns_failure_event(
    tmp_git_repo: Path,
    tmp_path: Path,
) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    outside = tmp_path / "outside.bin"
    outside.write_text("binary")
    _write_commit_cleanup_artifact(
        workspace,
        {
            "analysis_complete": False,
            "actions": [{"action": "delete_file", "path": "../outside.bin"}],
        },
    )
    ctx = PhaseContext.construct(
        workspace=workspace, registry=object(), chain_manager=object(),
        pipeline_policy=object(), artifacts_policy=object(), agents_policy=object(),
    )
    effect = InvokeAgentEffect(
        agent_name="dev", phase="development_commit_cleanup", prompt_file="cleanup.txt"
    )

    result = handle_commit_cleanup_phase(effect, ctx)

    assert len(result) == 1
    assert isinstance(result[0], PhaseFailureEvent)
    assert outside.exists()


def test_delete_file_with_absolute_path_returns_failure_event(
    tmp_git_repo: Path,
    tmp_path: Path,
) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    outside = tmp_path / "absolute.bin"
    outside.write_text("binary")
    _write_commit_cleanup_artifact(
        workspace,
        {
            "analysis_complete": False,
            "actions": [{"action": "delete_file", "path": str(outside)}],
        },
    )
    ctx = PhaseContext.construct(
        workspace=workspace, registry=object(), chain_manager=object(),
        pipeline_policy=object(), artifacts_policy=object(), agents_policy=object(),
    )
    effect = InvokeAgentEffect(
        agent_name="dev", phase="development_commit_cleanup", prompt_file="cleanup.txt"
    )

    result = handle_commit_cleanup_phase(effect, ctx)

    assert len(result) == 1
    assert isinstance(result[0], PhaseFailureEvent)
    assert outside.exists()
