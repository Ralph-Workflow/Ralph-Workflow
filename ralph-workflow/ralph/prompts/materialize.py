"""Policy-selected prompt materialization."""

from __future__ import annotations

import json
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from git import Repo

from ralph.config.enums import AgentTransport
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.handoffs import (
    ensure_markdown_handoff_from_artifact,
    handoff_path_for_artifact,
)
from ralph.mcp.artifacts.history import (
    clear_artifact_history,
    history_index_path,
)
from ralph.mcp.artifacts.plan import PLAN_ARTIFACT_PATH, PLAN_ARTIFACT_TYPE, PLAN_DRAFT_PATH
from ralph.mcp.tools.names import SUBMIT_ARTIFACT_TOOL, claude_tool_name_prefix
from ralph.phases.required_artifacts import (
    build_required_artifacts,
    resolve_required_artifact,
    retry_hint_path,
)
from ralph.pipeline.cycle_baseline import read_cycle_baseline
from ralph.policy.models import ROLE_REVIEW
from ralph.prompts.commit import CommitPromptPayloadConfig, prompt_commit_message
from ralph.prompts.debug_dump import (
    dump_rendered_prompt,
    media_session_path,
    multimodal_sidecar_path,
    prompt_dump_path,
)
from ralph.prompts.developer import (
    DeveloperPromptInputs,
    PlanningPromptInputs,
    prompt_developer_iteration_xml_with_context,
    prompt_planning_xml_with_context,
)
from ralph.prompts.payload_refs import (
    _sanitize_surrogates,
    build_prompt_payload_variables,
    write_payload_to_directory,
)
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.template_engine import render_template
from ralph.prompts.types import SessionCapabilities, capability_template_variables

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.phases.required_artifacts import RequiredArtifact
    from ralph.pipeline.work_units import WorkUnit
    from ralph.policy.models import ArtifactsPolicy, PhaseDefinition, PipelinePolicy
    from ralph.workspace.protocol import Workspace


class MissingPlanHandoffError(ValueError):
    """Raised when a template requires an existing plan handoff that is absent."""


@dataclass(frozen=True)
class MultimodalSidecarEntry:
    """A single multimodal artifact entry in the prompt-to-invoke handoff sidecar."""

    artifact_id: str
    uri: str
    mime_type: str
    title: str
    modality: str
    delivery: str
    reason: str = ""
    source_path: str = ""
    cache_path: str = ""
    source_uri: str = ""
    block_type: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "artifact_id": self.artifact_id,
            "uri": self.uri,
            "mime_type": self.mime_type,
            "title": self.title,
            "modality": self.modality,
            "delivery": self.delivery,
            "reason": self.reason,
            "source_path": self.source_path,
            "cache_path": self.cache_path,
            "source_uri": self.source_uri,
            "block_type": self.block_type,
        }


_SIDECAR_SCHEMA_VERSION = "2"


def _write_multimodal_sidecar(
    workspace: Workspace,
    phase: str,
    entries: list[MultimodalSidecarEntry],
) -> None:
    path = multimodal_sidecar_path(phase)
    payload = {
        "schema_version": _SIDECAR_SCHEMA_VERSION,
        "phase": phase,
        "artifacts": [e.to_dict() for e in entries],
    }
    workspace.write(path, json.dumps(payload, indent=2))


def _clear_multimodal_sidecar(workspace: Workspace, phase: str) -> None:
    path = multimodal_sidecar_path(phase)
    with suppress(Exception):
        workspace.remove(path)


def collect_media_entries_for_phase(
    workspace: Workspace,
    phase: str,
) -> list[MultimodalSidecarEntry]:
    """Read media entries from the persistent session index for a phase.

    The MCP server writes artifact metadata to this index whenever read_media
    creates a resource-reference artifact during a live session. The runner
    reads this index at the next prompt materialization to carry those artifacts
    forward so the agent can retrieve them via read_media / resources/read.

    Returns an empty list when no session index exists or it cannot be parsed.
    """
    path = media_session_path(phase)
    try:
        raw = workspace.read(path)
    except Exception:
        return []
    try:
        data: dict[str, object] = json.loads(raw)
        artifacts = data.get("artifacts")
        if not isinstance(artifacts, list):
            return []
        entries: list[MultimodalSidecarEntry] = []
        for item in artifacts:
            if not isinstance(item, dict):
                continue
            try:
                entries.append(MultimodalSidecarEntry(
                    artifact_id=str(item.get("artifact_id", "")),
                    uri=str(item.get("uri", "")),
                    mime_type=str(item.get("mime_type", "")),
                    title=str(item.get("title", "")),
                    modality=str(item.get("modality", "")),
                    delivery=str(item.get("delivery", "resource_reference_replay")),
                    reason=str(item.get("reason", "")),
                    source_path=str(item.get("source_path", "")),
                    cache_path=str(item.get("cache_path", "")),
                    source_uri=str(item.get("source_uri", "")),
                    block_type=str(item.get("block_type", "")),
                ))
            except Exception:
                continue
        return entries
    except Exception:
        return []


def materialize_prompt_for_phase(  # noqa: PLR0913
    *,
    phase: str,
    workspace: Workspace,
    pipeline_policy: PipelinePolicy,
    artifacts_policy: ArtifactsPolicy | None = None,
    session_caps: SessionCapabilities,
    workspace_root: Path,
    worker_namespace: Path | None = None,
    previous_phase: str | None = None,
    resume_existing_phase: bool = False,
    multimodal_entries: list[MultimodalSidecarEntry] | None = None,
) -> str:
    """Render and persist the prompt for a pipeline phase, returning its dump path."""
    prompt = _render_prompt_for_phase(
        phase=phase,
        workspace=workspace,
        pipeline_policy=pipeline_policy,
        artifacts_policy=artifacts_policy,
        session_caps=session_caps,
        workspace_root=workspace_root,
        worker_namespace=worker_namespace,
        previous_phase=previous_phase,
        resume_existing_phase=resume_existing_phase,
    )
    path = dump_rendered_prompt(workspace, phase, prompt)
    if multimodal_entries:
        _write_multimodal_sidecar(workspace, phase, multimodal_entries)
    else:
        _clear_multimodal_sidecar(workspace, phase)
    return path


def prompt_file_for_phase(phase: str) -> str:
    """Return the workspace-relative path where a phase's prompt is stored."""
    return prompt_dump_path(phase)


def _template_name_for_phase(phase: str, pipeline_policy: PipelinePolicy) -> str:
    phase_def = pipeline_policy.phases.get(phase)
    if phase_def is None or phase_def.prompt_template is None:
        msg = f"No prompt_template configured for phase '{phase}'"
        raise ValueError(msg)
    return phase_def.prompt_template


def _loopback_template_name_for_phase(phase_def: PhaseDefinition | None) -> str | None:
    if phase_def is None:
        return None
    return phase_def.loopback_prompt_template or phase_def.continuation_template


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


def _render_prompt_for_phase(  # noqa: PLR0913
    phase: str,
    workspace: Workspace,
    pipeline_policy: PipelinePolicy,
    artifacts_policy: ArtifactsPolicy | None,
    session_caps: SessionCapabilities,
    workspace_root: Path,
    worker_namespace: Path | None = None,
    previous_phase: str | None = None,
    resume_existing_phase: bool = False,
) -> str:
    context = TemplateContext.default(workspace_root)
    template_name = _template_name_for_phase(phase, pipeline_policy)
    prompt_content = _read_optional(workspace, "PROMPT.md")
    current_prompt_path = _persist_current_prompt(workspace_root, prompt_content)

    phase_def = pipeline_policy.phases.get(phase)
    phase_role = phase_def.role if phase_def is not None else None
    drain = phase_def.drain if phase_def is not None else phase
    drain_artifact_type = (
        _drain_artifact_type(drain, artifacts_policy) if artifacts_policy else None
    )

    # Planning-style prompt: execution role producing a plan artifact
    if phase_role == "execution" and drain_artifact_type == "plan":
        (
            plan_content,
            plan_path,
            analysis_feedback_content,
            analysis_feedback_path,
            template_name,
        ) = _prepare_planning_prompt_context(
            phase=phase,
            workspace=workspace,
            pipeline_policy=pipeline_policy,
            artifacts_policy=artifacts_policy,
            previous_phase=previous_phase,
            resume_existing_phase=resume_existing_phase,
        )
        last_retry_error = _read_and_clear_retry_hint(workspace, phase)
        artifact_history_path = _resolve_planning_history_path(workspace_root)
        return prompt_planning_xml_with_context(
            context=context,
            inputs=PlanningPromptInputs(
                prompt_content=prompt_content,
                plan_content=plan_content,
                analysis_feedback_content=analysis_feedback_content,
                plan_path=plan_path,
                analysis_feedback_path=analysis_feedback_path,
                artifact_history_path=artifact_history_path,
                artifact_history_dir=_artifact_history_dir_from_path(artifact_history_path),
                last_retry_error=last_retry_error,
            ),
            workspace=workspace,
            session_caps=session_caps,
            template_name=template_name,
        )

    # Commit-style prompt: commit role
    if phase_role == "commit":
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

    plan_content, plan_path = _resolve_required_plan_handoff(
        workspace,
        template_name=template_name,
    )

    # Developer-style prompt: execution role producing a development_result artifact
    if phase_role == "execution" and drain_artifact_type == "development_result":
        dev_is_loopback = _is_analysis_loopback_into_phase(
            phase=phase,
            previous_phase=previous_phase,
            pipeline_policy=pipeline_policy,
        )
        if dev_is_loopback:
            loopback_template_name = _loopback_template_name_for_phase(phase_def)
            if loopback_template_name:
                template_name = loopback_template_name
        dev_artifact_history_path = _resolve_and_clear_dev_artifact_history(
            workspace_root=workspace_root,
            phase_def=phase_def,
            drain_artifact_type=drain_artifact_type,
            is_loopback=dev_is_loopback,
        )
        analysis_feedback_content, analysis_feedback_path = _resolve_loopback_analysis_feedback(
            workspace, phase, pipeline_policy, artifacts_policy
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
                artifact_history_path=dev_artifact_history_path,
                artifact_history_dir=_artifact_history_dir_from_path(dev_artifact_history_path),
            ),
            workspace=workspace,
            session_caps=session_caps,
            template_name=template_name,
        )

    # Template-based prompt: review, analysis, or other execution-role phases
    if phase_role in (ROLE_REVIEW, "analysis", "execution", "verification"):
        template = context.registry.get_template(template_name)
        diff_content = _git_diff(workspace_root)
        latest_artifact_content, latest_artifact_path = _latest_artifact_content(
            workspace, phase, pipeline_policy, artifacts_policy
        )
        issues_content, issues_path = _resolve_issues_content(workspace)
        fix_result_content, fix_result_path = _resolve_fix_result_content(workspace)
        analysis_feedback_content, analysis_feedback_path = _resolve_loopback_analysis_feedback(
            workspace, phase, pipeline_policy, artifacts_policy
        )
        last_retry_error = _read_and_clear_retry_hint(workspace, phase)
        variables = _phase_payload_variables(
            phase=phase,
            workspace_root=workspace_root,
            worker_namespace=worker_namespace,
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
        if phase_def is not None and phase_def.skip_invocation:
            variables["HIDE_ARTIFACT_SUBMISSION_GUIDANCE"] = "true"
        variables.update(_current_prompt_variables(prompt_content, current_prompt_path))
        variables["LAST_RETRY_ERROR"] = last_retry_error
        return render_template(
            template,
            _merged_variables(variables, session_caps),
            context.partials,
        )

    msg = f"Unsupported phase '{phase}' (role={phase_role!r}) for prompt materialization"
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
    """Return the tool name prefix for the given agent transport."""
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
    worker_namespace: Path | None = None,
) -> dict[str, str]:
    output_dir = (
        worker_namespace / "tmp" / "prompt_payloads"
        if worker_namespace is not None
        else workspace_root / ".agent" / "tmp" / "prompt_payloads"
    )
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
    if prompt_content is None and current_prompt_path.exists():
        return str(current_prompt_path)
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


def _template_allows_missing_plan_handoff(template_name: str) -> bool:
    return template_name in {"planning.jinja", "planning_fallback.jinja"}


def _resolve_required_plan_handoff(
    workspace: Workspace,
    *,
    template_name: str,
    allow_draft_fallback: bool = False,
) -> tuple[str | None, str]:
    plan_content, plan_path = _resolve_plan_handoff(workspace)
    if plan_path:
        return plan_content, plan_path
    if allow_draft_fallback and workspace.exists(PLAN_DRAFT_PATH):
        with suppress(Exception):
            parsed = cast("object", json.loads(workspace.read(PLAN_DRAFT_PATH)))
            if isinstance(parsed, dict) and isinstance(parsed.get("sections"), dict):
                sections = cast("dict[str, object]", parsed["sections"])
                return _format_plan_for_execution(json.dumps(sections)), ""
    plan_handoff_path = handoff_path_for_artifact("plan") or ".agent/PLAN.md"
    msg = (
        f"Template '{template_name}' requires an existing plan handoff at "
        f"{plan_handoff_path}"
    )
    raise MissingPlanHandoffError(msg)


def _should_preserve_planning_context(
    *,
    phase: str,
    workspace: Workspace,
    previous_phase: str | None,
    pipeline_policy: PipelinePolicy,
    resume_existing_phase: bool,
) -> bool:
    is_loopback = _is_analysis_loopback_into_phase(
        phase=phase,
        previous_phase=previous_phase,
        pipeline_policy=pipeline_policy,
    )
    has_retry_hint = bool(_read_optional(workspace, retry_hint_path(phase)))
    preserve_retry_context = previous_phase == phase and has_retry_hint
    return is_loopback or preserve_retry_context or resume_existing_phase


def _prepare_planning_prompt_context(  # noqa: PLR0913
    *,
    phase: str,
    workspace: Workspace,
    pipeline_policy: PipelinePolicy,
    artifacts_policy: ArtifactsPolicy | None,
    previous_phase: str | None,
    resume_existing_phase: bool,
) -> tuple[str | None, str, str, str, str]:
    phase_def = pipeline_policy.phases.get(phase)
    template_name = _template_name_for_phase(phase, pipeline_policy)
    preserve_planning_context = _should_preserve_planning_context(
        phase=phase,
        workspace=workspace,
        previous_phase=previous_phase,
        pipeline_policy=pipeline_policy,
        resume_existing_phase=resume_existing_phase,
    )
    # Clear fresh planning context only for true fresh entry.
    # Analysis loopbacks, same-phase recoverable retries, and resumed
    # planning passes must preserve the current plan + history so the
    # planner can revise instead of restart.
    if not preserve_planning_context:
        _clear_fresh_planning_context(
            workspace,
            phase=phase,
            pipeline_policy=pipeline_policy,
            artifacts_policy=artifacts_policy,
        )
    elif phase_def is not None and phase_def.loopback_prompt_template:
        template_name = phase_def.loopback_prompt_template
    analysis_feedback_content, analysis_feedback_path = _resolve_loopback_analysis_feedback(
        workspace, phase, pipeline_policy, artifacts_policy
    )
    if _template_allows_missing_plan_handoff(template_name):
        plan_content, plan_path = _resolve_plan_handoff(workspace)
    else:
        plan_content, plan_path = _resolve_required_plan_handoff(
            workspace,
            template_name=template_name,
            allow_draft_fallback=resume_existing_phase,
        )
    return (
        plan_content,
        plan_path,
        analysis_feedback_content,
        analysis_feedback_path,
        template_name,
    )


def _resolve_artifact_history_path(workspace_root: Path, artifact_type: str) -> str:
    """Return the absolute path to the artifact history index for the given type, if it exists."""
    artifact_dir = workspace_root / ".agent" / "artifacts"
    index = history_index_path(artifact_dir, artifact_type)
    if index.exists():
        return str(index)
    return ""


def _artifact_history_dir_from_path(history_path: str) -> str:
    """Return the archive directory for a resolved artifact history index path."""
    if not history_path:
        return ""
    return str(Path(history_path).parent)


def _resolve_planning_history_path(
    workspace_root: Path,
) -> str:
    """Return the absolute path to the planning artifact history index, if it exists."""
    return _resolve_artifact_history_path(workspace_root, PLAN_ARTIFACT_TYPE)


def _resolve_and_clear_dev_artifact_history(
    *,
    workspace_root: Path,
    phase_def: PhaseDefinition | None,
    drain_artifact_type: str | None,
    is_loopback: bool,
) -> str:
    """Resolve the artifact history path and optionally clear it on fresh entry."""
    if phase_def is None or phase_def.artifact_history is None or not drain_artifact_type:
        return ""
    if not is_loopback and phase_def.artifact_history.clear_on_fresh_entry:
        artifact_dir = workspace_root / ".agent" / "artifacts"
        clear_artifact_history(
            artifact_dir, drain_artifact_type, backend=DEFAULT_FILE_BACKEND
        )
    return _resolve_artifact_history_path(workspace_root, drain_artifact_type)


def _clear_fresh_planning_context(
    workspace: Workspace,
    *,
    phase: str,
    pipeline_policy: PipelinePolicy,
    artifacts_policy: ArtifactsPolicy | None,
) -> None:
    """Delete prior planning state before rendering a fresh planning-creation prompt."""
    for path in (PLAN_ARTIFACT_PATH, PLAN_DRAFT_PATH):
        if workspace.exists(path):
            workspace.remove(path)
    handoff_path = handoff_path_for_artifact("plan")
    if handoff_path and workspace.exists(handoff_path):
        workspace.remove(handoff_path)

    # Clear planning artifact history when the phase policy opts in.
    phase_def = pipeline_policy.phases.get(phase)
    if (
        phase_def is not None
        and phase_def.artifact_history is not None
        and phase_def.artifact_history.clear_on_fresh_entry
    ):
        workspace_root = Path(workspace.absolute_path("."))
        artifact_dir = workspace_root / ".agent" / "artifacts"
        clear_artifact_history(artifact_dir, PLAN_ARTIFACT_TYPE, backend=DEFAULT_FILE_BACKEND)

    if artifacts_policy is None:
        return
    required_artifacts = build_required_artifacts(artifacts_policy)
    for p in pipeline_policy.phases.values():
        if p.role != "analysis" or p.transitions.on_loopback != phase:
            continue
        required_artifact = required_artifacts.get(p.drain)
        if required_artifact is None:
            continue
        if workspace.exists(required_artifact.json_path):
            workspace.remove(required_artifact.json_path)
        analysis_handoff = handoff_path_for_artifact(required_artifact.artifact_type)
        if analysis_handoff and workspace.exists(analysis_handoff):
            workspace.remove(analysis_handoff)


def _is_analysis_loopback_into_phase(
    *,
    phase: str,
    previous_phase: str | None,
    pipeline_policy: PipelinePolicy,
) -> bool:
    if previous_phase is None:
        return False
    previous_phase_def = pipeline_policy.phases.get(previous_phase)
    return bool(
        previous_phase_def is not None
        and previous_phase_def.role == "analysis"
        and previous_phase_def.transitions.on_loopback == phase
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
        artifact_path=".agent/artifacts/issues.json",
    )
    return content or "(no review issues available)", path


def _resolve_fix_result_content(workspace: Workspace) -> tuple[str, str]:
    content, path = _resolve_agent_handoff(
        workspace,
        artifact_type="fix_result",
        artifact_path=".agent/artifacts/fix_result.json",
    )
    return content or "(no fix result available)", path


def _resolve_loopback_analysis_feedback(
    workspace: Workspace,
    phase: str,
    pipeline_policy: PipelinePolicy,
    artifacts_policy: ArtifactsPolicy | None,
) -> tuple[str, str]:
    """Return the analysis decision feedback that loopbacks into this phase."""
    if artifacts_policy is None:
        return "", ""
    for pdef in pipeline_policy.phases.values():
        if pdef.role == "analysis" and pdef.transitions.on_loopback == phase:
            ra = resolve_required_artifact(artifacts_policy, drain=pdef.drain)
            if ra is not None:
                content, path = _resolve_agent_handoff(
                    workspace,
                    artifact_type=ra.artifact_type,
                    artifact_path=ra.json_path,
                )
                return content or "", path
    return "", ""


def _latest_artifact_content(
    workspace: Workspace,
    phase: str,
    pipeline_policy: PipelinePolicy,
    artifacts_policy: ArtifactsPolicy | None,
) -> tuple[str, str]:
    """Return the primary work artifact that this phase needs as input context.

    Traverses the pipeline graph backwards, skipping commit, analysis, and
    skip_invocation execution phases, to find the last phase that produces
    a concrete work artifact.
    """
    if artifacts_policy is None:
        return "", ""
    ra = _find_work_artifact(phase, pipeline_policy, artifacts_policy)
    if ra is None:
        return "", ""
    content, path = _resolve_agent_handoff(
        workspace,
        artifact_type=ra.artifact_type,
        artifact_path=ra.json_path,
    )
    return content or "", path


def _drain_artifact_type(drain: str, artifacts_policy: ArtifactsPolicy) -> str | None:
    """Return the artifact_type produced by the given drain, or None."""
    ra = resolve_required_artifact(artifacts_policy, drain=drain)
    return ra.artifact_type if ra is not None else None


def _predecessors(phase: str, pipeline_policy: PipelinePolicy) -> list[str]:
    """Return all phases that can transition to the given phase."""
    result = []
    for name, pdef in pipeline_policy.phases.items():
        t = pdef.transitions
        if phase in (t.on_success, t.on_loopback):
            result.append(name)
    return result


def _find_work_artifact(
    phase: str,
    pipeline_policy: PipelinePolicy,
    artifacts_policy: ArtifactsPolicy,
) -> RequiredArtifact | None:
    """Find the primary work artifact for the given phase via backwards graph traversal.

    Skips commit-role, analysis-role, and skip_invocation execution phases until
    it finds an execution or review phase that produces a concrete work artifact.
    """
    visited: set[str] = set()
    queue = list(_predecessors(phase, pipeline_policy))
    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        pdef = pipeline_policy.phases.get(current)
        if pdef is None:
            continue
        role = pdef.role
        skip = role in ("commit", "analysis") or (
            role == "execution" and pdef.skip_invocation
        )
        if skip:
            queue.extend(_predecessors(current, pipeline_policy))
        else:
            return resolve_required_artifact(artifacts_policy, drain=pdef.drain)
    return None


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
            committed = _sanitize_surrogates(cast("str", repo.git.diff(baseline_sha, "HEAD")))
            uncommitted = _sanitize_surrogates(cast("str", repo.git.diff("HEAD")))
            parts = [p for p in (committed, uncommitted) if p]
            return "\n".join(parts) if parts else "(no diff available)"
        return _sanitize_surrogates(cast("str", repo.git.diff("HEAD")))
    except Exception:
        return "(no diff available)"


def _pending_diff(workspace_root: Path) -> str:
    """Return only the current pending work against the last commit (HEAD).

    This covers staged and unstaged changes vs HEAD — exactly what a commit
    agent needs to describe. Unlike _git_diff(), this never includes commits
    from earlier in the dev cycle, so it is safe to use for commit-role prompts.
    """
    try:
        repo = Repo(workspace_root)
        return _sanitize_surrogates(cast("str", repo.git.diff("HEAD"))) or "(no diff available)"
    except Exception:
        return "(no diff available)"


def _commit_phase_diff(workspace_root: Path) -> str:
    diff = _pending_diff(workspace_root).strip()
    return diff or "(no diff available)"
