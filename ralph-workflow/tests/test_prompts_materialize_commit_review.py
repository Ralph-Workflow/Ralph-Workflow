from __future__ import annotations

from pathlib import Path

from ralph.mcp.protocol.capability_mapping import SessionDrain
from ralph.policy.models import (
    ArtifactContract,
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
    materialize_prompt_for_phase,
)
from ralph.prompts.types import SessionCapabilities
from ralph.workspace.memory import MemoryWorkspace


def test_commit_review_phase_renders_prompt_successfully(tmp_path: Path) -> None:
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
    ws = MemoryWorkspace(root=str(tmp_path))
    ws.write("PROMPT.md", "x")
    ws.write(".agent/PLAN.md", "# Plan")
    caps = SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT)
    ctx = PromptPhaseContext(
        phase="development_commit_review",
        workspace=ws,
        pipeline_policy=p,
        session_caps=caps,
        workspace_root=tmp_path,
    )
    rendered = ws.read(
        materialize_prompt_for_phase(
            ctx,
            PromptPhaseOptions(artifacts_policy=ArtifactsPolicy(artifacts={}), previous_phase=None),
        )
    )
    assert "ralph_submit_md_artifact" in rendered and "DIFF" in rendered
    assert rendered.startswith("Classify only the pending diff's unsafe or machine-local files")
    assert "Explicit Non-Goal" in rendered
    assert "approve-unless-citable-damage" in rendered
    assert "Rejection Consequence" in rendered
    assert "Stage 1 Evidence" in rendered
    assert "type: commit_review" in rendered
    assert "verdict: approve" in rendered


def test_commit_review_prompt_stage1_evidence(tmp_path: Path) -> None:
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
    ws = MemoryWorkspace(root=str(tmp_path))
    ws.write("PROMPT.md", "x")
    ws.write(".agent/PLAN.md", "# Plan")
    test_tree_sha = "1234567890abcdef1234567890abcdef12345678"
    test_proposal_ref = "refs/ralph/proposals/test-run/0"
    soft_tripwires: list[dict[str, object]] = [
        {"category": "binary", "message": "binary exceeds limit", "path": "large.bin"},
    ]
    caps = SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT)
    ctx = PromptPhaseContext(
        phase="development_commit_review",
        workspace=ws,
        pipeline_policy=p,
        session_caps=caps,
        workspace_root=tmp_path,
    )
    rendered = ws.read(
        materialize_prompt_for_phase(
            ctx,
            PromptPhaseOptions(
                artifacts_policy=ArtifactsPolicy(artifacts={}),
                proposal_ref=test_proposal_ref,
                tree_sha=test_tree_sha,
                soft_tripwires=soft_tripwires,
            ),
        )
    )
    assert test_proposal_ref in rendered
    assert test_tree_sha in rendered
    assert "binary exceeds limit" in rendered
    assert "large.bin" in rendered


def test_developer_prompt_injects_commit_review_rejection_reason_and_ref(tmp_path: Path) -> None:
    p = PipelinePolicy(
        phases={
            "development": PhaseDefinition(
                drain="development",
                role="execution",
                prompt_template="developer_iteration.jinja",
                transitions=PhaseTransition(
                    on_success="complete",
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
        entry_phase="development",
        terminal_phase="complete",
    )
    artifacts_policy = ArtifactsPolicy(
        artifacts={
            "development_result": ArtifactContract(
                drain="development",
                artifact_type="development_result",
            ),
        }
    )
    ws = MemoryWorkspace(root=str(tmp_path))
    ws.write("PROMPT.md", "Task criteria")
    ws.write(".agent/PRODUCT_CRITERIA.md", "Criteria")
    ws.write(".agent/artifacts/plan.md", "# Plan\n1. Do task\n")
    caps = SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT)
    ctx = PromptPhaseContext(
        phase="development",
        workspace=ws,
        pipeline_policy=p,
        session_caps=caps,
        workspace_root=tmp_path,
    )
    rendered = ws.read(
        materialize_prompt_for_phase(
            ctx,
            PromptPhaseOptions(
                artifacts_policy=artifacts_policy,
                last_review_rejection_reason="Accidental deletion of auth module",
                rollback_ref="refs/ralph/proposals/test-run/1",
            ),
        )
    )
    assert "PREVIOUS COMMIT REVIEW REJECTION" in rendered
    assert "Accidental deletion of auth module" in rendered
    assert "refs/ralph/proposals/test-run/1" in rendered
    assert "git checkout refs/ralph/proposals/test-run/1 -- ." in rendered


def test_developer_prompt_extracts_rejection_from_retry_hint_file(tmp_path: Path) -> None:
    p = PipelinePolicy(
        phases={
            "development": PhaseDefinition(
                drain="development",
                role="execution",
                prompt_template="developer_iteration.jinja",
                transitions=PhaseTransition(
                    on_success="complete",
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
        entry_phase="development",
        terminal_phase="complete",
    )
    artifacts_policy = ArtifactsPolicy(
        artifacts={
            "development_result": ArtifactContract(
                drain="development",
                artifact_type="development_result",
            ),
        }
    )
    ws = MemoryWorkspace(root=str(tmp_path))
    ws.write("PROMPT.md", "Task criteria")
    ws.write(".agent/PRODUCT_CRITERIA.md", "Criteria")
    ws.write(".agent/artifacts/plan.md", "# Plan\n1. Do task\n")
    hint_text = (
        "Commit review rejected previous iteration: Stray credentials committed\n"
        "Proposal ref: refs/ralph/proposals/test-run/2\n"
        "Working tree rolled back to HEAD. To inspect rejected changes: git checkout refs/ralph/proposals/test-run/2 -- .\n"
    )
    ws.write(".agent/tmp/last_retry_error_development.txt", hint_text)
    caps = SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT)
    ctx = PromptPhaseContext(
        phase="development",
        workspace=ws,
        pipeline_policy=p,
        session_caps=caps,
        workspace_root=tmp_path,
    )
    rendered = ws.read(
        materialize_prompt_for_phase(
            ctx,
            PromptPhaseOptions(artifacts_policy=artifacts_policy),
        )
    )
    assert "PREVIOUS COMMIT REVIEW REJECTION" in rendered
    assert "Stray credentials committed" in rendered
    assert "refs/ralph/proposals/test-run/2" in rendered
