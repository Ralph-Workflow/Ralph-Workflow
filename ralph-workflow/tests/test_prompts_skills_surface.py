"""Surface-level tests that all seven prompt templates contain BASELINE WORKFLOW SKILLS and visible docs-mcp hints.

This is the canonical test file referenced in the plan. It validates all seven live
prompt templates for the presence of required skill names and visible docs-mcp
false-branch hint text.

The seven templates are:
  1. planning.jinja
  2. planning_fallback.jinja
  3. planning_edit.jinja
  4. planning_edit_fallback.jinja
  5. developer_iteration.jinja
  6. developer_iteration_fallback.jinja
  7. developer_iteration_continuation.jinja
"""

from pathlib import Path

import pytest

from ralph.prompts.developer import (
    DeveloperPromptInputs,
    PlanningPromptInputs,
    prompt_developer_iteration_xml_with_context,
    prompt_planning_xml_with_context,
)
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace

# ---------------------------------------------------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------------------------------------------------

PLANNING_SKILL_NAMES = frozenset({
    "using-superpowers",
    "writing-plans",
    "brainstorming",
    "executing-plans",
    "dispatching-parallel-agents",
    "subagent-driven-development",
    "coding-standards",
    "verification-loop",
})

DEVELOPER_SKILL_NAMES = frozenset({
    "using-superpowers",
    "test-driven-development",
    "systematic-debugging",
    "verification-before-completion",
    "requesting-code-review",
    "receiving-code-review",
    "security-review",
    "verification-loop",
    "coding-standards",
    "using-git-worktrees",
    "finishing-a-development-branch",
})

# Phrases that MUST appear in rendered output when has_docs_mcp=False (visible hint text, not Jinja comments)
DOCS_MCP_FALSE_BRANCH_HINTS = (
    "arabold/docs-mcp-server",
    "localhost:6280",
    ".agent/mcp.toml",
    "improves library and API documentation lookup quality",
)


# ---------------------------------------------------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------------------------------------------------

def _render_planning(
    has_docs_mcp: bool,
    template: str | None = None,
    *,
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


def _render_developer(
    has_docs_mcp: bool,
    template: str | None = None,
    *,
    tmp_path: Path | None = None,
) -> str:
    """Render a developer prompt with optional has_docs_mcp."""
    import tempfile

    if tmp_path is None:
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
    context = TemplateContext.default()
    workspace = MemoryWorkspace(root=str(tmp_path))
    session_caps = SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT)
    inputs = DeveloperPromptInputs(
        prompt_content="Fix the bug",
        plan_content="1. Find the bug\n2. Fix it",
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
    return prompt_developer_iteration_xml_with_context(**kwargs)


# ---------------------------------------------------------------------------------------------------------------------
# Planning templates (1-4)
// ---------------------------------------------------------------------------------------------------------------------

class TestSurfacePlanningTemplates:
    """All four planning templates must contain BASELINE WORKFLOW SKILLS and visible docs-mcp hints."""

    @pytest.mark.parametrize(
        "template",
        [
            "planning.jinja",
            "planning_fallback.jinja",
            "planning_edit.jinja",
            "planning_edit_fallback.jinja",
        ],
    )
    def test_baseline_workflow_skills_section_present(
        self, template: str, tmp_path: Path
    ) -> None:
        prompt = _render_planning(False, template=template, tmp_path=tmp_path)
        assert "## BASELINE WORKFLOW SKILLS" in prompt

    @pytest.mark.parametrize(
        "template",
        [
            "planning.jinja",
            "planning_fallback.jinja",
            "planning_edit.jinja",
            "planning_edit_fallback.jinja",
        ],
    )
    def test_required_planning_skill_names_present(
        self, template: str, tmp_path: Path
    ) -> None:
        prompt = _render_planning(False, template=template, tmp_path=tmp_path)
        for skill_name in PLANNING_SKILL_NAMES:
            assert f"`{skill_name}`" in prompt, f"Missing skill: {skill_name}"

    @pytest.mark.parametrize(
        "template",
        [
            "planning.jinja",
            "planning_fallback.jinja",
            "planning_edit.jinja",
            "planning_edit_fallback.jinja",
        ],
    )
    def test_docs_mcp_false_branch_is_visible_text(
        self, template: str, tmp_path: Path
    ) -> None:
        """When has_docs_mcp=False, the false branch must render visible user-facing text."""
        prompt = _render_planning(False, template=template, tmp_path=tmp_path)
        for hint_phrase in DOCS_MCP_FALSE_BRANCH_HINTS:
            assert hint_phrase in prompt, f"Missing false-branch hint: {hint_phrase}"

    @pytest.mark.parametrize(
        "template",
        [
            "planning.jinja",
            "planning_edit.jinja",
        ],
    )
    def test_docs_mcp_true_branch_active_when_true(
        self, template: str, tmp_path: Path
    ) -> None:
        """When has_docs_mcp=True, the true branch should be active."""
        prompt = _render_planning(True, template=template, tmp_path=tmp_path)
        assert "arabold/docs-mcp-server" in prompt
        assert "localhost:6280" in prompt


# ---------------------------------------------------------------------------------------------------------------------
# Developer templates (5-7)
// ---------------------------------------------------------------------------------------------------------------------

class TestSurfaceDeveloperTemplates:
    """All three developer templates must contain BASELINE WORKFLOW SKILLS and visible docs-mcp hints."""

    @pytest.mark.parametrize(
        "template",
        [
            "developer_iteration.jinja",
            "developer_iteration_fallback.jinja",
            "developer_iteration_continuation.jinja",
        ],
    )
    def test_baseline_workflow_skills_section_present(
        self, template: str, tmp_path: Path
    ) -> None:
        prompt = _render_developer(False, template=template, tmp_path=tmp_path)
        assert "## BASELINE WORKFLOW SKILLS" in prompt

    @pytest.mark.parametrize(
        "template",
        [
            "developer_iteration.jinja",
            "developer_iteration_fallback.jinja",
            "developer_iteration_continuation.jinja",
        ],
    )
    def test_required_developer_skill_names_present(
        self, template: str, tmp_path: Path
    ) -> None:
        prompt = _render_developer(False, template=template, tmp_path=tmp_path)
        for skill_name in DEVELOPER_SKILL_NAMES:
            assert f"`{skill_name}`" in prompt, f"Missing skill: {skill_name}"

    @pytest.mark.parametrize(
        "template",
        [
            "developer_iteration.jinja",
            "developer_iteration_fallback.jinja",
            "developer_iteration_continuation.jinja",
        ],
    )
    def test_docs_mcp_false_branch_is_visible_text(
        self, template: str, tmp_path: Path
    ) -> None:
        """When has_docs_mcp=False, the false branch must render visible user-facing text."""
        prompt = _render_developer(False, template=template, tmp_path=tmp_path)
        for hint_phrase in DOCS_MCP_FALSE_BRANCH_HINTS:
            assert hint_phrase in prompt, f"Missing false-branch hint: {hint_phrase}"

    @pytest.mark.parametrize(
        "template",
        [
            "developer_iteration.jinja",
            "developer_iteration_continuation.jinja",
        ],
    )
    def test_docs_mcp_true_branch_active_when_true(
        self, template: str, tmp_path: Path
    ) -> None:
        """When has_docs_mcp=True, the true branch should be active."""
        prompt = _render_developer(True, template=template, tmp_path=tmp_path)
        assert "arabold/docs-mcp-server" in prompt
        assert "localhost:6280" in prompt
