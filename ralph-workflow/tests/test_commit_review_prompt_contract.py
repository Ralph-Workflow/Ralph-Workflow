from __future__ import annotations

from pathlib import Path

from ralph.mcp.protocol.capability_mapping import SessionDrain
from ralph.policy.models import (
    ArtifactsPolicy,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
)
from ralph.prompts.materialize import (
    PromptPhaseContext,
    PromptPhaseOptions,
    materialize_prompt_for_phase,
)
from ralph.prompts.types import SessionCapabilities
from ralph.workspace.memory import MemoryWorkspace

TREE_SHA = "1234567890abcdef1234567890abcdef12345678"
PROPOSAL_REF = "refs/ralph/proposals/test-run/1"


def _render(
    tmp_path: Path,
    *,
    soft_tripwires: list[dict[str, object]] | None = None,
) -> str:
    policy = PipelinePolicy(
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
    )
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "x")
    workspace.write(".agent/PLAN.md", "# Plan")
    context = PromptPhaseContext(
        phase="development_commit_review",
        workspace=workspace,
        pipeline_policy=policy,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        workspace_root=tmp_path,
    )
    return workspace.read(
        materialize_prompt_for_phase(
            context,
            PromptPhaseOptions(
                artifacts_policy=ArtifactsPolicy(artifacts={}),
                proposal_ref=PROPOSAL_REF,
                tree_sha=TREE_SHA,
                soft_tripwires=soft_tripwires,
            ),
        )
    )


def test_prompt_states_that_commit_review_is_not_a_source_code_review(tmp_path: Path) -> None:
    rendered = _render(tmp_path)
    assert "not a source code review" in rendered
    assert "does not judge whether the code is good, tests are adequate" in rendered


def test_prompt_states_that_commit_review_does_not_judge_source_change_membership(
    tmp_path: Path,
) -> None:
    rendered = _render(tmp_path)
    assert "does not judge whether a source change belongs in this commit" in rendered
    assert "unrelated source edits, scope creep and commit composition" in rendered


def test_prompt_gives_the_reviewer_the_stage1_evidence(tmp_path: Path) -> None:
    rendered = _render(
        tmp_path,
        soft_tripwires=[
            {"category": "binary_size", "message": "binary exceeds limit", "path": "large.bin"},
        ],
    )
    assert "Stage 1 Evidence" in rendered
    assert PROPOSAL_REF in rendered
    assert TREE_SHA in rendered
    assert "binary exceeds limit" in rendered
    assert "large.bin" in rendered


def test_prompt_states_reject_is_the_shortcut_for_resetting_every_pending_file(
    tmp_path: Path,
) -> None:
    rendered = _render(tmp_path)
    assert "`reject` is the shortcut for resetting every file in the pending commit" in rendered
    assert "instead of listing every file when all of it is trash" in rendered


def test_prompt_states_reject_only_for_citable_whole_commit_nonsense(tmp_path: Path) -> None:
    rendered = _render(tmp_path)
    assert "Reject only when the entire commit is nonsense" in rendered
    assert "evidence of damage that you can cite" in rendered


def test_prompt_states_edit_actions_then_approve_for_stray_content_beside_real_work(
    tmp_path: Path,
) -> None:
    rendered = _render(tmp_path)
    assert "When content that does not belong sits beside real work" in rendered
    assert "remove them with `delete_file`, `add_to_gitignore`, or `add_to_git_exclude`" in rendered
    assert "and approve the rest" in rendered


def test_prompt_states_approve_in_every_other_case_including_when_in_doubt(
    tmp_path: Path,
) -> None:
    rendered = _render(tmp_path)
    assert "Approve in every other case" in rendered
    assert "When in doubt, **approve**" in rendered


def test_commit_review_regression_editorial_text_is_never_rejection_evidence(
    tmp_path: Path,
) -> None:
    rendered = _render(tmp_path)

    assert "An ordinary textual edit to `AGENTS.md`, `PROMPT.md`, or documentation" in rendered
    assert "is never rejection evidence" in rendered
    assert "approve it even when you disagree with the wording" in rendered
