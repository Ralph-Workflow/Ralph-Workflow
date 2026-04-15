"""Developer prompt helpers for MCP RFC-009 templates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.prompts.template_engine import TemplateRenderingError, render_template
from ralph.prompts.template_registry import packaged_template_root
from ralph.prompts.types import SessionCapabilities, capability_template_variables

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.prompts.template_context import TemplateContext
    from ralph.workspace.protocol import Workspace


@dataclass(frozen=True)
class DeveloperPromptInputs:
    prompt_content: str | None
    plan_content: str | None


def prompt_developer_iteration_xml_with_context(
    context: TemplateContext,
    inputs: DeveloperPromptInputs,
    workspace: Workspace,
    session_caps: SessionCapabilities,
    *,
    template_name: str = "developer_iteration.jinja",
) -> str:
    template_content = context.registry.get_template(template_name)

    base_vars: dict[str, str] = {
        "PROMPT": inputs.prompt_content or "No requirements provided",
        "PLAN": inputs.plan_content or "(no plan available)",
        "DEVELOPMENT_RESULT_XML_PATH": workspace.absolute_path(
            ".agent/artifacts/development_result.json"
        ),
        "DEVELOPMENT_RESULT_XSD_PATH": workspace.absolute_path(
            ".agent/artifacts/development_result.schema.json"
        ),
    }

    capability_vars = capability_template_variables(
        session_caps.capabilities,
        session_caps.policy_flags,
        tool_name_prefix=session_caps.tool_name_prefix,
    )

    variables: Mapping[str, str] = {**base_vars, **capability_vars}

    try:
        return render_template(template_content, variables, context.partials)
    except TemplateRenderingError:
        return _render_static_fallback(
            "developer_iteration_fallback.jinja",
            {
                "PROMPT": inputs.prompt_content or "No requirements provided",
                "PLAN": inputs.plan_content or "(no plan available)",
            },
        )


def prompt_planning_xml_with_context(
    context: TemplateContext,
    prompt_content: str | None,
    workspace: Workspace,
    session_caps: SessionCapabilities,
    *,
    template_name: str = "planning.jinja",
) -> str:
    template_content = context.registry.get_template(template_name)

    prompt_md = prompt_content or "No requirements provided"
    base_vars: dict[str, str] = {
        "PROMPT": prompt_md,
        "PLAN_XML_PATH": workspace.absolute_path(".agent/artifacts/plan.json"),
        "PLAN_XSD_PATH": workspace.absolute_path(".agent/artifacts/plan.schema.json"),
    }

    capability_vars = capability_template_variables(
        session_caps.capabilities,
        session_caps.policy_flags,
        tool_name_prefix=session_caps.tool_name_prefix,
    )

    variables: Mapping[str, str] = {**base_vars, **capability_vars}

    try:
        return render_template(template_content, variables, context.partials)
    except TemplateRenderingError:
        return _render_static_fallback(
            "planning_fallback.jinja",
            {
                "PROMPT": prompt_md,
            },
        )


def _render_static_fallback(template_name: str, variables: Mapping[str, str]) -> str:
    template_path = packaged_template_root() / template_name
    template = template_path.read_text(encoding="utf-8")
    rendered = template
    for key, value in variables.items():
        rendered = rendered.replace(f"{{{{ {key} }}}}", value)
        rendered = rendered.replace(f"{{{{{key}}}}}", value)
    return rendered
