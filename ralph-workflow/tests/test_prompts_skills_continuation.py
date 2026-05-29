"""Tests that developer_iteration_continuation uses Skill-tool discovery."""

import tempfile
from pathlib import Path

from ralph.prompts.developer import (
    DeveloperPromptInputs,
    prompt_developer_iteration_xml_with_context,
)
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace

SHIPPED_SKILLS_DISCOVERY_HINTS = (
    "## SHIPPED SKILLS",
    "discovers them automatically",
    "Do not Read",
    "Skills and MCPs section",
)

DOCS_MCP_FALSE_BRANCH_HINTS_PRIMARY = (
    "arabold/docs-mcp-server",
    "localhost:6280",
    ".agent/mcp.toml",
    "improves library and API documentation lookup quality",
)


def _shared_render_developer(
    has_docs_mcp: bool,
    template: str | None = None,
    tmp_path: Path | None = None,
) -> str:
    """Render a developer prompt with optional has_docs_mcp."""
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


class TestDeveloperContinuationTemplateShippedSkills:
    """developer_iteration_continuation.jinja."""

    def test_continuation_jinja_has_shipped_skills_section(self, tmp_path: Path) -> None:
        prompt = _shared_render_developer(
            False, template="developer_iteration_continuation.jinja", tmp_path=tmp_path
        )
        for hint in SHIPPED_SKILLS_DISCOVERY_HINTS:
            assert hint in prompt, f"Missing shipped-skills hint: {hint}"

    def test_continuation_jinja_docs_mcp_false_branch_visible(
        self, tmp_path: Path
    ) -> None:
        prompt = _shared_render_developer(
            False, template="developer_iteration_continuation.jinja", tmp_path=tmp_path
        )
        for hint_phrase in DOCS_MCP_FALSE_BRANCH_HINTS_PRIMARY:
            assert hint_phrase in prompt, f"Missing false-branch hint: {hint_phrase}"

    def test_continuation_jinja_docs_mcp_true_branch_active(self, tmp_path: Path) -> None:
        prompt = _shared_render_developer(
            True, template="developer_iteration_continuation.jinja", tmp_path=tmp_path
        )
        assert "arabold/docs-mcp-server" in prompt
        assert "localhost:6280" in prompt
