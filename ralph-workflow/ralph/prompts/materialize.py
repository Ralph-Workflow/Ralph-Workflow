"""Policy-selected prompt materialization."""

from __future__ import annotations

import json
from collections import OrderedDict
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
from ralph.prompts.plan_format import format_plan_for_execution
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
    failure_kind: str = ""
    identity_key: str = ""

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
            "failure_kind": self.failure_kind,
            "identity_key": self.identity_key,
        }


@dataclass(frozen=True)
class PromptPhaseContext:
    """Required inputs for prompt materialization: the phase, workspace, and policy bindings."""

    phase: str
    workspace: Workspace
    pipeline_policy: PipelinePolicy
    session_caps: SessionCapabilities
    workspace_root: Path


@dataclass(frozen=True)
class PromptPhaseOptions:
    """Optional inputs for prompt materialization with sensible defaults."""

    artifacts_policy: ArtifactsPolicy | None = None
    worker_namespace: Path | None = None
    previous_phase: str | None = None
    resume_existing_phase: bool = False
    multimodal_entries: list[MultimodalSidecarEntry] | None = None


def _sidecar_entry_identity(entry: MultimodalSidecarEntry) -> str:
    """Return the bounded live-set identity for a multimodal sidecar entry."""
    if entry.identity_key:
        return entry.identity_key
    if entry.source_uri:
        return f"source-uri:{entry.modality}:{entry.source_uri}"
    if entry.source_path:
        return f"source-path:{entry.modality}:{entry.source_path}"
    return f"artifact-id:{entry.artifact_id or entry.uri}"


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
        entries: OrderedDict[str, MultimodalSidecarEntry] = OrderedDict()
        for item in artifacts:
            if not isinstance(item, dict):
                continue
            try:
                entry = MultimodalSidecarEntry(
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
                    failure_kind=str(item.get("failure_kind", "")),
                    identity_key=str(item.get("identity_key", "")),
                )
            except Exception:
                continue
            entries[_sidecar_entry_identity(entry)] = entry
        return list(entries.values())
    except Exception:
        return []


def materialize_prompt_for_phase(
    context: PromptPhaseContext | None = None,
    options: PromptPhaseOptions | None = None,
    **kwargs: object,
) -> str:
    """Render and persist the prompt for a pipeline phase, returning its dump path."""
    if context is None:
        context = PromptPhaseContext(
            phase=cast("str", kwargs["phase"]),
            workspace=cast("Workspace", kwargs["workspace"]),
            pipeline_policy=cast("PipelinePolicy", kwargs["pipeline_policy"]),
            session_caps=cast("SessionCapabilities", kwargs["session_caps"]),
            workspace_root=cast("Path", kwargs["workspace_root"]),
        )
        if options is None:
            options = PromptPhaseOptions(
                artifacts_policy=cast("ArtifactsPolicy | None", kwargs.get("artifacts_policy")),
                worker_namespace=cast("Path | None", kwargs.get("worker_namespace")),
                previous_phase=cast("str | None", kwargs.get("previous_phase")),
                resume_existing_phase=cast("bool", kwargs.get("resume_existing_phase", False)),
                multimodal_entries=cast(
                    "list[MultimodalSidecarEntry] | None", kwargs.get("multimodal_entries")
                ),
            )
    opts = options or PromptPhaseOptions()
    prompt = _render_prompt_for_phase(context, opts)
    path = dump_rendered_prompt(context.workspace, context.phase, prompt)
    if opts.multimodal_entries:
        _write_multimodal_sidecar(context.workspace, context.phase, opts.multimodal_entries)
    else:
        _clear_multimodal_sidecar(context.workspace, context.phase)
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


def _render_prompt_for_phase(
    context: PromptPhaseContext,
    options: PromptPhaseOptions,
) -> str:
    phase = context.phase
    workspace = context.workspace
    pipeline_policy = context.pipeline_policy
    session_caps = context.session_caps
    workspace_root = context.workspace_root
    artifacts_policy = options.artifacts_policy
    worker_namespace = options.worker_namespace
    previous_phase = options.previous_phase
    tmpl_ctx = TemplateContext.default(workspace_root)
    template_name = _template_name_for_phase(phase, pipeline_policy)
    prompt_content = _read_optional(workspace, "PROMPT.md")
    _clear_completed_planning_history_if_needed(
        workspace_root=workspace_root,
        pipeline_policy=pipeline_policy,
        phase=phase,
        previous_phase=previous_phase,
    )
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
        ) = _prepare_planning_prompt_context(context, options)
        last_retry_error = _read_and_clear_retry_hint(workspace, phase)
        artifact_history_path = _resolve_planning_history_path(workspace_root)
        return prompt_planning_xml_with_context(
            context=tmpl_ctx,
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
            template_registry=tmpl_ctx.registry,
            partials=tmpl_ctx.partials,
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
            context=tmpl_ctx,
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
        template = tmpl_ctx.registry.get_template(template_name)
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
        variables = phase_payload_variables(
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
        path_vars = {
            "PLAN_PATH": plan_path,
            "LATEST_ARTIFACT_PATH": latest_artifact_path,
            "ISSUES_PATH": issues_path,
            "FIX_RESULT_PATH": fix_result_path,
            "ANALYSIS_FEEDBACK_PATH": analysis_feedback_path,
        }
        variables.update({k: v for k, v in path_vars.items() if v})
        if phase_def is not None and phase_def.skip_invocation:
            variables["HIDE_ARTIFACT_SUBMISSION_GUIDANCE"] = "true"
        variables.update(_current_prompt_variables(prompt_content, current_prompt_path))
        variables["LAST_RETRY_ERROR"] = last_retry_error
        return render_template(
            template,
            _merged_variables(variables, session_caps),
            tmpl_ctx.partials,
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
    if transport in (AgentTransport.CLAUDE, AgentTransport.CLAUDE_INTERACTIVE):
        return claude_tool_name_prefix()
    return ""


def _read_optional(workspace: Workspace, path: str) -> str | None:
    if not workspace.exists(path):
        return None
    return workspace.read(path)


def phase_payload_variables(
    *,
    phase: str,
    workspace_root: Path,
    values: dict[str, str],
    worker_namespace: Path | None = None,
) -> dict[str, str]:
    """Build prompt payload variables, writing oversized values to disk under the phase prefix."""
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
        fallback_formatter=format_plan_for_execution,
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
                return format_plan_for_execution(json.dumps(sections)), ""
    plan_handoff_path = handoff_path_for_artifact("plan") or ".agent/PLAN.md"
    msg = f"Template '{template_name}' requires an existing plan handoff at {plan_handoff_path}"
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


def _prepare_planning_prompt_context(
    context: PromptPhaseContext,
    options: PromptPhaseOptions,
) -> tuple[str | None, str, str, str, str]:
    phase = context.phase
    workspace = context.workspace
    pipeline_policy = context.pipeline_policy
    artifacts_policy = options.artifacts_policy
    previous_phase = options.previous_phase
    resume_existing_phase = options.resume_existing_phase
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


def _clear_completed_planning_history_if_needed(
    *,
    workspace_root: Path,
    pipeline_policy: PipelinePolicy,
    phase: str,
    previous_phase: str | None,
) -> None:
    """Clear all artifact history when the planning cycle finishes and development begins.

    The history remains available throughout planning and replanning. Once the
    planning-analysis phase succeeds and the workflow advances to its on_success
    target (the developer-facing phase), all artifact history should be cleared so
    downstream execution starts from a clean slate.
    """
    if previous_phase is None:
        return
    previous_phase_def = pipeline_policy.phases.get(previous_phase)
    if previous_phase_def is None or previous_phase_def.role != "analysis":
        return
    if previous_phase_def.transitions.on_success != phase:
        return
    loopback_phase = previous_phase_def.transitions.on_loopback
    if loopback_phase is None:
        return
    loopback_phase_def = pipeline_policy.phases.get(loopback_phase)
    if loopback_phase_def is None:
        return
    if loopback_phase_def.role != "execution":
        return
    _clear_all_artifact_history(workspace_root)


def _clear_all_artifact_history(workspace_root: Path) -> None:
    """Remove every artifact history archive under .agent/artifacts/history/."""
    artifact_dir = workspace_root / ".agent" / "artifacts"
    history_root = artifact_dir / "history"
    if not history_root.exists():
        return
    for path in history_root.iterdir():
        if path.is_dir():
            clear_artifact_history(artifact_dir, path.name, backend=DEFAULT_FILE_BACKEND)


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
        clear_artifact_history(artifact_dir, drain_artifact_type, backend=DEFAULT_FILE_BACKEND)
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

    workspace_root = Path(workspace.absolute_path("."))
    _clear_all_artifact_history(workspace_root)

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
        skip = role in ("commit", "analysis") or (role == "execution" and pdef.skip_invocation)
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
    diff = pending_diff(workspace_root).strip()
    return diff or "(no diff available)"


pending_diff = _pending_diff
git_diff = _git_diff
