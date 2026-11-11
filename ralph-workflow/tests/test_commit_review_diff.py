from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from git import Repo

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
    commit_review_diff,
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

pytestmark = pytest.mark.timeout_seconds(5)


def test_commit_review_diff_includes_untracked_binary(tmp_git_repo: Path) -> None:
    (tmp_git_repo / "accidental_binary.exe").write_bytes(b"\x00MZ")
    diff = commit_review_diff(tmp_git_repo)
    assert "accidental_binary.exe" in diff
    assert _UNTRACKED_HEADER in diff


def test_commit_review_diff_includes_untracked_temp_name_text(
    tmp_git_repo: Path,
) -> None:
    (tmp_git_repo / "scratch-note.txt").write_text("notes")
    diff = commit_review_diff(tmp_git_repo)
    assert "scratch-note.txt" in diff
    assert _UNTRACKED_HEADER in diff


def test_commit_review_diff_includes_untracked_log_file(tmp_git_repo: Path) -> None:
    (tmp_git_repo / "debug.log").write_text("debug output")
    diff = commit_review_diff(tmp_git_repo)
    assert "debug.log" in diff
    assert _UNTRACKED_HEADER in diff


def test_commit_review_diff_fallback_on_non_repo_path(tmp_path: Path) -> None:
    diff = commit_review_diff(tmp_path)
    assert diff == "(no diff available)"


def test_commit_review_prompt_includes_untracked_files_in_header(
    tmp_git_repo: Path,
) -> None:
    (tmp_git_repo / "accidental_binary.exe").write_bytes(b"\x00MZ")
    p = PipelinePolicy(
        phases={
            "development_commit_review": PhaseDefinition(
                drain="commit",
                role="commit_review",
                prompt_template="commit_review.jinja",
                transitions=PhaseTransition(
                    on_success="complete",
                    on_loopback="development_commit_review",
                    on_failure="failed_terminal",
                ),
                loop_policy=PhaseLoopPolicy(iteration_state_field="commit_review_iteration"),
            ),
            "complete": PhaseDefinition(
                drain="complete",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(on_success="complete"),
            ),
        },
        entry_phase="development_commit_review",
        terminal_phase="complete",
        loop_counters={"commit_review_iteration": LoopCounterConfig(default_max=3)},
    )
    ws = MemoryWorkspace(root=str(tmp_git_repo))
    ws.write("PROMPT.md", "x")
    ws.write(".agent/PLAN.md", "# Plan")
    caps = SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT)
    ctx = PromptPhaseContext(
        phase="development_commit_review",
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


@pytest.mark.timeout_seconds(10)
def test_commit_review_diff_and_prompt_redact_recognized_secrets(
    tmp_git_repo: Path,
) -> None:
    tracked_secret = tmp_git_repo / "private" / ".env"
    tracked_secret.parent.mkdir()
    tracked_secret.write_text("TOKEN=tracked-old-placeholder\n", encoding="utf-8")
    safe_file = tmp_git_repo / "safe.txt"
    safe_file.write_text("safe old value\n", encoding="utf-8")
    with Repo(tmp_git_repo) as repo:
        repo.index.add(["private/.env", "safe.txt"])
        repo.index.commit("seed cleanup disclosure regression")

    tracked_secret.write_text("TOKEN=tracked-new-placeholder\n", encoding="utf-8")
    safe_file.write_text("safe new value\n", encoding="utf-8")
    untracked_secret = tmp_git_repo / "local" / "credentials.json"
    untracked_secret.parent.mkdir()
    untracked_secret.write_text(
        '{"token":"untracked-placeholder"}\n',
        encoding="utf-8",
    )

    p = PipelinePolicy(
        phases={
            "development_commit_review": PhaseDefinition(
                drain="commit",
                role="commit_review",
                prompt_template="commit_review.jinja",
                transitions=PhaseTransition(
                    on_success="complete",
                    on_loopback="development_commit_review",
                    on_failure="failed_terminal",
                ),
                loop_policy=PhaseLoopPolicy(iteration_state_field="commit_review_iteration"),
            ),
            "complete": PhaseDefinition(
                drain="complete",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(on_success="complete"),
            ),
        },
        entry_phase="development_commit_review",
        terminal_phase="complete",
        loop_counters={"commit_review_iteration": LoopCounterConfig(default_max=3)},
    )
    ws = MemoryWorkspace(root=str(tmp_git_repo))
    ws.write("PROMPT.md", "x")
    ws.write(".agent/PLAN.md", "# Plan")
    ctx = PromptPhaseContext(
        phase="development_commit_review",
        workspace=ws,
        pipeline_policy=p,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        workspace_root=tmp_git_repo,
    )

    diff = commit_review_diff(tmp_git_repo)
    rendered = ws.read(
        materialize_prompt_for_phase(
            ctx,
            PromptPhaseOptions(
                artifacts_policy=ArtifactsPolicy(artifacts={}),
                previous_phase=None,
            ),
        )
    )

    for agent_input in (diff, rendered):
        assert "safe.txt" in agent_input
        assert "safe new value" in agent_input
        assert "Recognized secret changes" in agent_input
        assert "secret files will be removed from version control" in agent_input
        assert "private/.env" not in agent_input
        assert "tracked-old-placeholder" not in agent_input
        assert "tracked-new-placeholder" not in agent_input
        assert "local/credentials.json" not in agent_input
        assert "untracked-placeholder" not in agent_input


def test_commit_review_diff_respects_gitignore(tmp_git_repo: Path) -> None:
    (tmp_git_repo / ".gitignore").write_text("*.generated\n")
    (tmp_git_repo / "noise.generated").write_text("ignored noise")
    (tmp_git_repo / "visible.tmp").write_text("visible noise")
    diff = commit_review_diff(tmp_git_repo)
    assert "noise.generated" not in diff
    assert "visible.tmp" in diff
    assert _UNTRACKED_HEADER in diff


def test_commit_review_diff_caps_untracked_list(tmp_git_repo: Path) -> None:
    for i in range(600):
        (tmp_git_repo / f"filler_{i}.tmp").write_text("x")
    diff = commit_review_diff(tmp_git_repo)
    assert _UNTRACKED_HEADER in diff
    visible_count = sum(1 for line in diff.splitlines() if line.startswith("filler_"))
    assert visible_count == 500
    assert "and 100 more untracked files not shown" in diff
    assert "filler_0.tmp" in diff
    for i in range(10):
        (tmp_git_repo / f"zzz_after_cap_{i}.tmp").write_text("x")
    diff = commit_review_diff(tmp_git_repo)
    assert _UNTRACKED_HEADER in diff
    visible_after = sum(1 for line in diff.splitlines() if line.startswith("filler_"))
    assert visible_after == 500
    assert "and 110 more untracked files not shown" in diff
    assert "zzz_after_cap_0.tmp" not in diff
    assert "zzz_after_cap_9.tmp" not in diff
