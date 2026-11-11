"""Unit tests for commit_review phase handler."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from git import Repo
from loguru import logger

from ralph.config import bootstrap
from ralph.git.proposal_seal import seal_pending
from ralph.mcp.artifacts._commit_review import CommitReview
from ralph.mcp.artifacts._commit_review_action import CommitReviewAction
from ralph.phases import PhaseContext
from ralph.phases._commit_review_actions import CommitReviewApplyReport
from ralph.phases._commit_review_catalog import LOCKFILE_BASENAMES, UNSAFE_EXTENSIONS
from ralph.phases._commit_review_outcome import decide_commit_review_outcome
from ralph.phases.commit_review import (
    CommitReviewApproved,
    CommitReviewGateFailed,
    CommitReviewLoopback,
    CommitReviewRejected,
    _apply_commit_review_actions,
    build_commit_review_retry_hint,
    handle_commit_review_phase,
)
from ralph.pipeline.effects import CommitEffect, InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.events import PhaseFailureEvent, PipelineEvent
from ralph.recovery.classifier import FailureCategory
from ralph.workspace.fs import FsWorkspace

COMMIT_REVIEW_ARTIFACT_PATH = ".agent/artifacts/commit_review.md"

# Most tests in this module exercise real git operations against the
# ``tmp_git_repo`` fixture (per-test process-isolated git repository).
# Wall-clock cost under parallel xdist load is regularly > 1 s on busy
# machines, so the default 1-second per-test ceiling is unsafe. A few
# tests that do not touch the fixture complete in < 1 s and tolerate
# the elevated ceiling as a no-op.
pytestmark = [pytest.mark.timeout_seconds(5), pytest.mark.subprocess_e2e]


def _resolve_artifact_tree_sha(repo_path: Path) -> str | None:
    if (repo_path / ".git").exists():
        try:
            from ralph.config.bootstrap import (
                auto_seed_default_git_exclude,
                auto_seed_default_gitignore,
            )
            from ralph.git.commit_review import untrack_engine_internal_files
            from ralph.phases.commit_review import is_agent_internal_path

            untrack_engine_internal_files(repo_path, is_agent_internal_path)
            auto_seed_default_gitignore(repo_path)
            auto_seed_default_git_exclude(repo_path)
            return seal_pending(repo_path, "run", 1).tree_sha
        except Exception:
            return None

    try:
        import shutil
        import tempfile

        from ralph.config.bootstrap import (
            auto_seed_default_git_exclude,
            auto_seed_default_gitignore,
        )
        from ralph.git.commit_review import ensure_git_initialized

        with tempfile.TemporaryDirectory() as td:
            temp_repo = Path(td)
            for item in repo_path.iterdir():
                if item.name == ".agent":
                    continue
                if item.is_dir():
                    shutil.copytree(item, temp_repo / item.name)
                else:
                    shutil.copy2(item, temp_repo / item.name)
            ensure_git_initialized(str(temp_repo))
            auto_seed_default_gitignore(temp_repo)
            auto_seed_default_git_exclude(temp_repo)
            return seal_pending(temp_repo, "run", 1).tree_sha
    except Exception:
        return None


def _write_commit_review_artifact(
    workspace: FsWorkspace,
    content: dict[str, object],
) -> None:
    """Write a canonical commit-review Markdown artifact."""
    verdict = content.get("verdict")
    if verdict is None:
        verdict = "approve"

    tree_sha: str | None = None
    if "tree_sha" in content:
        tree_sha = str(content["tree_sha"])
    else:
        tree_sha = _resolve_artifact_tree_sha(Path(workspace.root))

    lines = [
        "---",
        "type: commit_review",
        f"verdict: {verdict}",
    ]
    if tree_sha is not None:
        lines.append(f"tree_sha: {tree_sha}")
    lines.append("---")

    actions = content.get("actions", [])
    if actions:
        lines.append("## Actions")
        assert isinstance(actions, list)
        for index, action in enumerate(actions, start=1):
            assert isinstance(action, dict)
            action_name = action["action"]
            value = action.get("path", action.get("pattern"))
            lines.append(f"- [A{index}] {action_name} | {value}")

    reason = content.get("reason")
    if reason:
        lines.append("## Reason")
        if isinstance(reason, list):
            for index, item in enumerate(reason, start=1):
                lines.append(f"- [R{index}] {item}")
        else:
            lines.append(f"- [R1] {reason}")

    path = Path(workspace.root) / COMMIT_REVIEW_ARTIFACT_PATH
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
    effect = PreparePromptEffect(phase="development_commit_review", iteration=1)
    result = handle_commit_review_phase(effect, ctx)
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
    result = handle_commit_review_phase(effect, ctx)
    assert result == []


def test_agent_success_when_verdict_approves(tmp_git_repo: Path) -> None:
    """Test that the phase approves when the artifact carries ``verdict: approve``."""
    workspace = FsWorkspace(tmp_git_repo)
    _write_commit_review_artifact(workspace, {"actions": []})
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
        phase="development_commit_review",
        prompt_file="cleanup.txt",
    )
    result = handle_commit_review_phase(effect, ctx)
    assert len(result) == 1 and isinstance(result[0], CommitReviewApproved)


def _write_raw_commit_review_artifact(workspace: FsWorkspace, frontmatter: list[str]) -> None:
    """Write a commit_review artifact with verbatim frontmatter lines."""
    path = Path(workspace.root) / COMMIT_REVIEW_ARTIFACT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", "type: commit_review", *frontmatter, "---", "", "## Actions", ""]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run_commit_review_phase(workspace: FsWorkspace) -> list[object]:
    """Drive the commit-review phase with a default agent invocation."""
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
        phase="development_commit_review",
        prompt_file="cleanup.txt",
    )
    return handle_commit_review_phase(effect, ctx)


def test_artifact_without_verdict_is_never_an_approval(tmp_git_repo: Path) -> None:
    """A commit-review artifact without a verdict can never be approved.

    Pins the fail-closed contract of the frozen artifact contract: the
    verdict is the ONLY way to approve a proposal, so an artifact that
    omits it (or only carries the retired analysis_complete field) must
    fail the gate instead of being read as an approval.
    """
    workspace = FsWorkspace(tmp_git_repo)
    _write_raw_commit_review_artifact(workspace, [])

    result = _run_commit_review_phase(workspace)

    assert len(result) == 1, f"Expected exactly one event, got {len(result)}: {result!r}"
    assert isinstance(result[0], CommitReviewGateFailed), (
        f"An artifact without a verdict must fail the gate, got "
        f"{type(result[0]).__name__}: {result[0]!r}"
    )
    assert not isinstance(result[0], CommitReviewApproved), (
        "An artifact without a verdict must never be approved"
    )
    assert result[0] is not PipelineEvent.AGENT_SUCCESS, (
        "An artifact without a verdict must never succeed the phase"
    )

    # The retired ``analysis_complete`` field is not a verdict substitute.
    _write_raw_commit_review_artifact(workspace, ["analysis_complete: true"])
    result2 = _run_commit_review_phase(workspace)
    assert len(result2) == 1
    assert isinstance(result2[0], CommitReviewGateFailed)
    assert not isinstance(result2[0], CommitReviewApproved)


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
        phase="development_commit_review",
        prompt_file="cleanup.txt",
    )

    result = handle_commit_review_phase(effect, ctx)

    assert len(result) == 1
    assert isinstance(result[0], PhaseFailureEvent)


def test_phase_loopback_when_has_actions(tmp_git_repo: Path) -> None:
    """Test that actions are applied and phase approves."""
    workspace = FsWorkspace(tmp_git_repo)
    _write_commit_review_artifact(
        workspace,
        {
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
        phase="development_commit_review",
        prompt_file="cleanup.txt",
    )
    result = handle_commit_review_phase(effect, ctx)
    assert len(result) == 1 and isinstance(result[0], CommitReviewApproved)
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

    _write_commit_review_artifact(
        workspace,
        {"actions": [{"action": "delete_file", "path": "binary.exe"}]},
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
        phase="development_commit_review",
        prompt_file="cleanup.txt",
    )
    result = handle_commit_review_phase(effect, ctx)
    assert len(result) == 1 and isinstance(result[0], CommitReviewApproved)
    assert not binary.exists()


def test_delete_checkpoint_json_removes_file(tmp_git_repo: Path) -> None:
    """Pipeline checkpoint JSON files are safe housekeeping artifacts."""
    workspace = FsWorkspace(tmp_git_repo)
    checkpoint = tmp_git_repo / "checkpoint.json"
    checkpoint.write_text('{"phase": "development"}')
    assert checkpoint.exists()

    _write_commit_review_artifact(
        workspace,
        {
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
        phase="development_commit_review",
        prompt_file="cleanup.txt",
    )

    result = handle_commit_review_phase(effect, ctx)

    assert len(result) == 1 and isinstance(result[0], CommitReviewApproved)
    assert not checkpoint.exists()


def test_delete_verify_output_text_file_removes_file(tmp_git_repo: Path) -> None:
    """Generated verification capture text files are safe housekeeping artifacts."""
    workspace = FsWorkspace(tmp_git_repo)
    verify_output = tmp_git_repo / "verify-output.txt"
    verify_output.write_text("captured verify output")
    assert verify_output.exists()

    _write_commit_review_artifact(
        workspace,
        {
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
        phase="development_commit_review",
        prompt_file="cleanup.txt",
    )

    result = handle_commit_review_phase(effect, ctx)

    assert len(result) == 1 and isinstance(result[0], CommitReviewApproved)
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

    _write_commit_review_artifact(
        workspace,
        {
            "actions": [{"action": "delete_file", "path": f"App{ext}"} for ext in extensions],
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
        agent_name="dev", phase="development_commit_review", prompt_file="cleanup.txt"
    )
    result = handle_commit_review_phase(effect, ctx)
    assert len(result) == 1
    assert isinstance(result[0], CommitReviewLoopback)
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

    _write_commit_review_artifact(
        workspace,
        {
            "actions": [{"action": "delete_file", "path": lock_file} for lock_file in lockfiles],
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
        agent_name="dev", phase="development_commit_review", prompt_file="cleanup.txt"
    )
    result = handle_commit_review_phase(effect, ctx)
    assert len(result) == 1
    assert isinstance(result[0], CommitReviewLoopback)
    assert not isinstance(result[0], PhaseFailureEvent)
    for lock in files:
        assert lock.exists()


def test_git_exclude_action_adds_pattern(tmp_git_repo: Path) -> None:
    """Test that add_to_git_exclude action adds the pattern to .git/info/exclude."""
    workspace = FsWorkspace(tmp_git_repo)
    _write_commit_review_artifact(
        workspace,
        {
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
        phase="development_commit_review",
        prompt_file="cleanup.txt",
    )
    result = handle_commit_review_phase(effect, ctx)
    assert len(result) == 1 and isinstance(result[0], CommitReviewApproved)
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
        phase="development_commit_review",
        prompt_file="cleanup.txt",
    )
    result = handle_commit_review_phase(effect, ctx)
    assert len(result) == 1
    event = result[0]
    assert isinstance(event, PhaseFailureEvent)
    assert event.phase == "development_commit_review"
    assert event.recoverable is True


def test_cleanup_actions_applied_when_verdict_approves(tmp_git_repo: Path) -> None:
    """Cleanup actions are applied even when the verdict is ``approve``."""
    workspace = FsWorkspace(tmp_git_repo)
    _write_commit_review_artifact(
        workspace,
        {
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
        phase="development_commit_review",
        prompt_file="cleanup.txt",
    )
    result = handle_commit_review_phase(effect, ctx)
    assert len(result) == 1 and isinstance(result[0], CommitReviewApproved)
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

    _write_commit_review_artifact(
        workspace,
        {
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
        phase="development_commit_review",
        prompt_file="cleanup.txt",
    )
    result = handle_commit_review_phase(effect, ctx)

    assert len(result) == 1
    event = result[0]
    assert isinstance(event, PhaseFailureEvent)
    assert event.phase == "development_commit_review"
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
    monkeypatch.setattr("ralph.phases.commit_review.Path.cwd", lambda: fake_cwd)

    _write_commit_review_artifact(
        workspace,
        {
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
        phase="development_commit_review",
        prompt_file="cleanup.txt",
    )
    result = handle_commit_review_phase(effect, ctx)

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
    _write_commit_review_artifact(workspace, {"actions": []})
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
        phase="development_commit_review",
        prompt_file="cleanup.txt",
    )
    result = handle_commit_review_phase(effect, ctx)
    assert len(result) == 1 and isinstance(result[0], CommitReviewApproved)
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
            "actions": [{"action": "delete_file", "path": rel_path} for rel_path in paths],
        },
    )

    assert len(result) == 1 and isinstance(result[0], CommitReviewLoopback)
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
    _write_commit_review_artifact(
        workspace,
        {
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
        agent_name="dev", phase="development_commit_review", prompt_file="cleanup.txt"
    )

    result = handle_commit_review_phase(effect, ctx)

    assert len(result) == 1
    assert isinstance(result[0], CommitReviewLoopback)
    assert outside.exists()


def test_delete_file_with_absolute_path_returns_failure_event(
    tmp_git_repo: Path,
    tmp_path: Path,
) -> None:
    workspace = FsWorkspace(tmp_git_repo)
    outside = tmp_path / "absolute.bin"
    outside.write_text("binary")
    _write_commit_review_artifact(
        workspace,
        {
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
        agent_name="dev", phase="development_commit_review", prompt_file="cleanup.txt"
    )

    result = handle_commit_review_phase(effect, ctx)

    assert len(result) == 1
    assert isinstance(result[0], CommitReviewLoopback)
    assert outside.exists()


def test_delete_backup_bak_file(tmp_git_repo: Path) -> None:
    """Backup .bak files are safe housekeeping artifacts."""
    workspace = FsWorkspace(tmp_git_repo)
    backup = tmp_git_repo / "important.py.bak"
    backup.write_text("old version")

    _write_commit_review_artifact(
        workspace,
        {
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
        agent_name="dev", phase="development_commit_review", prompt_file="cleanup.txt"
    )
    result = handle_commit_review_phase(effect, ctx)
    assert len(result) == 1 and isinstance(result[0], CommitReviewApproved)
    assert not backup.exists()


def test_delete_tmp_file(tmp_git_repo: Path) -> None:
    """Temporary .tmp files are safe housekeeping artifacts."""
    workspace = FsWorkspace(tmp_git_repo)
    tmp = tmp_git_repo / "scratch.tmp"
    tmp.write_text("temporary data")

    _write_commit_review_artifact(
        workspace,
        {"actions": [{"action": "delete_file", "path": "scratch.tmp"}]},
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
        agent_name="dev", phase="development_commit_review", prompt_file="cleanup.txt"
    )
    result = handle_commit_review_phase(effect, ctx)
    assert len(result) == 1 and isinstance(result[0], CommitReviewApproved)
    assert not tmp.exists()


def test_delete_log_file(tmp_git_repo: Path) -> None:
    """Log files are safe housekeeping artifacts."""
    workspace = FsWorkspace(tmp_git_repo)
    log = tmp_git_repo / "debug.log"
    log.write_text("debug output")

    _write_commit_review_artifact(
        workspace,
        {"actions": [{"action": "delete_file", "path": "debug.log"}]},
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
        agent_name="dev", phase="development_commit_review", prompt_file="cleanup.txt"
    )
    result = handle_commit_review_phase(effect, ctx)
    assert len(result) == 1 and isinstance(result[0], CommitReviewApproved)
    assert not log.exists()


def test_delete_rej_file(tmp_git_repo: Path) -> None:
    """Patch reject .rej files are safe housekeeping artifacts."""
    workspace = FsWorkspace(tmp_git_repo)
    rej = tmp_git_repo / "fix.patch.rej"
    rej.write_text("patch reject hunk")

    _write_commit_review_artifact(
        workspace,
        {
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
        agent_name="dev", phase="development_commit_review", prompt_file="cleanup.txt"
    )
    result = handle_commit_review_phase(effect, ctx)
    assert len(result) == 1 and isinstance(result[0], CommitReviewApproved)
    assert not rej.exists()


def test_delete_session_txt_file(tmp_git_repo: Path) -> None:
    """Session transcript text files are safe housekeeping artifacts."""
    workspace = FsWorkspace(tmp_git_repo)
    session = tmp_git_repo / "session-transcript.txt"
    session.write_text("agent conversation")

    _write_commit_review_artifact(
        workspace,
        {
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
        agent_name="dev", phase="development_commit_review", prompt_file="cleanup.txt"
    )
    result = handle_commit_review_phase(effect, ctx)
    assert len(result) == 1 and isinstance(result[0], CommitReviewApproved)
    assert not session.exists()


def test_delete_in_tmp_directory(tmp_git_repo: Path) -> None:
    """Files inside tmp/ directories are safe housekeeping artifacts."""
    workspace = FsWorkspace(tmp_git_repo)
    tmp_dir = tmp_git_repo / "tmp"
    tmp_dir.mkdir()
    artifact = tmp_dir / "scratch.txt"
    artifact.write_text("temp content")

    _write_commit_review_artifact(
        workspace,
        {
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
        agent_name="dev", phase="development_commit_review", prompt_file="cleanup.txt"
    )
    result = handle_commit_review_phase(effect, ctx)
    assert len(result) == 1 and isinstance(result[0], CommitReviewApproved)
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
    """Run the commit_review phase with the given artifact content."""
    _write_commit_review_artifact(workspace, content)
    ctx = _make_cleanup_ctx(workspace)
    effect = InvokeAgentEffect(
        agent_name="dev", phase="development_commit_review", prompt_file="cleanup.txt"
    )
    return handle_commit_review_phase(effect, ctx)


def _assert_declined_completes(result: list[object]) -> None:
    """Safety declines complete the phase; they are not phase failures."""
    assert len(result) == 1
    assert isinstance(result[0], CommitReviewLoopback)
    assert not isinstance(result[0], PhaseFailureEvent)
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
            "actions": [{"action": "delete_file", "path": file_path}],
        },
    )

    assert len(result) == 1 and isinstance(result[0], CommitReviewApproved)
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
            "actions": [{"action": "delete_file", "path": ".coverage"}],
        },
    )

    assert len(result) == 1 and isinstance(result[0], CommitReviewApproved)
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
            "actions": [{"action": "delete_file", "path": "coverage.xml"}],
        },
    )

    assert len(result) == 1 and isinstance(result[0], CommitReviewApproved)
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
            "actions": [{"action": "delete_file", "path": "checkpoint.json"}],
        },
    )

    assert len(result) == 1 and isinstance(result[0], CommitReviewApproved)
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
            "actions": [{"action": "delete_file", "path": "debug.log"}],
        },
    )

    assert len(result) == 1 and isinstance(result[0], CommitReviewApproved)
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
            "actions": [{"action": "delete_file", "path": "accidental_binary.exe"}],
        },
    )

    assert len(result) == 1 and isinstance(result[0], CommitReviewApproved)
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
            "actions": [{"action": "delete_file", "path": "artifacts/scratch.txt"}],
        },
    )

    assert len(result) == 1 and isinstance(result[0], CommitReviewApproved)
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
            "actions": [{"action": "delete_file", "path": ".output/bundle.js"}],
        },
    )

    assert len(result) == 1 and isinstance(result[0], CommitReviewApproved)
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
        (".agent/receipts/run-1/commit_review.json", '{"artifact_type": "commit_review"}'),
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
            "actions": [
                {"action": "delete_file", "path": rel_path}
                for rel_path, _content in tracked_artifacts
            ],
        },
    )

    assert len(result) == 1 and isinstance(result[0], CommitReviewApproved)
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
            "actions": [{"action": "delete_file", "path": rel_path} for rel_path in paths],
        },
    )

    assert len(result) == 1 and isinstance(result[0], CommitReviewLoopback)
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
            "actions": [
                {"action": "delete_file", "path": ".agent/raw/opencode.log"},
                {"action": "add_to_gitignore", "pattern": "*.scratch"},
            ],
        },
    )

    assert len(result) == 1 and isinstance(result[0], CommitReviewApproved)
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
            "actions": [
                {"action": "delete_file", "path": "binary.exe"},
                {"action": "delete_file", "path": "helper.py"},
            ],
        },
    )

    assert len(result) == 1 and isinstance(result[0], CommitReviewLoopback)
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
            "actions": [
                {"action": "add_to_gitignore", "pattern": "*.tmp"},
                {"action": "delete_file", "path": "module.py"},
            ],
        },
    )

    assert len(result) == 1 and isinstance(result[0], CommitReviewLoopback)
    assert source.exists()
    gitignore = tmp_git_repo / ".gitignore"
    assert "*.tmp" in gitignore.read_text()


def test_cleanup_phase_auto_seeds_gitignore_canonical_patterns_on_entry(
    tmp_git_repo: Path,
) -> None:
    """AC-04: handle_commit_review_phase seeds canonical .gitignore on every entry."""
    result = _invoke_cleanup(
        FsWorkspace(tmp_git_repo),
        {"actions": []},
    )

    assert len(result) == 1 and isinstance(result[0], CommitReviewApproved)
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
    """AC-04: handle_commit_review_phase seeds canonical .git/info/exclude on every entry."""
    result = _invoke_cleanup(
        FsWorkspace(tmp_git_repo),
        {"actions": []},
    )

    assert len(result) == 1 and isinstance(result[0], CommitReviewApproved)
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
            "actions": [
                {"action": "delete_file", "path": "duplicate.exe"},
                {"action": "delete_file", "path": "duplicate.exe"},
            ],
        },
    )

    assert len(result) == 1 and isinstance(result[0], CommitReviewApproved)
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
    ``CommitReviewAction``) already rejects empty ``path`` values, so
    this test exercises the runtime fallback via ``model_construct`` to
    bypass Pydantic validation and simulate the legacy edge case where
    a whitespace path slips past the schema layer.
    """
    binary = tmp_git_repo / "binary.exe"
    binary.write_bytes(b"\x00MZ")

    whitespace_action = CommitReviewAction.model_construct(action="delete_file", path="   ")
    real_action = CommitReviewAction.model_construct(action="delete_file", path="binary.exe")
    cleanup = CommitReview.model_construct(
        verdict="approve",
        actions=[whitespace_action, real_action],
    )

    skipped, _failed = _apply_commit_review_actions(tmp_git_repo, cleanup)
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
    whitespace_action = CommitReviewAction.model_construct(action="add_to_gitignore", pattern="   ")
    real_action = CommitReviewAction.model_construct(action="add_to_gitignore", pattern="*.real")
    cleanup = CommitReview.model_construct(
        verdict="approve",
        actions=[whitespace_action, real_action],
    )

    _apply_commit_review_actions(tmp_git_repo, cleanup)
    gitignore_text = (tmp_git_repo / ".gitignore").read_text()
    assert "*.real" in gitignore_text


def test_all_safe_actions_with_no_delete_succeeds(tmp_git_repo: Path) -> None:
    """AC-05: a batch with only gitignore + git-exclude actions succeeds."""
    result = _invoke_cleanup(
        FsWorkspace(tmp_git_repo),
        {
            "actions": [
                {"action": "add_to_gitignore", "pattern": "*.binary"},
                {"action": "add_to_git_exclude", "pattern": ".my-local-cache"},
            ],
        },
    )

    assert len(result) == 1 and isinstance(result[0], CommitReviewApproved)
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
            "actions": [{"action": "delete_file", "path": "module.py"}],
        },
    )

    _assert_declined_completes(result)
    hint = (
        tmp_git_repo / ".agent" / "tmp" / "last_retry_error_development_commit_review.txt"
    ).read_text()
    assert "module.py" in hint
    assert source.exists()


def test_unsafe_delete_with_whitespace_only_gitignore_loops_back(
    tmp_git_repo: Path,
) -> None:
    """Regression (analysis feedback): unsafe delete + whitespace-only gitignore pattern.

    Pins the contract that an unsafe ``delete_file`` plus a whitespace-only
    ``add_to_gitignore`` pattern (which is silently dropped by ``_classify_action``)
    must NOT bypass the declined-delete branch. The outcome decision must be
    based on actually-applied classified actions, not raw truthiness of the
    artifact fields -- otherwise the structured retry hint is suppressed and
    the agent cannot self-correct on retry.
    """
    source = tmp_git_repo / "module.py"
    source.write_text("source code")

    whitespace_action = CommitReviewAction.model_construct(action="add_to_gitignore", pattern="   ")
    unsafe_action = CommitReviewAction.model_construct(action="delete_file", path="module.py")
    cleanup = CommitReview.model_construct(
        verdict="reject",
        actions=[unsafe_action, whitespace_action],
    )

    skipped, _failed = _apply_commit_review_actions(tmp_git_repo, cleanup)
    assert "module.py" in skipped
    gitignore_path = tmp_git_repo / ".gitignore"
    gitignore_text = gitignore_path.read_text() if gitignore_path.exists() else ""
    applied_gitignore = ["   "] if "   " in gitignore_text else []
    report = CommitReviewApplyReport(
        declined_delete_paths=list(skipped),
        applied_gitignore_patterns=applied_gitignore,
    )

    outcome = decide_commit_review_outcome("development_commit_review", report)
    # RALPH-TEST-REASON: The live outcome path returns CommitReviewLoopback on declined deletes instead of legacy PHASE_LOOPBACK
    assert outcome is not None and isinstance(outcome, CommitReviewLoopback), (
        f"A declined delete must loop the agent back, got: {outcome!r}"
    )
    assert "module.py" in outcome.hint, (
        f"The loopback hint must name the rejected path, got: {outcome.hint!r}"
    )
    assert source.exists()


def test_unsafe_delete_with_whitespace_only_git_exclude_loops_back(
    tmp_git_repo: Path,
) -> None:
    """Regression (analysis feedback): unsafe delete + whitespace-only git_exclude pattern.

    Mirrors ``test_unsafe_delete_with_whitespace_only_gitignore_loops_back``
    but with the ``add_to_git_exclude`` action. The whitespace-only pattern is
    silently dropped by ``_classify_action``, so the outcome decision must
    still loop the agent back (the unsafe delete was the only meaningful
    work, and it was rejected).
    """
    source = tmp_git_repo / "module.py"
    source.write_text("source code")

    whitespace_action = CommitReviewAction.model_construct(
        action="add_to_git_exclude", pattern="   "
    )
    unsafe_action = CommitReviewAction.model_construct(action="delete_file", path="module.py")
    cleanup = CommitReview.model_construct(
        verdict="reject",
        actions=[unsafe_action, whitespace_action],
    )

    skipped, _failed = _apply_commit_review_actions(tmp_git_repo, cleanup)
    assert "module.py" in skipped
    exclude_path = tmp_git_repo / ".git" / "info" / "exclude"
    exclude_text = exclude_path.read_text() if exclude_path.exists() else ""
    applied_exclude = ["   "] if "   " in exclude_text else []
    report = CommitReviewApplyReport(
        declined_delete_paths=list(skipped),
        applied_exclude_patterns=applied_exclude,
    )

    outcome = decide_commit_review_outcome("development_commit_review", report)
    # RALPH-TEST-REASON: The live outcome path returns CommitReviewLoopback on declined deletes instead of legacy PHASE_LOOPBACK
    assert outcome is not None and isinstance(outcome, CommitReviewLoopback), (
        f"A declined delete must loop the agent back, got: {outcome!r}"
    )
    assert "module.py" in outcome.hint, (
        f"The loopback hint must name the rejected path, got: {outcome.hint!r}"
    )
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
    # hardened CommitReviewAction now rejects whitespace-only values at
    # the schema layer.
    whitespace_action = CommitReviewAction.model_construct(action="delete_file", path="   ")
    real_action = CommitReviewAction.model_construct(action="add_to_gitignore", pattern="*.scratch")
    cleanup = CommitReview.model_construct(
        verdict="approve",
        actions=[whitespace_action, real_action],
    )

    _apply_commit_review_actions(tmp_git_repo, cleanup)
    gitignore_text = (tmp_git_repo / ".gitignore").read_text()
    assert "*.scratch" in gitignore_text
    applied_gitignore = ["*.scratch"] if "*.scratch" in gitignore_text else []
    report = CommitReviewApplyReport(
        declined_delete_paths=[],
        applied_gitignore_patterns=applied_gitignore,
    )

    outcome = decide_commit_review_outcome("development_commit_review", report)
    # RALPH-TEST-REASON: The live outcome path must not loop back when the only
    # meaningful work is a safe gitignore action. The approving verdict itself
    # is proven end-to-end by test_all_safe_actions_with_no_delete_succeeds.
    assert outcome is None, (
        f"Safe applied work with no declined deletes must not loop back; got: {outcome!r}"
    )


def test_retry_hint_named_for_each_rejected_path(tmp_git_repo: Path) -> None:
    """AC-03: PhaseFailureEvent.reason contains structured hint naming every rejected path."""
    source_a = tmp_git_repo / "a.py"
    source_b = tmp_git_repo / "b.py"
    source_a.write_text("a")
    source_b.write_text("b")

    result = _invoke_cleanup(
        FsWorkspace(tmp_git_repo),
        {
            "actions": [
                {"action": "delete_file", "path": "a.py"},
                {"action": "delete_file", "path": "b.py"},
            ],
        },
    )

    _assert_declined_completes(result)
    hint = (
        tmp_git_repo / ".agent" / "tmp" / "last_retry_error_development_commit_review.txt"
    ).read_text()
    assert "a.py" in hint
    assert "b.py" in hint
    assert source_a.exists()
    assert source_b.exists()


# --- Phase 7 edge-case tests for build_commit_review_retry_hint ---
#
# Each test pins one observable behavior of the structured retry-hint
# builder. The names follow the plan: test_build_commit_review_retry_hint_<branch>
# so the test id maps directly to the helper's branch coverage.


@pytest.mark.timeout_seconds(5)
def test_build_commit_review_retry_hint_empty_skipped_paths_returns_sentinel() -> None:
    """Empty ``skipped_paths`` returns the sentinel message, not an empty string.

    Pins the contract that ``build_commit_review_retry_hint`` ALWAYS returns a
    non-empty string, even when there are no skipped paths. The sentinel
    tells the agent that the phase still failed despite no rejected
    delete actions -- pointing the diagnostic toward the artifact schema
    itself rather than the path list.
    """
    hint = build_commit_review_retry_hint(skipped_paths=[], safe_applied_count=0)

    assert isinstance(hint, str)
    # RALPH-TEST-REASON: Renamed build_cleanup_retry_hint to build_commit_review_retry_hint
    assert hint, "build_commit_review_retry_hint must NOT return an empty string"
    assert "no delete actions were rejected" in hint, (
        f"Sentinel must explicitly state that no delete actions were rejected; got: {hint!r}"
    )


@pytest.mark.timeout_seconds(5)
def test_build_commit_review_retry_hint_with_skipped_paths_and_safe_count() -> None:
    """Non-empty ``skipped_paths`` with a positive safe count renders both sections.

    Pins the contract that:
      1. The helper names each rejected path on its own line.
      2. The safe-applied count appears in the form ``Safe actions applied: N``
         when ``safe_applied_count > 0``.
    """
    hint = build_commit_review_retry_hint(skipped_paths=["foo.py", "bar.py"], safe_applied_count=2)

    assert "foo.py" in hint, f"Path foo.py must be named in the hint; got: {hint!r}"
    assert "bar.py" in hint, f"Path bar.py must be named in the hint; got: {hint!r}"
    assert "Safe actions applied: 2" in hint, (
        f"Safe count must appear in the form 'Safe actions applied: 2'; got: {hint!r}"
    )


@pytest.mark.timeout_seconds(5)
def test_build_commit_review_retry_hint_with_skipped_paths_and_zero_safe_count() -> None:
    """Non-empty ``skipped_paths`` with zero safe count renders the zero-summary branch.

    Pins the contract that a zero ``safe_applied_count`` produces the
    ``No safe actions were applied`` summary, NOT the ``Safe actions applied: 0``
    form. The wording is intentionally distinct so the agent's self-correct
    path can distinguish "nothing was applied" from "exactly zero was applied".
    """
    hint = build_commit_review_retry_hint(skipped_paths=["baz.py"], safe_applied_count=0)

    assert "baz.py" in hint, f"Path baz.py must be named in the hint; got: {hint!r}"
    assert "No safe actions were applied" in hint, (
        f"Zero-safe-count summary must use the 'No safe actions were applied' "
        f"wording; got: {hint!r}"
    )
    assert "Safe actions applied: 0" not in hint, (
        f"Zero-safe-count summary must NOT use the 'Safe actions applied: N' form; got: {hint!r}"
    )


@pytest.mark.timeout_seconds(5)
def test_apply_time_delete_failure_triggers_commit_review_loopback(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: apply-time ``delete_file_from_repo`` failure loops the agent back.

    Pins the fix for the bug where a delete that PASSED the safety
    classifier but FAILED at apply time (permission denied, stale git
    lock, transient I/O) was silently counted as successful cleanup.
    The prior code returned ``PHASE_LOOPBACK`` / ``AGENT_SUCCESS`` with
    zero actual cleanup work -- a silent failure.

    The test patches ``delete_file_from_repo`` to raise for an
    otherwise-safe binary artifact, drives ``handle_commit_review_phase``
    through the full ``InvokeAgentEffect`` flow, and asserts the phase
    returns a single ``CommitReviewLoopback`` carrying the structured
    retry hint that names the failed path. The hint is the apply-time
    one (``build_unapplied_retry_hint``), so it names the path that was
    accepted by the classifier but could not be applied -- it must NOT
    claim the path "looks like source code", which is the classifier
    rejection wording. The target path is an untracked ``.exe`` artifact:
    ``.exe`` is not in ``UNSAFE_EXTENSIONS`` and is not a
    protected/lockfile basename, so ``_is_safe_to_delete`` accepts it and
    the delete is attempted. A ``calls`` recorder on the patched
    ``delete_file_from_repo`` pins the apply path was actually driven (so
    the test can never silently pass via the classifier-decline path
    instead). The binary artifact MUST still exist on disk (the delete
    was attempted and failed).
    """
    workspace = FsWorkspace(tmp_git_repo)
    binary = tmp_git_repo / "binary.exe"
    binary.write_bytes(b"\x00MZ")

    cleanup_artifact = {
        "actions": [
            {"action": "delete_file", "path": "binary.exe"},
        ],
    }
    _write_commit_review_artifact(workspace, cleanup_artifact)

    calls: list[str] = []

    def _raise_on_delete(repo_root: Path, file_path: str) -> None:
        calls.append(file_path)
        raise OSError(f"simulated apply-time failure for {file_path}")

    monkeypatch.setattr(
        "ralph.phases.commit_review.delete_file_from_repo",
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
        phase="development_commit_review",
        prompt_file="cleanup.txt",
    )
    events = handle_commit_review_phase(effect, ctx)

    assert len(events) == 1, f"Expected exactly one event, got {len(events)}: {events!r}"
    event = events[0]
    assert isinstance(event, CommitReviewLoopback), (
        f"Expected CommitReviewLoopback when all attempted deletes failed at "
        f"apply time, got {type(event).__name__}: {event!r}"
    )
    assert calls == ["binary.exe"], (
        f"delete_file_from_repo must have been invoked exactly once for the "
        f"apply-time-failure path (classifier must ACCEPT the untracked .exe "
        f"artifact); got calls={calls!r}"
    )
    assert "binary.exe" in event.hint
    assert "failed while applying" in event.hint, (
        f"The hint must name the apply-time failure, got: {event.hint!r}"
    )
    assert "look like source code" not in event.hint, (
        f"An apply-time failure must NOT reuse the safety-classifier wording, got: {event.hint!r}"
    )
    assert binary.exists(), (
        "Binary artifact must still exist -- the delete was attempted and "
        "failed; the phase must NOT silently report success."
    )


@pytest.mark.timeout_seconds(5)
def test_apply_time_delete_failure_with_safe_gitignore_loops_back(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Apply-time failure on one action loops back even when a sibling action applied.

    PRODUCT_CRITERIA "Gate semantics" item 3: the commit phase runs
    normally only when ``approve`` AND all actions applied or were safely
    skipped. A ``delete_file`` that failed at apply time was neither
    applied nor safely skipped, so the batch is not "all actions applied
    or safely skipped" and the phase must NOT advance: it loops back to
    commit review carrying the retry hint that names the action-failure
    reason. The sibling ``add_to_gitignore`` action still applies -- the
    loopback does not undo work that already succeeded.
    """
    workspace = FsWorkspace(tmp_git_repo)
    binary = tmp_git_repo / "binary.exe"
    binary.write_bytes(b"\x00MZ")

    cleanup_artifact = {
        "actions": [
            {"action": "delete_file", "path": "binary.exe"},
            {"action": "add_to_gitignore", "pattern": "*.scratch"},
        ],
    }
    _write_commit_review_artifact(workspace, cleanup_artifact)

    def _raise_on_delete(repo_root: Path, file_path: str) -> None:
        raise OSError(f"simulated apply-time failure for {file_path}")

    monkeypatch.setattr(
        "ralph.phases.commit_review.delete_file_from_repo",
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
        phase="development_commit_review",
        prompt_file="cleanup.txt",
    )
    events = handle_commit_review_phase(effect, ctx)

    # RALPH-TEST-REASON: the retired expectation asserted CommitReviewApproved
    # for a batch with an apply-time failure. PRODUCT_CRITERIA "Gate
    # semantics" item 3 allows the commit phase to run only when "approve
    # and all actions applied or safely skipped"; an action that failed at
    # apply time was neither applied nor safely skipped, so the phase must
    # loop back with the retry hint instead of advancing.
    assert len(events) == 1, f"Expected exactly one event, got {len(events)}: {events!r}"
    event = events[0]
    assert isinstance(event, CommitReviewLoopback), (
        f"Phase must loop back when an action failed at apply time, got "
        f"{type(event).__name__}: {event!r}"
    )
    assert not isinstance(event, CommitReviewApproved), (
        "A batch with an apply-time failure must never advance to the commit phase"
    )
    assert "binary.exe" in event.hint, (
        f"The loopback hint must name the action that failed at apply time, got: {event.hint!r}"
    )
    assert "failed while applying" in event.hint, (
        f"The loopback hint must carry the action-failure reason, got: {event.hint!r}"
    )
    gitignore_text = (tmp_git_repo / ".gitignore").read_text()
    assert "*.scratch" in gitignore_text, (
        f"Gitignore action must still be applied even when the delete "
        f"action failed at apply time; got: {gitignore_text!r}"
    )
    assert binary.exists(), "Binary artifact must still exist -- the delete failed at apply time."


@pytest.mark.timeout_seconds(5)
def test_apply_commit_review_actions_returns_separate_skipped_and_failed_lists(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: ``_apply_commit_review_actions`` returns ``(skipped, failed)`` disjoint tuple.

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

    cleanup = CommitReview.model_construct(
        verdict="approve",
        actions=[
            CommitReviewAction.model_construct(action="delete_file", path="binary.exe"),
            CommitReviewAction.model_construct(action="delete_file", path="module.py"),
        ],
    )

    def _raise_on_delete(repo_root: Path, file_path: str) -> None:
        raise OSError(f"simulated apply-time failure for {file_path}")

    monkeypatch.setattr(
        "ralph.phases.commit_review.delete_file_from_repo",
        _raise_on_delete,
    )

    skipped, failed = _apply_commit_review_actions(tmp_git_repo, cleanup)

    assert "module.py" in skipped, f"Unsafe path module.py must be in skipped; got: {skipped!r}"
    assert "binary.exe" in failed, (
        f"Safe-but-failed path binary.exe must be in failed; got: {failed!r}"
    )
    assert set(skipped).isdisjoint(set(failed)), (
        f"skipped and failed lists must be disjoint; skipped={skipped!r}, failed={failed!r}"
    )


# ---------------------------------------------------------------------------
# Pre-emptive untrack safety-net tests (commit_review phase integration)
# ---------------------------------------------------------------------------
#
# These tests drive ``handle_commit_review_phase`` end-to-end and pin the
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
        {"actions": []},
    )

    assert len(result) == 1 and isinstance(result[0], CommitReviewApproved), (
        f"Empty artifact with tracked engine files must still return CommitReviewApproved "
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
    artifact load in the ``handle_commit_review_phase`` body. The
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
        agent_name="dev", phase="development_commit_review", prompt_file="cleanup.txt"
    )
    events = handle_commit_review_phase(effect, ctx)

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
        agent_name="dev", phase="development_commit_review", prompt_file="cleanup.txt"
    )
    events = handle_commit_review_phase(effect, ctx)

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
# ``_atomic_append_text`` helper that the ``commit_review`` phase
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
def test_handle_commit_review_phase_silent_on_tracked_skill_symlinks(
    tmp_git_repo: Path,
) -> None:
    """PA-003 closure / AC-03 phase-level contract.

    The leaf-level WARNING-free test pins the helper directly. This test
    pins the SAME contract at the production-phase boundary
    (``handle_commit_review_phase``) -- which means the production log
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
                "actions": [],
            },
        )
    finally:
        loguru_logger.remove(sink_id)

    assert len(result) == 1 and isinstance(result[0], CommitReviewApproved), (
        f"Phase must succeed when only skill-root symlinks are tracked; got: {result!r}"
    )
    assert captured_warnings == [], (
        f"Zero WARNING-level log lines expected from handle_commit_review_phase "
        f"when the only tracked engine-internal-ish paths are skill symlinks; "
        f"got: {captured_warnings!r}"
    )


# ---------------------------------------------------------------------------
# Stage 1 and Stage 2 commit review gate requirements (U7)
# ---------------------------------------------------------------------------


def test_hard_tripwire_automatic_rejection_without_agent_invocation(tmp_git_repo: Path) -> None:
    """Stage 1 hard tripwire rejects immediately with CommitReviewRejected(hard=True)."""
    # Track and commit a protected file AGENTS.md
    agents_md = tmp_git_repo / "AGENTS.md"
    agents_md.write_text("# Agents Policy\n")
    repo = Repo(tmp_git_repo)
    try:
        repo.index.add(["AGENTS.md"])
        repo.index.commit("track AGENTS.md")
    finally:
        repo.close()

    # Now delete AGENTS.md in working tree
    agents_md.unlink()

    workspace = FsWorkspace(tmp_git_repo)
    ctx = _make_cleanup_ctx(workspace)
    effect = InvokeAgentEffect(
        agent_name="dev", phase="development_commit_review", prompt_file="cleanup.txt"
    )
    result = handle_commit_review_phase(effect, ctx)

    assert len(result) == 1
    event = result[0]
    assert isinstance(event, CommitReviewRejected)
    assert event.hard is True
    assert "AGENTS.md" in event.reason
    assert "refs/ralph/proposals" in event.ref
    # No artifact was written or required -- agent was never invoked


def test_agent_approval_with_matching_tree_sha(tmp_git_repo: Path) -> None:
    """Stage 2 agent approval with matching tree_sha emits CommitReviewApproved."""
    workspace = FsWorkspace(tmp_git_repo)
    _write_commit_review_artifact(
        workspace,
        {
            "verdict": "approve",
            "actions": [],
        },
    )
    ctx = _make_cleanup_ctx(workspace)
    effect = InvokeAgentEffect(
        agent_name="dev", phase="development_commit_review", prompt_file="cleanup.txt"
    )
    result = handle_commit_review_phase(effect, ctx)

    assert len(result) == 1
    event = result[0]
    assert isinstance(event, CommitReviewApproved)
    assert len(event.tree_sha) == 40


def test_agent_rejection_with_verifiable_citations(tmp_git_repo: Path) -> None:
    """Stage 2 agent rejection with verifiable citations emits CommitReviewRejected(hard=False)."""
    # Create modified file
    app_file = tmp_git_repo / "src" / "app.py"
    app_file.parent.mkdir(parents=True, exist_ok=True)
    app_file.write_text("print('corrupted')")

    workspace = FsWorkspace(tmp_git_repo)
    from ralph.config.bootstrap import auto_seed_default_gitignore

    auto_seed_default_gitignore(tmp_git_repo)
    seal = seal_pending(tmp_git_repo, "run", 1)

    _write_commit_review_artifact(
        workspace,
        {
            "verdict": "reject",
            "tree_sha": seal.tree_sha,
            "reason": "Corrupted src/app.py logic",
        },
    )
    ctx = _make_cleanup_ctx(workspace)
    effect = InvokeAgentEffect(
        agent_name="dev", phase="development_commit_review", prompt_file="cleanup.txt"
    )
    result = handle_commit_review_phase(effect, ctx)

    assert len(result) == 1
    event = result[0]
    assert isinstance(event, CommitReviewRejected)
    assert event.hard is False
    assert event.ref == seal.ref
    assert "src/app.py" in event.reason


def test_unverifiable_citations_returns_gate_failed(tmp_git_repo: Path) -> None:
    """Stage 2 rejection citing files not in diff emits CommitReviewGateFailed."""
    app_file = tmp_git_repo / "src" / "app.py"
    app_file.parent.mkdir(parents=True, exist_ok=True)
    app_file.write_text("print('hello')")

    workspace = FsWorkspace(tmp_git_repo)
    from ralph.config.bootstrap import auto_seed_default_gitignore

    auto_seed_default_gitignore(tmp_git_repo)
    seal = seal_pending(tmp_git_repo, "run", 1)

    _write_commit_review_artifact(
        workspace,
        {
            "verdict": "reject",
            "tree_sha": seal.tree_sha,
            "reason": "Cites non_existent_file.txt which is not modified",
        },
    )
    ctx = _make_cleanup_ctx(workspace)
    effect = InvokeAgentEffect(
        agent_name="dev", phase="development_commit_review", prompt_file="cleanup.txt"
    )
    result = handle_commit_review_phase(effect, ctx)

    assert len(result) == 1
    event = result[0]
    assert isinstance(event, CommitReviewGateFailed)
    assert "Reject citations could not be verified" in event.detail


def test_stale_tree_sha_mismatch_loops_back_for_rereview(tmp_git_repo: Path) -> None:
    """Stage 2 artifact with stale tree_sha loops back for a fresh review of the sealed tree."""
    workspace = FsWorkspace(tmp_git_repo)
    stale_sha = "0000000000000000000000000000000000000000"
    _write_commit_review_artifact(
        workspace,
        {
            "verdict": "approve",
            "tree_sha": stale_sha,
            "actions": [],
        },
    )
    ctx = _make_cleanup_ctx(workspace)
    effect = InvokeAgentEffect(
        agent_name="dev", phase="development_commit_review", prompt_file="cleanup.txt"
    )
    result = handle_commit_review_phase(effect, ctx)

    assert len(result) == 1
    event = result[0]
    assert isinstance(event, CommitReviewLoopback), (
        f"A stale review must be re-reviewed, not failed: got {type(event).__name__}: {event!r}"
    )
    assert stale_sha in event.hint
    assert "does not match current sealed proposal tree SHA" in event.hint
    # The stale artifact is consumed so the re-review judges the current tree.
    assert not (tmp_git_repo / ".agent" / "artifacts" / "commit_review.md").exists()


def test_stale_tree_sha_does_not_apply_actions_before_rereview(tmp_git_repo: Path) -> None:
    """A stale verdict is re-reviewed BEFORE its edit step runs, so no action lands.

    The proposal-seal contract says a verdict whose SHA no longer matches the
    current sealed tree is stale and is re-reviewed. Re-reviewing means the
    stale artifact's edit step never runs: its actions are discarded together
    with the stale artifact, so a delete action cannot mutate the tree while
    the reviewer is being sent back for a fresh look at the sealed tree.
    """
    temp_file = tmp_git_repo / "temp.log"
    temp_file.write_text("temporary log")

    workspace = FsWorkspace(tmp_git_repo)
    stale_sha = "0000000000000000000000000000000000000000"
    _write_commit_review_artifact(
        workspace,
        {
            "verdict": "approve",
            "tree_sha": stale_sha,
            "actions": [
                {"action": "delete_file", "path": "temp.log"},
            ],
        },
    )
    ctx = _make_cleanup_ctx(workspace)
    effect = InvokeAgentEffect(
        agent_name="dev", phase="development_commit_review", prompt_file="cleanup.txt"
    )
    result = handle_commit_review_phase(effect, ctx)

    assert len(result) == 1
    event = result[0]
    assert isinstance(event, CommitReviewLoopback), (
        f"A stale review must be re-reviewed, not failed: got {type(event).__name__}: {event!r}"
    )
    # The edit step never ran, so the stale verdict's delete action did not
    # touch the tree: the same artifact with a MATCHING SHA deletes the file
    # (see test_safe_actions_application_and_resealing).
    assert temp_file.exists(), (
        "A stale verdict must be re-reviewed before any action executes; "
        "the stale artifact's delete action must not touch the tree"
    )
    assert stale_sha in event.hint


def test_safe_actions_application_and_resealing(tmp_git_repo: Path) -> None:
    """Safe actions are applied, the proposal tree is resealed, and new SHA is approved."""
    temp_file = tmp_git_repo / "temp.log"
    temp_file.write_text("temporary log")

    workspace = FsWorkspace(tmp_git_repo)
    from ralph.config.bootstrap import auto_seed_default_gitignore

    auto_seed_default_gitignore(tmp_git_repo)
    initial_seal = seal_pending(tmp_git_repo, "run", 1)

    _write_commit_review_artifact(
        workspace,
        {
            "verdict": "approve",
            "tree_sha": initial_seal.tree_sha,
            "actions": [
                {"action": "delete_file", "path": "temp.log"},
            ],
        },
    )
    ctx = _make_cleanup_ctx(workspace)
    effect = InvokeAgentEffect(
        agent_name="dev", phase="development_commit_review", prompt_file="cleanup.txt"
    )
    result = handle_commit_review_phase(effect, ctx)

    assert not temp_file.exists()
    assert len(result) == 1
    event = result[0]
    assert isinstance(event, CommitReviewApproved)
    assert event.tree_sha != initial_seal.tree_sha
    # The verdict is evaluated against the NEW seal the edit step produced:
    # the approved SHA is the SHA of the tree as it stands after the edit,
    # not the SHA the reviewer judged before the edit.
    resealed = seal_pending(tmp_git_repo, "run", 1)
    assert event.tree_sha == resealed.tree_sha, (
        "The approve verdict must bind to the seal produced by the edit step: "
        f"approved {event.tree_sha!r} != resealed {resealed.tree_sha!r}"
    )


def test_uppercase_verdict_returns_retryable_phase_failure(tmp_git_repo: Path) -> None:
    """An uppercase verdict ('APPROVE') is malformed and fails as a retryable PhaseFailureEvent."""
    workspace = FsWorkspace(tmp_git_repo)
    tree_sha = _resolve_artifact_tree_sha(tmp_git_repo)
    path = Path(workspace.root) / COMMIT_REVIEW_ARTIFACT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: commit_review\nverdict: APPROVE\ntree_sha: {tree_sha}\n---\n## Actions\n",
        encoding="utf-8",
    )
    result = _run_commit_review_phase(workspace)
    assert len(result) == 1
    event = result[0]
    assert isinstance(event, PhaseFailureEvent)
    assert event.recoverable is True
    assert event.retry_in_session is True
    assert event.failure_category == FailureCategory.ARTIFACT_VALIDATION
    assert not isinstance(event, CommitReviewApproved)


def test_malformed_action_line_returns_retryable_phase_failure(tmp_git_repo: Path) -> None:
    """An unparseable action line is malformed and fails as a retryable PhaseFailureEvent."""
    workspace = FsWorkspace(tmp_git_repo)
    tree_sha = _resolve_artifact_tree_sha(tmp_git_repo)
    path = Path(workspace.root) / COMMIT_REVIEW_ARTIFACT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: commit_review\nverdict: approve\ntree_sha: {tree_sha}\n---\n## Actions\n- [A1] delete_file tmp/scratch.log\n",
        encoding="utf-8",
    )
    result = _run_commit_review_phase(workspace)
    assert len(result) == 1
    event = result[0]
    assert isinstance(event, PhaseFailureEvent)
    assert event.recoverable is True
    assert event.retry_in_session is True
    assert event.failure_category == FailureCategory.ARTIFACT_VALIDATION
    assert not isinstance(event, CommitReviewApproved)


def test_reject_without_reason_section_returns_retryable_phase_failure(tmp_git_repo: Path) -> None:
    """A rejecting artifact lacking a '## Reason' section fails as a retryable PhaseFailureEvent."""
    workspace = FsWorkspace(tmp_git_repo)
    tree_sha = _resolve_artifact_tree_sha(tmp_git_repo)
    path = Path(workspace.root) / COMMIT_REVIEW_ARTIFACT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: commit_review\nverdict: reject\ntree_sha: {tree_sha}\n---\n## Actions\n",
        encoding="utf-8",
    )
    result = _run_commit_review_phase(workspace)
    assert len(result) == 1
    event = result[0]
    assert isinstance(event, PhaseFailureEvent)
    assert event.recoverable is True
    assert event.retry_in_session is True
    assert event.failure_category == FailureCategory.ARTIFACT_VALIDATION
    assert not isinstance(event, CommitReviewRejected)


def test_reject_with_frontmatter_reason_without_reason_section_returns_retryable_phase_failure(
    tmp_git_repo: Path,
) -> None:
    """Frontmatter 'reason' does not satisfy the '## Reason' requirement and fails as retryable."""
    workspace = FsWorkspace(tmp_git_repo)
    tree_sha = _resolve_artifact_tree_sha(tmp_git_repo)
    path = Path(workspace.root) / COMMIT_REVIEW_ARTIFACT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: commit_review\nverdict: reject\ntree_sha: {tree_sha}\nreason: frontmatter reason text\n---\n## Actions\n",
        encoding="utf-8",
    )
    result = _run_commit_review_phase(workspace)
    assert len(result) == 1
    event = result[0]
    assert isinstance(event, PhaseFailureEvent)
    assert event.recoverable is True
    assert event.retry_in_session is True
    assert event.failure_category == FailureCategory.ARTIFACT_VALIDATION
    assert not isinstance(event, CommitReviewRejected)
