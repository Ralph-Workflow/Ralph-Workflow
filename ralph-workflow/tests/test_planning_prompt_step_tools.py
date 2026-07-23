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


def test_planning_prompt_forbids_test_step_type(tmp_path: Path) -> None:
    """The planning.jinja template forbids step_type='test' and includes the
    PROMPT SCOPE CLASSIFICATION section.
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
    assert "PROMPT SCOPE CLASSIFICATION" in rendered
    assert "Common StepType mistakes" in rendered
    assert "Do NOT use `Type: test`" in rendered


def test_planning_prompt_has_plan_artifact_scope_callout(tmp_path: Path) -> None:
    """The planning.jinja template has the '## Plan-artifact scope (planner-meta-task)'
    callout with the four sub-task bullets and the four worked examples.
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
    assert "Plan-artifact scope (planner-meta-task)" in rendered
    # The four sub-task bullets
    for sub_task in (
        "Plan-artifact grammar",
        "Planning prompt",
        "Planning MCP tools",
        "Planning audit checks",
    ):
        assert sub_task in rendered, f"Missing sub-task bullet: {sub_task!r}"
    # The four worked examples
    for example in (
        "add a labeled field to `## Design`",
        "document planning quality guidance in the format doc",
        "rewrite the planning prompt to be more universal",
        "add an audit check for plan-field drift",
    ):
        assert example in rendered, f"Missing worked example: {example!r}"


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
