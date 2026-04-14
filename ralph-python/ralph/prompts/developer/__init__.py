"""Developer prompt helpers for MCP RFC-009 templates."""

from __future__ import annotations

from typing import Mapping

from ralph.prompts.policy_templates import (
    DEVELOPER_ITERATION_TEMPLATE,
    PLANNING_TEMPLATE,
    SHARED_PARTIALS,
)
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.template_engine import TemplateRenderingError, render_template
from ralph.prompts.types import SessionCapabilities, capability_template_variables
from ralph.workspace.protocol import Workspace


def prompt_developer_iteration_xml_with_context(
    context: TemplateContext,
    prompt_content: str | None,
    plan_content: str | None,
    workspace: Workspace,
    session_caps: SessionCapabilities,
) -> str:
    template_name = "developer_iteration"
    try:
        template_content = context.registry.get_template(template_name)
    except KeyError:
        template_content = DEVELOPER_ITERATION_TEMPLATE

    base_vars: dict[str, str] = {
        "PROMPT": prompt_content or "No requirements provided",
        "PLAN": plan_content or "(no plan available)",
        "DEVELOPMENT_RESULT_XML_PATH": workspace.absolute_path(
            ".agent/tmp/development_result.xml"
        ),
        "DEVELOPMENT_RESULT_XSD_PATH": workspace.absolute_path(
            ".agent/tmp/development_result.xsd"
        ),
    }

    capability_vars = capability_template_variables(
        session_caps.capabilities, session_caps.policy_flags
    )

    variables: Mapping[str, str] = {**base_vars, **capability_vars}

    try:
        return render_template(template_content, variables, SHARED_PARTIALS)
    except TemplateRenderingError:
        prompt = prompt_content or "No requirements provided"
        plan = plan_content or "(no plan available)"
        return (
            "IMPLEMENTATION MODE\n\nORIGINAL REQUEST:\n{prompt}\n\n"
            "IMPLEMENTATION PLAN:\n{plan}\n\n"
            "Output format: <ralph-development-result><ralph-status>completed|partial|failed</ralph-status>"
            "<ralph-summary>Summary</ralph-summary></ralph-development-result>\n"
        ).format(prompt=prompt, plan=plan)


def prompt_planning_xml_with_context(
    context: TemplateContext,
    prompt_content: str | None,
    workspace: Workspace,
    session_caps: SessionCapabilities,
) -> str:
    template_name = "planning"
    try:
        template_content = context.registry.get_template(template_name)
    except KeyError:
        template_content = PLANNING_TEMPLATE

    prompt_md = prompt_content or "No requirements provided"
    base_vars: dict[str, str] = {
        "PROMPT": prompt_md,
        "PLAN_XML_PATH": workspace.absolute_path(".agent/tmp/plan.xml"),
        "PLAN_XSD_PATH": workspace.absolute_path(".agent/tmp/plan.xsd"),
    }

    capability_vars = capability_template_variables(
        session_caps.capabilities, session_caps.policy_flags
    )

    variables: Mapping[str, str] = {**base_vars, **capability_vars}

    try:
        return render_template(template_content, variables, SHARED_PARTIALS)
    except TemplateRenderingError:
        return (
            "PLANNING MODE\n\nCreate an implementation plan for:\n\n{prompt}\n\n"
            "Output format: <ralph-plan><ralph-summary>Summary</ralph-summary><ralph-implementation-steps>Steps</ralph-implementation-steps></ralph-plan>\n"
        ).format(prompt=prompt_md)
