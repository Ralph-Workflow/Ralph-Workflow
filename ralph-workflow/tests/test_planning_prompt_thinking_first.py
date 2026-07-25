"""Tests for the thinking-first planning prompt rewrite.

The rewrite puts the ``## How to think about this plan`` content BEFORE the
submission mechanics so the planner's first reading is how to think, not how
to format. These tests pin the structural contract: ordering, content, the
shared thinking partial across the three planning templates, and the line
budget on ``planning.jinja`` (target: at or under 170 lines after the rewrite
removed the rubric include, worked example, step-type guidance, and the
prompt-scope classification taxonomy).
"""

from __future__ import annotations

from pathlib import Path

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
from ralph.prompts.developer import (
    PlanningPromptInputs,
    prompt_planning_xml_with_context,
)
from ralph.prompts.materialize import (
    PromptPhaseContext,
    PromptPhaseOptions,
    materialize_prompt_for_phase,
)
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.template_registry import packaged_template_root
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace

_PLANNING_TEMPLATE = (
    packaged_template_root() / "planning.jinja"
)
_PLANNING_FALLBACK_TEMPLATE = (
    packaged_template_root() / "planning_fallback.jinja"
)
_PLANNING_EDIT_TEMPLATE = (
    packaged_template_root() / "planning_edit.jinja"
)
_PLANNING_THINKING_TEMPLATE = (
    packaged_template_root() / "shared" / "_planning_thinking.jinja"
)

# Hard line budget per the plan: ``planning.jinja`` must be at or under 170
# lines after the thinking-first rewrite (from 301 lines pre-rewrite).
_PLANNING_TEMPLATE_LINE_BUDGET = 170

_THINKING_HEADING = "## How to think about this plan"
_PHASE_COVERAGE_HEADING = "## Required phase coverage"
_DOCUMENT_CONTRACT_HEADING = "## Document contract"
_OVERRIDES_HEADING = "## Validation Overrides"
_SUBMISSION_HEADING = "## Submission mechanics"

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


def _render_planning(tmp_path: Path, has_docs_mcp: bool = False) -> str:
    """Render ``planning.jinja`` through the existing prompt materializer."""
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
    return workspace.read(prompt_path)


def _render_planning_template(
    template: str,
    tmp_path: Path,
    has_docs_mcp: bool = False,
) -> str:
    """Render an arbitrary planning template by name via the developer helper."""
    context = TemplateContext.default()
    workspace = MemoryWorkspace(root=str(tmp_path))
    session_caps = SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING)
    inputs = PlanningPromptInputs(prompt_content="Plan the work", has_docs_mcp=has_docs_mcp)
    return prompt_planning_xml_with_context(
        context, inputs, workspace, session_caps, template_name=template
    )


# ---------------------------------------------------------------------------
# Source-level structure: the line budget and the removed-section pins
# ---------------------------------------------------------------------------


def test_planning_template_is_at_or_under_the_line_budget() -> None:
    """``planning.jinja`` is at or under 170 lines after the rewrite.

    The thinking-first rewrite collapsed the rubric include, worked example,
    step-type guidance, and the prompt-scope classification taxonomy into the
    shared partial. The remaining prompt is short enough to re-read under
    context pressure.
    """
    line_count = sum(1 for _ in _PLANNING_TEMPLATE.read_text(encoding="utf-8").splitlines())
    assert line_count <= _PLANNING_TEMPLATE_LINE_BUDGET, (
        f"planning.jinja is {line_count} lines, exceeds the {_PLANNING_TEMPLATE_LINE_BUDGET} "
        "line budget for the thinking-first rewrite"
    )


def test_planning_template_drops_legacy_sections() -> None:
    """The thinking-first rewrite removed duplicated content from planning.jinja.

    Each removed item lives in the format doc (or the analysis prompt for
    the rubric) so a planner has one canonical owner for every concept.
    """
    source = _PLANNING_TEMPLATE.read_text(encoding="utf-8")
    for removed in (
        "## Worked plan example",
        "## Step type and target guidance",
        "## PLAN QUALITY RUBRIC",
        "## PROMPT SCOPE CLASSIFICATION",
        "## Plan-artifact scope (planner-meta-task)",
        "## DISCOVERY PREFLIGHT",
    ):
        assert removed not in source, (
            f"planning.jinja still contains the removed section {removed!r}; "
            "the thinking-first rewrite should have deleted it"
        )


def test_planning_template_includes_the_shared_thinking_partial() -> None:
    source = _PLANNING_TEMPLATE.read_text(encoding="utf-8")
    assert "shared/_planning_thinking.j2" in source, (
        "planning.jinja must include the shared thinking-first partial so the "
        "thinking content appears BEFORE the submission mechanics"
    )


def test_planning_fallback_includes_the_shared_thinking_partial() -> None:
    source = _PLANNING_FALLBACK_TEMPLATE.read_text(encoding="utf-8")
    assert "shared/_planning_thinking.j2" in source, (
        "planning_fallback.jinja must include the shared thinking-first "
        "partial; one standard stated once across all three planning prompts"
    )


def test_planning_edit_includes_the_shared_thinking_partial() -> None:
    source = _PLANNING_EDIT_TEMPLATE.read_text(encoding="utf-8")
    assert "shared/_planning_thinking.j2" in source, (
        "planning_edit.jinja must include the shared thinking-first "
        "partial; one standard stated once across all three planning prompts"
    )


# ---------------------------------------------------------------------------
# Rendered output: ordering, content, and what must NOT appear
# ---------------------------------------------------------------------------


def test_rendered_planning_prompt_orders_thinking_before_submission(
    tmp_path: Path,
) -> None:
    """The rendered prompt reads thinking-first: how-to-think before mechanics."""
    rendered = _render_planning(tmp_path)
    assert _THINKING_HEADING in rendered, (
        "rendered planning prompt must include the thinking-first heading "
        "before the submission mechanics"
    )
    assert _SUBMISSION_HEADING in rendered
    assert rendered.index(_THINKING_HEADING) < rendered.index(_SUBMISSION_HEADING), (
        "thinking-first rewrite requires the thinking heading to appear "
        "BEFORE the submission mechanics heading in the rendered prompt"
    )


def test_rendered_planning_prompt_keeps_characterization_phase_mandatory(
    tmp_path: Path,
) -> None:
    """Characterization is mandatory even for "easy" tasks.

    The thinking-first rewrite folds the DISCOVERY PREFLIGHT content into the
    phase coverage paragraph and explicitly forbids skipping characterization
    on easy-looking tasks. The render must carry both signals.
    """
    rendered = _render_planning(tmp_path)
    normalized = " ".join(rendered.split())
    assert _PHASE_COVERAGE_HEADING in rendered
    # The four-phase paragraph must list every phase in order.
    assert "Orient." in normalized
    assert "Characterize current behavior." in normalized
    assert "Change." in normalized
    assert "Verify." in normalized
    # No-skip characterization must be visible in the rendered prompt.
    assert "Characterization is mandatory" in normalized
    assert "Do not skip this phase" in normalized or "easy" in normalized


def test_rendered_planning_prompt_does_not_restate_rubric_dimensions(
    tmp_path: Path,
) -> None:
    """The planning prompt must not restate the analysis prompt's nine dimensions."""
    rendered = _render_planning(tmp_path)
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


def test_rendered_planning_prompt_does_not_carry_a_worked_example_document(
    tmp_path: Path,
) -> None:
    """The thinking-first rewrite removed the worked-example document.

    Worked examples now live in the format doc (``.agent/artifact-formats/plan.md``);
    the planning prompt only points at the format doc.
    """
    rendered = _render_planning(tmp_path)
    # Worked-example sub-task labels that the legacy prompt restated must
    # not appear as inlined content in the rendered prompt.
    for example in (
        "add a labeled field to `## Design`",
        "document planning quality guidance in the format doc",
        "rewrite the planning prompt to be more universal",
        "add an audit check for plan-field drift",
    ):
        assert example not in rendered, (
            f"Worked example leaked back into the planning prompt: {example!r}"
        )


def test_rendered_planning_prompt_documents_the_validation_override_ledger(
    tmp_path: Path,
) -> None:
    """The rendered planning prompt tells the planner about the override ledger.

    The document-contract paragraph introduces the three severities and the
    ``## Validation Overrides`` ledger so the planner can choose to record
    an override instead of fighting a non-blocking warning.
    """
    rendered = _render_planning(tmp_path)
    normalized = " ".join(rendered.split())
    assert _DOCUMENT_CONTRACT_HEADING in rendered
    assert "error" in normalized
    assert "warning" in normalized
    assert "info" in normalized
    assert _OVERRIDES_HEADING in normalized
    # Optional narrowing label is part of the grammar.
    assert "Where: <section>" in normalized or "Where: <section>" in rendered


# ---------------------------------------------------------------------------
# Shared partial content: the partial itself owns the contract wording
# ---------------------------------------------------------------------------


def test_shared_thinking_partial_owns_the_thinking_first_contract() -> None:
    """The shared partial is the single owner of the thinking-first contract."""
    source = _PLANNING_THINKING_TEMPLATE.read_text(encoding="utf-8")
    assert _THINKING_HEADING in source
    assert _PHASE_COVERAGE_HEADING in source
    assert _DOCUMENT_CONTRACT_HEADING in source
    assert _SUBMISSION_HEADING in source
    # The four questions drive the planner's framing.
    for question in (
        "What outcome does the user need?",
        "What does the repository look like today?",
        "What risks or coordination costs will the executor hit?",
        "How will anyone know it worked?",
    ):
        assert question in source, (
            f"shared thinking partial is missing the framing question {question!r}"
        )


def test_three_planning_prompts_share_the_thinking_partial() -> None:
    """All three planning prompts include the same shared thinking partial.

    One standard stated once: ``planning.jinja``, ``planning_fallback.jinja``,
    and ``planning_edit.jinja`` each include ``shared/_planning_thinking.j2``
    so the thinking content appears identically in all three.
    """
    for template in (_PLANNING_TEMPLATE, _PLANNING_FALLBACK_TEMPLATE, _PLANNING_EDIT_TEMPLATE):
        source = template.read_text(encoding="utf-8")
        assert "shared/_planning_thinking.j2" in source, (
            f"{template.name} must include the shared thinking partial"
        )


def test_rendered_planning_fallback_carries_thinking_first_content(
    tmp_path: Path,
) -> None:
    """The fallback rendering also leads with the thinking content."""
    rendered = _render_planning_template("planning_fallback.jinja", tmp_path)
    assert _THINKING_HEADING in rendered
    assert _PHASE_COVERAGE_HEADING in rendered
    assert _DOCUMENT_CONTRACT_HEADING in rendered
    assert rendered.index(_THINKING_HEADING) < rendered.index(_SUBMISSION_HEADING)


def test_rendered_planning_edit_carries_thinking_first_content(
    tmp_path: Path,
) -> None:
    """The edit-mode prompt also leads with the thinking content."""
    rendered = _render_planning_template("planning_edit.jinja", tmp_path)
    assert _THINKING_HEADING in rendered
    assert _PHASE_COVERAGE_HEADING in rendered
    assert _DOCUMENT_CONTRACT_HEADING in rendered
    assert rendered.index(_THINKING_HEADING) < rendered.index(_SUBMISSION_HEADING)
