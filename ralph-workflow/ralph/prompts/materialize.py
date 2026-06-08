"""Policy-selected prompt materialization."""

from __future__ import annotations

import json
import typing
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ralph.config.enums import AgentTransport
from ralph.executor.process import ProcessRunOptions, run_process
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.handoffs import (
    ensure_markdown_handoff_from_artifact,
    handoff_path_for_artifact,
)
from ralph.mcp.artifacts.history import (
    clear_artifact_history,
    history_index_path,
)
from ralph.mcp.artifacts.plan import (
    PLAN_ARTIFACT_PATH,
    PLAN_ARTIFACT_TYPE,
    PLAN_DRAFT_PATH,
)
from ralph.mcp.tools.names import (
    SUBMIT_ARTIFACT_TOOL,
    claude_tool_name,
    claude_tool_name_prefix,
    opencode_tool_name,
    opencode_tool_name_prefix,
)
from ralph.phases.required_artifacts import (
    resolve_required_artifact,
    retry_hint_path,
)
from ralph.pipeline.cycle_baseline import read_cycle_baseline
from ralph.pipeline.phase_entry_cleaner import (
    clear_phase_entry_drains as _clear_phase_entry_drains,
)
from ralph.pipeline.phase_entry_cleaner import (
    is_fresh_phase_entry,
)
from ralph.policy.models import ROLE_REVIEW
from ralph.prompts._missing_plan_handoff_error import MissingPlanHandoffError
from ralph.prompts._prompt_phase_context import PromptPhaseContext
from ralph.prompts.commit import CommitPromptPayloadConfig, prompt_commit_message
from ralph.prompts.commit_cleanup import render_commit_cleanup_prompt
from ralph.prompts.debug_dump import (
    clear_multimodal_sidecar,
    collect_media_entries_for_phase,
    dump_rendered_prompt,
    prompt_dump_path,
    write_multimodal_sidecar,
)
from ralph.prompts.developer import (
    DeveloperPromptInputs,
    PlanningPromptInputs,
    prompt_developer_iteration_xml_with_context,
    prompt_planning_xml_with_context,
)
from ralph.prompts.materialize_support import (
    current_prompt_variables as _current_prompt_variables,
)
from ralph.prompts.materialize_support import (
    merged_variables as _merged_variables,
)
from ralph.prompts.materialize_support import (
    persist_current_prompt as _persist_current_prompt,
)
from ralph.prompts.materialize_support import (
    phase_payload_variables,
)
from ralph.prompts.payload_refs import (
    sanitize_surrogates as _sanitize_surrogates,
)
from ralph.prompts.plan_format import format_plan_for_execution
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.template_engine import render_template
from ralph.skills._skill_resolver import get_inline_skill_content
from ralph.skills.manager import SkillManager

__all__ = [
    "MissingPlanHandoffError",
    "PromptPhaseContext",
    "PromptPhaseOptions",
    "collect_media_entries_for_phase",
    "materialize_prompt_for_phase",
    "prompt_file_for_phase",
    "submit_artifact_tool_name_for_transport",
    "tool_name_prefix_for_transport",
]
if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.phases.required_artifacts import RequiredArtifact
    from ralph.pipeline.work_units import WorkUnit
    from ralph.policy.models import ArtifactsPolicy, PhaseDefinition, PipelinePolicy
    from ralph.prompts._multimodal_sidecar_entry import MultimodalSidecarEntry
    from ralph.prompts.types import SessionCapabilities
    from ralph.workspace.protocol import Workspace


class _RepoGitProtocol(typing.Protocol):
    def diff(self, *_args: object, **_kwargs: object) -> str: ...

    def ls_files(self, *_args: object, **_kwargs: object) -> str: ...


class _RepoProtocol(typing.Protocol):
    git: _RepoGitProtocol


class _RepoFactoryProtocol(typing.Protocol):
    def __call__(self, *_args: object, **_kwargs: object) -> _RepoProtocol: ...


Repo: _RepoFactoryProtocol | None = None


@dataclass(frozen=True)
class PromptPhaseOptions:
    """Optional inputs for prompt materialization with sensible defaults."""

    artifacts_policy: ArtifactsPolicy | None = None
    worker_namespace: Path | None = None
    previous_phase: str | None = None
    resume_existing_phase: bool = False
    multimodal_entries: list[MultimodalSidecarEntry] | None = None
    work_unit: WorkUnit | None = None


def __getattr__(name: str) -> object:
    if name == "MultimodalSidecarEntry":
        from ralph.prompts._multimodal_sidecar_entry import (  # noqa: PLC0415
            MultimodalSidecarEntry as _Entry,
        )

        return _Entry
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)


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
                work_unit=cast("WorkUnit | None", kwargs.get("work_unit")),
            )
    opts = options or PromptPhaseOptions()
    prompt = _render_prompt_for_phase(context, opts)
    if _should_wrap_worker_prompt(context.phase, context.pipeline_policy, opts):
        assert opts.work_unit is not None
        prompt = render_worker_prompt(
            unit=opts.work_unit,
            base_prompt=prompt,
            policy=context.pipeline_policy,
        )
    path = dump_rendered_prompt(
        context.workspace,
        context.phase,
        prompt,
        worker_namespace=opts.worker_namespace,
    )
    if opts.multimodal_entries:
        write_multimodal_sidecar(
            context.workspace,
            context.phase,
            opts.multimodal_entries,
            worker_namespace=opts.worker_namespace,
        )
    else:
        clear_multimodal_sidecar(
            context.workspace,
            context.phase,
            worker_namespace=opts.worker_namespace,
        )
    return path


def _should_wrap_worker_prompt(
    phase: str,
    pipeline_policy: PipelinePolicy,
    options: PromptPhaseOptions,
) -> bool:
    if options.work_unit is None:
        return False
    phase_def = pipeline_policy.phases.get(phase)
    if phase_def is None or phase_def.role != "execution":
        return False
    artifacts_policy = options.artifacts_policy
    if artifacts_policy is None:
        return phase == "development"
    drain = phase_def.drain if phase_def.drain is not None else phase
    return _drain_artifact_type(drain, artifacts_policy) == "development_result"


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


def read_and_clear_retry_hint(workspace: Workspace, phase: str) -> str:
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
    _clear_accepted_analysis_history_if_needed(
        workspace_root=workspace_root,
        pipeline_policy=pipeline_policy,
        phase=phase,
        previous_phase=previous_phase,
        artifacts_policy=artifacts_policy,
    )
    current_prompt_path = _persist_current_prompt(
        workspace_root,
        prompt_content,
        worker_namespace=worker_namespace,
    )
    phase_def = pipeline_policy.phases.get(phase)
    phase_role = phase_def.role if phase_def is not None else None
    drain = phase_def.drain if phase_def is not None else phase
    drain_artifact_type = (
        _drain_artifact_type(drain, artifacts_policy) if artifacts_policy else None
    )
    # Planning-style prompt: execution role producing a plan artifact
    if phase_role == "execution" and drain_artifact_type == "plan":
        return _render_planning_prompt(
            context=context,
            options=options,
            phase=phase,
            workspace=workspace,
            session_caps=session_caps,
            tmpl_ctx=tmpl_ctx,
            template_name=template_name,
            prompt_content=prompt_content,
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
    # Commit-cleanup prompt: commit_cleanup role
    if phase_role == "commit_cleanup":
        return render_commit_cleanup_prompt(
            phase=phase,
            workspace_root=workspace_root,
            worker_namespace=worker_namespace,
            prompt_content=prompt_content,
            current_prompt_path=current_prompt_path,
            template_name=template_name,
            tmpl_ctx=tmpl_ctx,
            session_caps=session_caps,
        )
    plan_content, plan_path = _resolve_required_plan_handoff(
        workspace,
        template_name=template_name,
    )
    # Developer-style prompt: execution role producing a development_result artifact
    if phase_role == "execution" and drain_artifact_type == "development_result":
        return _render_developer_prompt(
            context=context,
            options=options,
            phase=phase,
            workspace=workspace,
            session_caps=session_caps,
            tmpl_ctx=tmpl_ctx,
            template_name=template_name,
            prompt_content=prompt_content,
            plan_content=plan_content,
            plan_path=plan_path,
        )
    # Template-based prompt: review, analysis, or other execution-role phases
    if phase_role in (ROLE_REVIEW, "analysis", "execution", "verification"):
        return _render_template_based_prompt(
            phase=phase,
            workspace=workspace,
            workspace_root=workspace_root,
            worker_namespace=worker_namespace,
            session_caps=session_caps,
            tmpl_ctx=tmpl_ctx,
            template_name=template_name,
            prompt_content=prompt_content,
            plan_content=plan_content,
            plan_path=plan_path,
            phase_def=phase_def,
            pipeline_policy=pipeline_policy,
            artifacts_policy=artifacts_policy,
            current_prompt_path=current_prompt_path,
        )
    msg = f"Unsupported phase '{phase}' (role={phase_role!r}) for prompt materialization"
    raise ValueError(msg)


def _render_planning_prompt(
    context: PromptPhaseContext,
    options: PromptPhaseOptions,
    phase: str,
    workspace: Workspace,
    session_caps: SessionCapabilities,
    tmpl_ctx: TemplateContext,
    template_name: str,
    prompt_content: str | None,
) -> str:
    workspace_root = context.workspace_root
    (
        plan_content,
        plan_path,
        analysis_feedback_content,
        analysis_feedback_path,
        template_name,
    ) = _prepare_planning_prompt_context(context, options)
    last_retry_error = read_and_clear_retry_hint(workspace, phase)
    artifact_history_path = resolve_planning_history_path(workspace_root)
    has_docs_mcp = SkillManager().get_docs_mcp_available(workspace_root=workspace_root)
    skills_inline_content = get_inline_skill_content()
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
            current_prompt_path=str(
                options.worker_namespace / "tmp" / "CURRENT_PROMPT.md"
                if options.worker_namespace is not None
                else workspace_root / ".agent" / "CURRENT_PROMPT.md"
            ),
            payload_root=str(
                options.worker_namespace / "tmp" / "prompt_payloads"
                if options.worker_namespace is not None
                else workspace_root / ".agent" / "tmp" / "prompt_payloads"
            ),
            last_retry_error=last_retry_error,
            skills_inline_content=skills_inline_content,
            has_docs_mcp=has_docs_mcp,
        ),
        workspace=workspace,
        session_caps=session_caps,
        template_name=template_name,
    )


def _render_developer_prompt(
    context: PromptPhaseContext,
    options: PromptPhaseOptions,
    phase: str,
    workspace: Workspace,
    session_caps: SessionCapabilities,
    tmpl_ctx: TemplateContext,
    template_name: str,
    prompt_content: str | None,
    plan_content: str | None,
    plan_path: str,
) -> str:
    workspace_root = context.workspace_root
    pipeline_policy = context.pipeline_policy
    previous_phase = options.previous_phase
    phase_def = pipeline_policy.phases.get(phase)
    drain = phase_def.drain if phase_def is not None else phase
    artifacts_policy = options.artifacts_policy
    drain_artifact_type = (
        _drain_artifact_type(drain, artifacts_policy) if artifacts_policy else None
    )
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
    last_retry_error = read_and_clear_retry_hint(workspace, phase)
    has_docs_mcp = SkillManager().get_docs_mcp_available(workspace_root=workspace_root)
    skills_inline_content = get_inline_skill_content()
    return prompt_developer_iteration_xml_with_context(
        context=tmpl_ctx,
        inputs=DeveloperPromptInputs(
            prompt_content=prompt_content,
            plan_content=plan_content,
            analysis_feedback_content=analysis_feedback_content,
            plan_path=plan_path,
            analysis_feedback_path=analysis_feedback_path,
            current_prompt_path=str(
                options.worker_namespace / "tmp" / "CURRENT_PROMPT.md"
                if options.worker_namespace is not None
                else workspace_root / ".agent" / "CURRENT_PROMPT.md"
            ),
            payload_root=str(
                options.worker_namespace / "tmp" / "prompt_payloads"
                if options.worker_namespace is not None
                else workspace_root / ".agent" / "tmp" / "prompt_payloads"
            ),
            prompt_name_prefix=phase,
            last_retry_error=last_retry_error,
            skills_inline_content=skills_inline_content,
            artifact_history_path=dev_artifact_history_path,
            artifact_history_dir=_artifact_history_dir_from_path(dev_artifact_history_path),
            has_docs_mcp=has_docs_mcp,
        ),
        workspace=workspace,
        session_caps=session_caps,
        template_name=template_name,
    )


def _render_template_based_prompt(
    phase: str,
    workspace: Workspace,
    workspace_root: Path,
    worker_namespace: Path | None,
    session_caps: SessionCapabilities,
    tmpl_ctx: TemplateContext,
    template_name: str,
    prompt_content: str | None,
    plan_content: str | None,
    plan_path: str,
    phase_def: PhaseDefinition | None,
    pipeline_policy: PipelinePolicy,
    artifacts_policy: ArtifactsPolicy | None,
    current_prompt_path: str | Path,
) -> str:
    template = tmpl_ctx.registry.get_template(template_name)
    diff_content = _git_diff(workspace_root)
    latest_artifact_content, latest_artifact_path = _latest_artifact_content(
        workspace, phase, pipeline_policy, artifacts_policy
    )
    issues_content, issues_path = _resolve_issues_content(workspace)
    fix_result_content, fix_result_path = resolve_fix_result_content(workspace)
    analysis_feedback_content, analysis_feedback_path = _resolve_loopback_analysis_feedback(
        workspace, phase, pipeline_policy, artifacts_policy
    )
    last_retry_error = read_and_clear_retry_hint(workspace, phase)
    has_docs_mcp = SkillManager().get_docs_mcp_available(workspace_root=workspace_root)
    skills_inline_content = get_inline_skill_content()
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
    variables.update(_current_prompt_variables(prompt_content, str(current_prompt_path)))
    variables["LAST_RETRY_ERROR"] = last_retry_error
    variables["HAS_DOCS_MCP"] = "true" if has_docs_mcp else ""
    variables["SKILLS_INLINE_CONTENT"] = skills_inline_content
    return render_template(
        template,
        _merged_variables(variables, session_caps),
        tmpl_ctx.partials,
    )


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


# Transports that expose every MCP tool as ``mcp__<server>__<tool>``: Claude Code
# and Codex CLI (its core qualifies tool names with the ``__`` delimiter
# unconditionally, even for a single server). Both must be prompted with that prefix.
_CLAUDE_STYLE_TRANSPORTS = (
    AgentTransport.CLAUDE,
    AgentTransport.CLAUDE_INTERACTIVE,
    AgentTransport.CODEX,
)


def submit_artifact_tool_name_for_transport(transport: AgentTransport | None) -> str:
    """Return the submit-artifact tool name for the given transport."""
    if transport in _CLAUDE_STYLE_TRANSPORTS:
        return claude_tool_name(SUBMIT_ARTIFACT_TOOL)
    if transport == AgentTransport.OPENCODE:
        return opencode_tool_name(SUBMIT_ARTIFACT_TOOL)
    return SUBMIT_ARTIFACT_TOOL


def tool_name_prefix_for_transport(transport: AgentTransport | None) -> str:
    """Return the tool name prefix for the given agent transport.

    Prompt templates must use the same MCP tool names the active transport sees,
    or the model calls a name that does not exist. OpenCode namespaces remote MCP
    tools as ``<server>_<tool>`` (Ralph's server is ``ralph``), so its prompts use
    the ``ralph_`` prefix — matching the ``ralph_*`` permission Ralph already
    grants in the OpenCode config. Claude AND Codex use ``mcp__ralph__``.
    """
    if transport in _CLAUDE_STYLE_TRANSPORTS:
        return claude_tool_name_prefix()
    if transport == AgentTransport.OPENCODE:
        return opencode_tool_name_prefix()
    return ""


def _read_optional(workspace: Workspace, path: str) -> str | None:
    if not workspace.exists(path):
        return None
    return workspace.read(path)


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
    # Preserve planning context for loopbacks and resumed passes.
    if not preserve_planning_context:
        # Clear drain artifacts for a genuine fresh planning entry.
        if (
            is_fresh_phase_entry(phase, previous_phase, pipeline_policy)
            and artifacts_policy is not None
        ):
            _clear_phase_entry_drains(
                workspace,
                phase,
                previous_phase,
                pipeline_policy,
                artifacts_policy,
            )
        _clear_fresh_planning_context(workspace, pipeline_policy, artifacts_policy)
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


def resolve_planning_history_path(
    workspace_root: Path,
) -> str:
    """Return the absolute path to the planning artifact history index, if it exists."""
    return _resolve_artifact_history_path(workspace_root, PLAN_ARTIFACT_TYPE)


def _clear_accepted_analysis_history_if_needed(
    *,
    workspace_root: Path,
    pipeline_policy: PipelinePolicy,
    phase: str,
    previous_phase: str | None,
    artifacts_policy: ArtifactsPolicy | None,
) -> None:
    """Clear artifact history per policy when an analysis phase accepts and advances.
    Handles both planning_analysis\u2192development and
    development_analysis\u2192development_commit transitions. The history remains
    available throughout analysis iterations. Once an analysis phase succeeds and
    the workflow advances to its on_success target, artifact history is cleared
    per the per-phase clear_on_fresh_entry policy.
    Also handles the bypass case where an analysis phase is skipped due to
    iteration cap being hit. In that case, previous_phase is an execution-role
    phase whose on_success leads to an analysis phase that routes to the current
    phase.
    """
    if previous_phase is None:
        return
    previous_phase_def = pipeline_policy.phases.get(previous_phase)
    if previous_phase_def is None:
        return
    if previous_phase_def.role == "analysis":
        _handle_analysis_accepted(
            workspace_root, pipeline_policy, phase, previous_phase_def, artifacts_policy
        )
    elif previous_phase_def.role == "execution":
        _handle_execution_bypass(
            workspace_root, pipeline_policy, phase, previous_phase_def, artifacts_policy
        )


def _handle_analysis_accepted(
    workspace_root: Path,
    pipeline_policy: PipelinePolicy,
    phase: str,
    previous_phase_def: PhaseDefinition,
    artifacts_policy: ArtifactsPolicy | None,
) -> None:
    """Handle the normal case where an analysis phase accepted and advanced."""
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
    _clear_artifact_history_per_policy(workspace_root, pipeline_policy, artifacts_policy)


def _handle_execution_bypass(
    workspace_root: Path,
    pipeline_policy: PipelinePolicy,
    phase: str,
    previous_phase_def: PhaseDefinition,
    artifacts_policy: ArtifactsPolicy | None,
) -> None:
    """Handle bypass case where execution phase skipped its analysis phase."""
    analysis_phase = previous_phase_def.transitions.on_success
    analysis_phase_def = pipeline_policy.phases.get(analysis_phase)
    if analysis_phase_def is None:
        return
    if analysis_phase_def.role != "analysis":
        return
    if analysis_phase_def.transitions.on_success != phase:
        return
    _clear_artifact_history_per_policy(workspace_root, pipeline_policy, artifacts_policy)


def _clear_artifact_history_per_policy(
    workspace_root: Path,
    pipeline_policy: PipelinePolicy,
    artifacts_policy: ArtifactsPolicy | None,
) -> None:
    """Clear artifact history per-phase policy declarations.
    Iterates over pipeline phases and clears artifact history only for phases
    with artifact_history.clear_on_fresh_entry=True. When artifacts_policy is None,
    returns immediately without clearing (safe conservative fallback).
    """
    if artifacts_policy is None:
        return
    artifact_dir = workspace_root / ".agent" / "artifacts"
    for phase_def in pipeline_policy.phases.values():
        if phase_def.artifact_history is None:
            continue
        if not phase_def.artifact_history.clear_on_fresh_entry:
            continue
        drain_type = _drain_artifact_type(phase_def.drain, artifacts_policy)
        if drain_type is not None:
            clear_artifact_history(artifact_dir, drain_type, backend=DEFAULT_FILE_BACKEND)


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
    pipeline_policy: PipelinePolicy,
    artifacts_policy: ArtifactsPolicy | None,
) -> None:
    """Delete prior planning state before rendering a fresh planning-creation prompt.
    Clears the plan draft (.plan_draft.json) and artifact history per policy.
    Drain artifact clearing is handled by phase_entry_cleaner.clear_phase_entry_drains
    at PreparePromptEffect time in the runner flow, and by the direct call in
    _prepare_planning_prompt_context for the direct materialization path.
    """
    if workspace.exists(PLAN_DRAFT_PATH):
        workspace.remove(PLAN_DRAFT_PATH)
    workspace_root = Path(workspace.absolute_path("."))
    _clear_artifact_history_per_policy(workspace_root, pipeline_policy, artifacts_policy)


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


def resolve_fix_result_content(workspace: Workspace) -> tuple[str, str]:
    """Return (content, path) for the fix_result artifact, with a fallback if absent."""
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
        skip = role in ("commit", "commit_cleanup", "analysis") or (
            role == "execution" and pdef.skip_invocation
        )
        if skip:
            queue.extend(_predecessors(current, pipeline_policy))
        else:
            return resolve_required_artifact(artifacts_policy, drain=pdef.drain)
    return None


def _git_output(workspace_root: Path, *args: str) -> str:
    """Run a git command in the workspace and return sanitized stdout."""
    result = run_process(
        "git",
        args,
        options=ProcessRunOptions(cwd=workspace_root),
    )
    if result.returncode != 0:
        return "(no diff available)"
    return _sanitize_surrogates(result.stdout).strip() or "(no diff available)"


def _git_diff(workspace_root: Path) -> str:
    """Return the cumulative diff from the dev-cycle baseline through the working tree.
    When a baseline SHA is recorded in .agent/start_commit, the diff includes:
    - All commits landed since the baseline (baseline..HEAD), and any uncommitted
      changes on top (HEAD vs working tree). This is correct whether the user
      commits once per dev cycle or once per individual dev iteration within a cycle.
    """
    baseline_sha = read_cycle_baseline(workspace_root)
    if baseline_sha:
        committed = _git_output(workspace_root, "diff", baseline_sha, "HEAD")
        uncommitted = _git_output(workspace_root, "diff", "HEAD")
        parts = [p for p in (committed, uncommitted) if p and p != "(no diff available)"]
        return "\n".join(parts) if parts else "(no diff available)"
    return _git_output(workspace_root, "diff", "HEAD")


def _pending_diff(workspace_root: Path) -> str:
    """Return the pending (staged but not committed) diff for a workspace."""
    return _git_output(workspace_root, "diff", "HEAD")


def _commit_phase_diff(workspace_root: Path) -> str:
    diff = _pending_diff(workspace_root).strip()
    if Repo is not None:
        repo: _RepoProtocol | None = None
        try:
            repo = Repo(workspace_root)
            untracked = repo.git.ls_files("--others", "--exclude-standard").strip()
        except Exception:
            untracked = ""
        finally:
            close = cast("Callable[[], None] | None", getattr(repo, "close", None))
            if close is not None:
                close()
    else:
        untracked = _git_output(
            workspace_root, "ls-files", "--others", "--exclude-standard"
        ).strip()
        if untracked == "(no diff available)":
            untracked = ""
    if not untracked:
        return diff or "(no diff available)"
    if diff == "(no diff available)":
        diff = ""
    combined = (diff + "\n\n## Untracked files (staged by git add -A):\n" + untracked).strip()
    return combined or "(no diff available)"

