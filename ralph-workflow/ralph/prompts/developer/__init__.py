"""Developer prompt helpers for MCP RFC-009 templates."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.plan import PLAN_ARTIFACT_PATH
from ralph.prompts import template_engine
from ralph.prompts.developer.developer_prompt_inputs import DeveloperPromptInputs
from ralph.prompts.payload_refs import build_prompt_payload_variables, write_payload_to_directory

__all__ = [
    "DeveloperPromptInputs",
    "PlanningPromptInputs",
    "prompt_developer_iteration_xml_with_context",
    "prompt_planning_xml_with_context",
]
from ralph.prompts.template_engine import TemplateRenderingError, render_template
from ralph.prompts.types import SessionCapabilities, capability_template_variables

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.prompts.template_context import TemplateContext
    from ralph.workspace.protocol import Workspace


@dataclass(frozen=True)
class PlanningPromptInputs:
    """Inputs for rendering a planning-phase prompt."""

    prompt_content: str | None
    plan_content: str | None = None
    analysis_feedback_content: str | None = None
    plan_path: str = ""
    analysis_feedback_path: str = ""
    artifact_history_path: str = ""
    artifact_history_dir: str = ""
    current_prompt_path: str = ""
    payload_root: str = ""
    last_retry_error: str = ""
    has_docs_mcp: bool = False


def prompt_developer_iteration_xml_with_context(
    context: TemplateContext,
    inputs: DeveloperPromptInputs,
    workspace: Workspace,
    session_caps: SessionCapabilities,
    *,
    template_name: str = "developer_iteration.jinja",
) -> str:
    """Render the developer-iteration prompt, falling back to a static template on error."""
    template_content = context.registry.get_template(template_name)
    current_prompt_path = inputs.current_prompt_path or workspace.absolute_path(
        ".agent/CURRENT_PROMPT.md"
    )
    payload_root = inputs.payload_root or workspace.absolute_path(".agent/tmp/prompt_payloads")

    base_vars: dict[str, str] = {
        "DEVELOPMENT_RESULT_XML_PATH": workspace.absolute_path(
            ".agent/artifacts/development_result.json"
        ),
        "DEVELOPMENT_RESULT_XSD_PATH": workspace.absolute_path(
            ".agent/artifacts/development_result.schema.json"
        ),
        "HIDE_ARTIFACT_SUBMISSION_GUIDANCE": "true",
        "LAST_RETRY_ERROR": inputs.last_retry_error,
        "HAS_DOCS_MCP": "true" if inputs.has_docs_mcp else "",
    }
    base_vars.update(
        _current_prompt_variables(
            inputs.prompt_content,
            current_prompt_path,
        )
    )
    payload_values = {
        "PLAN": inputs.plan_content or "(no plan available)",
        "ANALYSIS_FEEDBACK": inputs.analysis_feedback_content or "",
    }
    base_vars.update(
        _prompt_payload_variables(
            payload_values,
            workspace=workspace,
            payload_root=payload_root,
            prompt_name_prefix=inputs.prompt_name_prefix,
        )
    )
    if inputs.plan_path:
        base_vars.update({"PLAN": "", "PLAN_PATH": inputs.plan_path})
    if inputs.analysis_feedback_path:
        base_vars.update(
            {
                "ANALYSIS_FEEDBACK": "",
                "ANALYSIS_FEEDBACK_PATH": inputs.analysis_feedback_path,
            }
        )
    base_vars["ARTIFACT_HISTORY_PATH"] = inputs.artifact_history_path
    base_vars["ARTIFACT_HISTORY_DIR"] = inputs.artifact_history_dir

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
            context,
            "developer_iteration_fallback.jinja",
            {
                **capability_vars,
                "PROMPT": inputs.prompt_content or "No requirements provided",
                "PLAN": inputs.plan_content or "(no plan available)",
                "ANALYSIS_FEEDBACK": inputs.analysis_feedback_content or "",
                "ARTIFACT_HISTORY_PATH": inputs.artifact_history_path,
                "ARTIFACT_HISTORY_DIR": inputs.artifact_history_dir,
                "LAST_RETRY_ERROR": inputs.last_retry_error,
                "HAS_DOCS_MCP": "true" if inputs.has_docs_mcp else "",
                "PROMPT_PATH": workspace.absolute_path(".agent/CURRENT_PROMPT.md"),
                "PLAN_PATH": inputs.plan_path
                or str(Path(payload_root) / f"{inputs.prompt_name_prefix}_plan.txt"),
                "ANALYSIS_FEEDBACK_PATH": inputs.analysis_feedback_path
                or str(
                    Path(payload_root) / f"{inputs.prompt_name_prefix}_analysis_feedback.txt"
                ),
            },
        )


def prompt_planning_xml_with_context(
    context: TemplateContext,
    inputs: PlanningPromptInputs,
    workspace: Workspace,
    session_caps: SessionCapabilities,
    *,
    template_name: str = "planning.jinja",
) -> str:
    """Render the planning-phase prompt, falling back to a static template on error."""
    template_content = context.registry.get_template(template_name)
    current_prompt_path = inputs.current_prompt_path or workspace.absolute_path(
        ".agent/CURRENT_PROMPT.md"
    )
    payload_root = inputs.payload_root or workspace.absolute_path(".agent/tmp/prompt_payloads")

    base_vars: dict[str, str] = {
        "PLAN_XML_PATH": workspace.absolute_path(PLAN_ARTIFACT_PATH),
        "PLAN_XSD_PATH": workspace.absolute_path(".agent/artifacts/plan.schema.json"),
        "LAST_RETRY_ERROR": inputs.last_retry_error,
        "HAS_DOCS_MCP": "true" if inputs.has_docs_mcp else "",
    }
    base_vars.update(
        _current_prompt_variables(
            inputs.prompt_content,
            current_prompt_path,
        )
    )
    payload_values = {
        "PLAN": inputs.plan_content or "(no plan available)",
        "ANALYSIS_FEEDBACK": inputs.analysis_feedback_content or "",
    }
    base_vars.update(
        _prompt_payload_variables(
            payload_values,
            workspace=workspace,
            payload_root=payload_root,
            prompt_name_prefix="planning",
        )
    )
    if inputs.plan_path:
        base_vars.update({"PLAN": "", "PLAN_PATH": inputs.plan_path})
    if inputs.analysis_feedback_path:
        base_vars.update(
            {
                "ANALYSIS_FEEDBACK": "",
                "ANALYSIS_FEEDBACK_PATH": inputs.analysis_feedback_path,
            }
        )
    base_vars["ARTIFACT_HISTORY_PATH"] = inputs.artifact_history_path
    base_vars["ARTIFACT_HISTORY_DIR"] = inputs.artifact_history_dir

    capability_vars = capability_template_variables(
        session_caps.capabilities,
        session_caps.policy_flags,
        tool_name_prefix=session_caps.tool_name_prefix,
    )

    variables: Mapping[str, str] = {**base_vars, **capability_vars}

    try:
        return render_template(template_content, variables, context.partials)
    except TemplateRenderingError:
        fallback_template = (
            "planning_edit_fallback.jinja"
            if template_name == "planning_edit.jinja"
            else "planning_fallback.jinja"
        )
        fallback_vars: dict[str, str] = {
            **capability_vars,
            "PROMPT": inputs.prompt_content or "No requirements provided",
            "PLAN": inputs.plan_content or "(no plan available)",
            "ANALYSIS_FEEDBACK": inputs.analysis_feedback_content or "",
            "PROMPT_PATH": current_prompt_path,
            "PLAN_PATH": inputs.plan_path
            or str(Path(payload_root) / "planning_plan.txt"),
            "ANALYSIS_FEEDBACK_PATH": inputs.analysis_feedback_path
            or str(
                Path(workspace.absolute_path(".agent/tmp/prompt_payloads"))
                / "planning_analysis_feedback.txt"
            ),
            "HAS_DOCS_MCP": "true" if inputs.has_docs_mcp else "",
        }
        fallback_vars["ARTIFACT_HISTORY_PATH"] = inputs.artifact_history_path
        fallback_vars["ARTIFACT_HISTORY_DIR"] = inputs.artifact_history_dir
        return _render_static_fallback(
            context,
            fallback_template,
            fallback_vars,
        )


def _render_static_fallback(
    context: TemplateContext,
    template_name: str,
    variables: Mapping[str, str],
) -> str:
    template = context.registry.get_template(template_name)
    return template_engine.render_template(template, variables, context.partials)


def _prompt_payload_variables(
    values: Mapping[str, str],
    *,
    workspace: Workspace,
    payload_root: str,
    prompt_name_prefix: str,
) -> dict[str, str]:
    del workspace
    output_dir = Path(payload_root)
    return build_prompt_payload_variables(
        values,
        prompt_name_prefix=prompt_name_prefix,
        write_payload=lambda relative_path, content: write_payload_to_directory(
            output_dir,
            relative_path,
            content,
        ),
    )


def _current_prompt_variables(
    prompt_content: str | None, current_prompt_path: str
) -> dict[str, str]:
    del prompt_content
    return {"PROMPT": "", "PROMPT_PATH": current_prompt_path}
