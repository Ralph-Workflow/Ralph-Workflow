"""Tests for planner-side subagent guidance and the shared plan-quality rubric.

The planning templates must tell the planning agent how to use its own
subagents (discovery scouts, optional parallel section drafting, pre-finalize
review fan-out) in addition to the executor-facing ``work_units`` guidance,
and must surface the exact quality dimensions planning analysis judges on.
"""

from pathlib import Path

from ralph.mcp.protocol.capability_mapping import SessionDrain
from ralph.prompts.developer import (
    DeveloperPromptInputs,
    PlanningPromptInputs,
    prompt_developer_iteration_xml_with_context,
    prompt_planning_xml_with_context,
)
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.template_registry import packaged_template_root
from ralph.prompts.types import SessionCapabilities
from ralph.workspace.memory import MemoryWorkspace

PLANNER_SUBAGENT_HINTS = (
    "## PLANNING WITH SUBAGENTS",
    "subagent",
    "discovery scouts",
    "draft a plan section",
    "read-only",
    "main session",
    "run the same lenses sequentially",
)

DISCOVERY_SCOUT_LENSES = (
    "structure and ownership",
    "requirement-to-file mapping",
    "verification inventory",
    "risk scan",
)

REVIEW_FANOUT_HINTS = (
    "one reviewer per rubric dimension",
    "refute",
)

RUBRIC_DIMENSIONS = (
    "Prompt Compliance",
    "Executor Readiness",
    "Gap Analysis and Consistency",
    "Repository Accuracy",
    "Risk Coverage",
    "Verification Quality",
    "Parallelization Safety",
    "Maintainability of the Plan",
    "Parallel Execution (Agent-Driven)",
)

RUBRIC_HEADING = "## PLAN QUALITY RUBRIC"

FALLBACK_SUBAGENT_HINTS = (
    "subagent",
    "sequentially",
)


def _render(template: str | None, tmp_path: Path) -> str:
    context = TemplateContext.default()
    workspace = MemoryWorkspace(root=str(tmp_path))
    session_caps = SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT)
    inputs = PlanningPromptInputs(
        prompt_content="Implement the feature",
        analysis_feedback_content="Feedback: tighten verification",
        has_docs_mcp=False,
    )
    if template is None:
        return prompt_planning_xml_with_context(context, inputs, workspace, session_caps)
    return prompt_planning_xml_with_context(
        context,
        inputs,
        workspace,
        session_caps,
        template_name=template,
    )


def _assert_hints(prompt: str, hints: tuple[str, ...]) -> None:
    for hint in hints:
        assert hint in prompt, f"Missing hint: {hint!r}"


def _render_developer_fallback(tmp_path: Path) -> str:
    return prompt_developer_iteration_xml_with_context(
        TemplateContext.default(),
        DeveloperPromptInputs(
            prompt_content="Implement the feature",
            plan_content="## Work Units\n- [unit-a] Implement the feature",
        ),
        MemoryWorkspace(root=str(tmp_path)),
        SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        template_name="developer_iteration_fallback.jinja",
    )


class TestPlannerSubagentGuidance:
    """planning.jinja and planning_edit.jinja describe planner-side subagent use."""

    def test_planning_has_subagent_section(self, tmp_path: Path) -> None:
        prompt = _render(None, tmp_path)
        _assert_hints(prompt, PLANNER_SUBAGENT_HINTS)

    def test_planning_names_discovery_scout_lenses(self, tmp_path: Path) -> None:
        prompt = _render(None, tmp_path)
        _assert_hints(prompt, DISCOVERY_SCOUT_LENSES)

    def test_planning_describes_review_fanout(self, tmp_path: Path) -> None:
        prompt = _render(None, tmp_path)
        _assert_hints(prompt, REVIEW_FANOUT_HINTS)

    def test_planning_edit_has_subagent_section(self, tmp_path: Path) -> None:
        prompt = _render("planning_edit.jinja", tmp_path)
        _assert_hints(prompt, PLANNER_SUBAGENT_HINTS)

    def test_planning_edit_verifies_finding_closure_with_scouts(self, tmp_path: Path) -> None:
        prompt = _render("planning_edit.jinja", tmp_path)
        assert "one scout per analyzer finding" in prompt

    def test_planning_keeps_executor_side_parallel_section(self, tmp_path: Path) -> None:
        """Planner-side subagent guidance must coexist with executor work_units."""
        prompt = _render(None, tmp_path)
        assert "## Agent-Driven Parallel Execution" in prompt
        assert "## PLANNING WITH SUBAGENTS" in prompt

    def test_fallback_templates_keep_condensed_subagent_hint(self, tmp_path: Path) -> None:
        for template in ("planning_fallback.jinja", "planning_edit_fallback.jinja"):
            prompt = _render(template, tmp_path)
            _assert_hints(prompt, FALLBACK_SUBAGENT_HINTS)

    def test_developer_fallback_keeps_work_unit_subagent_dispatch_hint(
        self, tmp_path: Path
    ) -> None:
        """Even the developer fallback must route work_units to agent sub-agents."""
        prompt = _render_developer_fallback(tmp_path)
        assert "## Work Units" in prompt
        assert "sub-agents" in prompt
        assert "sequentially" in prompt

    def test_no_template_references_phantom_coordinate_command(self) -> None:
        """``ralph coordinate`` does not exist in the Python CLI; no bundled
        template may mention it, even as a prohibition."""
        root = packaged_template_root()
        offenders = [
            str(path.relative_to(root))
            for path in sorted(root.rglob("*"))
            if path.is_file() and "ralph coordinate" in path.read_text(encoding="utf-8")
        ]
        assert not offenders, (
            f"Templates reference the nonexistent ralph coordinate command: {offenders}"
        )


class TestPlanQualityRubric:
    """The planner sees the exact dimensions planning analysis judges on."""

    def test_planning_surfaces_rubric(self, tmp_path: Path) -> None:
        prompt = _render(None, tmp_path)
        assert RUBRIC_HEADING in prompt
        _assert_hints(prompt, RUBRIC_DIMENSIONS)

    def test_planning_edit_surfaces_rubric(self, tmp_path: Path) -> None:
        prompt = _render("planning_edit.jinja", tmp_path)
        assert RUBRIC_HEADING in prompt
        _assert_hints(prompt, RUBRIC_DIMENSIONS)

    def test_rubric_dimensions_match_planning_analysis_checklist(self) -> None:
        """Every rubric dimension must exist verbatim in the analyzer template."""
        analysis_source = (packaged_template_root() / "planning_analysis.jinja").read_text(
            encoding="utf-8"
        )
        for dimension in RUBRIC_DIMENSIONS:
            assert dimension.upper() in analysis_source.upper(), (
                f"Rubric dimension {dimension!r} is missing from "
                "planning_analysis.jinja — the shared rubric has drifted"
            )
