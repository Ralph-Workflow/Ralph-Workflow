"""Policy-selected prompt materialization."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, cast

from git import Repo

from ralph.config.enums import AgentTransport
from ralph.mcp.tool_names import SUBMIT_ARTIFACT_TOOL, claude_tool_name_prefix
from ralph.prompts.commit import CommitPromptPayloadConfig, prompt_commit_message
from ralph.prompts.debug_dump import dump_rendered_prompt, prompt_dump_path
from ralph.prompts.developer import (
    DeveloperPromptInputs,
    prompt_developer_iteration_xml_with_context,
    prompt_planning_xml_with_context,
)
from ralph.prompts.payload_refs import build_prompt_payload_variables, write_payload_to_directory
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.template_engine import render_template
from ralph.prompts.types import SessionCapabilities, capability_template_variables

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.policy.models import PipelinePolicy
    from ralph.workspace.protocol import Workspace


def materialize_prompt_for_phase(
    *,
    phase: str,
    workspace: Workspace,
    pipeline_policy: PipelinePolicy,
    session_caps: SessionCapabilities,
    workspace_root: Path,
) -> str:
    prompt = _render_prompt_for_phase(
        phase=phase,
        workspace=workspace,
        pipeline_policy=pipeline_policy,
        session_caps=session_caps,
        workspace_root=workspace_root,
    )
    return dump_rendered_prompt(workspace, phase, prompt)


def prompt_file_for_phase(phase: str) -> str:
    return prompt_dump_path(phase)


def _template_name_for_phase(phase: str, pipeline_policy: PipelinePolicy) -> str:
    phase_def = pipeline_policy.phases.get(phase)
    if phase_def is None or phase_def.prompt_template is None:
        msg = f"No prompt_template configured for phase '{phase}'"
        raise ValueError(msg)
    return phase_def.prompt_template


def _render_prompt_for_phase(
    phase: str,
    workspace: Workspace,
    pipeline_policy: PipelinePolicy,
    session_caps: SessionCapabilities,
    workspace_root: Path,
) -> str:
    context = TemplateContext.default(workspace_root)
    template_name = _template_name_for_phase(phase, pipeline_policy)
    prompt_content = _read_optional(workspace, "PROMPT.md")
    current_prompt_path = _persist_current_prompt(workspace_root, prompt_content)
    plan_content = _resolve_plan_content(workspace)
    if phase == "planning":
        return prompt_planning_xml_with_context(
            context=context,
            prompt_content=prompt_content,
            workspace=workspace,
            session_caps=session_caps,
            template_name=template_name,
        )
    if phase == "development":
        return prompt_developer_iteration_xml_with_context(
            context=context,
            inputs=DeveloperPromptInputs(
                prompt_content=prompt_content,
                plan_content=plan_content,
                prompt_name_prefix=phase,
            ),
            workspace=workspace,
            session_caps=session_caps,
            template_name=template_name,
        )
    if phase in {"review", "fix", "development_analysis", "review_analysis"}:
        template = context.registry.get_template(template_name)
        diff_content = _git_diff(workspace_root)
        variables = _phase_payload_variables(
            phase=phase,
            workspace_root=workspace_root,
            values={
                "PLAN": plan_content or "(no plan available)",
                "DIFF": diff_content,
                "CHANGES": diff_content,
                "LATEST_ARTIFACT": _latest_artifact_content(workspace, phase),
                "ISSUES": _resolve_issues_content(workspace),
                "FIX_RESULT": _resolve_fix_result_content(workspace),
            },
        )
        variables.update(_current_prompt_variables(prompt_content, current_prompt_path))
        return render_template(
            template,
            _merged_variables(variables, session_caps),
            context.partials,
        )
    if phase in {"development_commit", "review_commit"}:
        return prompt_commit_message(
            _commit_phase_diff(workspace_root),
            template_registry=context.registry,
            partials=context.partials,
            submit_artifact_tool_names=SUBMIT_ARTIFACT_TOOL.prompt_aliases(
                tool_name_prefix=session_caps.tool_name_prefix,
            ),
            payload_config=CommitPromptPayloadConfig(
                output_dir=workspace_root / ".agent" / "tmp" / "prompt_payloads",
                name_prefix=phase,
            ),
        )
    msg = f"Unsupported phase '{phase}' for prompt materialization"
    raise ValueError(msg)


def _merged_variables(base: dict[str, str], session_caps: SessionCapabilities) -> dict[str, str]:
    return {
        **base,
        **capability_template_variables(
            session_caps.capabilities,
            session_caps.policy_flags,
            tool_name_prefix=session_caps.tool_name_prefix,
        ),
    }


def tool_name_prefix_for_transport(transport: AgentTransport | None) -> str:
    # Prompt templates should talk about the same tool names the current agent
    # transport will actually see. Claude gets namespaced MCP tools; other
    # transports continue to see Ralph's bare tool names.
    if transport == AgentTransport.CLAUDE:
        return claude_tool_name_prefix()
    return ""


def _read_optional(workspace: Workspace, path: str) -> str | None:
    if not workspace.exists(path):
        return None
    return workspace.read(path)


def _phase_payload_variables(
    *,
    phase: str,
    workspace_root: Path,
    values: dict[str, str],
) -> dict[str, str]:
    output_dir = workspace_root / ".agent" / "tmp" / "prompt_payloads"
    return build_prompt_payload_variables(
        values,
        prompt_name_prefix=phase,
        write_payload=lambda relative_path, content: write_payload_to_directory(
            output_dir,
            relative_path,
            content,
        ),
    )


def _persist_current_prompt(workspace_root: Path, prompt_content: str | None) -> str:
    current_prompt_path = workspace_root / ".agent" / "CURRENT_PROMPT.md"
    current_prompt_path.parent.mkdir(parents=True, exist_ok=True)
    current_prompt_path.write_text(prompt_content or "No requirements provided", encoding="utf-8")
    return str(current_prompt_path)


def _current_prompt_variables(
    prompt_content: str | None, current_prompt_path: str
) -> dict[str, str]:
    del prompt_content
    return {"PROMPT": "", "PROMPT_PATH": current_prompt_path}


def _resolve_plan_content(workspace: Workspace) -> str | None:
    wrapped_plan = _read_optional(workspace, ".agent/artifacts/plan.json")
    if wrapped_plan:
        return _format_plan_for_execution(wrapped_plan)

    markdown_plan = _read_optional(workspace, ".agent/PLAN.md")
    if markdown_plan:
        return markdown_plan
    return None


def _format_plan_for_execution(content: str) -> str:
    plan = _parse_plan_content(content)
    if plan is None:
        return content

    sections = [
        _format_summary_section(plan),
        _format_steps_section(plan),
        _format_critical_files_section(plan),
        _format_risks_section(plan),
        _format_verification_section(plan),
        _format_work_units_section(plan),
    ]
    return "\n\n".join(section for section in sections if section) or content


def _parse_plan_content(content: str) -> dict[str, object] | None:
    try:
        parsed_obj: object = json.loads(content)
    except json.JSONDecodeError:
        return None

    if not isinstance(parsed_obj, dict):
        return None

    parsed = cast("dict[str, object]", parsed_obj)
    plan = parsed.get("content") if parsed.get("type") == "plan" else parsed_obj
    return plan if isinstance(plan, dict) else None


def _format_summary_section(plan: dict[str, object]) -> str:
    summary = plan.get("summary")
    if not isinstance(summary, dict):
        return ""

    sections: list[str] = []
    context = summary.get("context")
    if context:
        sections.append(f"Summary:\n{context}")

    scope_lines = _bullet_lines(summary.get("scope_items"), "text")
    if scope_lines:
        sections.append("\n".join(["Scope items:", *scope_lines]))

    return "\n\n".join(sections)


def _format_steps_section(plan: dict[str, object]) -> str:
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        return ""

    lines = ["Implementation steps:"]
    for step in steps:
        if not isinstance(step, dict):
            continue
        number = step.get("number", "?")
        title = step.get("title", "Untitled step")
        content_text = step.get("content", "")
        lines.append(f"{number}. {title}")
        if content_text:
            lines.append(f"   {content_text}")
    return "\n".join(lines)


def _format_critical_files_section(plan: dict[str, object]) -> str:
    critical_files = plan.get("critical_files")
    if not isinstance(critical_files, dict):
        return ""

    primary_files = critical_files.get("primary_files")
    if not isinstance(primary_files, list) or not primary_files:
        return ""

    lines = ["Critical files:"]
    for file_info in primary_files:
        if not isinstance(file_info, dict):
            continue
        path = file_info.get("path")
        action = file_info.get("action")
        why = file_info.get("why")
        if not (path and action):
            continue
        line = f"- {path} ({action})"
        if why:
            line = f"{line}: {why}"
        lines.append(line)
    return "\n".join(lines)


def _format_risks_section(plan: dict[str, object]) -> str:
    risks = plan.get("risks_mitigations")
    if not isinstance(risks, list) or not risks:
        return ""

    lines = ["Risks and mitigations:"]
    for risk in risks:
        if not isinstance(risk, dict):
            continue
        risk_text = risk.get("risk")
        mitigation = risk.get("mitigation")
        if risk_text and mitigation:
            lines.append(f"- {risk_text} -> {mitigation}")
    return "\n".join(lines)


def _format_verification_section(plan: dict[str, object]) -> str:
    verification = plan.get("verification_strategy")
    if not isinstance(verification, list) or not verification:
        return ""

    lines = ["Verification strategy:"]
    for check in verification:
        if not isinstance(check, dict):
            continue
        method = check.get("method")
        outcome = check.get("expected_outcome")
        if method and outcome:
            lines.append(f"- {method}: {outcome}")
    return "\n".join(lines)


def _format_work_units_section(plan: dict[str, object]) -> str:
    work_units = plan.get("work_units")
    if not isinstance(work_units, list) or not work_units:
        return ""

    lines = ["Work units:"]
    for unit in work_units:
        if not isinstance(unit, dict):
            continue
        unit_id = unit.get("unit_id")
        description = unit.get("description")
        if unit_id and description:
            lines.append(f"- {unit_id}: {description}")
    return "\n".join(lines)


def _bullet_lines(items: object, text_key: str) -> list[str]:
    if not isinstance(items, list):
        return []

    return [
        f"- {item[text_key]}" for item in items if isinstance(item, dict) and item.get(text_key)
    ]


def _resolve_issues_content(workspace: Workspace) -> str:
    for path in (".agent/ISSUES.md", ".agent/artifacts/issues.json"):
        content = _read_optional(workspace, path)
        if content:
            return content
    return "(no review issues available)"


def _resolve_fix_result_content(workspace: Workspace) -> str:
    content = _read_optional(workspace, ".agent/artifacts/fix_result.json")
    if content:
        return content
    return "(no fix result available)"


def _latest_artifact_content(workspace: Workspace, phase: str) -> str:
    artifact_paths = {
        "development_analysis": ".agent/artifacts/development_result.json",
        "review_analysis": ".agent/artifacts/issues.json",
        "fix": ".agent/artifacts/issues.json",
        "review": ".agent/artifacts/development_result.json",
    }
    path = artifact_paths.get(phase)
    if path is None:
        return ""
    return _read_optional(workspace, path) or ""


def _git_diff(workspace_root: Path) -> str:
    try:
        repo = Repo(workspace_root)
        start_commit_path = workspace_root / ".agent" / "start_commit"
        if start_commit_path.exists():
            baseline_sha = start_commit_path.read_text().strip()
            return cast("str", repo.git.diff(baseline_sha))
        return cast("str", repo.git.diff("HEAD"))
    except Exception:
        return "(no diff available)"


def _commit_phase_diff(workspace_root: Path) -> str:
    diff = _git_diff(workspace_root).strip()
    return diff or "(no diff available)"
