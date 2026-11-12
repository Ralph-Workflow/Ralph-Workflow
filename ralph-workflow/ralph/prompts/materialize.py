from __future__ import annotations

import os
import typing
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ralph.checked_accessors import optional_str
from ralph.config.enums import AgentTransport
from ralph.executor.process import ProcessRunOptions, run_process
from ralph.mcp.artifacts.development_result import DEVELOPMENT_RESULT_ARTIFACT_TYPE
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.handoffs import handoff_path_for_artifact
from ralph.mcp.artifacts.history import (
    clear_artifact_history,
    history_index_path,
)
from ralph.mcp.artifacts.markdown import parse_and_validate, parse_markdown_document
from ralph.mcp.artifacts.markdown.specs.development_result import DEVELOPMENT_RESULT_SPEC
from ralph.mcp.artifacts.markdown.specs.plan import PLAN_SPEC
from ralph.mcp.artifacts.plan import PLAN_ARTIFACT_PATH
from ralph.mcp.protocol.cycle_deadline_env import read_published_epoch
from ralph.mcp.protocol.env import DEV_DEADLINE_EPOCH_ENV, DEV_WARN_EPOCH_ENV
from ralph.mcp.tools.names import (
    SUBMIT_MD_ARTIFACT_TOOL,
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
from ralph.pro_support.env import PROMPT_PATH as _PROMPT_PATH_ENV
from ralph.pro_support.prompt import resolve_effective_prompt_path
from ralph.prompts._commit_diff import _UNTRACKED_HEADER, _is_inside_git_repo
from ralph.prompts._missing_plan_handoff_error import MissingPlanHandoffError
from ralph.prompts._prompt_phase_context import PromptPhaseContext
from ralph.prompts.commit import CommitPromptPayloadConfig, prompt_commit_message
from ralph.prompts.commit_evidence import build_commit_evidence_bundle
from ralph.prompts.commit_review import render_commit_review_prompt
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
    merged_variables as _merged_variables,
)
from ralph.prompts.materialize_support import (
    persist_product_criteria as _persist_product_criteria,
)
from ralph.prompts.materialize_support import (
    phase_payload_variables,
)
from ralph.prompts.materialize_support import (
    product_criteria_variables as _product_criteria_variables,
)
from ralph.prompts.payload_refs import (
    sanitize_surrogates as _sanitize_surrogates,
)
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.template_engine import render_template
from ralph.recovery.retry_prompt import build_validation_retry_footer
from ralph.skills._skill_resolver import get_inline_skill_content
from ralph.skills.manager import SkillManager

PLAN_MD_DRAFT_PATH = ".agent/artifacts/.plan.draft.md"
PARTIAL_DEVELOPMENT_RESULT_CONTEXT_PATH = (
    ".agent/tmp/prompt_payloads/development_result_continuation.md"
)

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
    artifacts_policy: ArtifactsPolicy | None = None

    worker_namespace: Path | None = None
    previous_phase: str | None = None
    resume_existing_phase: bool = False
    multimodal_entries: list[MultimodalSidecarEntry] | None = None

    work_unit: WorkUnit | None = None

    proposal_ref: str | None = None
    tree_sha: str | None = None
    soft_tripwires: list[dict[str, object]] | str | None = None

    last_review_rejection_reason: str | None = None
    rollback_ref: str | None = None
    last_proposal_ref: str | None = None


def __getattr__(name: str) -> object:
    if name == "MultimodalSidecarEntry":
        module = import_module(
            "ralph.prompts._multimodal_sidecar_entry",
        )
        return typing.cast("object", module.MultimodalSidecarEntry)
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)


def materialize_prompt_for_phase(
    context: PromptPhaseContext | None = None,
    options: PromptPhaseOptions | None = None,
    **kwargs: object,
) -> str:
    if context is None:
        context = PromptPhaseContext(
            phase=cast("str", kwargs["phase"]),
            workspace=cast("Workspace", kwargs["workspace"]),
            pipeline_policy=cast("PipelinePolicy", kwargs["pipeline_policy"]),
            session_caps=cast("SessionCapabilities", kwargs["session_caps"]),
            workspace_root=cast("Path", kwargs["workspace_root"]),
        )
    if options is None:
        opts = PromptPhaseOptions(
            artifacts_policy=cast("ArtifactsPolicy | None", kwargs.get("artifacts_policy")),
            worker_namespace=cast("Path | None", kwargs.get("worker_namespace")),
            previous_phase=cast("str | None", kwargs.get("previous_phase")),
            resume_existing_phase=cast("bool", kwargs.get("resume_existing_phase", False)),
            multimodal_entries=cast(
                "list[MultimodalSidecarEntry] | None", kwargs.get("multimodal_entries")
            ),
            work_unit=cast("WorkUnit | None", kwargs.get("work_unit")),
            proposal_ref=optional_str(kwargs.get("proposal_ref")),
            tree_sha=optional_str(kwargs.get("tree_sha")),
            soft_tripwires=cast(
                "list[dict[str, object]] | str | None", kwargs.get("soft_tripwires")
            ),
            last_review_rejection_reason=optional_str(kwargs.get("last_review_rejection_reason")),
            rollback_ref=optional_str(kwargs.get("rollback_ref")),
            last_proposal_ref=optional_str(kwargs.get("last_proposal_ref")),
        )
    else:
        opts = PromptPhaseOptions(
            artifacts_policy=options.artifacts_policy,
            worker_namespace=options.worker_namespace,
            previous_phase=options.previous_phase,
            resume_existing_phase=options.resume_existing_phase,
            multimodal_entries=options.multimodal_entries,
            work_unit=options.work_unit,
            proposal_ref=options.proposal_ref or optional_str(kwargs.get("proposal_ref")),
            tree_sha=options.tree_sha or optional_str(kwargs.get("tree_sha")),
            soft_tripwires=options.soft_tripwires
            or cast("list[dict[str, object]] | str | None", kwargs.get("soft_tripwires")),
            last_review_rejection_reason=options.last_review_rejection_reason
            or optional_str(kwargs.get("last_review_rejection_reason")),
            rollback_ref=options.rollback_ref or optional_str(kwargs.get("rollback_ref")),
            last_proposal_ref=options.last_proposal_ref
            or optional_str(kwargs.get("last_proposal_ref")),
        )
    if opts.work_unit is not None or opts.worker_namespace is not None:
        raise ValueError(
            "worker and work-unit execution is deprecated; use the developer execution path"
        )
    prompt = _render_prompt_for_phase(context, opts)
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


def prompt_file_for_phase(phase: str) -> str:
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


def read_and_clear_retry_hint(
    workspace: Workspace,
    phase: str,
    *,
    worker_namespace: Path | None = None,
    pipeline_policy: PipelinePolicy | None = None,
) -> str:
    drain = _retry_hint_drain(phase, pipeline_policy)
    path = (
        str(worker_namespace / "tmp" / f"last_retry_error_{drain}.txt")
        if worker_namespace is not None
        else retry_hint_path(phase, pipeline_policy=pipeline_policy)
    )
    legacy_path = (
        str(worker_namespace / "tmp" / f"last_retry_error_{phase}.txt")
        if worker_namespace is not None
        else retry_hint_path(phase)
    )
    source_path = path if workspace.exists(path) else legacy_path
    if not workspace.exists(source_path):
        return ""
    try:
        return workspace.read(source_path)
    except Exception:
        return ""


def _retry_hint_drain(phase: str, pipeline_policy: PipelinePolicy | None) -> str:
    phase_def = pipeline_policy.phases.get(phase) if pipeline_policy is not None else None
    return phase_def.drain if phase_def is not None else phase


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
    workspace_root_for_resolver = Path(workspace.absolute_path("."))
    raw_prompt_path = os.environ.get(_PROMPT_PATH_ENV, "")
    if raw_prompt_path:
        source_prompt_path = resolve_effective_prompt_path(workspace_root_for_resolver, os.environ)
        prompt_content = _read_optional(workspace, str(source_prompt_path))
    else:
        prompt_content = _read_optional(workspace, "PROMPT.md")
    _clear_accepted_analysis_history_if_needed(
        workspace_root=workspace_root,
        pipeline_policy=pipeline_policy,
        phase=phase,
        previous_phase=previous_phase,
        artifacts_policy=artifacts_policy,
    )
    product_criteria_path = _persist_product_criteria(
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
    if phase_role == "commit":
        evidence = (
            build_commit_evidence_bundle(workspace_root)
            if (workspace_root / ".git").exists()
            else _pending_diff(workspace_root)
        )
        optional_context_paths = _optional_commit_context_paths(
            phase=phase,
            previous_phase=previous_phase,
            pipeline_policy=pipeline_policy,
            artifacts_policy=artifacts_policy,
        )
        return prompt_commit_message(
            evidence,
            template_registry=tmpl_ctx.registry,
            partials=tmpl_ctx.partials,
            submit_artifact_tool_names=SUBMIT_MD_ARTIFACT_TOOL.prompt_aliases(
                tool_name_prefix=session_caps.tool_name_prefix,
            ),
            payload_config=CommitPromptPayloadConfig(
                output_dir=workspace_root / ".agent" / "tmp" / "prompt_payloads",
                name_prefix=phase,
            ),
            workspace_root=workspace_root,
            optional_context_paths=optional_context_paths,
            allow_empty_diff=True,
        )
    if phase_role == "commit_review":
        _snapshot_partial_execution_result(
            workspace,
            previous_phase=previous_phase,
            pipeline_policy=pipeline_policy,
            artifacts_policy=artifacts_policy,
        )
        return render_commit_review_prompt(
            phase=phase,
            workspace_root=workspace_root,
            worker_namespace=worker_namespace,
            prompt_content=prompt_content,
            product_criteria_path=product_criteria_path,
            template_name=template_name,
            tmpl_ctx=tmpl_ctx,
            session_caps=session_caps,
            pipeline_policy=pipeline_policy,
            proposal_ref=options.proposal_ref,
            tree_sha=options.tree_sha,
            soft_tripwires=options.soft_tripwires,
        )
    plan_content, plan_path = _resolve_required_plan_handoff(
        workspace,
        template_name=template_name,
    )
    if phase_role == "execution" and (
        drain_artifact_type == "development_result"
        or (options.work_unit is not None and artifacts_policy is None)
    ):
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
            product_criteria_path=product_criteria_path,
            options=options,
        )
    msg = f"Unsupported phase '{phase}' (role={phase_role!r}) for prompt materialization"
    raise ValueError(msg)


def _append_retry_footer(rendered: str, last_retry_error: str) -> str:
    if not last_retry_error.startswith("VALIDATION FAILURE"):
        return rendered
    return f"{rendered.rstrip()}\n\n{build_validation_retry_footer()}\n"


append_retry_footer = _append_retry_footer


def _development_execution_envelope(pipeline_policy: PipelinePolicy) -> tuple[str, str]:
    timebox = pipeline_policy.development_timebox
    development = pipeline_policy.phases.get("development")
    parallelization = development.parallelization if development is not None else None
    budget_minutes = f"{timebox.duration_seconds / 60:g}" if timebox is not None else "unknown"
    worker_cap = str(parallelization.max_parallel_workers) if parallelization is not None else "1"
    return budget_minutes, worker_cap


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
        analysis_feedback_status,
        template_name,
        preserve_planning_context,
    ) = _prepare_planning_prompt_context(context, options)
    last_retry_error = (
        read_and_clear_retry_hint(
            workspace,
            phase,
            worker_namespace=options.worker_namespace,
            pipeline_policy=context.pipeline_policy,
        )
        if preserve_planning_context
        else ""
    )
    has_docs_mcp = SkillManager().get_docs_mcp_available(workspace_root=workspace_root)
    skills_inline_content = get_inline_skill_content()
    development_budget_minutes, development_worker_cap = _development_execution_envelope(
        context.pipeline_policy
    )
    rendered = prompt_planning_xml_with_context(
        context=tmpl_ctx,
        inputs=PlanningPromptInputs(
            prompt_content=prompt_content,
            plan_content=plan_content,
            analysis_feedback_content=analysis_feedback_content,
            plan_path=plan_path,
            analysis_feedback_path=analysis_feedback_path,
            analysis_feedback_status=analysis_feedback_status,
            product_criteria_path=str(
                options.worker_namespace / "tmp" / "PRODUCT_CRITERIA.md"
                if options.worker_namespace is not None
                else workspace_root / ".agent" / "PRODUCT_CRITERIA.md"
            ),
            payload_root=str(
                options.worker_namespace / "tmp" / "prompt_payloads"
                if options.worker_namespace is not None
                else workspace_root / ".agent" / "tmp" / "prompt_payloads"
            ),
            last_retry_error=last_retry_error,
            skills_inline_content=skills_inline_content,
            has_docs_mcp=has_docs_mcp,
            development_budget_minutes=development_budget_minutes,
            development_max_parallel_workers=development_worker_cap,
        ),
        workspace=workspace,
        session_caps=session_caps,
        template_name=template_name,
    )
    return _append_retry_footer(rendered, last_retry_error)


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
    prior_partial_result = _resolve_partial_development_result(
        workspace,
        drain=drain,
        artifacts_policy=artifacts_policy,
        worker_namespace=options.worker_namespace,
    )
    is_continuation = dev_is_loopback or prior_partial_result is not None
    if is_continuation:
        loopback_template_name = _loopback_template_name_for_phase(phase_def)
        if loopback_template_name:
            template_name = loopback_template_name
    dev_artifact_history_path = _resolve_and_clear_dev_artifact_history(
        workspace_root=workspace_root,
        phase_def=phase_def,
        drain_artifact_type=drain_artifact_type,
        is_loopback=is_continuation,
        worker_namespace=options.worker_namespace,
    )
    (
        analysis_feedback_content,
        analysis_feedback_path,
        analysis_feedback_status,
    ) = _resolve_loopback_analysis_feedback(workspace, phase, pipeline_policy, artifacts_policy)
    last_retry_error = read_and_clear_retry_hint(
        workspace,
        phase,
        worker_namespace=options.worker_namespace,
        pipeline_policy=pipeline_policy,
    )
    has_docs_mcp = SkillManager().get_docs_mcp_available(workspace_root=workspace_root)
    skills_inline_content = get_inline_skill_content()
    dev_warn_epoch = read_published_epoch(DEV_WARN_EPOCH_ENV)
    dev_deadline_epoch = read_published_epoch(DEV_DEADLINE_EPOCH_ENV)
    rejection_reason = options.last_review_rejection_reason or ""
    rollback_ref = options.rollback_ref or options.last_proposal_ref or ""
    if not rejection_reason and "Commit review rejected previous iteration:" in last_retry_error:
        for line in last_retry_error.splitlines():
            if line.startswith("Commit review rejected previous iteration:"):
                rejection_reason = line.split(":", 1)[1].strip()
            elif line.startswith("Proposal ref:"):
                rollback_ref = line.split(":", 1)[1].strip()
    rendered = prompt_developer_iteration_xml_with_context(
        context=tmpl_ctx,
        inputs=DeveloperPromptInputs(
            prompt_content=prompt_content,
            plan_content=plan_content,
            analysis_feedback_content=analysis_feedback_content,
            plan_path=plan_path,
            analysis_feedback_path=analysis_feedback_path,
            analysis_feedback_status=analysis_feedback_status,
            product_criteria_path=str(
                options.worker_namespace / "tmp" / "PRODUCT_CRITERIA.md"
                if options.worker_namespace is not None
                else workspace_root / ".agent" / "PRODUCT_CRITERIA.md"
            ),
            payload_root=str(
                options.worker_namespace / "tmp" / "prompt_payloads"
                if options.worker_namespace is not None
                else workspace_root / ".agent" / "tmp" / "prompt_payloads"
            ),
            prompt_name_prefix=phase,
            last_retry_error=last_retry_error,
            last_review_rejection_reason=rejection_reason,
            rollback_ref=rollback_ref,
            last_proposal_ref=rollback_ref,
            prior_result_status=prior_partial_result[0] if prior_partial_result else "",
            prior_result_markdown=prior_partial_result[1] if prior_partial_result else "",
            prior_session_id=prior_partial_result[2] if prior_partial_result else "",
            skills_inline_content=skills_inline_content,
            artifact_history_path=dev_artifact_history_path,
            artifact_history_dir=_artifact_history_dir_from_path(dev_artifact_history_path),
            has_docs_mcp=has_docs_mcp,
            is_continuation=is_continuation,
            dev_warn_epoch=dev_warn_epoch,
            dev_deadline_epoch=dev_deadline_epoch,
        ),
        workspace=workspace,
        session_caps=session_caps,
        template_name=template_name,
    )
    return _append_retry_footer(rendered, last_retry_error)


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
    product_criteria_path: str | Path,
    options: PromptPhaseOptions | None = None,
) -> str:
    template = tmpl_ctx.registry.get_template(template_name)
    diff_content = ""
    latest_artifact_content, latest_artifact_path = _latest_artifact_content(
        workspace, phase, pipeline_policy, artifacts_policy
    )
    issues_content, issues_path = _resolve_issues_content(workspace)
    fix_result_content, fix_result_path = resolve_fix_result_content(workspace)
    (
        analysis_feedback_content,
        analysis_feedback_path,
        analysis_feedback_status,
    ) = _resolve_loopback_analysis_feedback(workspace, phase, pipeline_policy, artifacts_policy)
    last_retry_error = read_and_clear_retry_hint(
        workspace,
        phase,
        worker_namespace=worker_namespace,
        pipeline_policy=pipeline_policy,
    )
    has_docs_mcp = SkillManager().get_docs_mcp_available(workspace_root=workspace_root)
    skills_inline_content = get_inline_skill_content()
    variables = phase_payload_variables(
        phase=phase,
        workspace_root=workspace_root,
        worker_namespace=worker_namespace,
        values={
            "PLAN": "" if plan_path else (plan_content or "(no plan available)"),
            "DIFF": diff_content,
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
    variables["ANALYSIS_FEEDBACK_STATUS"] = analysis_feedback_status
    if phase_def is not None and phase_def.skip_invocation:
        variables["HIDE_ARTIFACT_SUBMISSION_GUIDANCE"] = "true"
    variables.update(_product_criteria_variables(prompt_content, str(product_criteria_path)))
    variables["LAST_RETRY_ERROR"] = last_retry_error
    rejection_reason = (options.last_review_rejection_reason if options else None) or ""
    rollback_ref = (options.rollback_ref or options.last_proposal_ref if options else None) or ""
    if not rejection_reason and "Commit review rejected previous iteration:" in last_retry_error:
        for line in last_retry_error.splitlines():
            if line.startswith("Commit review rejected previous iteration:"):
                rejection_reason = line.split(":", 1)[1].strip()
            elif line.startswith("Proposal ref:"):
                rollback_ref = line.split(":", 1)[1].strip()
    variables["LAST_REVIEW_REJECTION_REASON"] = rejection_reason
    variables["ROLLBACK_REF"] = rollback_ref
    variables["LAST_PROPOSAL_REF"] = rollback_ref
    variables["HAS_DOCS_MCP"] = "true" if has_docs_mcp else ""
    variables["DOCS_MCP_PORT"] = "localhost:6280"
    variables["SKILLS_INLINE_CONTENT"] = skills_inline_content
    development_budget_minutes, development_worker_cap = _development_execution_envelope(
        pipeline_policy
    )
    variables["DEVELOPMENT_BUDGET_MINUTES"] = development_budget_minutes
    variables["DEVELOPMENT_MAX_PARALLEL_WORKERS"] = development_worker_cap
    rendered = render_template(
        template,
        _merged_variables(variables, session_caps),
        tmpl_ctx.partials,
    )
    return _append_retry_footer(rendered, last_retry_error)


_CLAUDE_STYLE_TRANSPORTS = (
    AgentTransport.CLAUDE,
    AgentTransport.CLAUDE_INTERACTIVE,
    AgentTransport.CODEX,
    AgentTransport.CURSOR,
    AgentTransport.KIMI,
)


def submit_artifact_tool_name_for_transport(transport: AgentTransport | None) -> str:
    if transport in _CLAUDE_STYLE_TRANSPORTS:
        return claude_tool_name(SUBMIT_MD_ARTIFACT_TOOL)
    if transport == AgentTransport.OPENCODE:
        return opencode_tool_name(SUBMIT_MD_ARTIFACT_TOOL)
    return SUBMIT_MD_ARTIFACT_TOOL


def tool_name_prefix_for_transport(transport: AgentTransport | None) -> str:
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
    return _resolve_agent_handoff(
        workspace,
        artifact_type="plan",
        artifact_path=PLAN_ARTIFACT_PATH,
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
    if allow_draft_fallback and workspace.exists(PLAN_MD_DRAFT_PATH):
        draft = workspace.read(PLAN_MD_DRAFT_PATH)
        _content, diagnostics = parse_and_validate(draft, PLAN_SPEC)
        if not any(diagnostic.severity == "error" for diagnostic in diagnostics):
            return draft, ""
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
    has_retry_hint = bool(
        _read_optional(workspace, retry_hint_path(phase, pipeline_policy=pipeline_policy))
    )
    preserve_retry_context = previous_phase == phase and has_retry_hint
    return is_loopback or preserve_retry_context or resume_existing_phase


def _prepare_planning_prompt_context(
    context: PromptPhaseContext,
    options: PromptPhaseOptions,
) -> tuple[str | None, str, str, str, str, str, bool]:
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
    if not preserve_planning_context:
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
    (
        analysis_feedback_content,
        analysis_feedback_path,
        analysis_feedback_status,
    ) = (
        _resolve_loopback_analysis_feedback(workspace, phase, pipeline_policy, artifacts_policy)
        if preserve_planning_context
        else ("", "", "")
    )
    if preserve_planning_context and not _template_allows_missing_plan_handoff(template_name):
        plan_content, plan_path = _resolve_required_plan_handoff(
            workspace,
            template_name=template_name,
            allow_draft_fallback=resume_existing_phase,
        )
    else:
        plan_content, plan_path = None, ""
    return (
        plan_content,
        plan_path,
        analysis_feedback_content,
        analysis_feedback_path,
        analysis_feedback_status,
        template_name,
        preserve_planning_context,
    )


def _resolve_artifact_history_path(workspace_root: Path, artifact_type: str) -> str:
    artifact_dir = workspace_root / ".agent" / "artifacts"
    index = history_index_path(artifact_dir, artifact_type)
    if index.exists():
        return str(index)
    return ""


def _artifact_history_dir_from_path(history_path: str) -> str:
    if not history_path:
        return ""
    return str(Path(history_path).parent)


def _clear_accepted_analysis_history_if_needed(
    *,
    workspace_root: Path,
    pipeline_policy: PipelinePolicy,
    phase: str,
    previous_phase: str | None,
    artifacts_policy: ArtifactsPolicy | None,
) -> None:
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
    worker_namespace: Path | None = None,
) -> str:
    if worker_namespace is not None:
        return ""
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
    if workspace.exists(PLAN_MD_DRAFT_PATH):
        workspace.remove(PLAN_MD_DRAFT_PATH)
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
) -> tuple[str | None, str]:
    relative_handoff_path = handoff_path_for_artifact(artifact_type)
    if relative_handoff_path:
        handoff_path = workspace.absolute_path(relative_handoff_path)
        markdown = _read_optional(workspace, relative_handoff_path)
        if markdown:
            return markdown, handoff_path
    artifact_content = _read_optional(workspace, artifact_path)
    if artifact_content:
        return artifact_content, workspace.absolute_path(artifact_path)
    return None, ""


def _resolve_issues_content(workspace: Workspace) -> tuple[str, str]:
    content, path = _resolve_agent_handoff(
        workspace,
        artifact_type="issues",
        artifact_path=".agent/artifacts/issues.md",
    )
    return content or "(no review issues available)", path


def resolve_fix_result_content(workspace: Workspace) -> tuple[str, str]:
    content, path = _resolve_agent_handoff(
        workspace,
        artifact_type="fix_result",
        artifact_path=".agent/artifacts/fix_result.md",
    )
    return content or "(no fix result available)", path


def _resolve_loopback_analysis_feedback(
    workspace: Workspace,
    phase: str,
    pipeline_policy: PipelinePolicy,
    artifacts_policy: ArtifactsPolicy | None,
) -> tuple[str, str, str]:
    if artifacts_policy is None:
        return "", "", ""
    for pdef in pipeline_policy.phases.values():
        if pdef.role == "analysis" and pdef.transitions.on_loopback == phase:
            ra = resolve_required_artifact(artifacts_policy, drain=pdef.drain)
            if ra is not None:
                content, path = _resolve_agent_handoff(
                    workspace,
                    artifact_type=ra.artifact_type,
                    artifact_path=ra.artifact_path,
                )
                document, _diagnostics = parse_markdown_document(content or "")
                return content or "", path, document.frontmatter.get("status", "")
    return "", "", ""


def _resolve_partial_development_result(
    workspace: Workspace,
    *,
    drain: str,
    artifacts_policy: ArtifactsPolicy | None,
    worker_namespace: Path | None = None,
) -> tuple[str, str, str, str] | None:
    if artifacts_policy is None:
        return None
    required_artifact = resolve_required_artifact(artifacts_policy, drain=drain)
    if (
        required_artifact is None
        or required_artifact.artifact_type != DEVELOPMENT_RESULT_ARTIFACT_TYPE
    ):
        return None
    if worker_namespace is None:
        markdown, _path = _resolve_agent_handoff(
            workspace,
            artifact_type=required_artifact.artifact_type,
            artifact_path=required_artifact.artifact_path,
        )
    else:
        markdown = _read_optional(
            workspace,
            str(worker_namespace / "handoffs" / "DEVELOPMENT_RESULT.md"),
        ) or _read_optional(
            workspace,
            str(worker_namespace / "artifacts" / "development_result.md"),
        )
    if not markdown and worker_namespace is None:
        markdown = _read_optional(workspace, PARTIAL_DEVELOPMENT_RESULT_CONTEXT_PATH)
    if not markdown:
        return None
    content = _validated_development_result_content(markdown)
    if content is None or content.get("status") != "partial":
        return None
    continuation = content.get("continuation")
    prior_session_id = (
        continuation.get("prior_session_id") if isinstance(continuation, dict) else None
    )
    session_id_str = prior_session_id if isinstance(prior_session_id, str) else ""
    return (
        "partial",
        markdown,
        session_id_str,
        "",
    )


def _snapshot_partial_execution_result(
    workspace: Workspace,
    *,
    previous_phase: str | None,
    pipeline_policy: PipelinePolicy,
    artifacts_policy: ArtifactsPolicy | None,
) -> None:
    if previous_phase is None or artifacts_policy is None:
        return
    previous_phase_def = pipeline_policy.phases.get(previous_phase)
    if previous_phase_def is None or previous_phase_def.role != "execution":
        return
    required_artifact = resolve_required_artifact(
        artifacts_policy,
        drain=previous_phase_def.drain,
    )
    if (
        required_artifact is None
        or required_artifact.artifact_type != DEVELOPMENT_RESULT_ARTIFACT_TYPE
    ):
        return
    markdown, _path = _resolve_agent_handoff(
        workspace,
        artifact_type=required_artifact.artifact_type,
        artifact_path=required_artifact.artifact_path,
    )
    if not markdown:
        return
    content = _validated_development_result_content(markdown)
    if content is None:
        return
    if content.get("status") == "partial":
        workspace.write(PARTIAL_DEVELOPMENT_RESULT_CONTEXT_PATH, markdown)
    elif workspace.exists(PARTIAL_DEVELOPMENT_RESULT_CONTEXT_PATH):
        workspace.remove(PARTIAL_DEVELOPMENT_RESULT_CONTEXT_PATH)


def _optional_commit_context_paths(
    *,
    phase: str,
    previous_phase: str | None,
    pipeline_policy: PipelinePolicy,
    artifacts_policy: ArtifactsPolicy | None,
) -> tuple[str, ...]:
    if previous_phase is None or artifacts_policy is None:
        return ()
    previous_phase_def = pipeline_policy.phases.get(previous_phase)
    if previous_phase_def is None:
        return ()

    execution_predecessors = (
        (previous_phase_def,)
        if previous_phase_def.role == "execution"
        else tuple(
            phase_def
            for phase_def in pipeline_policy.phases.values()
            if previous_phase_def.role == "commit_review"
            and previous_phase_def.transitions.on_success == phase
            and phase_def.role == "execution"
            and phase_def.transitions.on_success == previous_phase
        )
    )
    for predecessor in execution_predecessors:
        required_artifact = resolve_required_artifact(
            artifacts_policy,
            drain=predecessor.drain,
        )
        if (
            required_artifact is not None
            and required_artifact.artifact_type == DEVELOPMENT_RESULT_ARTIFACT_TYPE
        ):
            return (
                required_artifact.artifact_path,
                PLAN_ARTIFACT_PATH,
                ".agent/PRODUCT_CRITERIA.md",
            )
    return ()


def _validated_development_result_content(markdown: str) -> dict[str, object] | None:
    content, diagnostics = parse_and_validate(markdown, DEVELOPMENT_RESULT_SPEC)
    if any(diagnostic.severity == "error" for diagnostic in diagnostics):
        return None
    return content


def _latest_artifact_content(
    workspace: Workspace,
    phase: str,
    pipeline_policy: PipelinePolicy,
    artifacts_policy: ArtifactsPolicy | None,
) -> tuple[str, str]:
    if artifacts_policy is None:
        return "", ""
    ra = _find_work_artifact(phase, pipeline_policy, artifacts_policy)
    if ra is None:
        return "", ""
    content, path = _resolve_agent_handoff(
        workspace,
        artifact_type=ra.artifact_type,
        artifact_path=ra.artifact_path,
    )
    return content or "", path


def _drain_artifact_type(drain: str, artifacts_policy: ArtifactsPolicy) -> str | None:
    ra = resolve_required_artifact(artifacts_policy, drain=drain)
    return ra.artifact_type if ra is not None else None


def _predecessors(phase: str, pipeline_policy: PipelinePolicy) -> list[str]:
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
        skip = role in ("commit", "commit_review", "analysis") or (
            role == "execution" and pdef.skip_invocation
        )
        if skip:
            queue.extend(_predecessors(current, pipeline_policy))
        else:
            return resolve_required_artifact(artifacts_policy, drain=pdef.drain)
    return None


def _git_output(workspace_root: Path, *args: str) -> str:
    result = run_process(
        "git",
        args,
        options=ProcessRunOptions(cwd=workspace_root),
    )
    if result.returncode != 0:
        return "(no diff available)"
    return _sanitize_surrogates(result.stdout).strip() or "(no diff available)"


def _git_diff(workspace_root: Path) -> str:
    if not _is_inside_git_repo(workspace_root):
        return "(no diff available)"
    baseline_sha = read_cycle_baseline(workspace_root)
    if baseline_sha:
        return _git_output(workspace_root, "diff", baseline_sha)
    return _git_output(workspace_root, "diff", "HEAD")


def _pending_diff(workspace_root: Path) -> str:
    if not _is_inside_git_repo(workspace_root):
        return "(no diff available)"
    return _git_output(workspace_root, "diff", "HEAD")


def _commit_phase_diff(workspace_root: Path) -> str:
    diff = _pending_diff(workspace_root).strip()
    if not _is_inside_git_repo(workspace_root):
        return diff or "(no diff available)"
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
    combined = (diff + "\n\n" + _UNTRACKED_HEADER + "\n" + untracked).strip()
    return combined or "(no diff available)"
