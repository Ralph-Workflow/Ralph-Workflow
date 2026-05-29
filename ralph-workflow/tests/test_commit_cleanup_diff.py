"""Tests for commit_cleanup_diff function."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.policy.models import (
    ArtifactsPolicy,
    LoopCounterConfig,
    PhaseDefinition,
    PhaseLoopPolicy,
    PhaseTransition,
    PipelinePolicy,
)
from ralph.prompts.materialize import (
    PromptPhaseContext,
    PromptPhaseOptions,
    commit_cleanup_diff,
    materialize_prompt_for_phase,
)
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace

if TYPE_CHECKING:
    from pathlib import Path


def test_commit_cleanup_diff_excludes_untracked_files(tmp_git_repo: Path) -> None:
    """Untracked files do not appear in the cleanup diff helper output."""
    (tmp_git_repo / "accidental_binary.exe").write_bytes(b"\x00MZ")
    diff = commit_cleanup_diff(tmp_git_repo)
    assert "accidental_binary.exe" not in diff


def test_commit_cleanup_diff_fallback_on_non_repo_path(tmp_path: Path) -> None:
    """Non-git directory returns sentinel '(no diff available)'."""
    diff = commit_cleanup_diff(tmp_path)
    assert diff == "(no diff available)"


def test_commit_cleanup_prompt_excludes_untracked_files(tmp_git_repo: Path) -> None:
    """Rendered cleanup prompt does not list untracked files as commit candidates."""
    (tmp_git_repo / "accidental_binary.exe").write_bytes(b"\x00MZ")
    p = PipelinePolicy(
        phases={
            "development_commit_cleanup": PhaseDefinition(
                drain="commit",
                role="commit_cleanup",
                prompt_template="commit_cleanup.jinja",
                transitions=PhaseTransition(
                    on_success="complete",
                    on_loopback="development_commit_cleanup",
                    on_failure="failed_terminal",
                ),
                loop_policy=PhaseLoopPolicy(iteration_state_field="commit_cleanup_iteration"),
            ),
            "complete": PhaseDefinition(
                drain="complete",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(on_success="complete"),
            ),
        },
        entry_phase="development_commit_cleanup",
        terminal_phase="complete",
        loop_counters={"commit_cleanup_iteration": LoopCounterConfig(default_max=3)},
    )
    ws = MemoryWorkspace(root=str(tmp_git_repo))
    ws.write("PROMPT.md", "x")
    ws.write(".agent/PLAN.md", "# Plan")
    caps = SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT)
    ctx = PromptPhaseContext(
        phase="development_commit_cleanup",
        workspace=ws,
        pipeline_policy=p,
        session_caps=caps,
        workspace_root=tmp_git_repo,
    )
    rendered = ws.read(
        materialize_prompt_for_phase(
            ctx,
            PromptPhaseOptions(
                artifacts_policy=ArtifactsPolicy(artifacts={}),
                previous_phase=None,
            ),
        )
    )
    assert "accidental_binary.exe" not in rendered
