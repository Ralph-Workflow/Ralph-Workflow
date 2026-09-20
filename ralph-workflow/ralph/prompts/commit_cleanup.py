"""Commit cleanup prompt rendering."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.tools.names import SUBMIT_MD_ARTIFACT_TOOL
from ralph.phases._commit_cleanup_catalog import render_delete_decision_rules_markdown
from ralph.phases.required_artifacts import retry_hint_path
from ralph.prompts._commit_diff import commit_cleanup_diff
from ralph.prompts.commit import _format_submit_artifact_tool_instructions
from ralph.prompts.materialize_support import (
    merged_variables as _merged_variables,
)
from ralph.prompts.materialize_support import (
    product_criteria_variables as _product_criteria_variables,
)
from ralph.prompts.payload_refs import (
    build_prompt_payload_variables,
    write_payload_to_directory,
)
from ralph.prompts.template_engine import render_template
from ralph.recovery.retry_prompt import build_validation_retry_footer

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.policy.models import PipelinePolicy
    from ralph.prompts.template_context import TemplateContext
    from ralph.prompts.types import SessionCapabilities


def render_commit_cleanup_prompt(
    phase: str,
    workspace_root: Path,
    worker_namespace: Path | None,
    prompt_content: str | None,
    product_criteria_path: str,
    template_name: str,
    tmpl_ctx: TemplateContext,
    session_caps: SessionCapabilities,
    pipeline_policy: PipelinePolicy | None = None,
) -> str:
    """Render the commit cleanup prompt using the commit_cleanup.jinja template."""
    diff = commit_cleanup_diff(workspace_root)
    output_dir = workspace_root / ".agent/tmp/prompt_payloads"
    if worker_namespace:
        output_dir = worker_namespace / "tmp/prompt_payloads"
    last_retry_error = _read_and_clear_retry_hint(
        workspace_root,
        phase,
        worker_namespace,
        pipeline_policy,
    )
    bv = {
        "SUBMIT_MD_ARTIFACT_TOOL_INSTRUCTIONS": _format_submit_artifact_tool_instructions(
            SUBMIT_MD_ARTIFACT_TOOL.prompt_aliases(
                tool_name_prefix=session_caps.tool_name_prefix,
            )
        ),
        "LAST_RETRY_ERROR": last_retry_error,
        "DELETE_DECISION_RULES": render_delete_decision_rules_markdown(),
        **build_prompt_payload_variables(
            {"DIFF": diff},
            prompt_name_prefix=phase,
            write_payload=lambda relative_path, content: write_payload_to_directory(
                output_dir,
                relative_path,
                content,
            ),
        ),
        **_product_criteria_variables(prompt_content, product_criteria_path),
    }
    rendered = render_template(
        tmpl_ctx.registry.get_template(template_name),
        _merged_variables(bv, session_caps),
        tmpl_ctx.partials,
    )
    if not last_retry_error.startswith("VALIDATION FAILURE"):
        return rendered
    return f"{rendered.rstrip()}\n\n{build_validation_retry_footer()}\n"


def _read_and_clear_retry_hint(
    workspace_root: Path,
    phase: str,
    worker_namespace: Path | None,
    pipeline_policy: PipelinePolicy | None,
) -> str:
    """Read the phase retry hint without consuming active validation context."""
    phase_def = pipeline_policy.phases.get(phase) if pipeline_policy is not None else None
    drain = phase_def.drain if phase_def is not None else phase
    hint_file = (
        worker_namespace / "tmp" / f"last_retry_error_{drain}.txt"
        if worker_namespace is not None
        else workspace_root / retry_hint_path(phase, pipeline_policy=pipeline_policy)
    )
    legacy_hint_file = (
        worker_namespace / "tmp" / f"last_retry_error_{phase}.txt"
        if worker_namespace is not None
        else workspace_root / retry_hint_path(phase)
    )
    source_file = hint_file if hint_file.is_file() else legacy_hint_file
    if not source_file.is_file():
        return ""
    try:
        return source_file.read_text(encoding="utf-8")
    except OSError:
        return ""
