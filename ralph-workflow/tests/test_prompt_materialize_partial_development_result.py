"""Focused prompt tests for partial development-result continuation handoff."""

from pathlib import Path

from ralph.policy.models import (
    ArtifactContract,
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
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace


def _policy() -> tuple[PipelinePolicy, ArtifactsPolicy]:
    pipeline = PipelinePolicy(
        phases={
            "build_work": PhaseDefinition(
                drain="implementation_output",
                role="execution",
                prompt_template="developer_iteration.jinja",
                continuation_template="developer_iteration_continuation.jinja",
                transitions=PhaseTransition(on_success="polish_changes"),
            ),
            "polish_changes": PhaseDefinition(
                drain="change_cleanup",
                role="commit_cleanup",
                prompt_template="commit_cleanup.jinja",
                transitions=PhaseTransition(on_success="sync_changes"),
            ),
            "sync_changes": PhaseDefinition(
                drain="change_record",
                role="commit",
                prompt_template="commit_message.jinja",
                transitions=PhaseTransition(on_success="build_work"),
            ),
            "done": PhaseDefinition(
                drain="done",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(on_success="done", on_loopback="done"),
            ),
        },
        entry_phase="build_work",
        terminal_phase="done",
    )
    artifacts = ArtifactsPolicy(
        artifacts={
            "implementation_output": ArtifactContract(
                drain="implementation_output",
                artifact_type="development_result",
            ),
        }
    )
    return pipeline, artifacts


def _render_after_commit(
    tmp_path: Path,
    result_document: str,
    *,
    stale_context_document: str | None = None,
) -> str:
    pipeline, artifacts = _policy()
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Finish the policy-selected implementation.")
    workspace.write(".agent/PLAN.md", "# Execution Plan\n\n1. Finish the implementation.\n")
    workspace.write(".agent/artifacts/development_result.md", result_document)
    if stale_context_document is not None:
        workspace.write(
            ".agent/tmp/prompt_payloads/development_result_continuation.md",
            stale_context_document,
        )

    materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="polish_changes",
            workspace=workspace,
            pipeline_policy=pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.COMMIT),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(
            artifacts_policy=artifacts,
            previous_phase="build_work",
        ),
    )
    workspace.remove(".agent/artifacts/development_result.md")

    prompt_path = materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="build_work",
            workspace=workspace,
            pipeline_policy=pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(
            artifacts_policy=artifacts,
            previous_phase="sync_changes",
        ),
    )
    return workspace.read(prompt_path)


def test_partial_result_regression_starts_new_session_with_complete_handoff(
    tmp_path: Path,
) -> None:
    """Regression for the partial-result continuation prompt requested by this task."""
    rendered = _render_after_commit(
        tmp_path,
        """---
type: development_result
status: partial
---
## Summary
- [SUM-1] Implemented the parser but the prompt integration remains.
## Files Changed
- [FC-1] ralph/prompts/materialize.py
## Next Steps
- [NEXT-1] Wire the partial result into the continuation prompt.
## Continuation
- [CONT-1] prior-session-42
""",
    )

    assert "continuing a DEVELOPMENT iteration" in rendered
    assert "genuinely new agent session" in rendered
    assert "PARTIAL — NOT COMPLETE" in rendered
    assert "Implemented the parser but the prompt integration remains." in rendered
    assert "Wire the partial result into the continuation prompt." in rendered
    assert "prior-session-42" in rendered


def test_completed_result_keeps_normal_development_prompt(tmp_path: Path) -> None:
    rendered = _render_after_commit(
        tmp_path,
        """---
type: development_result
status: completed
---
## Summary
- [SUM-1] Finished the implementation.
## Files Changed
- [FC-1] ralph/prompts/materialize.py
""",
        stale_context_document="""---
type: development_result
status: partial
---
## Summary
- [SUM-1] Stale incomplete work.
## Files Changed
- [FC-1] stale.py
## Next Steps
- [NEXT-1] This stale continuation must be discarded.
## Continuation
- [CONT-1] stale-session
""",
    )

    assert "You are in IMPLEMENTATION MODE" in rendered
    assert "continuing a DEVELOPMENT iteration" not in rendered
    assert "PRIOR DEVELOPMENT RESULT" not in rendered
    assert "Finished the implementation." not in rendered
    assert "Stale incomplete work." not in rendered
