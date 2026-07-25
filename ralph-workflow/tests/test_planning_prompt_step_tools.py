from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.policy.models import (
    ArtifactContract,
    ArtifactsPolicy,
    LoopCounterConfig,
    PhaseDecisionRoute,
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
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace

if TYPE_CHECKING:
    from pathlib import Path


_MINIMAL_PROMPT_PLAN_HANDOFF = "# Execution Plan\n\n1. Existing plan handoff.\n"

_MINIMAL_PLANNING_ARTIFACTS_POLICY = ArtifactsPolicy(
    artifacts={
        "plan": ArtifactContract(drain="planning", artifact_type="plan"),
        "planning_analysis_decision": ArtifactContract(
            drain="analysis",
            artifact_type="planning_analysis_decision",
            decision_vocabulary=["approve", "request_changes"],
        ),
    }
)

_MINIMAL_PLANNING_POLICY = PipelinePolicy(
    phases={
        "planning": PhaseDefinition(
            drain="planning",
            role="execution",
            prompt_template="planning.jinja",
            transitions=PhaseTransition(on_success="planning_analysis"),
        ),
        "planning_analysis": PhaseDefinition(
            drain="analysis",
            role="analysis",
            prompt_template="planning_analysis.jinja",
            transitions=PhaseTransition(on_success="complete", on_loopback="planning"),
            loop_policy=PhaseLoopPolicy(iteration_state_field="planning_analysis_iteration"),
            decisions={
                "approve": PhaseDecisionRoute(target="complete"),
                "request_changes": PhaseDecisionRoute(target="planning"),
            },
        ),
        "complete": PhaseDefinition(
            drain="complete",
            role="terminal",
            terminal_outcome="success",
            transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
        ),
    },
    entry_phase="planning",
    terminal_phase="complete",
    loop_counters={"planning_analysis_iteration": LoopCounterConfig(default_max=3)},
)


def test_planning_prompt_mentions_markdown_plan_tools(tmp_path: Path) -> None:
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Plan the work")

    prompt_path = materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="planning",
            workspace=workspace,
            pipeline_policy=_MINIMAL_PLANNING_POLICY,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(artifacts_policy=_MINIMAL_PLANNING_ARTIFACTS_POLICY),
    )

    rendered = workspace.read(prompt_path)
    assert "ralph_verify_md_artifact" in rendered
    assert "ralph_submit_md_artifact" in rendered
    assert "ralph_edit_md_plan_step" in rendered
    assert "### [S-n] Title" in rendered
    assert "IDs are stable and never renumbered" in rendered


# ---------------------------------------------------------------------------
# Step 8: regression lock for the planning.jinja prompt content
# ---------------------------------------------------------------------------


def test_planning_prompt_recommends_verify_without_closing_step_types(
    tmp_path: Path,
) -> None:
    """The planning prompt recommends useful built-ins without coercion."""
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Plan the work")

    prompt_path = materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="planning",
            workspace=workspace,
            pipeline_policy=_MINIMAL_PLANNING_POLICY,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(artifacts_policy=_MINIMAL_PLANNING_ARTIFACTS_POLICY),
    )

    rendered = workspace.read(prompt_path)
    # The thinking-first rewrite keeps the type-vocabulary guidance short:
    # recommend `Type: verify` for test-running steps without coercing
    # project-specific types into a closed set.
    assert "Type: verify" in rendered
    assert "preserved verbatim" in rendered
    assert "Do NOT use `Type: test`" not in rendered


def test_planning_prompt_drops_bloated_subagent_sections(tmp_path: Path) -> None:
    """The thinking-first rewrite removes the four-worked-example bucket.

    The plan artifact scope callout, the rubric include, and the worked
    example document are all gone from planning.jinja; the format doc
    carries them once instead.
    """
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Plan the work")

    prompt_path = materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="planning",
            workspace=workspace,
            pipeline_policy=_MINIMAL_PLANNING_POLICY,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(artifacts_policy=_MINIMAL_PLANNING_ARTIFACTS_POLICY),
    )

    rendered = workspace.read(prompt_path)
    assert "PROMPT SCOPE CLASSIFICATION" not in rendered
    assert "Plan-artifact scope (planner-meta-task)" not in rendered
    # planning.jinja may reference the rubric by name so subagents know
    # where to find it, but it must not restate the nine dimensions.
    assert "## Worked plan example" not in rendered
    assert "## Step type and target guidance" not in rendered
    for dimension in (
        "Product Criteria Compliance",
        "Executor Readiness",
        "Gap Analysis and Consistency",
        "Repository Accuracy",
        "Risk Coverage",
        "Verification Quality",
        "Parallelization Safety",
        "Maintainability of the Plan",
        "Parallel Execution (Agent-Driven)",
    ):
        assert f"**{dimension}**" not in rendered, (
            f"Planning prompt restates rubric dimension {dimension!r}; "
            "the analysis prompt owns this content."
        )
    # The four worked-example sub-tasks the previous prompt restated must
    # not be inlined into planning.jinja anymore.
    for example in (
        "add a labeled field to `## Design`",
        "document planning quality guidance in the format doc",
        "rewrite the planning prompt to be more universal",
        "add an audit check for plan-field drift",
    ):
        assert example not in rendered, f"Worked example leaked back: {example!r}"


def test_planning_analysis_prompt_mentions_markdown_step_edit_remediation_flow(
    tmp_path: Path,
) -> None:
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Plan the work")
    workspace.write(".agent/PLAN.md", _MINIMAL_PROMPT_PLAN_HANDOFF)

    prompt_path = materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="planning_analysis",
            workspace=workspace,
            pipeline_policy=_MINIMAL_PLANNING_POLICY,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.ANALYSIS),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(artifacts_policy=_MINIMAL_PLANNING_ARTIFACTS_POLICY),
    )

    rendered = workspace.read(prompt_path)
    assert "ralph_edit_md_plan_step" in rendered
    assert "`replace` for a vague or wrong step" in rendered
    assert "`insert` for missing work" in rendered
    assert "`remove` for unsupported work" in rendered
    assert "ralph_submit_md_artifact" in rendered
