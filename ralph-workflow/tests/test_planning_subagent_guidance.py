"""Rendered regression checks for concise planning guidance."""

from __future__ import annotations

import pytest

from ralph.mcp.protocol.capability_mapping import SessionDrain
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.template_engine import render_template
from ralph.prompts.types import SessionCapabilities, capability_template_variables


def _render_planner(name: str) -> str:
    context = TemplateContext.default()
    session = SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING)
    return render_template(
        context.registry.get_template(name),
        {
            **capability_template_variables(session.capabilities, session.policy_flags),
            "PRODUCT_CRITERIA": "Update independent command and documentation behavior.",
            "PRODUCT_CRITERIA_PATH": "fixtures/request.md",
            "ANALYSIS_FEEDBACK": "Avoidable serialization: split command and documentation units.",
            "ANALYSIS_FEEDBACK_PATH": "fixtures/feedback.md",
            "HAS_DOCS_MCP": "",
            "DOCS_MCP_PORT": "localhost:6280",
            "LAST_RETRY_ERROR": "",
            "SKILLS_INLINE_CONTENT": "",
        },
        context.partials,
    )


@pytest.mark.parametrize(
    "name", ("planning", "planning_fallback", "planning_edit", "planning_edit_fallback")
)
def test_rendered_planners_delegate_independent_work_by_default(name: str) -> None:
    rendered = " ".join(_render_planner(name).split())

    for required in (
        "Delegate exploration, research, verification, and review to read-only",
        "Fan-out is the default",
        "multiple independent areas",
        "Two independent tasks are enough to fan out",
    ):
        assert required in rendered
    assert "Use a subagent only" not in rendered
