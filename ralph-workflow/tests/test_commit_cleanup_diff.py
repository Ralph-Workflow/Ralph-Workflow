"""Tests for commit_cleanup_diff function."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.policy.models import (
    ArtifactsPolicy,
    LoopCounterConfig,
    PhaseDefinition,
    PhaseLoopPolicy,
    PhaseTransition,
    PipelinePolicy,
)
from ralph.prompts._commit_diff import (
    _UNTRACKED_HEADER,
    commit_cleanup_diff,
)
from ralph.prompts.materialize import (
    PromptPhaseContext,
    PromptPhaseOptions,
    materialize_prompt_for_phase,
)
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace

if TYPE_CHECKING:
    from pathlib import Path

# Real-git tests fork `git` subprocesses; under full-suite worksteal
# parallelism the default 1s wall-clock alarm intermittently fires on a
# loaded machine even though each test normally finishes in ~100ms.
pytestmark = pytest.mark.timeout_seconds(5)


def test_commit_cleanup_diff_includes_untracked_binary(tmp_git_repo: Path) -> None:
    """Untracked binary files now appear in the cleanup diff helper output."""
    (tmp_git_repo / "accidental_binary.exe").write_bytes(b"\x00MZ")
    diff = commit_cleanup_diff(tmp_git_repo)
    assert "accidental_binary.exe" in diff
    assert _UNTRACKED_HEADER in diff


def test_commit_cleanup_diff_includes_untracked_temp_name_text(
    tmp_git_repo: Path,
) -> None:
    """Untracked ``.txt`` files with a temporary marker appear in the diff."""
    (tmp_git_repo / "scratch-note.txt").write_text("notes")
    diff = commit_cleanup_diff(tmp_git_repo)
    assert "scratch-note.txt" in diff
    assert _UNTRACKED_HEADER in diff


def test_commit_cleanup_diff_includes_untracked_log_file(tmp_git_repo: Path) -> None:
    """Untracked log files appear in the cleanup diff helper output."""
    (tmp_git_repo / "debug.log").write_text("debug output")
    diff = commit_cleanup_diff(tmp_git_repo)
    assert "debug.log" in diff
    assert _UNTRACKED_HEADER in diff


def test_commit_cleanup_diff_fallback_on_non_repo_path(tmp_path: Path) -> None:
    """Non-git directory returns sentinel '(no diff available)'."""
    diff = commit_cleanup_diff(tmp_path)
    assert diff == "(no diff available)"


def test_commit_cleanup_prompt_includes_untracked_files_in_header(
    tmp_git_repo: Path,
) -> None:
    """Rendered cleanup prompt lists untracked files under the shared header."""
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
    assert "accidental_binary.exe" in rendered
    assert _UNTRACKED_HEADER in rendered


def test_commit_cleanup_diff_respects_gitignore(tmp_git_repo: Path) -> None:
    """``git ls-files --others --exclude-standard`` honors ``.gitignore``."""
    (tmp_git_repo / ".gitignore").write_text("*.generated\n")
    (tmp_git_repo / "noise.generated").write_text("ignored noise")
    (tmp_git_repo / "visible.tmp").write_text("visible noise")
    diff = commit_cleanup_diff(tmp_git_repo)
    assert "noise.generated" not in diff
    assert "visible.tmp" in diff
    assert _UNTRACKED_HEADER in diff


def test_commit_cleanup_diff_caps_untracked_list(tmp_git_repo: Path) -> None:
    """Untracked file list is capped and a truncation footer is emitted."""
    for i in range(600):
        (tmp_git_repo / f"filler_{i}.tmp").write_text("x")
    diff = commit_cleanup_diff(tmp_git_repo)
    assert _UNTRACKED_HEADER in diff
    # Exactly 500 paths should be visible.
    visible_count = sum(
        1 for line in diff.splitlines() if line.startswith("filler_")
    )
    assert visible_count == 500
    # Truncation footer reports the remaining count.
    assert "and 100 more untracked files not shown" in diff
    # The first visible path is present.
    assert "filler_0.tmp" in diff
    # Files with zzz_ prefix sort after the 500 cutoff so they are never visible.
    for i in range(10):
        (tmp_git_repo / f"zzz_after_cap_{i}.tmp").write_text("x")
    diff = commit_cleanup_diff(tmp_git_repo)
    assert _UNTRACKED_HEADER in diff
    visible_after = sum(
        1 for line in diff.splitlines() if line.startswith("filler_")
    )
    assert visible_after == 500
    # The 10 zzz_after_cap files sort lexically after all filler files, so
    # they must be in the truncated tail (the footer reports 110 more, not
    # 100, because the zzz files are now also untracked).
    assert "and 110 more untracked files not shown" in diff
    assert "zzz_after_cap_0.tmp" not in diff
    assert "zzz_after_cap_9.tmp" not in diff
