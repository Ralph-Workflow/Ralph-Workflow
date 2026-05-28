"""Tests that planning_edit templates contain BASELINE WORKFLOW SKILLS and docs-mcp guidance."""

from pathlib import Path

from ralph.prompts.developer import (
    PlanningPromptInputs,
    prompt_planning_xml_with_context,
)
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace

PLANNING_SKILL_NAMES = frozenset(
    {
        "using-superpowers",
        "writing-plans",
        "brainstorming",
        "executing-plans",
        "dispatching-parallel-agents",
        "subagent-driven-development",
        "coding-standards",
        "verification-loop",
        "security-review",
    }
)

DOCS_MCP_FALSE_BRANCH_HINTS_PRIMARY = (
    "arabold/docs-mcp-server",
    "localhost:6280",
    ".agent/mcp.toml",
    "improves library and API documentation lookup quality",
)

DOCS_MCP_FALSE_BRANCH_HINTS_FALLBACK = (
    "arabold/docs-mcp-server",
    "localhost:6280",
    ".agent/mcp.toml",
    "improves documentation lookup quality",
)


def _shared_render_planning(
    has_docs_mcp: bool,
    template: str | None = None,
    tmp_path: Path | None = None,
) -> str:
    """Render a planning prompt with optional has_docs_mcp."""
    import tempfile

    if tmp_path is None:
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
    context = TemplateContext.default()
    workspace = MemoryWorkspace(root=str(tmp_path))
    session_caps = SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT)
    inputs = PlanningPromptInputs(
        prompt_content="Implement the feature",
        has_docs_mcp=has_docs_mcp,
    )
    kwargs: dict = {
        "context": context,
        "inputs": inputs,
        "workspace": workspace,
        "session_caps": session_caps,
    }
    if template is not None:
        kwargs["template_name"] = template
    return prompt_planning_xml_with_context(**kwargs)


class TestPlanningEditTemplatesBaselineSkills:
    """planning_edit.jinja and planning_edit_fallback.jinja."""

    def test_planning_edit_jinja_has_baseline_workflow_skills(self, tmp_path: Path) -> None:
        prompt = _shared_render_planning(False, template="planning_edit.jinja", tmp_path=tmp_path)
        assert "## BASELINE WORKFLOW SKILLS" in prompt

    def test_planning_edit_jinja_contains_required_skill_names(self, tmp_path: Path) -> None:
        prompt = _shared_render_planning(False, template="planning_edit.jinja", tmp_path=tmp_path)
        for skill_name in PLANNING_SKILL_NAMES:
            assert f"`{skill_name}`" in prompt, f"Missing skill: {skill_name}"

    def test_planning_edit_jinja_docs_mcp_false_branch_visible(self, tmp_path: Path) -> None:
        prompt = _shared_render_planning(False, template="planning_edit.jinja", tmp_path=tmp_path)
        for hint_phrase in DOCS_MCP_FALSE_BRANCH_HINTS_PRIMARY:
            assert hint_phrase in prompt, f"Missing false-branch hint: {hint_phrase}"

    def test_planning_edit_fallback_jinja_has_baseline_workflow_skills(
        self, tmp_path: Path
    ) -> None:
        prompt = _shared_render_planning(
            False, template="planning_edit_fallback.jinja", tmp_path=tmp_path
        )
        assert "## BASELINE WORKFLOW SKILLS" in prompt

    def test_planning_edit_fallback_jinja_contains_required_skill_names(
        self, tmp_path: Path
    ) -> None:
        prompt = _shared_render_planning(
            False, template="planning_edit_fallback.jinja", tmp_path=tmp_path
        )
        for skill_name in PLANNING_SKILL_NAMES:
            assert f"`{skill_name}`" in prompt, f"Missing skill: {skill_name}"

    def test_planning_edit_fallback_jinja_docs_mcp_false_branch_visible(
        self, tmp_path: Path
    ) -> None:
        prompt = _shared_render_planning(
            False, template="planning_edit_fallback.jinja", tmp_path=tmp_path
        )
        for hint_phrase in DOCS_MCP_FALSE_BRANCH_HINTS_FALLBACK:
            assert hint_phrase in prompt, f"Missing false-branch hint: {hint_phrase}"
