"""Commit cleanup prompt rendering."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.tools.names import SUBMIT_ARTIFACT_TOOL
from ralph.prompts.commit import _format_submit_artifact_tool_instructions
from ralph.prompts.payload_refs import (
    build_prompt_payload_variables,
    write_payload_to_directory,
)
from ralph.prompts.template_engine import render_template

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.prompts.template_context import TemplateContext
    from ralph.prompts.types import SessionCapabilities


def render_commit_cleanup_prompt(
    phase: str,
    workspace_root: Path,
    worker_namespace: Path | None,
    prompt_content: str | None,
    current_prompt_path: str,
    template_name: str,
    tmpl_ctx: TemplateContext,
    session_caps: SessionCapabilities,
) -> str:
    """Render the commit cleanup prompt using the commit_cleanup.jinja template."""
    from ralph.prompts.materialize import (
        _merged_variables,
        commit_cleanup_diff,
    )
    from ralph.prompts.materialize_support import (
        current_prompt_variables as _current_prompt_variables,
    )

    diff = commit_cleanup_diff(workspace_root)
    output_dir = workspace_root / ".agent/tmp/prompt_payloads"
    if worker_namespace:
        output_dir = worker_namespace / "tmp/prompt_payloads"
    bv = {
        "SUBMIT_ARTIFACT_TOOL_INSTRUCTIONS": _format_submit_artifact_tool_instructions(
            SUBMIT_ARTIFACT_TOOL.prompt_aliases(
                tool_name_prefix=session_caps.tool_name_prefix,
            )
        ),
        **build_prompt_payload_variables(
            {"DIFF": diff},
            prompt_name_prefix=phase,
            write_payload=lambda relative_path, content: write_payload_to_directory(
                output_dir,
                relative_path,
                content,
            ),
        ),
        **_current_prompt_variables(prompt_content, current_prompt_path),
    }
    return render_template(
        tmpl_ctx.registry.get_template(template_name),
        _merged_variables(bv, session_caps),
        tmpl_ctx.partials,
    )
