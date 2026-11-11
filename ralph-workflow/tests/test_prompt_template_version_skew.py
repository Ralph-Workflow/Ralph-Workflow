from __future__ import annotations

import pytest

from ralph.mcp.protocol.capability_mapping import SessionDrain
from ralph.mcp.tools.names import RalphToolName
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.template_engine import render_template
from ralph.prompts.template_rendering_error import TemplateRenderingError
from ralph.prompts.template_variables import (
    capability_template_variables,
    default_caps_and_flags_for_drain,
)

SKEW_SENSITIVE_TEMPLATES = (
    "developer_iteration.jinja",
    "developer_iteration_fallback.jinja",
    "developer_iteration_continuation.jinja",
)

BASE_VARIABLES = {
    "HIDE_ARTIFACT_SUBMISSION_GUIDANCE": "true",
    "LAST_RETRY_ERROR": "",
    "PRIOR_RESULT_STATUS": "",
    "PRIOR_RESULT_MARKDOWN": "",
    "PRIOR_SESSION_ID": "",
    "SKILLS_INLINE_CONTENT": "",
    "HAS_DOCS_MCP": "",
    "DOCS_MCP_PORT": "localhost:6280",
    "IS_CONTINUATION": "",
    "PRODUCT_CRITERIA": "",
    "PRODUCT_CRITERIA_PATH": "/workspace/.agent/PRODUCT_CRITERIA.md",
    "PLAN": "Step 1",
    "PLAN_PATH": "",
    "ANALYSIS_FEEDBACK": "",
    "ANALYSIS_FEEDBACK_PATH": "",
    "ARTIFACT_HISTORY_PATH": "",
    "ARTIFACT_HISTORY_DIR": "",
}


def _variables_without_tool_variables() -> dict[str, str]:
    variables = capability_template_variables(
        *default_caps_and_flags_for_drain(SessionDrain.DEVELOPMENT)
    )
    return {
        name: value
        for name, value in variables.items()
        if not name.endswith(("_TOOL_NAME", "_TOOL_REFERENCE"))
    }


def test_unknown_tool_reference_variable_renders_as_an_absent_tool() -> None:
    rendered = render_template("READ: [{{FUTURE_THING_TOOL_REFERENCE}}]", {}, {})

    assert rendered == "READ: []"


def test_unknown_tool_variable_is_falsey_in_a_conditional() -> None:
    template = "{% if FUTURE_THING_TOOL_NAME %}present{% else %}absent{% endif %}"

    assert render_template(template, {}, {}) == "absent"


def test_unknown_non_tool_variable_still_fails_the_render() -> None:
    with pytest.raises(TemplateRenderingError, match="'MISSING_SECTION' is undefined"):
        render_template("{{MISSING_SECTION}}", {}, {})


def test_every_mcp_tool_exposes_both_of_its_template_variables() -> None:
    variables = capability_template_variables(
        *default_caps_and_flags_for_drain(SessionDrain.DEVELOPMENT)
    )

    missing = [
        f"{tool.name}_TOOL_{suffix}"
        for tool in RalphToolName
        for suffix in ("NAME", "REFERENCE")
        if f"{tool.name}_TOOL_{suffix}" not in variables
    ]

    assert missing == []


@pytest.mark.parametrize("template_name", SKEW_SENSITIVE_TEMPLATES)
def test_developer_templates_survive_a_stale_capability_variable_set(
    template_name: str,
) -> None:
    context = TemplateContext.default()
    variables = {**BASE_VARIABLES, **_variables_without_tool_variables()}

    rendered = render_template(
        context.registry.get_template(template_name),
        variables,
        context.partials,
    )

    assert "UNATTENDED MODE" in rendered
