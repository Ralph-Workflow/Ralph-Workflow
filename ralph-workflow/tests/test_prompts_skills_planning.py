"""Tests for capability-neutral planning skill and docs-MCP guidance."""

import tempfile
from pathlib import Path

from ralph.prompts.developer import (
    PlanningPromptInputs,
    prompt_planning_xml_with_context,
)
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace

SHIPPED_SKILLS_DISCOVERY_HINTS = (
    "## SHIPPED SKILLS",
    "for runtimes that support",
    "If this runtime exposes a skill mechanism",
    "Read a selected `SKILL.md`",
    "`Skills:` line",
)

PLANNING_SKILLS_MCP_HINTS = (
    "Before authoring the plan's `## Skills MCP` section",
    "already available to you",
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

DESIGN_SECTION_HINTS = (
    "## Plan-artifact canonical contract",
    # The thinking-first rewrite collapses the separate Design-section prose
    # into a one-paragraph canonical-contract pointer; the rendered text
    # refers to it as `` `## Design` section `` to match the markdown
    # heading exactly, so the rendered prompt carries ``Design section``
    # only after stripping the backticks.  Accept either phrasing.
    "Design section",
    ".agent/artifact-formats/plan.md",
)


DESIGN_SECTION_HINTS_FALLBACK = (
    # The fallback prompt is condensed for non-brokered runtimes; the
    # expanded Design-section vocabulary lives in the format doc and the
    # canonical planning prompt, not in the fallback. We pin only the
    # marker that the fallback still owns the design contract reference.
    "## Plan artifact contract",
    ".agent/artifact-formats/plan.md",
)

_PLANNING_FALLBACK_TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / "ralph"
    / "prompts"
    / "templates"
    / "planning_fallback.jinja"
)


def _shared_render_planning(
    has_docs_mcp: bool,
    template: str | None = None,
    tmp_path: Path | None = None,
) -> str:
    """Render a planning prompt with optional has_docs_mcp."""
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


def _assert_shipped_skills_discovery(prompt: str) -> None:
    for hint in SHIPPED_SKILLS_DISCOVERY_HINTS:
        assert hint in prompt, f"Missing shipped-skills hint: {hint}"


def _assert_design_section_hints(prompt: str) -> None:
    for hint in DESIGN_SECTION_HINTS:
        # ``## Design`` is rendered as a literal markdown heading, so the
        # string appears as `` `## Design` section `` with backticks around
        # the heading; allow either ``Design section`` (normalized) or the
        # rendered ``## Design` section`` form.
        if hint == "Design section":
            assert (
                "Design section" in prompt or "## Design` section" in prompt
            ), f"Missing design-section hint: {hint}"
        else:
            assert hint in prompt, f"Missing design-section hint: {hint}"


def _assert_design_section_hints_fallback(prompt: str) -> None:
    for hint in DESIGN_SECTION_HINTS_FALLBACK:
        assert hint in prompt, f"Missing design-section hint: {hint}"


class TestPlanningTemplatesShippedSkills:
    """Planning templates must describe conditional skill discovery."""

    def test_planning_jinja_has_shipped_skills_section(self, tmp_path: Path) -> None:
        prompt = _shared_render_planning(False, tmp_path=tmp_path)
        _assert_shipped_skills_discovery(prompt)

    def test_planning_jinja_describes_skills_mcp_discovery(self, tmp_path: Path) -> None:
        prompt = _shared_render_planning(False, tmp_path=tmp_path)
        for hint in PLANNING_SKILLS_MCP_HINTS:
            assert hint in prompt, f"Missing skills_mcp hint: {hint}"

    def test_planning_jinja_docs_mcp_false_branch_is_visible_text(self, tmp_path: Path) -> None:
        """When has_docs_mcp=False, the false branch must render visible text."""
        prompt = _shared_render_planning(False, tmp_path=tmp_path)
        for hint_phrase in DOCS_MCP_FALSE_BRANCH_HINTS_PRIMARY:
            assert hint_phrase in prompt, f"Missing false-branch hint: {hint_phrase}"

    def test_planning_jinja_docs_mcp_true_branch_active_when_true(self, tmp_path: Path) -> None:
        """When has_docs_mcp=True, the true branch should be active."""
        prompt = _shared_render_planning(True, tmp_path=tmp_path)
        assert "arabold/docs-mcp-server" in prompt
        assert "localhost:6280" in prompt

    def test_planning_fallback_jinja_has_shipped_skills_section(self, tmp_path: Path) -> None:
        prompt = _shared_render_planning(
            False, template="planning_fallback.jinja", tmp_path=tmp_path
        )
        _assert_shipped_skills_discovery(prompt)
        normalized_prompt = " ".join(prompt.split())
        assert "Use only tools the active runtime actually exposes" in prompt
        assert "call that exact name through the runtime's tool mechanism" in normalized_prompt
        assert "Ralph guarantees" not in prompt
        assert "native orchestration tools remain enabled" not in prompt

    def test_planning_fallback_jinja_docs_mcp_false_branch_visible(self, tmp_path: Path) -> None:
        prompt = _shared_render_planning(
            False, template="planning_fallback.jinja", tmp_path=tmp_path
        )
        for hint_phrase in DOCS_MCP_FALSE_BRANCH_HINTS_FALLBACK:
            assert hint_phrase in prompt, f"Missing false-branch hint: {hint_phrase}"

    def test_planning_jinja_has_design_section(self, tmp_path: Path) -> None:
        prompt = _shared_render_planning(False, tmp_path=tmp_path)
        _assert_design_section_hints(prompt)

    def test_planning_fallback_jinja_has_design_section(self, tmp_path: Path) -> None:
        prompt = _shared_render_planning(
            False, template="planning_fallback.jinja", tmp_path=tmp_path
        )
        _assert_design_section_hints_fallback(prompt)

    def test_planning_fallback_teaches_executor_shapes_and_proof(self) -> None:
        source = _PLANNING_FALLBACK_TEMPLATE.read_text(encoding="utf-8")
        normalized = " ".join(source.split())

        for phrase in (
            "small bounded tasks or verification gates",
            "substantial execution-subagent mini-plan",
            "four or five independent execution Subplans",
            "main-session integration and verification",
            "project-specific `Type:` values are accepted and preserved verbatim",
        ):
            assert phrase in normalized
        assert "normalize to `action`" not in source
