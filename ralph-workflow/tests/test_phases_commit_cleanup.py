"""Unit tests for commit_cleanup phase handler."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from git import Repo

from ralph.phases import PhaseContext
from ralph.phases.commit_cleanup import handle_commit_cleanup_phase
from ralph.pipeline.effects import CommitEffect, InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.events import PhaseFailureEvent, PipelineEvent
from ralph.workspace.fs import FsWorkspace

COMMIT_CLEANUP_ARTIFACT_PATH = ".agent/artifacts/commit_cleanup.json"

# Most tests in this module exercise real git operations against the
# ``tmp_git_repo`` fixture (per-test process-isolated git repository).
# Wall-clock cost under parallel xdist load is regularly > 1 s on busy
# machines, so the default 1-second per-test ceiling is unsafe. A few
# tests that do not touch the fixture complete in < 1 s and tolerate
# the elevated ceiling as a no-op.
pytestmark = pytest.mark.timeout_seconds(5)


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


def test_delete_checkpoint_json_removes_file(tmp_git_repo: Path) -> None:
    """Pipeline checkpoint JSON files are safe housekeeping artifacts."""
    workspace = FsWorkspace(tmp_git_repo)
    checkpoint = tmp_git_repo / "checkpoint.json"
    checkpoint.write_text('{"phase": "development"}')
    assert checkpoint.exists()

    _write_commit_cleanup_artifact(
        workspace,
        {
            "analysis_complete": True,
            "actions": [{"action": "delete_file", "path": "checkpoint.json"}],
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
    assert not checkpoint.exists()


def test_delete_verify_output_text_file_removes_file(tmp_git_repo: Path) -> None:
    """Generated verification capture text files are safe housekeeping artifacts."""
    workspace = FsWorkspace(tmp_git_repo)
    verify_output = tmp_git_repo / "verify-output.txt"
    verify_output.write_text("captured verify output")
    assert verify_output.exists()

    _write_commit_cleanup_artifact(
        workspace,
        {
            "analysis_complete": False,
            "actions": [{"action": "delete_file", "path": "verify-output.txt"}],
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
    assert not verify_output.exists()


def test_delete_tracked_verify_output_text_file_returns_failure_event(
    tmp_git_repo: Path,
) -> None:
    """Generated-looking text files stay protected once they are tracked repo content."""
    workspace = FsWorkspace(tmp_git_repo)
    verify_output = tmp_git_repo / "verify-output.txt"
    verify_output.write_text("intentional checked-in artifact")
    repo = Repo(tmp_git_repo)
    try:
        repo.index.add(["verify-output.txt"])
        repo.index.commit("track verify output")
    finally:
        repo.close()

    _write_commit_cleanup_artifact(
        workspace,
        {
            "analysis_complete": False,
            "actions": [{"action": "delete_file", "path": "verify-output.txt"}],
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

    assert len(result) == 1
    assert isinstance(result[0], PhaseFailureEvent)
    assert verify_output.exists()


@pytest.mark.parametrize(
    "ext",
    [
        ".swift",
        ".kt",
        ".kts",
        ".scala",
        ".php",
        ".sh",
        ".bash",
        ".zsh",
        ".fish",
        ".ps1",
        ".pl",
        ".pm",
        ".lua",
        ".r",
        ".m",
        ".mm",
        ".cs",
        ".fs",
        ".fsx",
        ".vb",
        ".dart",
        ".groovy",
        ".clj",
        ".cljs",
        ".hs",
        ".lhs",
        ".elm",
        ".erl",
        ".ex",
        ".exs",
        ".ml",
        ".mli",
        ".nim",
        ".cr",
        ".pas",
        ".pp",
        ".sql",
        ".graphql",
        ".gql",
        ".prisma",
        ".proto",
        ".asm",
        ".s",
        ".inc",
        ".def",
        ".cmake",
        ".mak",
        ".ninja",
        ".dockerfile",
        ".jenkinsfile",
        ".xml",
        ".csv",
        ".tsv",
    ],
)
def test_delete_source_code_extension_rejected(tmp_git_repo: Path, ext: str) -> None:
    """Every new source-code and config extension must NOT be deleted."""
    workspace = FsWorkspace(tmp_git_repo)
    src = tmp_git_repo / f"App{ext}"
    src.write_text("source code")

    _write_commit_cleanup_artifact(
        workspace,
        {"analysis_complete": False, "actions": [{"action": "delete_file", "path": f"App{ext}"}]},
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
        agent_name="dev", phase="development_commit_cleanup", prompt_file="cleanup.txt"
    )
    result = handle_commit_cleanup_phase(effect, ctx)
    assert len(result) == 1
    assert isinstance(result[0], PhaseFailureEvent)
    assert src.exists()


@pytest.mark.parametrize(
    "lock_file",
    [
        "package-lock.json",
        "yarn.lock",
        "Cargo.lock",
        "poetry.lock",
        "uv.lock",
        "Pipfile.lock",
        "composer.lock",
        "Gemfile.lock",
        "go.sum",
    ],
)
def test_delete_lock_file_rejected(tmp_git_repo: Path, lock_file: str) -> None:
    """Lock files and dependency manifests must NOT be deleted."""
    workspace = FsWorkspace(tmp_git_repo)
    lock = tmp_git_repo / lock_file
    lock.write_text("lock content")

    _write_commit_cleanup_artifact(
        workspace,
        {"analysis_complete": False, "actions": [{"action": "delete_file", "path": lock_file}]},
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
        agent_name="dev", phase="development_commit_cleanup", prompt_file="cleanup.txt"
    )
    result = handle_commit_cleanup_phase(effect, ctx)
    assert len(result) == 1
    assert isinstance(result[0], PhaseFailureEvent)
    assert lock.exists()


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


@pytest.mark.parametrize(
    "file_path",
    [
        "ralph/models.py",  # Python source file
        "tests/test_foo.py",  # test file in tests/ directory
        "pyproject.toml",  # TOML configuration file
        "README.md",  # Markdown documentation file
        "config.json",  # JSON configuration file
        "NOTES.txt",  # text documentation file
    ],
)
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
        workspace=workspace,
        registry=object(),
        chain_manager=object(),
        pipeline_policy=object(),
        artifacts_policy=object(),
        agents_policy=object(),
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
        workspace=workspace,
        registry=object(),
        chain_manager=object(),
        pipeline_policy=object(),
        artifacts_policy=object(),
        agents_policy=object(),
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
        workspace=workspace,
        registry=object(),
        chain_manager=object(),
        pipeline_policy=object(),
        artifacts_policy=object(),
        agents_policy=object(),
    )
    effect = InvokeAgentEffect(
        agent_name="dev", phase="development_commit_cleanup", prompt_file="cleanup.txt"
    )

    result = handle_commit_cleanup_phase(effect, ctx)

    assert len(result) == 1
    assert isinstance(result[0], PhaseFailureEvent)
    assert outside.exists()


def test_delete_backup_bak_file(tmp_git_repo: Path) -> None:
    """Backup .bak files are safe housekeeping artifacts."""
    workspace = FsWorkspace(tmp_git_repo)
    backup = tmp_git_repo / "important.py.bak"
    backup.write_text("old version")

    _write_commit_cleanup_artifact(
        workspace,
        {
            "analysis_complete": True,
            "actions": [{"action": "delete_file", "path": "important.py.bak"}],
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
        agent_name="dev", phase="development_commit_cleanup", prompt_file="cleanup.txt"
    )
    result = handle_commit_cleanup_phase(effect, ctx)
    assert result == [PipelineEvent.AGENT_SUCCESS]
    assert not backup.exists()


def test_delete_tmp_file(tmp_git_repo: Path) -> None:
    """Temporary .tmp files are safe housekeeping artifacts."""
    workspace = FsWorkspace(tmp_git_repo)
    tmp = tmp_git_repo / "scratch.tmp"
    tmp.write_text("temporary data")

    _write_commit_cleanup_artifact(
        workspace,
        {"analysis_complete": True, "actions": [{"action": "delete_file", "path": "scratch.tmp"}]},
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
        agent_name="dev", phase="development_commit_cleanup", prompt_file="cleanup.txt"
    )
    result = handle_commit_cleanup_phase(effect, ctx)
    assert result == [PipelineEvent.AGENT_SUCCESS]
    assert not tmp.exists()


def test_delete_log_file(tmp_git_repo: Path) -> None:
    """Log files are safe housekeeping artifacts."""
    workspace = FsWorkspace(tmp_git_repo)
    log = tmp_git_repo / "debug.log"
    log.write_text("debug output")

    _write_commit_cleanup_artifact(
        workspace,
        {"analysis_complete": True, "actions": [{"action": "delete_file", "path": "debug.log"}]},
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
        agent_name="dev", phase="development_commit_cleanup", prompt_file="cleanup.txt"
    )
    result = handle_commit_cleanup_phase(effect, ctx)
    assert result == [PipelineEvent.AGENT_SUCCESS]
    assert not log.exists()


def test_delete_rej_file(tmp_git_repo: Path) -> None:
    """Patch reject .rej files are safe housekeeping artifacts."""
    workspace = FsWorkspace(tmp_git_repo)
    rej = tmp_git_repo / "fix.patch.rej"
    rej.write_text("patch reject hunk")

    _write_commit_cleanup_artifact(
        workspace,
        {
            "analysis_complete": True,
            "actions": [{"action": "delete_file", "path": "fix.patch.rej"}],
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
        agent_name="dev", phase="development_commit_cleanup", prompt_file="cleanup.txt"
    )
    result = handle_commit_cleanup_phase(effect, ctx)
    assert result == [PipelineEvent.AGENT_SUCCESS]
    assert not rej.exists()


def test_delete_session_txt_file(tmp_git_repo: Path) -> None:
    """Session transcript text files are safe housekeeping artifacts."""
    workspace = FsWorkspace(tmp_git_repo)
    session = tmp_git_repo / "session-transcript.txt"
    session.write_text("agent conversation")

    _write_commit_cleanup_artifact(
        workspace,
        {
            "analysis_complete": True,
            "actions": [{"action": "delete_file", "path": "session-transcript.txt"}],
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
        agent_name="dev", phase="development_commit_cleanup", prompt_file="cleanup.txt"
    )
    result = handle_commit_cleanup_phase(effect, ctx)
    assert result == [PipelineEvent.AGENT_SUCCESS]
    assert not session.exists()


def test_delete_in_tmp_directory(tmp_git_repo: Path) -> None:
    """Files inside tmp/ directories are safe housekeeping artifacts."""
    workspace = FsWorkspace(tmp_git_repo)
    tmp_dir = tmp_git_repo / "tmp"
    tmp_dir.mkdir()
    artifact = tmp_dir / "scratch.txt"
    artifact.write_text("temp content")

    _write_commit_cleanup_artifact(
        workspace,
        {
            "analysis_complete": True,
            "actions": [{"action": "delete_file", "path": "tmp/scratch.txt"}],
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
        agent_name="dev", phase="development_commit_cleanup", prompt_file="cleanup.txt"
    )
    result = handle_commit_cleanup_phase(effect, ctx)
    assert result == [PipelineEvent.AGENT_SUCCESS]
    assert not artifact.exists()


@pytest.mark.timeout_seconds(5)
def test_delete_tracked_backup_file_rejected(tmp_git_repo: Path) -> None:
    """Backup files already tracked in git must NOT be deleted."""
    workspace = FsWorkspace(tmp_git_repo)
    backup = tmp_git_repo / "config.yml.bak"
    backup.write_text("backup config")
    repo = Repo(tmp_git_repo)
    try:
        repo.index.add(["config.yml.bak"])
        repo.index.commit("track backup")
    finally:
        repo.close()

    _write_commit_cleanup_artifact(
        workspace,
        {
            "analysis_complete": False,
            "actions": [{"action": "delete_file", "path": "config.yml.bak"}],
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
        agent_name="dev", phase="development_commit_cleanup", prompt_file="cleanup.txt"
    )
    result = handle_commit_cleanup_phase(effect, ctx)
    assert len(result) == 1
    assert isinstance(result[0], PhaseFailureEvent)
    assert backup.exists()


def _make_cleanup_ctx(workspace: FsWorkspace) -> PhaseContext:
    """Build a minimal PhaseContext wired to ``workspace`` for cleanup tests."""
    return PhaseContext.construct(
        workspace=workspace,
        registry=object(),
        chain_manager=object(),
        pipeline_policy=object(),
        artifacts_policy=object(),
        agents_policy=object(),
    )


def _invoke_cleanup(workspace: FsWorkspace, content: dict) -> list:
    """Run the commit_cleanup phase with the given artifact content."""
    _write_commit_cleanup_artifact(workspace, content)
    ctx = _make_cleanup_ctx(workspace)
    effect = InvokeAgentEffect(
        agent_name="dev", phase="development_commit_cleanup", prompt_file="cleanup.txt"
    )
    return handle_commit_cleanup_phase(effect, ctx)


@pytest.mark.parametrize(
    "file_path",
    [
        "temp_script.py",
        "scratch_script.go",
        "generated_utils.js",
        "dump_helper.rs",
        "tmp_helper.ts",
        "throwaway.java",
        "dump.cpp",
        "tmp/scratch.py",
        "temp/App.java",
    ],
)
def test_untracked_temporary_source_code_is_deleted(
    tmp_git_repo: Path,
    file_path: str,
) -> None:
    """Untracked source files with temporary markers are housekeeping artifacts."""
    workspace = FsWorkspace(tmp_git_repo)
    target = tmp_git_repo / file_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("temp source code")
    assert target.exists()

    result = _invoke_cleanup(
        workspace,
        {
            "analysis_complete": True,
            "actions": [{"action": "delete_file", "path": file_path}],
        },
    )

    assert PipelineEvent.AGENT_SUCCESS in result or PipelineEvent.PHASE_LOOPBACK in result
    assert not target.exists()


@pytest.mark.parametrize(
    "file_path",
    [
        "log.py",
        "model.py",
        "worker.py",
        "message.py",
        "session.py",
        "chat.py",
        "plan.py",
        "debug.py",
        "output.py",
        "report.py",
        "capture.py",
        "completion.py",
        "note.go",
        "message.rs",
        "log.ts",
        "model.js",
    ],
)
def test_untracked_legitimate_source_file_rejected(
    tmp_git_repo: Path,
    file_path: str,
) -> None:
    """Untracked source files with common programming-term names must NOT be deleted.

    Pins the false-positive guarantee: a source file whose name tokenizes to
    a value in the broad _GENERATED_TEXT_MARKERS set (e.g. ``log``, ``model``,
    ``worker``) is NOT a candidate for deletion, because the source-file
    branch uses the narrow ``_SOURCE_FILE_GENERATED_MARKERS`` allowlist which
    excludes these common programming terms.
    """
    workspace = FsWorkspace(tmp_git_repo)
    target = tmp_git_repo / file_path
    target.write_text("legitimate source code")
    assert target.exists()

    result = _invoke_cleanup(
        workspace,
        {
            "analysis_complete": False,
            "actions": [{"action": "delete_file", "path": file_path}],
        },
    )

    assert len(result) == 1
    assert isinstance(result[0], PhaseFailureEvent)
    assert target.exists()


def test_tracked_temporary_source_code_rejected(tmp_git_repo: Path) -> None:
    """Tracked source files with temporary names must NOT be deleted."""
    workspace = FsWorkspace(tmp_git_repo)
    src = tmp_git_repo / "temp_script.py"
    src.write_text("committed source")
    repo = Repo(tmp_git_repo)
    try:
        repo.index.add(["temp_script.py"])
        repo.index.commit("track temp script")
    finally:
        repo.close()

    result = _invoke_cleanup(
        workspace,
        {
            "analysis_complete": False,
            "actions": [{"action": "delete_file", "path": "temp_script.py"}],
        },
    )

    assert len(result) == 1
    assert isinstance(result[0], PhaseFailureEvent)
    assert src.exists()


def test_tracked_source_code_in_tmp_directory_rejected(tmp_git_repo: Path) -> None:
    """Tracked source files inside tmp/ directories must NOT be deleted."""
    workspace = FsWorkspace(tmp_git_repo)
    tmp_dir = tmp_git_repo / "tmp"
    tmp_dir.mkdir()
    src = tmp_dir / "utility.py"
    src.write_text("committed utility")
    repo = Repo(tmp_git_repo)
    try:
        repo.index.add(["tmp/utility.py"])
        repo.index.commit("track tmp utility")
    finally:
        repo.close()

    result = _invoke_cleanup(
        workspace,
        {
            "analysis_complete": False,
            "actions": [{"action": "delete_file", "path": "tmp/utility.py"}],
        },
    )

    assert len(result) == 1
    assert isinstance(result[0], PhaseFailureEvent)
    assert src.exists()


def test_delete_coverage_untracked_succeeds(tmp_git_repo: Path) -> None:
    """Untracked ``.coverage`` files are safe housekeeping artifacts."""
    workspace = FsWorkspace(tmp_git_repo)
    coverage = tmp_git_repo / ".coverage"
    coverage.write_text("coverage data")
    assert coverage.exists()

    result = _invoke_cleanup(
        workspace,
        {
            "analysis_complete": True,
            "actions": [{"action": "delete_file", "path": ".coverage"}],
        },
    )

    assert result == [PipelineEvent.AGENT_SUCCESS]
    assert not coverage.exists()


def test_delete_coverage_xml_untracked_succeeds(tmp_git_repo: Path) -> None:
    """Untracked ``coverage.xml`` files are safe housekeeping artifacts."""
    workspace = FsWorkspace(tmp_git_repo)
    coverage_xml = tmp_git_repo / "coverage.xml"
    coverage_xml.write_text("<coverage></coverage>")
    assert coverage_xml.exists()

    result = _invoke_cleanup(
        workspace,
        {
            "analysis_complete": True,
            "actions": [{"action": "delete_file", "path": "coverage.xml"}],
        },
    )

    assert result == [PipelineEvent.AGENT_SUCCESS]
    assert not coverage_xml.exists()


def test_reject_delete_untracked_dockerfile(tmp_git_repo: Path) -> None:
    """Untracked ``Dockerfile`` is protected by ``_PROTECTED_BASENAMES``."""
    workspace = FsWorkspace(tmp_git_repo)
    dockerfile = tmp_git_repo / "Dockerfile"
    dockerfile.write_text("FROM scratch")
    assert dockerfile.exists()

    result = _invoke_cleanup(
        workspace,
        {
            "analysis_complete": False,
            "actions": [{"action": "delete_file", "path": "Dockerfile"}],
        },
    )

    assert len(result) == 1
    assert isinstance(result[0], PhaseFailureEvent)
    assert dockerfile.exists()


def test_reject_delete_untracked_makefile(tmp_git_repo: Path) -> None:
    """Untracked ``Makefile`` is protected by ``_PROTECTED_BASENAMES``."""
    workspace = FsWorkspace(tmp_git_repo)
    makefile = tmp_git_repo / "Makefile"
    makefile.write_text("all:\n\ttrue\n")
    assert makefile.exists()

    result = _invoke_cleanup(
        workspace,
        {
            "analysis_complete": False,
            "actions": [{"action": "delete_file", "path": "Makefile"}],
        },
    )

    assert len(result) == 1
    assert isinstance(result[0], PhaseFailureEvent)
    assert makefile.exists()


def test_reject_delete_untracked_license_txt(tmp_git_repo: Path) -> None:
    """``LICENSE.txt`` is protected even though ``.txt`` is a generated-text suffix."""
    workspace = FsWorkspace(tmp_git_repo)
    license_txt = tmp_git_repo / "LICENSE.txt"
    license_txt.write_text("license text")
    assert license_txt.exists()

    result = _invoke_cleanup(
        workspace,
        {
            "analysis_complete": False,
            "actions": [{"action": "delete_file", "path": "LICENSE.txt"}],
        },
    )

    assert len(result) == 1
    assert isinstance(result[0], PhaseFailureEvent)
    assert license_txt.exists()


def test_delete_tracked_coverage_rejected(tmp_git_repo: Path) -> None:
    """Tracked ``.coverage`` files must NOT be deleted."""
    workspace = FsWorkspace(tmp_git_repo)
    coverage = tmp_git_repo / ".coverage"
    coverage.write_text("committed coverage")
    repo = Repo(tmp_git_repo)
    try:
        repo.index.add([".coverage"])
        repo.index.commit("track coverage")
    finally:
        repo.close()

    result = _invoke_cleanup(
        workspace,
        {
            "analysis_complete": False,
            "actions": [{"action": "delete_file", "path": ".coverage"}],
        },
    )

    assert len(result) == 1
    assert isinstance(result[0], PhaseFailureEvent)
    assert coverage.exists()


@pytest.mark.timeout_seconds(5)
def test_delete_tracked_coverage_xml_rejected(tmp_git_repo: Path) -> None:
    """Tracked ``coverage.xml`` files must NOT be deleted."""
    workspace = FsWorkspace(tmp_git_repo)
    coverage_xml = tmp_git_repo / "coverage.xml"
    coverage_xml.write_text("<coverage></coverage>")
    repo = Repo(tmp_git_repo)
    try:
        repo.index.add(["coverage.xml"])
        repo.index.commit("track coverage xml")
    finally:
        repo.close()

    result = _invoke_cleanup(
        workspace,
        {
            "analysis_complete": False,
            "actions": [{"action": "delete_file", "path": "coverage.xml"}],
        },
    )

    assert len(result) == 1
    assert isinstance(result[0], PhaseFailureEvent)
    assert coverage_xml.exists()


def test_delete_untracked_checkpoint_json(tmp_git_repo: Path) -> None:
    """Untracked ``checkpoint.json`` files are safe housekeeping artifacts."""
    workspace = FsWorkspace(tmp_git_repo)
    checkpoint = tmp_git_repo / "checkpoint.json"
    checkpoint.write_text('{"phase": "development"}')
    assert checkpoint.exists()

    result = _invoke_cleanup(
        workspace,
        {
            "analysis_complete": True,
            "actions": [{"action": "delete_file", "path": "checkpoint.json"}],
        },
    )

    assert result == [PipelineEvent.AGENT_SUCCESS]
    assert not checkpoint.exists()


def test_delete_untracked_log_file(tmp_git_repo: Path) -> None:
    """Untracked log files are safe housekeeping artifacts."""
    workspace = FsWorkspace(tmp_git_repo)
    log = tmp_git_repo / "debug.log"
    log.write_text("debug output")
    assert log.exists()

    result = _invoke_cleanup(
        workspace,
        {
            "analysis_complete": True,
            "actions": [{"action": "delete_file", "path": "debug.log"}],
        },
    )

    assert result == [PipelineEvent.AGENT_SUCCESS]
    assert not log.exists()


def test_delete_untracked_binary_file(tmp_git_repo: Path) -> None:
    """Untracked binary files are safe housekeeping artifacts."""
    workspace = FsWorkspace(tmp_git_repo)
    binary = tmp_git_repo / "accidental_binary.exe"
    binary.write_bytes(b"\x00MZ")
    assert binary.exists()

    result = _invoke_cleanup(
        workspace,
        {
            "analysis_complete": True,
            "actions": [{"action": "delete_file", "path": "accidental_binary.exe"}],
        },
    )

    assert result == [PipelineEvent.AGENT_SUCCESS]
    assert not binary.exists()


def test_reject_delete_untracked_source_code(tmp_git_repo: Path) -> None:
    """Untracked source code files are NEVER safe to delete."""
    workspace = FsWorkspace(tmp_git_repo)
    helper = tmp_git_repo / "helper.py"
    helper.write_text("def helper(): pass")
    assert helper.exists()

    result = _invoke_cleanup(
        workspace,
        {
            "analysis_complete": False,
            "actions": [{"action": "delete_file", "path": "helper.py"}],
        },
    )

    assert len(result) == 1
    assert isinstance(result[0], PhaseFailureEvent)
    assert helper.exists()


def test_delete_untracked_in_artifacts_directory(tmp_git_repo: Path) -> None:
    """Untracked files inside ``artifacts/`` are safe housekeeping artifacts."""
    workspace = FsWorkspace(tmp_git_repo)
    artifacts_dir = tmp_git_repo / "artifacts"
    artifacts_dir.mkdir()
    scratch = artifacts_dir / "scratch.txt"
    scratch.write_text("scratch")
    assert scratch.exists()

    result = _invoke_cleanup(
        workspace,
        {
            "analysis_complete": True,
            "actions": [{"action": "delete_file", "path": "artifacts/scratch.txt"}],
        },
    )

    assert result == [PipelineEvent.AGENT_SUCCESS]
    assert not scratch.exists()


def test_delete_untracked_file_in_generated_directory(tmp_git_repo: Path) -> None:
    """Untracked files inside ``.output/`` are safe housekeeping artifacts."""
    workspace = FsWorkspace(tmp_git_repo)
    output_dir = tmp_git_repo / ".output"
    output_dir.mkdir()
    bundle = output_dir / "bundle.js"
    bundle.write_text("// generated")
    assert bundle.exists()

    result = _invoke_cleanup(
        workspace,
        {
            "analysis_complete": True,
            "actions": [{"action": "delete_file", "path": ".output/bundle.js"}],
        },
    )

    assert result == [PipelineEvent.AGENT_SUCCESS]
    assert not bundle.exists()


# ---------------------------------------------------------------------------
# Agent-runtime artifact fast-path regression tests (PA-001 / PA-002 / PA-004)
# ---------------------------------------------------------------------------
#
# Every canonical Ralph runtime artifact (per the ``_agent_internal_paths``
# allowlist) MUST be deletable even when tracked in HEAD. This is the
# fast-path exemption in ``_is_safe_to_delete`` that bypasses the universal
# HEAD veto for engine-owned paths only. The negative cases pin the security
# boundary: source-code files under ``.agent/`` that are NOT in the allowlist
# MUST remain rejected.
def _track_and_commit(repo_root: Path, rel_path: str) -> None:
    """Stage a relative path in ``repo_root`` and commit it (helper for the 13 positive tests)."""
    repo = Repo(repo_root)
    try:
        repo.index.add([rel_path])
        repo.index.commit(f"track {rel_path}")
    finally:
        repo.close()


def test_delete_tracked_agent_raw_log_succeeds(tmp_git_repo: Path) -> None:
    """Tracked ``.agent/raw/opencode.log`` is an engine runtime artifact -- must delete."""
    target = tmp_git_repo / ".agent" / "raw" / "opencode.log"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("log content")
    _track_and_commit(tmp_git_repo, ".agent/raw/opencode.log")

    result = _invoke_cleanup(
        FsWorkspace(tmp_git_repo),
        {
            "analysis_complete": True,
            "actions": [{"action": "delete_file", "path": ".agent/raw/opencode.log"}],
        },
    )

    assert result == [PipelineEvent.AGENT_SUCCESS]
    assert not target.exists()


def test_delete_tracked_agent_tmp_mcp_server_log_succeeds(tmp_git_repo: Path) -> None:
    """Tracked ``.agent/tmp/mcp-server.log`` is an engine runtime artifact -- must delete."""
    target = tmp_git_repo / ".agent" / "tmp" / "mcp-server.log"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("mcp log")
    _track_and_commit(tmp_git_repo, ".agent/tmp/mcp-server.log")

    result = _invoke_cleanup(
        FsWorkspace(tmp_git_repo),
        {
            "analysis_complete": True,
            "actions": [{"action": "delete_file", "path": ".agent/tmp/mcp-server.log"}],
        },
    )

    assert result == [PipelineEvent.AGENT_SUCCESS]
    assert not target.exists()


def test_delete_tracked_agent_checkpoint_json_succeeds(tmp_git_repo: Path) -> None:
    """Tracked ``.agent/checkpoint.json`` is an engine runtime artifact -- must delete."""
    target = tmp_git_repo / ".agent" / "checkpoint.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{"phase": "development"}')
    _track_and_commit(tmp_git_repo, ".agent/checkpoint.json")

    result = _invoke_cleanup(
        FsWorkspace(tmp_git_repo),
        {
            "analysis_complete": True,
            "actions": [{"action": "delete_file", "path": ".agent/checkpoint.json"}],
        },
    )

    assert result == [PipelineEvent.AGENT_SUCCESS]
    assert not target.exists()


def test_delete_tracked_root_checkpoint_json_succeeds(tmp_git_repo: Path) -> None:
    """Tracked root-level ``checkpoint.json`` is an engine runtime artifact -- must delete."""
    target = tmp_git_repo / "checkpoint.json"
    target.write_text('{"phase": "development"}')
    _track_and_commit(tmp_git_repo, "checkpoint.json")

    result = _invoke_cleanup(
        FsWorkspace(tmp_git_repo),
        {
            "analysis_complete": True,
            "actions": [{"action": "delete_file", "path": "checkpoint.json"}],
        },
    )

    assert result == [PipelineEvent.AGENT_SUCCESS]
    assert not target.exists()


def test_delete_tracked_agent_completion_seen_succeeds(tmp_git_repo: Path) -> None:
    """Tracked ``.agent/completion_seen_*.json`` is an engine sentinel -- must delete (PA-004)."""
    target = tmp_git_repo / ".agent" / "completion_seen_run-abc-123.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{"run_id": "run-abc-123"}')
    _track_and_commit(tmp_git_repo, ".agent/completion_seen_run-abc-123.json")

    result = _invoke_cleanup(
        FsWorkspace(tmp_git_repo),
        {
            "analysis_complete": True,
            "actions": [
                {"action": "delete_file", "path": ".agent/completion_seen_run-abc-123.json"}
            ],
        },
    )

    assert result == [PipelineEvent.AGENT_SUCCESS]
    assert not target.exists()


def test_delete_tracked_agent_rebase_checkpoint_succeeds(tmp_git_repo: Path) -> None:
    """Tracked ``.agent/rebase_checkpoint.json`` is an engine artifact -- must delete."""
    target = tmp_git_repo / ".agent" / "rebase_checkpoint.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("rebase state")
    _track_and_commit(tmp_git_repo, ".agent/rebase_checkpoint.json")

    result = _invoke_cleanup(
        FsWorkspace(tmp_git_repo),
        {
            "analysis_complete": True,
            "actions": [{"action": "delete_file", "path": ".agent/rebase_checkpoint.json"}],
        },
    )

    assert result == [PipelineEvent.AGENT_SUCCESS]
    assert not target.exists()


def test_delete_tracked_agent_rebase_checkpoint_bak_succeeds(tmp_git_repo: Path) -> None:
    """Tracked ``.agent/rebase_checkpoint.json.bak`` is an engine artifact -- must delete."""
    target = tmp_git_repo / ".agent" / "rebase_checkpoint.json.bak"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("rebase backup")
    _track_and_commit(tmp_git_repo, ".agent/rebase_checkpoint.json.bak")

    result = _invoke_cleanup(
        FsWorkspace(tmp_git_repo),
        {
            "analysis_complete": True,
            "actions": [{"action": "delete_file", "path": ".agent/rebase_checkpoint.json.bak"}],
        },
    )

    assert result == [PipelineEvent.AGENT_SUCCESS]
    assert not target.exists()


def test_delete_tracked_agent_rebase_lock_succeeds(tmp_git_repo: Path) -> None:
    """Tracked ``.agent/rebase.lock`` is an engine artifact -- must delete."""
    target = tmp_git_repo / ".agent" / "rebase.lock"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("lock content")
    _track_and_commit(tmp_git_repo, ".agent/rebase.lock")

    result = _invoke_cleanup(
        FsWorkspace(tmp_git_repo),
        {
            "analysis_complete": True,
            "actions": [{"action": "delete_file", "path": ".agent/rebase.lock"}],
        },
    )

    assert result == [PipelineEvent.AGENT_SUCCESS]
    assert not target.exists()


def test_delete_tracked_agent_start_commit_succeeds(tmp_git_repo: Path) -> None:
    """Tracked ``.agent/start_commit`` is an engine artifact -- must delete."""
    target = tmp_git_repo / ".agent" / "start_commit"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("baseline sha")
    _track_and_commit(tmp_git_repo, ".agent/start_commit")

    result = _invoke_cleanup(
        FsWorkspace(tmp_git_repo),
        {
            "analysis_complete": True,
            "actions": [{"action": "delete_file", "path": ".agent/start_commit"}],
        },
    )

    assert result == [PipelineEvent.AGENT_SUCCESS]
    assert not target.exists()


def test_delete_tracked_agent_plan_md_succeeds(tmp_git_repo: Path) -> None:
    """Tracked ``.agent/PLAN.md`` is an engine handoff artifact -- must delete."""
    target = tmp_git_repo / ".agent" / "PLAN.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Plan")
    _track_and_commit(tmp_git_repo, ".agent/PLAN.md")

    result = _invoke_cleanup(
        FsWorkspace(tmp_git_repo),
        {
            "analysis_complete": True,
            "actions": [{"action": "delete_file", "path": ".agent/PLAN.md"}],
        },
    )

    assert result == [PipelineEvent.AGENT_SUCCESS]
    assert not target.exists()


def test_delete_tracked_agent_planning_analysis_decision_md_succeeds(tmp_git_repo: Path) -> None:
    """Tracked ``.agent/PLANNING_ANALYSIS_DECISION.md`` (PA-001 gap) is an engine artifact."""
    target = tmp_git_repo / ".agent" / "PLANNING_ANALYSIS_DECISION.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("decision")
    _track_and_commit(tmp_git_repo, ".agent/PLANNING_ANALYSIS_DECISION.md")

    result = _invoke_cleanup(
        FsWorkspace(tmp_git_repo),
        {
            "analysis_complete": True,
            "actions": [{"action": "delete_file", "path": ".agent/PLANNING_ANALYSIS_DECISION.md"}],
        },
    )

    assert result == [PipelineEvent.AGENT_SUCCESS]
    assert not target.exists()


def test_delete_tracked_agent_receipt_succeeds(tmp_git_repo: Path) -> None:
    """Tracked receipt file under ``.agent/receipts/<run-id>/`` -- must delete."""
    target = tmp_git_repo / ".agent" / "receipts" / "run-1" / "commit_cleanup.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{"artifact_type": "commit_cleanup"}')
    _track_and_commit(tmp_git_repo, ".agent/receipts/run-1/commit_cleanup.json")

    result = _invoke_cleanup(
        FsWorkspace(tmp_git_repo),
        {
            "analysis_complete": True,
            "actions": [
                {"action": "delete_file", "path": ".agent/receipts/run-1/commit_cleanup.json"},
            ],
        },
    )

    assert result == [PipelineEvent.AGENT_SUCCESS]
    assert not target.exists()


def test_delete_tracked_agent_worker_checkpoint_succeeds(tmp_git_repo: Path) -> None:
    """Tracked nested checkpoint inside ``.agent/workers/<unit>/tmp/`` -- must delete."""
    target = tmp_git_repo / ".agent" / "workers" / "unit-a" / "tmp" / "checkpoint.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{"phase": "unit"}')
    _track_and_commit(tmp_git_repo, ".agent/workers/unit-a/tmp/checkpoint.json")

    result = _invoke_cleanup(
        FsWorkspace(tmp_git_repo),
        {
            "analysis_complete": True,
            "actions": [
                {"action": "delete_file", "path": ".agent/workers/unit-a/tmp/checkpoint.json"},
            ],
        },
    )

    assert result == [PipelineEvent.AGENT_SUCCESS]
    assert not target.exists()


# --- NEGATIVE security-regression tests (security boundary) ---


@pytest.mark.parametrize(
    "rel_path",
    [
        ".agent/test.py",
        ".agent/utils.py",
        ".agent/CHANGELOG.md",
        ".agent/note.txt",
        ".agent/scripts/build.sh",
        ".agent/lib/foo.py",
        ".agent/hooks/pre-commit.py",
    ],
)
def test_delete_tracked_source_code_in_agent_dir_rejected(
    tmp_git_repo: Path, rel_path: str
) -> None:
    """Tracked user-authored source files under ``.agent/`` MUST stay rejected.

    Pins the security boundary: a blanket ``.agent/`` path-prefix match in
    the fast-path predicate would silently allow deletion of these files.
    """
    target = tmp_git_repo / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("source content")
    _track_and_commit(tmp_git_repo, rel_path)

    result = _invoke_cleanup(
        FsWorkspace(tmp_git_repo),
        {"analysis_complete": False, "actions": [{"action": "delete_file", "path": rel_path}]},
    )

    assert len(result) == 1
    assert isinstance(result[0], PhaseFailureEvent)
    assert target.exists()


@pytest.mark.parametrize(
    "rel_path",
    [
        ".agent/notes/foo.txt",
        ".agent/data/seed.json",
    ],
)
def test_delete_tracked_arbitrary_subdir_in_agent_dir_rejected(
    tmp_git_repo: Path, rel_path: str
) -> None:
    """Tracked files under non-allowlisted subdirs of ``.agent/`` MUST stay rejected."""
    target = tmp_git_repo / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("user content")
    _track_and_commit(tmp_git_repo, rel_path)

    result = _invoke_cleanup(
        FsWorkspace(tmp_git_repo),
        {"analysis_complete": False, "actions": [{"action": "delete_file", "path": rel_path}]},
    )

    assert len(result) == 1
    assert isinstance(result[0], PhaseFailureEvent)
    assert target.exists()


@pytest.mark.parametrize(
    "rel_path",
    [
        "app/controllers/foo.rb",
        "src/main.go",
        "lib/utils.rb",
        "scripts/build.sh",
    ],
)
def test_delete_tracked_source_code_outside_agent_dir_rejected(
    tmp_git_repo: Path, rel_path: str
) -> None:
    """Tracked user-authored source files outside ``.agent/`` MUST stay rejected."""
    target = tmp_git_repo / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("source content")
    _track_and_commit(tmp_git_repo, rel_path)

    result = _invoke_cleanup(
        FsWorkspace(tmp_git_repo),
        {"analysis_complete": False, "actions": [{"action": "delete_file", "path": rel_path}]},
    )

    assert len(result) == 1
    assert isinstance(result[0], PhaseFailureEvent)
    assert target.exists()


def test_delete_tracked_checkpoint_json_bak_outside_agent_rejected(tmp_git_repo: Path) -> None:
    """Tracked ``checkpoint.json.bak`` at the repo root MUST stay rejected.

    Only ``checkpoint.json`` is a canonical root-level engine artifact; the
    ``.bak`` suffix is a separate extension-based housekeeping rule and a
    tracked ``.bak`` file is project content.
    """
    target = tmp_git_repo / "checkpoint.json.bak"
    target.write_text("backup")
    _track_and_commit(tmp_git_repo, "checkpoint.json.bak")

    result = _invoke_cleanup(
        FsWorkspace(tmp_git_repo),
        {
            "analysis_complete": False,
            "actions": [{"action": "delete_file", "path": "checkpoint.json.bak"}],
        },
    )

    assert len(result) == 1
    assert isinstance(result[0], PhaseFailureEvent)
    assert target.exists()


def test_delete_tracked_random_json_in_agent_root_rejected(tmp_git_repo: Path) -> None:
    """Tracked ``.agent/random_config.json`` MUST stay rejected -- not in the allowlist."""
    target = tmp_git_repo / ".agent" / "random_config.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("user config")
    _track_and_commit(tmp_git_repo, ".agent/random_config.json")

    result = _invoke_cleanup(
        FsWorkspace(tmp_git_repo),
        {
            "analysis_complete": False,
            "actions": [{"action": "delete_file", "path": ".agent/random_config.json"}],
        },
    )

    assert len(result) == 1
    assert isinstance(result[0], PhaseFailureEvent)
    assert target.exists()
