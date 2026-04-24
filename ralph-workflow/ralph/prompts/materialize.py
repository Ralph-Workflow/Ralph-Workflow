"""Policy-selected prompt materialization."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, cast

from git import Repo

from ralph.config.enums import AgentTransport
from ralph.mcp.artifacts.handoffs import (
    ensure_markdown_handoff_from_artifact,
    handoff_path_for_artifact,
)
from ralph.mcp.artifacts.plan import PLAN_ARTIFACT_PATH
from ralph.mcp.tools.names import SUBMIT_ARTIFACT_TOOL, claude_tool_name_prefix
from ralph.phases.required_artifacts import (
    DEV_ANALYSIS_DECISION_JSON_PATH,
    DEV_RESULT_ARTIFACT_JSON_PATH,
    FIX_RESULT_ARTIFACT_JSON_PATH,
    ISSUES_ARTIFACT_JSON_PATH,
    REVIEW_ANALYSIS_DECISION_JSON_PATH,
    retry_hint_path,
)
from ralph.pipeline.cycle_baseline import read_cycle_baseline
from ralph.prompts.commit import CommitPromptPayloadConfig, prompt_commit_message
from ralph.prompts.debug_dump import dump_rendered_prompt, prompt_dump_path
from ralph.prompts.developer import (
    DeveloperPromptInputs,
    PlanningPromptInputs,
    prompt_developer_iteration_xml_with_context,
    prompt_planning_xml_with_context,
)
from ralph.prompts.payload_refs import build_prompt_payload_variables, write_payload_to_directory
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.template_engine import render_template
from ralph.prompts.types import SessionCapabilities, capability_template_variables

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.pipeline.work_units import WorkUnit
    from ralph.policy.models import PipelinePolicy
    from ralph.workspace.protocol import Workspace

_ANALYSIS_PHASES = frozenset({"development_analysis", "review_analysis"})


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


def _read_and_clear_retry_hint(workspace: Workspace, phase: str) -> str:
    """Read the retry hint file for a phase and delete it after reading."""
    path = retry_hint_path(phase)
    if not workspace.exists(path):
        return ""
    try:
        hint = workspace.read(path)
        workspace.remove(path)
        return hint
    except Exception:
        return ""


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
    plan_content, plan_path = _resolve_plan_handoff(workspace)
    if phase == "planning":
        last_retry_error = _read_and_clear_retry_hint(workspace, phase)
        return prompt_planning_xml_with_context(
            context=context,
            inputs=PlanningPromptInputs(
                prompt_content=prompt_content,
                last_retry_error=last_retry_error,
            ),
            workspace=workspace,
            session_caps=session_caps,
            template_name=template_name,
        )
    if phase == "development":
        analysis_feedback_content, analysis_feedback_path = _resolve_loopback_analysis_feedback(
            workspace,
            phase,
        )
        last_retry_error = _read_and_clear_retry_hint(workspace, phase)
        return prompt_developer_iteration_xml_with_context(
            context=context,
            inputs=DeveloperPromptInputs(
                prompt_content=prompt_content,
                plan_content=plan_content,
                analysis_feedback_content=analysis_feedback_content,
                plan_path=plan_path,
                analysis_feedback_path=analysis_feedback_path,
                prompt_name_prefix=phase,
                last_retry_error=last_retry_error,
            ),
            workspace=workspace,
            session_caps=session_caps,
            template_name=template_name,
        )
    if phase in {"review", "fix", "development_analysis", "review_analysis"}:
        template = context.registry.get_template(template_name)
        diff_content = _git_diff(workspace_root)
        latest_artifact_content, latest_artifact_path = _latest_artifact_content(workspace, phase)
        issues_content, issues_path = _resolve_issues_content(workspace)
        fix_result_content, fix_result_path = _resolve_fix_result_content(workspace)
        analysis_feedback_content, analysis_feedback_path = _resolve_loopback_analysis_feedback(
            workspace,
            phase,
        )
        last_retry_error = _read_and_clear_retry_hint(workspace, phase)
        variables = _phase_payload_variables(
            phase=phase,
            workspace_root=workspace_root,
            values={
                "PLAN": "" if plan_path else (plan_content or "(no plan available)"),
                "DIFF": diff_content,
                "CHANGES": diff_content,
                "LATEST_ARTIFACT": latest_artifact_content,
                "ISSUES": issues_content,
                "FIX_RESULT": fix_result_content,
                "ANALYSIS_FEEDBACK": analysis_feedback_content,
            },
        )
        if plan_path:
            variables["PLAN_PATH"] = plan_path
        if latest_artifact_path:
            variables["LATEST_ARTIFACT_PATH"] = latest_artifact_path
        if issues_path:
            variables["ISSUES_PATH"] = issues_path
        if fix_result_path:
            variables["FIX_RESULT_PATH"] = fix_result_path
        if analysis_feedback_path:
            variables["ANALYSIS_FEEDBACK_PATH"] = analysis_feedback_path
        if phase == "fix":
            variables["HIDE_ARTIFACT_SUBMISSION_GUIDANCE"] = "true"
        variables.update(_current_prompt_variables(prompt_content, current_prompt_path))
        # For analysis phases, PROMPT and PLAN are SECONDARY context: force path
        # references regardless of content size so they are never inlined.
        if phase in _ANALYSIS_PHASES:
            variables.update(
                _force_plan_path_for_analysis(
                    workspace_root=workspace_root,
                    phase=phase,
                    plan_content=plan_content,
                    plan_path=variables.get("PLAN_PATH", ""),
                )
            )
        variables["LAST_RETRY_ERROR"] = last_retry_error
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


def _force_plan_path_for_analysis(
    *,
    workspace_root: Path,
    phase: str,
    plan_content: str | None,
    plan_path: str,
) -> dict[str, str]:
    """Return PLAN/PLAN_PATH variables that always use a file reference.

    Called only for analysis phases where PLAN is secondary context. If
    plan_path is already set (handoff file exists), we preserve it. When
    plan_path is absent, we write the content to a temp file so the template
    macro always has a non-empty path to reference.
    """
    if plan_path:
        return {"PLAN": "", "PLAN_PATH": plan_path}
    content = plan_content or "(no plan available)"
    output_dir = workspace_root / ".agent" / "tmp" / "prompt_payloads"
    written_path = write_payload_to_directory(
        output_dir, f"{phase}_plan.txt", content
    )
    return {"PLAN": "", "PLAN_PATH": written_path}


def _merged_variables(base: dict[str, str], session_caps: SessionCapabilities) -> dict[str, str]:
    return {
        **base,
        **capability_template_variables(
            session_caps.capabilities,
            session_caps.policy_flags,
            tool_name_prefix=session_caps.tool_name_prefix,
        ),
    }


def render_worker_prompt(unit: WorkUnit, base_prompt: str, policy: PipelinePolicy) -> str:
    """Render the isolated developer prompt for a single parallel work unit."""

    del policy
    context = TemplateContext.default()
    template = context.registry.get_template("worker_developer")
    return render_template(
        template,
        {
            "unit_id": unit.unit_id,
            "description": unit.description,
            "allowed_directories": json.dumps(unit.allowed_directories, indent=2),
            "base_prompt": base_prompt,
        },
        context.partials,
    )


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


def _resolve_plan_handoff(workspace: Workspace) -> tuple[str | None, str]:
    """Return the plan handoff users and downstream agents should consume."""
    return _resolve_agent_handoff(
        workspace,
        artifact_type="plan",
        artifact_path=PLAN_ARTIFACT_PATH,
        fallback_formatter=_format_plan_for_execution,
    )


def _resolve_agent_handoff(
    workspace: Workspace,
    *,
    artifact_type: str,
    artifact_path: str,
    fallback_formatter: Callable[[str], str] | None = None,
) -> tuple[str | None, str]:
    """Return the Markdown handoff for an agent-consumed artifact.

    JSON artifacts are Ralph's machine-readable source of truth; prompts should
    point agents at mirrored Markdown handoffs whenever one is defined.
    """
    relative_handoff_path = handoff_path_for_artifact(artifact_type)
    handoff_path = workspace.absolute_path(relative_handoff_path) if relative_handoff_path else ""

    artifact_content = _read_optional(workspace, artifact_path)
    if artifact_content:
        created_path = ensure_markdown_handoff_from_artifact(
            Path(workspace.absolute_path(".")),
            artifact_type,
            artifact_content,
        )
        if created_path is not None:
            try:
                markdown = Path(created_path).read_text(encoding="utf-8")
            except OSError:
                markdown = None
            if markdown:
                return markdown, created_path
        if fallback_formatter is not None:
            return fallback_formatter(artifact_content), ""

    if relative_handoff_path:
        markdown = _read_optional(workspace, relative_handoff_path)
        if markdown:
            return markdown, handoff_path
        if handoff_path:
            try:
                markdown = Path(handoff_path).read_text(encoding="utf-8")
            except OSError:
                markdown = None
            if markdown:
                return markdown, handoff_path

    return None, ""


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


def _resolve_issues_content(workspace: Workspace) -> tuple[str, str]:
    content, path = _resolve_agent_handoff(
        workspace,
        artifact_type="issues",
        artifact_path=ISSUES_ARTIFACT_JSON_PATH,
    )
    return content or "(no review issues available)", path


def _resolve_fix_result_content(workspace: Workspace) -> tuple[str, str]:
    content, path = _resolve_agent_handoff(
        workspace,
        artifact_type="fix_result",
        artifact_path=FIX_RESULT_ARTIFACT_JSON_PATH,
    )
    return content or "(no fix result available)", path


def _resolve_loopback_analysis_feedback(workspace: Workspace, phase: str) -> tuple[str, str]:
    sources = {
        "development": (
            "development_analysis_decision",
            DEV_ANALYSIS_DECISION_JSON_PATH,
        ),
        "fix": (
            "review_analysis_decision",
            REVIEW_ANALYSIS_DECISION_JSON_PATH,
        ),
    }
    source = sources.get(phase)
    if source is None:
        return "", ""
    artifact_type, artifact_path = source
    content, path = _resolve_agent_handoff(
        workspace,
        artifact_type=artifact_type,
        artifact_path=artifact_path,
    )
    return content or "", path


def _latest_artifact_content(workspace: Workspace, phase: str) -> tuple[str, str]:
    handoff_sources = {
        "development_analysis": ("development_result", DEV_RESULT_ARTIFACT_JSON_PATH),
        "review_analysis": ("issues", ISSUES_ARTIFACT_JSON_PATH),
        "fix": ("issues", ISSUES_ARTIFACT_JSON_PATH),
        "review": ("development_result", DEV_RESULT_ARTIFACT_JSON_PATH),
    }
    source = handoff_sources.get(phase)
    if source is None:
        return "", ""
    artifact_type, artifact_path = source
    content, path = _resolve_agent_handoff(
        workspace,
        artifact_type=artifact_type,
        artifact_path=artifact_path,
    )
    return content or "", path


def _git_diff(workspace_root: Path) -> str:
    """Return the cumulative diff from the dev-cycle baseline through the working tree.

    When a baseline SHA is recorded in .agent/start_commit, the diff includes:
    - All commits landed since the baseline (baseline..HEAD)
    - Any uncommitted changes on top (HEAD vs working tree)

    This is correct whether the user commits once per dev cycle or once per
    individual dev iteration within a cycle.
    """
    try:
        repo = Repo(workspace_root)
        baseline_sha = read_cycle_baseline(workspace_root)
        if baseline_sha:
            committed = cast("str", repo.git.diff(baseline_sha, "HEAD"))
            uncommitted = cast("str", repo.git.diff("HEAD"))
            parts = [p for p in (committed, uncommitted) if p]
            return "\n".join(parts) if parts else "(no diff available)"
        return cast("str", repo.git.diff("HEAD"))
    except Exception:
        return "(no diff available)"


def _commit_phase_diff(workspace_root: Path) -> str:
    diff = _git_diff(workspace_root).strip()
    return diff or "(no diff available)"
