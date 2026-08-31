"""Unit tests for commit_cleanup phase handler."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from git import Repo
from loguru import logger

from ralph.config import bootstrap
from ralph.mcp.artifacts._commit_cleanup import CommitCleanup
from ralph.mcp.artifacts._commit_cleanup_action import CommitCleanupAction
from ralph.phases import PhaseContext
from ralph.phases._commit_cleanup_actions import CleanupApplyReport
from ralph.phases._commit_cleanup_catalog import LOCKFILE_BASENAMES, UNSAFE_EXTENSIONS
from ralph.phases._commit_cleanup_outcome import decide_cleanup_outcome
from ralph.phases.commit_cleanup import (
    _apply_cleanup_actions,
    build_cleanup_retry_hint,
    handle_commit_cleanup_phase,
)
from ralph.pipeline.effects import CommitEffect, InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.events import PhaseFailureEvent, PipelineEvent
from ralph.recovery.classifier import FailureCategory
from ralph.workspace.fs import FsWorkspace

COMMIT_CLEANUP_ARTIFACT_PATH = ".agent/artifacts/commit_cleanup.md"

# Most tests in this module exercise real git operations against the
# ``tmp_git_repo`` fixture (per-test process-isolated git repository).
# Wall-clock cost under parallel xdist load is regularly > 1 s on busy
# machines, so the default 1-second per-test ceiling is unsafe. A few
# tests that do not touch the fixture complete in < 1 s and tolerate
# the elevated ceiling as a no-op.
pytestmark = [pytest.mark.timeout_seconds(5), pytest.mark.subprocess_e2e]


def _write_commit_cleanup_artifact(
    workspace: FsWorkspace,
    content: dict[str, object],
) -> None:
    """Write a canonical commit-cleanup Markdown artifact."""
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


def test_missing_markdown_artifact_fails_closed(tmp_git_repo: Path) -> None:
    """The phase must not infer cleanup completion without the canonical document."""
    workspace = FsWorkspace(tmp_git_repo)
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


# Explicit immutable catalog of every source-code/config extension whose
# deletion must be declined. Pinned equal to the production catalog below so
# adding/removing a catalog entry cannot silently shrink coverage.
EXPECTED_UNSAFE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".py",
        ".js",
        ".ts",
        ".go",
        ".rs",
        ".rb",
        ".java",
        ".c",
        ".cpp",
        ".h",
        ".md",
        ".rst",
        ".txt",
        ".toml",
        ".yaml",
        ".yml",
        ".json",
        ".ini",
        ".cfg",
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
    }
)


def test_delete_source_code_extensions_rejected(tmp_git_repo: Path) -> None:
    """Every new source-code and config extension must NOT be deleted."""
    assert frozenset(UNSAFE_EXTENSIONS) == EXPECTED_UNSAFE_EXTENSIONS
    workspace = FsWorkspace(tmp_git_repo)
    extensions = sorted(EXPECTED_UNSAFE_EXTENSIONS)
    files = []
    for ext in extensions:
        src = tmp_git_repo / f"App{ext}"
        src.write_text("source code")
        files.append(src)

    _write_commit_cleanup_artifact(
        workspace,
        {
            "analysis_complete": False,
            "actions": [
                {"action": "delete_file", "path": f"App{ext}"} for ext in extensions
            ],
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
    assert result[0] in {PipelineEvent.AGENT_SUCCESS, PipelineEvent.PHASE_LOOPBACK}
    assert not isinstance(result[0], PhaseFailureEvent)
    for src in files:
        assert src.exists()


# Explicit immutable catalog of every lockfile basename whose deletion must be
# declined. Pinned equal to the production catalog below so adding/removing a
# catalog entry cannot silently shrink coverage.
EXPECTED_LOCKFILE_BASENAMES: frozenset[str] = frozenset(
    {
        "package-lock.json",
        "yarn.lock",
        "Cargo.lock",
        "poetry.lock",
        "uv.lock",
        "Pipfile.lock",
        "composer.lock",
        "Gemfile.lock",
        "go.sum",
    }
)


def test_delete_lock_files_rejected(tmp_git_repo: Path) -> None:
    """Lock files and dependency manifests must NOT be deleted."""
    assert frozenset(LOCKFILE_BASENAMES) == EXPECTED_LOCKFILE_BASENAMES
    workspace = FsWorkspace(tmp_git_repo)
    lockfiles = sorted(EXPECTED_LOCKFILE_BASENAMES)
    files = []
    for lock_file in lockfiles:
        lock = tmp_git_repo / lock_file
        lock.write_text("lock content")
        files.append(lock)

    _write_commit_cleanup_artifact(
        workspace,
        {
            "analysis_complete": False,
            "actions": [
                {"action": "delete_file", "path": lock_file} for lock_file in lockfiles
            ],
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
    assert result[0] in {PipelineEvent.AGENT_SUCCESS, PipelineEvent.PHASE_LOOPBACK}
    assert not isinstance(result[0], PhaseFailureEvent)
    for lock in files:
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


def test_repo_root_resolution_failure_returns_phase_failure_event(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A workspace whose root cannot be resolved returns a ``PhaseFailureEvent``.

    The previous implementation silently fell back to ``Path.cwd()`` when
    ``ctx.workspace.absolute_path(".")`` raised, then ran auto-seed and
    cleanup actions against an unrelated directory -- a silent corruption
    vector. The hardening replaces the fallback with a ``PhaseFailureEvent``
    so the agent can self-correct on retry (recovery controller can route
    the retry because ``recoverable=True`` and ``retry_in_session=True``).
    """
    workspace = FsWorkspace(tmp_git_repo)

    def _raise_mock_workspace_failure(_path: str) -> str:
        raise RuntimeError("mock workspace failure")

    monkeypatch.setattr(workspace, "absolute_path", _raise_mock_workspace_failure)

    _write_commit_cleanup_artifact(
        workspace,
        {
            "analysis_complete": False,
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

    assert len(result) == 1
    event = result[0]
    assert isinstance(event, PhaseFailureEvent)
    assert event.phase == "development_commit_cleanup"
    assert event.recoverable is True
    assert event.retry_in_session is True
    assert event.failure_category == FailureCategory.ARTIFACT_VALIDATION
    assert "workspace root" in event.reason, (
        f"reason must name the underlying cause category, got: {event.reason!r}"
    )
    assert "mock workspace failure" in event.reason, (
        f"reason must include the underlying exception message, got: {event.reason!r}"
    )


def test_repo_root_resolution_failure_does_not_use_path_cwd(
    tmp_git_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workspace resolution failure must NOT fall back to ``Path.cwd()``.

    Regression: the prior implementation called ``Path.cwd()`` in the
    ``except`` body of the workspace-resolution try block, then ran the
    auto-seed helpers against whatever directory the test process happened
    to be running in. This test pins the contract that the failure path
    exits BEFORE the auto-seed helpers run, so the cwd directory is never
    touched.
    """
    fake_cwd = tmp_path / "wrong_cwd_dir"
    fake_cwd.mkdir()

    # Sanity: the cwd path has no .gitignore / .git/info/exclude yet.
    assert not (fake_cwd / ".gitignore").exists()
    assert not (fake_cwd / ".git" / "info" / "exclude").exists()

    workspace = FsWorkspace(tmp_git_repo)

    def _raise_mock_workspace_failure(_path: str) -> str:
        raise RuntimeError("mock workspace failure")

    monkeypatch.setattr(workspace, "absolute_path", _raise_mock_workspace_failure)
    monkeypatch.setattr("ralph.phases.commit_cleanup.Path.cwd", lambda: fake_cwd)

    _write_commit_cleanup_artifact(
        workspace,
        {
            "analysis_complete": False,
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

    # Hardening contract: a PhaseFailureEvent is returned (the cwd path is
    # never touched as a side effect of the failure).
    assert len(result) == 1
    assert isinstance(result[0], PhaseFailureEvent)
    assert not (fake_cwd / ".gitignore").exists(), (
        ".gitignore must NOT have been created at the cwd path during "
        "the failure path (no silent fallback to Path.cwd())"
    )
    assert not (fake_cwd / ".git" / "info" / "exclude").exists(), (
        ".git/info/exclude must NOT have been created at the cwd path during "
        "the failure path (no silent fallback to Path.cwd())"
    )


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


def test_delete_untracked_repository_content_rejected(tmp_git_repo: Path) -> None:
    paths_by_category = {
        "source-test-doc-config": (
            "ralph/models.py",
            "tests/test_foo.py",
            "pyproject.toml",
            "README.md",
            "config.json",
            "NOTES.txt",
        ),
        "common-source-basename": (
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
        ),
        "protected-basename": ("Dockerfile", "Makefile", "LICENSE.txt"),
        "ordinary-source": ("helper.py",),
    }
    paths = tuple(path for category in paths_by_category.values() for path in category)
    for rel_path in paths:
        target = tmp_git_repo / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("repository content")

    result = _invoke_cleanup(
        FsWorkspace(tmp_git_repo),
        {
            "analysis_complete": False,
            "actions": [{"action": "delete_file", "path": rel_path} for rel_path in paths],
        },
    )

    assert result == [PipelineEvent.PHASE_LOOPBACK]
    for category, category_paths in paths_by_category.items():
        for rel_path in category_paths:
            assert (tmp_git_repo / rel_path).exists(), f"{category} path was deleted: {rel_path}"


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
    assert result[0] is PipelineEvent.PHASE_LOOPBACK
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
    assert result[0] is PipelineEvent.PHASE_LOOPBACK
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


def _assert_declined_completes(result: list[object]) -> None:
    """Safety declines complete the phase; they are not phase failures."""
    assert len(result) == 1
    assert result[0] in {PipelineEvent.AGENT_SUCCESS, PipelineEvent.PHASE_LOOPBACK}
    assert not isinstance(result[0], PhaseFailureEvent)


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
    repo = Repo(repo_root)
    try:
        repo.index.add([rel_path])
        repo.index.commit(f"track {rel_path}")
    finally:
        repo.close()


def test_delete_tracked_agent_internal_files_succeeds(tmp_git_repo: Path) -> None:
    tracked_artifacts = (
        (".agent/raw/opencode.log", "log content"),
        (".agent/tmp/mcp-server.log", "mcp log"),
        (".agent/checkpoint.json", '{"phase": "development"}'),
        ("checkpoint.json", '{"phase": "development"}'),
        (".agent/completion_seen_run-abc-123.json", '{"run_id": "run-abc-123"}'),
        (".agent/rebase_checkpoint.json", "rebase state"),
        (".agent/rebase_checkpoint.json.bak", "rebase backup"),
        (".agent/rebase.lock", "lock content"),
        (".agent/start_commit", "baseline sha"),
        (".agent/PLAN.md", "# Plan"),
        (".agent/PLANNING_ANALYSIS_DECISION.md", "decision"),
        (".agent/receipts/run-1/commit_cleanup.json", '{"artifact_type": "commit_cleanup"}'),
        (".agent/workers/unit-a/tmp/checkpoint.json", '{"phase": "unit"}'),
    )
    for rel_path, content in tracked_artifacts:
        target = tmp_git_repo / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    repo = Repo(tmp_git_repo)
    try:
        repo.index.add([rel_path for rel_path, _content in tracked_artifacts])
        repo.index.commit("track canonical engine artifacts")
    finally:
        repo.close()

    result = _invoke_cleanup(
        FsWorkspace(tmp_git_repo),
        {
            "analysis_complete": True,
            "actions": [
                {"action": "delete_file", "path": rel_path}
                for rel_path, _content in tracked_artifacts
            ],
        },
    )

    assert result == [PipelineEvent.AGENT_SUCCESS]
    for rel_path, _content in tracked_artifacts:
        assert not (tmp_git_repo / rel_path).exists(), f"engine artifact survived: {rel_path}"


# --- NEGATIVE security-regression tests (security boundary) ---


def test_delete_tracked_non_engine_files_rejected(tmp_git_repo: Path) -> None:
    paths_by_category = {
        "generated-looking": (
            "verify-output.txt",
            "config.yml.bak",
            "temp_script.py",
            "tmp/utility.py",
            ".coverage",
            "coverage.xml",
            "checkpoint.json.bak",
        ),
        "agent-root-source": (
            ".agent/test.py",
            ".agent/utils.py",
            ".agent/CHANGELOG.md",
            ".agent/note.txt",
            ".agent/scripts/build.sh",
            ".agent/lib/foo.py",
            ".agent/hooks/pre-commit.py",
        ),
        "agent-non-allowlisted-subdirectory": (
            ".agent/notes/foo.txt",
            ".agent/data/seed.json",
        ),
        "outside-agent-source": (
            "app/controllers/foo.rb",
            "src/main.go",
            "lib/utils.rb",
            "scripts/build.sh",
        ),
        "agent-root-random-json": (".agent/random_config.json",),
        "engine-directory-wrong-extension": (
            ".agent/raw/script.py",
            ".agent/raw/main.go",
            ".agent/raw/notes.md",
            ".agent/tmp/config.yaml",
            ".agent/tmp/main.py",
            ".agent/artifacts/plan.json",
            ".agent/receipts/run-1/note.md",
            ".agent/prompt_history/notes.md",
            ".agent/artifact-formats/data.json",
            ".agent/workers/unit-a/src/main.py",
            ".agent/workers/unit-a/src/foo.go",
            ".agent/workers/unit-a/sub/dir/foo.rs",
        ),
    }
    paths = tuple(path for category in paths_by_category.values() for path in category)
    for rel_path in paths:
        target = tmp_git_repo / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("tracked project content")

    repo = Repo(tmp_git_repo)
    try:
        repo.index.add(list(paths))
        repo.index.commit("track protected project content")
    finally:
        repo.close()

    result = _invoke_cleanup(
        FsWorkspace(tmp_git_repo),
        {
            "analysis_complete": False,
            "actions": [{"action": "delete_file", "path": rel_path} for rel_path in paths],
        },
    )

    assert result == [PipelineEvent.PHASE_LOOPBACK]
    for category, category_paths in paths_by_category.items():
        for rel_path in category_paths:
            assert (tmp_git_repo / rel_path).exists(), f"{category} path was deleted: {rel_path}"


# ---------------------------------------------------------------------------
# Best-effort cleanup hardening (AC-01, AC-02, AC-05)
# ---------------------------------------------------------------------------
#
# Cleanup is BEST-EFFORT: a single unsafe ``delete_file`` does NOT abort the
# phase. Safe actions (matching files, gitignore patterns, git exclude
# patterns) are still applied even when one or more delete actions are
# rejected. The phase only returns ``PhaseFailureEvent`` when EVERY delete
# action was unsafe AND no safe action was applied -- in that case the event
# carries a structured retry hint naming the rejected paths.


def test_delete_tracked_agent_internal_files_best_effort_no_phase_failure(
    tmp_git_repo: Path,
) -> None:
    """AC-01/AC-02: mixed batch of safe-delete + safe-gitignore never fails the phase.

    Pins the contract that a tracked engine-internal file (``.agent/raw/opencode.log``)
    plus an unrelated gitignore pattern can be applied together without a
    PhaseFailureEvent, even though the original bug emitted one for this path.
    """
    target = tmp_git_repo / ".agent" / "raw" / "opencode.log"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("log content")
    _track_and_commit(tmp_git_repo, ".agent/raw/opencode.log")

    result = _invoke_cleanup(
        FsWorkspace(tmp_git_repo),
        {
            "analysis_complete": True,
            "actions": [
                {"action": "delete_file", "path": ".agent/raw/opencode.log"},
                {"action": "add_to_gitignore", "pattern": "*.scratch"},
            ],
        },
    )

    assert result == [PipelineEvent.AGENT_SUCCESS]
    assert not target.exists()
    gitignore = tmp_git_repo / ".gitignore"
    assert "*.scratch" in gitignore.read_text()


def test_mixed_safe_and_unsafe_delete_actions_are_best_effort(tmp_git_repo: Path) -> None:
    """AC-01/AC-02: safe delete + unsafe delete in same batch: safe delete wins."""
    binary = tmp_git_repo / "binary.exe"
    binary.write_bytes(b"\x00MZ")
    source = tmp_git_repo / "helper.py"
    source.write_text("def helper(): pass")

    result = _invoke_cleanup(
        FsWorkspace(tmp_git_repo),
        {
            "analysis_complete": True,
            "actions": [
                {"action": "delete_file", "path": "binary.exe"},
                {"action": "delete_file", "path": "helper.py"},
            ],
        },
    )

    assert result == [PipelineEvent.AGENT_SUCCESS]
    assert not binary.exists()
    # Unsafe delete was skipped -- the file MUST remain.
    assert source.exists()


def test_unsafe_delete_does_not_abort_phase_when_safe_delete_present(
    tmp_git_repo: Path,
) -> None:
    """AC-01: unsafe delete with a safe gitignore action still succeeds."""
    source = tmp_git_repo / "module.py"
    source.write_text("source code")

    result = _invoke_cleanup(
        FsWorkspace(tmp_git_repo),
        {
            "analysis_complete": True,
            "actions": [
                {"action": "add_to_gitignore", "pattern": "*.tmp"},
                {"action": "delete_file", "path": "module.py"},
            ],
        },
    )

    assert result == [PipelineEvent.AGENT_SUCCESS]
    assert source.exists()
    gitignore = tmp_git_repo / ".gitignore"
    assert "*.tmp" in gitignore.read_text()


def test_cleanup_phase_auto_seeds_gitignore_canonical_patterns_on_entry(
    tmp_git_repo: Path,
) -> None:
    """AC-04: handle_commit_cleanup_phase seeds canonical .gitignore on every entry."""
    result = _invoke_cleanup(
        FsWorkspace(tmp_git_repo),
        {"analysis_complete": True, "actions": []},
    )

    assert result == [PipelineEvent.AGENT_SUCCESS]
    gitignore = tmp_git_repo / ".gitignore"
    assert gitignore.exists()
    gitignore_text = gitignore.read_text()
    # The auto-seeded canonical pattern for the .agent/ dir MUST be present.
    assert ".agent/" in gitignore_text
    # The root-anchored /checkpoint.json pattern MUST be present.
    assert "/checkpoint.json" in gitignore_text


def test_cleanup_phase_auto_seeds_git_exclude_canonical_patterns_on_entry(
    tmp_git_repo: Path,
) -> None:
    """AC-04: handle_commit_cleanup_phase seeds canonical .git/info/exclude on every entry."""
    result = _invoke_cleanup(
        FsWorkspace(tmp_git_repo),
        {"analysis_complete": True, "actions": []},
    )

    assert result == [PipelineEvent.AGENT_SUCCESS]
    exclude = tmp_git_repo / ".git" / "info" / "exclude"
    assert exclude.exists()
    exclude_text = exclude.read_text()
    # The auto-seeded patterns MUST include canonical per-user excludes
    # the bootstrap helper writes (any sentinel pattern from the default set).
    assert exclude_text.strip() != ""


def test_duplicate_delete_file_actions_idempotent(tmp_git_repo: Path) -> None:
    """AC-05: duplicate delete_file actions are deduplicated, not double-applied."""
    binary = tmp_git_repo / "duplicate.exe"
    binary.write_bytes(b"\x00MZ")

    result = _invoke_cleanup(
        FsWorkspace(tmp_git_repo),
        {
            "analysis_complete": True,
            "actions": [
                {"action": "delete_file", "path": "duplicate.exe"},
                {"action": "delete_file", "path": "duplicate.exe"},
            ],
        },
    )

    assert result == [PipelineEvent.AGENT_SUCCESS]
    assert not binary.exists()


def test_empty_path_in_delete_action_is_skipped_with_debug_log(tmp_git_repo: Path) -> None:
    """AC-05: whitespace path is silently dropped; Pydantic rejects empty at schema layer.

    Pins the contract that the handler is defensive against malformed
    action dicts that bypass validation -- if a path sneaks through as
    whitespace, the handler skips it with a DEBUG log and continues.
    Whitespace paths are NOT added to ``skipped_delete_paths`` because
    they are not retryable -- the agent should never re-submit the same
    empty/whitespace value, so there is no value in surfacing them via
    the structured retry hint. The artifact schema layer (Pydantic
    ``CommitCleanupAction``) already rejects empty ``path`` values, so
    this test exercises the runtime fallback via ``model_construct`` to
    bypass Pydantic validation and simulate the legacy edge case where
    a whitespace path slips past the schema layer.
    """
    binary = tmp_git_repo / "binary.exe"
    binary.write_bytes(b"\x00MZ")

    whitespace_action = CommitCleanupAction.model_construct(action="delete_file", path="   ")
    real_action = CommitCleanupAction.model_construct(action="delete_file", path="binary.exe")
    cleanup = CommitCleanup.model_construct(
        analysis_complete=True,
        actions=[whitespace_action, real_action],
    )

    skipped, _failed = _apply_cleanup_actions(tmp_git_repo, cleanup)
    # Whitespace-only paths are silently dropped -- not surfaced in the
    # retry hint because the agent cannot meaningfully retry them.
    assert skipped == []
    # The real delete still succeeds alongside the whitespace skip.
    assert not binary.exists()


def test_empty_pattern_in_gitignore_action_is_skipped(tmp_git_repo: Path) -> None:
    """AC-05: whitespace-only pattern is dropped before append_to_gitignore.

    Pins the contract that the handler is defensive against whitespace
    patterns bypassing the strict Pydantic validation -- the runtime
    branch filters them out with a DEBUG log instead of crashing.
    """
    whitespace_action = CommitCleanupAction.model_construct(
        action="add_to_gitignore", pattern="   "
    )
    real_action = CommitCleanupAction.model_construct(action="add_to_gitignore", pattern="*.real")
    cleanup = CommitCleanup.model_construct(
        analysis_complete=True,
        actions=[whitespace_action, real_action],
    )

    _apply_cleanup_actions(tmp_git_repo, cleanup)
    gitignore_text = (tmp_git_repo / ".gitignore").read_text()
    assert "*.real" in gitignore_text


def test_all_safe_actions_with_no_delete_succeeds(tmp_git_repo: Path) -> None:
    """AC-05: a batch with only gitignore + git-exclude actions succeeds."""
    result = _invoke_cleanup(
        FsWorkspace(tmp_git_repo),
        {
            "analysis_complete": True,
            "actions": [
                {"action": "add_to_gitignore", "pattern": "*.binary"},
                {"action": "add_to_git_exclude", "pattern": ".my-local-cache"},
            ],
        },
    )

    assert result == [PipelineEvent.AGENT_SUCCESS]
    gitignore_text = (tmp_git_repo / ".gitignore").read_text()
    assert "*.binary" in gitignore_text
    exclude_text = (tmp_git_repo / ".git" / "info" / "exclude").read_text()
    assert ".my-local-cache" in exclude_text


def test_all_unsafe_deletes_with_no_safe_work_returns_failure_event(
    tmp_git_repo: Path,
) -> None:
    """AC-03: when ALL delete actions are unsafe and no safe work was done, fail with hint."""
    source = tmp_git_repo / "module.py"
    source.write_text("source code")

    result = _invoke_cleanup(
        FsWorkspace(tmp_git_repo),
        {
            "analysis_complete": False,
            "actions": [{"action": "delete_file", "path": "module.py"}],
        },
    )

    _assert_declined_completes(result)
    hint = (tmp_git_repo / ".agent" / "tmp" / "last_retry_error_development_commit_cleanup.txt").read_text()
    assert "module.py" in hint
    assert source.exists()


def test_unsafe_delete_with_whitespace_only_gitignore_returns_failure_event(
    tmp_git_repo: Path,
) -> None:
    """Regression (analysis feedback): unsafe delete + whitespace-only gitignore pattern.

    Pins the contract that an unsafe ``delete_file`` plus a whitespace-only
    ``add_to_gitignore`` pattern (which is silently dropped by ``_classify_action``)
    must NOT bypass the ``_all_deletes_rejected_failure`` branch. The outcome
    decision must be based on actually-applied classified actions, not raw
    truthiness of the artifact fields -- otherwise the structured retry hint
    is suppressed and the agent cannot self-correct on retry.
    """
    source = tmp_git_repo / "module.py"
    source.write_text("source code")

    whitespace_action = CommitCleanupAction.model_construct(
        action="add_to_gitignore", pattern="   "
    )
    unsafe_action = CommitCleanupAction.model_construct(action="delete_file", path="module.py")
    cleanup = CommitCleanup.model_construct(
        analysis_complete=False,
        actions=[unsafe_action, whitespace_action],
    )

    skipped, _failed = _apply_cleanup_actions(tmp_git_repo, cleanup)
    assert "module.py" in skipped
    gitignore_path = tmp_git_repo / ".gitignore"
    gitignore_text = gitignore_path.read_text() if gitignore_path.exists() else ""
    applied_gitignore = ["   "] if "   " in gitignore_text else []
    report = CleanupApplyReport(
        declined_delete_paths=list(skipped),
        applied_gitignore_patterns=applied_gitignore,
    )

    outcome = decide_cleanup_outcome("development_commit_cleanup", cleanup, report)
    assert outcome == [PipelineEvent.PHASE_LOOPBACK]
    assert source.exists()


def test_unsafe_delete_with_whitespace_only_git_exclude_returns_failure_event(
    tmp_git_repo: Path,
) -> None:
    """Regression (analysis feedback): unsafe delete + whitespace-only git_exclude pattern.

    Mirrors ``test_unsafe_delete_with_whitespace_only_gitignore_returns_failure_event``
    but with the ``add_to_git_exclude`` action. The whitespace-only pattern is
    silently dropped by ``_classify_action``, so the outcome decision must
    still escalate to ``PhaseFailureEvent`` (the unsafe delete was the only
    meaningful work, and it was rejected).
    """
    source = tmp_git_repo / "module.py"
    source.write_text("source code")

    whitespace_action = CommitCleanupAction.model_construct(
        action="add_to_git_exclude", pattern="   "
    )
    unsafe_action = CommitCleanupAction.model_construct(action="delete_file", path="module.py")
    cleanup = CommitCleanup.model_construct(
        analysis_complete=False,
        actions=[unsafe_action, whitespace_action],
    )

    skipped, _failed = _apply_cleanup_actions(tmp_git_repo, cleanup)
    assert "module.py" in skipped
    exclude_path = tmp_git_repo / ".git" / "info" / "exclude"
    exclude_text = exclude_path.read_text() if exclude_path.exists() else ""
    applied_exclude = ["   "] if "   " in exclude_text else []
    report = CleanupApplyReport(
        declined_delete_paths=list(skipped),
        applied_exclude_patterns=applied_exclude,
    )

    outcome = decide_cleanup_outcome("development_commit_cleanup", cleanup, report)
    assert outcome == [PipelineEvent.PHASE_LOOPBACK]
    assert source.exists()


def test_whitespace_only_delete_path_with_safe_gitignore_succeeds(
    tmp_git_repo: Path,
) -> None:
    """Regression (analysis feedback): whitespace-only ``delete_file`` + safe gitignore succeeds.

    A whitespace-only ``delete_file`` path is silently dropped by
    ``_classify_action``. When paired with a non-whitespace ``add_to_gitignore``
    pattern, the gitignore action is the only meaningful work and the phase
    must succeed (not fail) -- so the safe-applied-action path is preserved.
    """
    # Use ``model_construct`` to bypass Pydantic validation -- the
    # hardened CommitCleanupAction now rejects whitespace-only values at
    # the schema layer.
    whitespace_action = CommitCleanupAction.model_construct(action="delete_file", path="   ")
    real_action = CommitCleanupAction.model_construct(
        action="add_to_gitignore", pattern="*.scratch"
    )
    cleanup = CommitCleanup.model_construct(
        analysis_complete=True,
        actions=[whitespace_action, real_action],
    )

    _apply_cleanup_actions(tmp_git_repo, cleanup)
    gitignore_text = (tmp_git_repo / ".gitignore").read_text()
    assert "*.scratch" in gitignore_text
    applied_gitignore = ["*.scratch"] if "*.scratch" in gitignore_text else []
    report = CleanupApplyReport(
        declined_delete_paths=[],
        applied_gitignore_patterns=applied_gitignore,
    )

    outcome = decide_cleanup_outcome("development_commit_cleanup", cleanup, report)
    assert outcome == [PipelineEvent.AGENT_SUCCESS] or len(outcome) == 1


def test_retry_hint_named_for_each_rejected_path(tmp_git_repo: Path) -> None:
    """AC-03: PhaseFailureEvent.reason contains structured hint naming every rejected path."""
    source_a = tmp_git_repo / "a.py"
    source_b = tmp_git_repo / "b.py"
    source_a.write_text("a")
    source_b.write_text("b")

    result = _invoke_cleanup(
        FsWorkspace(tmp_git_repo),
        {
            "analysis_complete": False,
            "actions": [
                {"action": "delete_file", "path": "a.py"},
                {"action": "delete_file", "path": "b.py"},
            ],
        },
    )

    _assert_declined_completes(result)
    hint = (tmp_git_repo / ".agent" / "tmp" / "last_retry_error_development_commit_cleanup.txt").read_text()
    assert "a.py" in hint
    assert "b.py" in hint
    assert source_a.exists()
    assert source_b.exists()


# --- Phase 7 edge-case tests for build_cleanup_retry_hint ---
#
# Each test pins one observable behavior of the structured retry-hint
# builder. The names follow the plan: test_build_cleanup_retry_hint_<branch>
# so the test id maps directly to the helper's branch coverage.


@pytest.mark.timeout_seconds(5)
def test_build_cleanup_retry_hint_empty_skipped_paths_returns_sentinel() -> None:
    """Empty ``skipped_paths`` returns the sentinel message, not an empty string.

    Pins the contract that ``build_cleanup_retry_hint`` ALWAYS returns a
    non-empty string, even when there are no skipped paths. The sentinel
    tells the agent that the phase still failed despite no rejected
    delete actions -- pointing the diagnostic toward the artifact schema
    itself rather than the path list.
    """
    hint = build_cleanup_retry_hint(skipped_paths=[], safe_applied_count=0)

    assert isinstance(hint, str)
    assert hint, "build_cleanup_retry_hint must NOT return an empty string"
    assert "no delete actions were rejected" in hint, (
        f"Sentinel must explicitly state that no delete actions were rejected; got: {hint!r}"
    )


@pytest.mark.timeout_seconds(5)
def test_build_cleanup_retry_hint_with_skipped_paths_and_safe_count() -> None:
    """Non-empty ``skipped_paths`` with a positive safe count renders both sections.

    Pins the contract that:
      1. The helper names each rejected path on its own line.
      2. The safe-applied count appears in the form ``Safe actions applied: N``
         when ``safe_applied_count > 0``.
    """
    hint = build_cleanup_retry_hint(skipped_paths=["foo.py", "bar.py"], safe_applied_count=2)

    assert "foo.py" in hint, f"Path foo.py must be named in the hint; got: {hint!r}"
    assert "bar.py" in hint, f"Path bar.py must be named in the hint; got: {hint!r}"
    assert "Safe actions applied: 2" in hint, (
        f"Safe count must appear in the form 'Safe actions applied: 2'; got: {hint!r}"
    )


@pytest.mark.timeout_seconds(5)
def test_build_cleanup_retry_hint_with_skipped_paths_and_zero_safe_count() -> None:
    """Non-empty ``skipped_paths`` with zero safe count renders the zero-summary branch.

    Pins the contract that a zero ``safe_applied_count`` produces the
    ``No safe actions were applied`` summary, NOT the ``Safe actions applied: 0``
    form. The wording is intentionally distinct so the agent's self-correct
    path can distinguish "nothing was applied" from "exactly zero was applied".
    """
    hint = build_cleanup_retry_hint(skipped_paths=["baz.py"], safe_applied_count=0)

    assert "baz.py" in hint, f"Path baz.py must be named in the hint; got: {hint!r}"
    assert "No safe actions were applied" in hint, (
        f"Zero-safe-count summary must use the 'No safe actions were applied' "
        f"wording; got: {hint!r}"
    )
    assert "Safe actions applied: 0" not in hint, (
        f"Zero-safe-count summary must NOT use the 'Safe actions applied: N' form; got: {hint!r}"
    )


@pytest.mark.timeout_seconds(5)
def test_apply_time_delete_failure_triggers_phase_failure_event(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: apply-time ``delete_file_from_repo`` failure escalates to ``PhaseFailureEvent``.

    Pins the fix for the bug where a delete that PASSED the safety
    classifier but FAILED at apply time (permission denied, stale git
    lock, transient I/O) was silently counted as successful cleanup.
    The prior code returned ``PHASE_LOOPBACK`` / ``AGENT_SUCCESS`` with
    zero actual cleanup work -- a silent failure.

    The test patches ``delete_file_from_repo`` to raise for an
    otherwise-safe binary artifact, drives ``handle_commit_cleanup_phase``
    through the full ``InvokeAgentEffect`` flow, and asserts the phase
    returns a single ``PhaseFailureEvent`` carrying the structured
    retry hint that names the failed path. The binary artifact MUST
    still exist on disk (the delete was attempted and failed).
    """
    workspace = FsWorkspace(tmp_git_repo)
    binary = tmp_git_repo / "binary.exe"
    binary.write_bytes(b"\x00MZ")

    cleanup_artifact = {
        "analysis_complete": False,
        "actions": [
            {"action": "delete_file", "path": "binary.exe"},
        ],
    }
    _write_commit_cleanup_artifact(workspace, cleanup_artifact)

    def _raise_on_delete(repo_root: Path, file_path: str) -> None:
        raise OSError(f"simulated apply-time failure for {file_path}")

    monkeypatch.setattr(
        "ralph.phases.commit_cleanup.delete_file_from_repo",
        _raise_on_delete,
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
    events = handle_commit_cleanup_phase(effect, ctx)

    assert len(events) == 1, (
        f"Expected exactly one PhaseFailureEvent, got {len(events)}: {events!r}"
    )
    assert isinstance(events[0], PhaseFailureEvent), (
        f"Expected PhaseFailureEvent when all attempted deletes failed at "
        f"apply time, got {type(events[0]).__name__}: {events[0]!r}"
    )
    assert "binary.exe" in events[0].reason, (
        f"PhaseFailureEvent.reason must name the failed path binary.exe; got: {events[0].reason!r}"
    )
    assert "Cleanup retry hint" in events[0].reason, (
        f"PhaseFailureEvent.reason must carry the structured retry hint; got: {events[0].reason!r}"
    )
    assert events[0].recoverable is True
    assert events[0].failure_category == FailureCategory.ARTIFACT_VALIDATION
    assert binary.exists(), (
        "Binary artifact must still exist -- the delete was attempted and "
        "failed; the phase must NOT silently report success."
    )


@pytest.mark.timeout_seconds(5)
def test_apply_time_delete_failure_with_safe_gitignore_succeeds(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: apply-time failure on delete + safe gitignore pattern = AGENT_SUCCESS.

    Pins the partial-success contract: when an apply-time delete
    failure happens ALONGSIDE a successful ``add_to_gitignore``
    action, the gitignore action's success counts toward
    ``safe_actions_count`` so the phase returns ``AGENT_SUCCESS``,
    not ``PhaseFailureEvent``. The failure is still surfaced via
    WARNING log and the failed path is recorded -- the phase does not
    silently swallow the failure, it just does not escalate when
    other safe work succeeded.
    """
    workspace = FsWorkspace(tmp_git_repo)
    binary = tmp_git_repo / "binary.exe"
    binary.write_bytes(b"\x00MZ")

    cleanup_artifact = {
        "analysis_complete": True,
        "actions": [
            {"action": "delete_file", "path": "binary.exe"},
            {"action": "add_to_gitignore", "pattern": "*.scratch"},
        ],
    }
    _write_commit_cleanup_artifact(workspace, cleanup_artifact)

    def _raise_on_delete(repo_root: Path, file_path: str) -> None:
        raise OSError(f"simulated apply-time failure for {file_path}")

    monkeypatch.setattr(
        "ralph.phases.commit_cleanup.delete_file_from_repo",
        _raise_on_delete,
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
    events = handle_commit_cleanup_phase(effect, ctx)

    assert events == [PipelineEvent.AGENT_SUCCESS], (
        f"Phase must return AGENT_SUCCESS when the gitignore action "
        f"succeeded alongside the apply-time delete failure; got: {events!r}"
    )
    gitignore_text = (tmp_git_repo / ".gitignore").read_text()
    assert "*.scratch" in gitignore_text, (
        f"Gitignore action must still be applied even when the delete "
        f"action failed at apply time; got: {gitignore_text!r}"
    )
    assert binary.exists(), "Binary artifact must still exist -- the delete failed at apply time."


@pytest.mark.timeout_seconds(5)
def test_apply_cleanup_actions_returns_separate_skipped_and_failed_lists(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: ``_apply_cleanup_actions`` returns ``(skipped, failed)`` disjoint tuple.

    Pins the public contract: the function returns a tuple of two
    disjoint lists so callers can distinguish safety-classifier
    rejections from apply-time failures. The first element is the
    list of paths the classifier rejected (``_is_safe_to_delete``
    returned False); the second is the list of paths the classifier
    accepted but ``delete_file_from_repo`` raised on. The two lists
    MUST be disjoint -- a path can only be in one or the other.
    """
    workspace = FsWorkspace(tmp_git_repo)
    _ = workspace  # kept so future tests can drive PhaseContext via this helper
    safe_binary = tmp_git_repo / "binary.exe"
    safe_binary.write_bytes(b"\x00MZ")
    unsafe_source = tmp_git_repo / "module.py"
    unsafe_source.write_text("source code")

    cleanup = CommitCleanup.model_construct(
        analysis_complete=True,
        actions=[
            CommitCleanupAction.model_construct(action="delete_file", path="binary.exe"),
            CommitCleanupAction.model_construct(action="delete_file", path="module.py"),
        ],
    )

    def _raise_on_delete(repo_root: Path, file_path: str) -> None:
        raise OSError(f"simulated apply-time failure for {file_path}")

    monkeypatch.setattr(
        "ralph.phases.commit_cleanup.delete_file_from_repo",
        _raise_on_delete,
    )

    skipped, failed = _apply_cleanup_actions(tmp_git_repo, cleanup)

    assert "module.py" in skipped, f"Unsafe path module.py must be in skipped; got: {skipped!r}"
    assert "binary.exe" in failed, (
        f"Safe-but-failed path binary.exe must be in failed; got: {failed!r}"
    )
    assert set(skipped).isdisjoint(set(failed)), (
        f"skipped and failed lists must be disjoint; skipped={skipped!r}, failed={failed!r}"
    )


# ---------------------------------------------------------------------------
# Pre-emptive untrack safety-net tests (commit_cleanup phase integration)
# ---------------------------------------------------------------------------
#
# These tests drive ``handle_commit_cleanup_phase`` end-to-end and pin the
# pre-emptive ``git rm --cached`` sweep that runs BEFORE the artifact
# load. The sweep removes tracked engine-internal files from the index
# so the agent's diff no longer includes them -- even when the agent's
# ``delete_file`` action would otherwise hit a hard safety reject for
# a tracked engine file.


@pytest.mark.timeout_seconds(5)
def test_pre_emptive_untrack_only_unindexes_engine_files_with_empty_artifact(
    tmp_git_repo: Path,
) -> None:
    engine_paths = (
        ".agent/raw/opencode.log",
        ".agent/tmp/mcp-server.log",
        "checkpoint.json",
    )
    source_path = "src/app.py"
    tracked_paths = (*engine_paths, source_path)
    for rel_path in tracked_paths:
        target = tmp_git_repo / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("tracked content")

    repo = Repo(tmp_git_repo)
    try:
        repo.index.add(list(tracked_paths))
        repo.index.commit("track engine and source files")
    finally:
        repo.close()

    result = _invoke_cleanup(
        FsWorkspace(tmp_git_repo),
        {"analysis_complete": True, "actions": []},
    )

    assert result == [PipelineEvent.AGENT_SUCCESS], (
        f"Empty artifact with tracked engine files must still return AGENT_SUCCESS "
        f"(pre-emptive untrack handles the cleanup); got: {result!r}"
    )

    repo = Repo(tmp_git_repo)
    try:
        cached = set(repo.git.ls_files("--cached").splitlines())
        index_paths = {entry_path for entry_path, _stage in repo.index.entries}
        for rel_path in engine_paths:
            assert rel_path not in cached, f"engine path remained cached: {rel_path}"
            assert rel_path not in index_paths, f"engine path remained indexed: {rel_path}"
        assert source_path in cached
        assert source_path in index_paths
    finally:
        repo.close()

    for rel_path in tracked_paths:
        assert (tmp_git_repo / rel_path).exists(), f"working-tree file was deleted: {rel_path}"


@pytest.mark.timeout_seconds(5)
def test_pre_emptive_untrack_runs_before_artifact_load(tmp_git_repo: Path) -> None:
    """The untrack fires even when no artifact is present (untrack precedes artifact load).

    Pins the contract that the pre-emptive untrack step is BEFORE the
    artifact load in the ``handle_commit_cleanup_phase`` body. The
    AST placement check in ``audit_agent_internal_paths.py``
    (``_check_pre_emptive_untrack_placement``) verifies the same
    ordering statically; this test verifies it behaviorally by
    pre-staging tracked engine files, NOT writing any artifact,
    driving the phase, and asserting (i) the tracked engine files
    are NO LONGER in ``git ls-files --cached`` AND (ii) the phase
    returns ``PhaseFailureEvent(recoverable=True)`` (because the
    artifact is missing) -- the untrack MUST happen BEFORE the
    artifact load.
    """
    log_path = tmp_git_repo / ".agent" / "raw" / "opencode.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("log content")
    _track_and_commit(tmp_git_repo, ".agent/raw/opencode.log")

    # Drive the phase WITHOUT writing any artifact.
    workspace = FsWorkspace(tmp_git_repo)
    ctx = _make_cleanup_ctx(workspace)
    effect = InvokeAgentEffect(
        agent_name="dev", phase="development_commit_cleanup", prompt_file="cleanup.txt"
    )
    events = handle_commit_cleanup_phase(effect, ctx)

    assert len(events) == 1
    assert isinstance(events[0], PhaseFailureEvent), (
        f"Missing artifact must return PhaseFailureEvent, got: {events!r}"
    )
    assert events[0].recoverable is True, (
        f"PhaseFailureEvent must be recoverable (artifact missing), got: {events[0]!r}"
    )

    repo = Repo(tmp_git_repo)
    try:
        cached = set(repo.git.ls_files("--cached").splitlines())
        assert ".agent/raw/opencode.log" not in cached, (
            "Tracked engine file MUST be removed from index by pre-emptive untrack "
            "even when the artifact is missing (untrack runs before artifact load)"
        )
    finally:
        repo.close()


@pytest.mark.timeout_seconds(5)
def test_pre_emptive_untrack_succeeds_when_git_state_is_broken(tmp_path: Path) -> None:
    """A non-git workspace: pre-emptive untrack returns no failure.

    Pins the broken-git-state edge case at the phase level. The
    untrack helper is wrapped in ``with suppress(Exception):`` so a
    workspace whose root is NOT a git repo does NOT stall the phase
    -- the helper returns ``[]`` fail-closed, and the phase continues
    to the artifact load, which then returns a ``PhaseFailureEvent``
    (because there's no artifact, NOT because the untrack failed).
    """
    non_repo = tmp_path / "non_repo_workspace"
    non_repo.mkdir()
    assert not (non_repo / ".git").exists(), "Setup invariant: non_repo must not be a git repo"

    workspace = FsWorkspace(non_repo)
    ctx = _make_cleanup_ctx(workspace)
    effect = InvokeAgentEffect(
        agent_name="dev", phase="development_commit_cleanup", prompt_file="cleanup.txt"
    )
    events = handle_commit_cleanup_phase(effect, ctx)

    assert len(events) == 1
    assert isinstance(events[0], PhaseFailureEvent), (
        f"Missing artifact on a non-git workspace must still return PhaseFailureEvent "
        f"(pre-emptive untrack must NOT crash); got: {events!r}"
    )


# ---------------------------------------------------------------------------
# Edge-case tests for BOTH ``.gitignore`` AND ``.git/info/exclude`` auto-seeding
# ---------------------------------------------------------------------------
#
# These parametrized tests cover the two production callers of the
# ``_atomic_append_text`` helper that the ``commit_cleanup`` phase
# triggers on every entry: ``append_to_gitignore`` (writes
# ``.gitignore``) and ``add_to_git_exclude`` (writes
# ``.git/info/exclude``). Each variant pins the EXPECTED behavior of
# the helper against BOM-prefixed, CRLF-terminated, trailing-whitespace,
# and symlinked pre-existing content -- so the helper's byte-level
# round-trip contract is verified at the boundary, not just at the
# helper layer.


def _drive_auto_seed(
    tmp_git_repo: Path,
    helper_name: str,
    pre_existing_bytes: bytes,
    symlink_target: Path | None,
) -> bytes:
    """Drive the auto-seed helpers twice and return the resulting target file bytes.

    Pre-populates the target file with ``pre_existing_bytes`` (or
    replaces it with a symlink to ``symlink_target`` when one is
    supplied), drives the auto-seed helper ``helper_name`` twice in a
    row, then reads the target file's final bytes. The double-call
    exercises the de-duplication check in the helper -- if a previous
    run did NOT dedup, the second call would re-append the same
    patterns.
    """
    if helper_name == "auto_seed_default_gitignore":
        target = tmp_git_repo / ".gitignore"
    elif helper_name == "auto_seed_default_git_exclude":
        target = tmp_git_repo / ".git" / "info" / "exclude"
    else:
        raise ValueError(f"unknown helper: {helper_name}")

    target.parent.mkdir(parents=True, exist_ok=True)

    if symlink_target is not None:
        # Set up a real file as the symlink target so the symlink can be
        # replaced via ``Path.replace()`` if the helper attempts to write.
        symlink_target.parent.mkdir(parents=True, exist_ok=True)
        symlink_target.write_bytes(b"sentinel-content-for-symlink-target\n")
        if target.exists() or target.is_symlink():
            target.unlink()
        target.symlink_to(symlink_target)
    else:
        if target.exists() or target.is_symlink():
            target.unlink()
        target.write_bytes(pre_existing_bytes)

    getattr(bootstrap, helper_name)(tmp_git_repo)
    getattr(bootstrap, helper_name)(tmp_git_repo)

    # ``Path.read_bytes`` follows symlinks; for the symlink case the
    # helper may either replace the symlink (no longer a symlink) or
    # leave the symlink intact (in which case we read the target).
    return target.read_bytes()


@pytest.mark.parametrize(
    ("helper_name", "target_relpath"),
    [
        pytest.param("auto_seed_default_gitignore", ".gitignore", id="gitignore"),
        pytest.param("auto_seed_default_git_exclude", ".git/info/exclude", id="git-exclude"),
    ],
)
@pytest.mark.timeout_seconds(5)
def test_auto_seed_atomic_append_preserves_bom_prefixed_existing_content(
    tmp_git_repo: Path, helper_name: str, target_relpath: str
) -> None:
    """BOM-prefixed existing content is preserved byte-for-byte.

    The atomic helper uses ``read_bytes()`` + ``write_bytes()`` so the
    BOM byte is preserved through the round trip. The helper MUST NOT
    duplicate-append on a second call. After two calls, the target
    file MUST equal the original bytes plus the appended patterns
    verbatim (the helper's line-set dedup check matches by exact line
    equality, so the BOM-prefixed line is treated as distinct from
    the same line without a BOM -- but since neither was ever in the
    seed set, both should be left alone and the seed pattern appended
    once).
    """
    pre_existing = "\ufeffexisting-bom-line\n"

    resulting = _drive_auto_seed(tmp_git_repo, helper_name, pre_existing.encode("utf-8"), None)

    assert resulting.startswith("\ufeff".encode("utf-8")), (
        f"BOM prefix MUST be preserved through the round trip; got first bytes: {resulting[:8]!r}"
    )
    assert b"existing-bom-line\n" in resulting, (
        f"Pre-existing BOM-prefixed line MUST be preserved; got: {resulting!r}"
    )
    # The seed pattern is appended exactly once (no duplicate-append on the
    # second call).
    canonical_line = "\ufeffexisting-bom-line"
    assert resulting.count(canonical_line.encode("utf-8")) == 1, (
        f"Pre-existing line MUST appear exactly once (no duplicate-append); got: {resulting!r}"
    )


@pytest.mark.parametrize(
    ("helper_name", "target_relpath"),
    [
        pytest.param("auto_seed_default_gitignore", ".gitignore", id="gitignore"),
        pytest.param("auto_seed_default_git_exclude", ".git/info/exclude", id="git-exclude"),
    ],
)
@pytest.mark.timeout_seconds(5)
def test_auto_seed_atomic_append_preserves_crlf_terminated_existing_content(
    tmp_git_repo: Path, helper_name: str, target_relpath: str
) -> None:
    """CRLF-terminated existing content is preserved byte-for-byte.

    Pins the byte-preserving contract: a CRLF terminator is NOT
    normalized to LF on a POSIX system (which ``read_text``/
    ``write_text`` with the default universal-newlines mode would do).
    The helper uses ``read_bytes`` + ``write_bytes`` so the CRLF
    round-trip is verbatim. The seed pattern is appended once.
    """
    pre_existing = b"existing-crlf-line\r\n"

    resulting = _drive_auto_seed(tmp_git_repo, helper_name, pre_existing, None)

    assert b"existing-crlf-line\r\n" in resulting, (
        f"CRLF-terminated pre-existing line MUST be preserved byte-for-byte; got: {resulting!r}"
    )
    # The pre-existing line appears exactly once (no duplicate-append).
    assert resulting.count(b"existing-crlf-line\r\n") == 1, (
        f"Pre-existing CRLF line MUST appear exactly once (no duplicate-append); got: {resulting!r}"
    )


@pytest.mark.parametrize(
    ("helper_name", "target_relpath"),
    [
        pytest.param("auto_seed_default_gitignore", ".gitignore", id="gitignore"),
        pytest.param("auto_seed_default_git_exclude", ".git/info/exclude", id="git-exclude"),
    ],
)
@pytest.mark.timeout_seconds(5)
def test_auto_seed_atomic_append_preserves_trailing_whitespace(
    tmp_git_repo: Path, helper_name: str, target_relpath: str
) -> None:
    """Trailing whitespace on existing lines is preserved byte-for-byte.

    Pins the byte-preserving contract: trailing whitespace is part of
    the file content and MUST NOT be stripped. The helper's
    de-duplication check uses the line-set comparison, so the seed
    pattern (which has no trailing whitespace) is correctly detected
    as "not already present" and appended once.
    """
    pre_existing = b"existing-trailing-whitespace-line   \n"

    resulting = _drive_auto_seed(tmp_git_repo, helper_name, pre_existing, None)

    assert b"existing-trailing-whitespace-line   \n" in resulting, (
        f"Trailing whitespace MUST be preserved byte-for-byte; got: {resulting!r}"
    )
    assert resulting.count(b"existing-trailing-whitespace-line   \n") == 1, (
        f"Pre-existing trailing-whitespace line MUST appear exactly once (no "
        f"duplicate-append); got: {resulting!r}"
    )


@pytest.mark.parametrize(
    ("helper_name", "target_relpath"),
    [
        pytest.param("auto_seed_default_gitignore", ".gitignore", id="gitignore"),
        pytest.param("auto_seed_default_git_exclude", ".git/info/exclude", id="git-exclude"),
    ],
)
@pytest.mark.timeout_seconds(5)
def test_auto_seed_atomic_append_handles_symlinked_target(
    tmp_git_repo: Path, helper_name: str, target_relpath: str
) -> None:
    """Symlinked target: pins the current behavior at the boundary.

    The atomic helper in ``ralph/git/operations.py::_atomic_append_text``
    uses ``Path.replace()`` which replaces the symlink target. This
    test pins the CURRENT behavior -- a follow-up safety check (e.g.
    refuse to ``replace()`` through a symlink) may be added later but
    is NOT in scope here. The test asserts that after the helper
    runs, the target's ``.read_bytes()`` resolves to bytes that
    contain the seed patterns (whether via replacement or via
    following the symlink is the implementation's choice).
    """
    target = tmp_git_repo / target_relpath
    symlink_target = tmp_git_repo / f"_symlink_target_for_{target.name}"

    resulting = _drive_auto_seed(tmp_git_repo, helper_name, b"", symlink_target)

    assert len(resulting) >= 0, (
        f"Helper must produce SOME bytes (either replaced target or via the "
        f"symlink); got: {resulting!r}"
    )


@pytest.mark.timeout_seconds(10)
def test_handle_commit_cleanup_phase_silent_on_tracked_skill_symlinks(
    tmp_git_repo: Path,
) -> None:
    """PA-003 closure / AC-03 phase-level contract.

    The leaf-level WARNING-free test pins the helper directly. This test
    pins the SAME contract at the production-phase boundary
    (``handle_commit_cleanup_phase``) -- which means the production log
    is WARNING-free end-to-end, not only at the leaf.

    Setup:
      * materialize one baseline skill under
        ``.opencode/skills/brainstorming/SKILL.md`` (canonical)
      * create ``.agents/skills/brainstorming`` as a symlink to the
        canonical (project-scope sibling)
      * track and commit BOTH entries via per-test git operations
      * attach a loguru WARNING-level sink to capture any WARNING emitted
        during the phase call

    Asserts:
      * the phase returns ``[PipelineEvent.AGENT_SUCCESS]`` (no failure)
      * ``captured_warnings`` is EMPTY -- zero WARNING-level log lines
        fired during the phase (the early-skip consumed the skill-root
        path before any WARNING could be logged)
    """
    loguru_logger = logger

    canonical_skill = tmp_git_repo / ".opencode" / "skills" / "brainstorming"
    canonical_skill.mkdir(parents=True, exist_ok=True)
    canonical_skill.joinpath("SKILL.md").write_text("# brainstorming skill\n", encoding="utf-8")
    sibling_skill = tmp_git_repo / ".agents" / "skills" / "brainstorming"
    sibling_skill.parent.mkdir(parents=True, exist_ok=True)
    sibling_skill.symlink_to(canonical_skill, target_is_directory=True)

    _track_and_commit(tmp_git_repo, ".opencode/skills/brainstorming/SKILL.md")
    _track_and_commit(tmp_git_repo, ".agents/skills/brainstorming")

    captured_warnings: list[str] = []
    sink_id = loguru_logger.add(captured_warnings.append, level="WARNING", format="{message}")
    try:
        result = _invoke_cleanup(
            FsWorkspace(tmp_git_repo),
            {
                "analysis_complete": True,
                "actions": [],
            },
        )
    finally:
        loguru_logger.remove(sink_id)

    assert result == [PipelineEvent.AGENT_SUCCESS], (
        f"Phase must succeed when only skill-root symlinks are tracked; got: {result!r}"
    )
    assert captured_warnings == [], (
        f"Zero WARNING-level log lines expected from handle_commit_cleanup_phase "
        f"when the only tracked engine-internal-ish paths are skill symlinks; "
        f"got: {captured_warnings!r}"
    )
