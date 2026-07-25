"""Regression tests for prompt-template / capability-variable version skew.

Packaged prompt templates are read from disk when a prompt is
rendered, but the capability variables that feed them are fixed when
the process imports ``ralph.prompts``. A run that is in flight while
its checkout moves underneath it therefore renders *new* templates
against *old* variables.

Observed 2026-07-25: a run rendered post-upgrade templates against
pre-upgrade variables and died with
``'READ_MULTIPLE_FILES_TOOL_REFERENCE' is undefined``. The static
fallback template failed the same way
(``'GIT_STATUS_TOOL_REFERENCE' is undefined``), so the pipeline had no
recovery path and terminated the whole run.

Two independent guards are asserted here:

- an unknown ``*_TOOL_NAME`` / ``*_TOOL_REFERENCE`` variable renders as
  an absent tool instead of raising, so version skew degrades the
  prompt rather than killing the run;
- every tool the MCP tool enum defines always has both variables, so a
  newly added tool cannot ship a template reference without them.
"""

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
    "worker_developer.jinja",
)

BASE_VARIABLES = {
    "HIDE_ARTIFACT_SUBMISSION_GUIDANCE": "true",
    "LAST_RETRY_ERROR": "",
    "PRIOR_RESULT_STATUS": "",
    "PRIOR_RESULT_SUMMARY": "",
    "PRIOR_RESULT_NEXT_STEPS": "",
    "PRIOR_RESULT_CONTINUATION": "",
    "SKILLS_INLINE_CONTENT": "",
    "HAS_DOCS_MCP": "",
    "DOCS_MCP_PORT": "localhost:6280",
    "unit_id": "",
    "description": "",
    "allowed_directories": "",
    "IS_WORKER": "",
    "IS_CONTINUATION": "",
    "WORKER_NAMESPACE": "",
    "WORKER_FALLBACK_PATH": "",
    "PROMPT": "",
    "PROMPT_PATH": "/workspace/.agent/PRODUCT_CRITERIA.md",
    "PLAN": "Step 1",
    "PLAN_PATH": "",
    "ANALYSIS_FEEDBACK": "",
    "ANALYSIS_FEEDBACK_PATH": "",
    "ARTIFACT_HISTORY_PATH": "",
    "ARTIFACT_HISTORY_DIR": "",
}


def _variables_without_tool_variables() -> dict[str, str]:
    """Return development capability variables as an older build would supply them.

    Every tool NAME/REFERENCE variable is dropped, which is the worst
    case of the observed skew: templates that reference tools the
    running code has never heard of.
    """
    variables = capability_template_variables(
        *default_caps_and_flags_for_drain(SessionDrain.DEVELOPMENT)
    )
    return {
        name: value
        for name, value in variables.items()
        if not name.endswith(("_TOOL_NAME", "_TOOL_REFERENCE"))
    }


def test_unknown_tool_reference_variable_renders_as_an_absent_tool() -> None:
    """A tool variable the running build does not know renders empty, not fatally."""
    rendered = render_template("READ: [{{FUTURE_THING_TOOL_REFERENCE}}]", {}, {})

    assert rendered == "READ: []"


def test_unknown_tool_variable_is_falsey_in_a_conditional() -> None:
    """Templates guard optional tools with ``{% if %}``; skew must take the off branch."""
    template = "{% if FUTURE_THING_TOOL_NAME %}present{% else %}absent{% endif %}"

    assert render_template(template, {}, {}) == "absent"


def test_unknown_non_tool_variable_still_fails_the_render() -> None:
    """Tolerance is scoped to tool variables; every other typo stays a hard error."""
    with pytest.raises(TemplateRenderingError, match="'MISSING_SECTION' is undefined"):
        render_template("{{MISSING_SECTION}}", {}, {})


def test_every_mcp_tool_exposes_both_of_its_template_variables() -> None:
    """A tool cannot exist without the variables its templates reference."""
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
    """Current templates rendered by an older build must still produce a prompt."""
    context = TemplateContext.default()
    variables = {**BASE_VARIABLES, **_variables_without_tool_variables()}

    rendered = render_template(
        context.registry.get_template(template_name),
        variables,
        context.partials,
    )

    assert "UNATTENDED MODE" in rendered
