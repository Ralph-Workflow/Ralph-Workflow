"""Developer prompt helpers for MCP RFC-009 templates."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.prompts.policy_templates import (
    DEVELOPER_ITERATION_TEMPLATE,
    PLANNING_TEMPLATE,
    SHARED_PARTIALS,
)
from ralph.prompts.template_engine import TemplateRenderingError, render_template
from ralph.prompts.types import SessionCapabilities, capability_template_variables

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.prompts.template_context import TemplateContext
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
            ".agent/artifacts/development_result.json"
        ),
        "DEVELOPMENT_RESULT_XSD_PATH": workspace.absolute_path(
            ".agent/artifacts/development_result.schema.json"
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
            f"IMPLEMENTATION MODE\n\nORIGINAL REQUEST:\n{prompt}\n\n"
            f"IMPLEMENTATION PLAN:\n{plan}\n\n"
            'When done, call `ralph_submit_artifact` with artifact_type="development_result" '
            "and content as JSON.\n"
            "Write the result artifact to .agent/artifacts/development_result.json.\n"
            '{"status":"completed","summary":"Summary","files_changed":"- src/foo.rs"}\n'
        )


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
        "PLAN_XML_PATH": workspace.absolute_path(".agent/artifacts/plan.json"),
        "PLAN_XSD_PATH": workspace.absolute_path(".agent/artifacts/plan.schema.json"),
    }

    capability_vars = capability_template_variables(
        session_caps.capabilities, session_caps.policy_flags
    )

    variables: Mapping[str, str] = {**base_vars, **capability_vars}

    try:
        return render_template(template_content, variables, SHARED_PARTIALS)
    except TemplateRenderingError:
        return (
            f"PLANNING MODE\n\nCreate an implementation plan for:\n\n{prompt_md}\n\n"
            'Submit the plan via `ralph_submit_artifact` with artifact_type="plan".\n'
            "Write the plan artifact to .agent/artifacts/plan.json.\n"
            '{"summary":{"context":"What is being done and why","scope_items":[]},'
            '"steps":[],"critical_files":{"primary_files":[]},'
            '"risks_mitigations":[],"verification_strategy":[]}\n'
        )
