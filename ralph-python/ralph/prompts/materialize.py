"""Policy-selected prompt materialization."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from git import Repo

from ralph.prompts.commit import prompt_commit_message
from ralph.prompts.debug_dump import dump_rendered_prompt, prompt_dump_path
from ralph.prompts.developer import (
    DeveloperPromptInputs,
    prompt_developer_iteration_xml_with_context,
    prompt_planning_xml_with_context,
)
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
            ),
            workspace=workspace,
            session_caps=session_caps,
            template_name=template_name,
        )
    if phase in {"review", "fix", "development_analysis", "review_analysis"}:
        template = context.registry.get_template(template_name)
        variables = {
            "PROMPT": prompt_content or "No requirements provided",
            "PLAN": plan_content or "(no plan available)",
            "DIFF": _git_diff(workspace_root),
            "LATEST_ARTIFACT": _latest_artifact_content(workspace, phase),
            "ISSUES": _resolve_issues_content(workspace),
        }
        return render_template(
            template, _merged_variables(variables, session_caps), context.partials
        )
    if phase in {"development_commit", "review_commit"}:
        return prompt_commit_message(
            _git_diff(workspace_root),
            template_registry=context.registry,
        )
    msg = f"Unsupported phase '{phase}' for prompt materialization"
    raise ValueError(msg)


def _merged_variables(base: dict[str, str], session_caps: SessionCapabilities) -> dict[str, str]:
    return {
        **base,
        **capability_template_variables(session_caps.capabilities, session_caps.policy_flags),
    }


def _read_optional(workspace: Workspace, path: str) -> str | None:
    if not workspace.exists(path):
        return None
    return workspace.read(path)


def _resolve_plan_content(workspace: Workspace) -> str | None:
    for path in (".agent/PLAN.md", ".agent/artifacts/plan.json"):
        content = _read_optional(workspace, path)
        if content:
            return content
    return None


def _resolve_issues_content(workspace: Workspace) -> str:
    for path in (".agent/ISSUES.md", ".agent/artifacts/issues.json"):
        content = _read_optional(workspace, path)
        if content:
            return content
    return "(no review issues available)"


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
        return cast("str", Repo(workspace_root).git.diff("HEAD"))
    except Exception:
        return "(no diff available)"
