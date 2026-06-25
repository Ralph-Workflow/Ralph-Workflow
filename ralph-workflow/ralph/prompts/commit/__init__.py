"""Commit prompt generation utilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.mcp.tools.names import DECLARE_COMPLETE_TOOL, WRITE_FILE_TOOL

from ..payload_refs import build_prompt_payload_variables, write_payload_to_directory
from ..template_engine import render_template
from ..template_registry import (
    TemplateNotFoundError,
    TemplateRegistry,
    _packaged_template_cache,
    load_partial_templates,
    packaged_template_root,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path


@dataclass(frozen=True)
class CommitPromptPayloadConfig:
    """Configuration for where commit prompt payload files are written."""

    output_dir: Path | None = None
    name_prefix: str = "commit_message"


DEFAULT_COMMIT_TEMPLATE_NAME = "commit_message"
DEFAULT_SUBMIT_ARTIFACT_TOOL_NAME = "ralph_submit_artifact"


def prompt_commit_message(
    diff: str,
    *,
    template_registry: TemplateRegistry | None = None,
    partials: Mapping[str, str] | None = None,
    submit_artifact_tool_names: Sequence[str] = (DEFAULT_SUBMIT_ARTIFACT_TOOL_NAME,),
    payload_config: CommitPromptPayloadConfig | None = None,
) -> str:
    """Return the commit message prompt for the provided diff."""

    diff_content = diff.strip()
    if not diff_content:
        raise ValueError("empty diff provided; cannot build commit prompt")

    template = _select_template(template_registry)
    submit_reference = _format_submit_artifact_tool_reference(submit_artifact_tool_names)
    variables = {
        "SUBMIT_ARTIFACT_TOOL_INSTRUCTIONS": _format_submit_artifact_tool_instructions(
            submit_artifact_tool_names
        ),
        "SUBMIT_ARTIFACT_TOOL_REFERENCE": submit_reference,
        "DECLARE_COMPLETE_TOOL_REFERENCE": DECLARE_COMPLETE_TOOL,
        "WRITE_FILE_TOOL_REFERENCE": f"`{WRITE_FILE_TOOL}`",
    }
    variables.update(
        _commit_payload_variables(
            diff_content,
            payload_config=payload_config,
        )
    )
    return render_template(
        template,
        variables,
        dict(partials or _default_commit_partials()),
    ).lstrip()


def prompt_commit_message_for_opencode(
    diff: str,
    *,
    submit_artifact_tool_name: str,
    payload_config: CommitPromptPayloadConfig | None = None,
) -> str:
    """Return a simplified commit message prompt for OpenCode's single-tool interface."""
    diff_content = diff.strip()
    if not diff_content:
        raise ValueError("empty diff provided; cannot build commit prompt")

    template = _packaged_template_cache.get(
        "commit_simplified.jinja", root=packaged_template_root()
    )
    variables = {
        "SUBMIT_ARTIFACT_TOOL_NAME": submit_artifact_tool_name,
        "SUBMIT_ARTIFACT_TOOL_REFERENCE": f"`{submit_artifact_tool_name}`",
        "DECLARE_COMPLETE_TOOL_REFERENCE": DECLARE_COMPLETE_TOOL,
        "WRITE_FILE_TOOL_REFERENCE": f"`{WRITE_FILE_TOOL}`",
    }
    variables.update(
        _commit_payload_variables(
            diff_content,
            payload_config=payload_config,
        )
    )
    return render_template(
        template,
        variables,
        _default_commit_partials(),
    ).lstrip()


def _select_template(template_registry: TemplateRegistry | None) -> str:
    """Choose the commit_message template, falling back to the default."""

    if template_registry is not None:
        try:
            return template_registry.get_template(DEFAULT_COMMIT_TEMPLATE_NAME)
        except TemplateNotFoundError:
            pass
    return _packaged_template_cache.get(
        "commit_message.jinja", root=packaged_template_root()
    )


def _format_submit_artifact_tool_instructions(tool_names: Sequence[str]) -> str:
    unique_list: list[str] = []
    for name in tool_names:
        if name and name not in unique_list:
            unique_list.append(name)
    unique_names = tuple(unique_list)
    if not unique_names:
        unique_names = (DEFAULT_SUBMIT_ARTIFACT_TOOL_NAME,)
    if len(unique_names) == 1:
        return f"the tool named `{unique_names[0]}`"

    formatted = ", ".join(f"`{name}`" for name in unique_names[:-1])
    return f"one of the following tool names: {formatted}, or `{unique_names[-1]}`"


def _format_submit_artifact_tool_reference(tool_names: Sequence[str]) -> str:
    """Render the canonical submit-tool reference for the single-shot macro.

    Returns a single backtick-quoted tool name so the rendered macro
    surfaces one alias (the canonical MCP tool name) the agent can
    call. When multiple aliases are registered for the active
    transport, the first non-empty one wins; the macro's worked
    example uses this same name to keep the call site and the example
    in lock-step.
    """
    unique_list: list[str] = []
    for name in tool_names:
        if name and name not in unique_list:
            unique_list.append(name)
    if not unique_list:
        return f"`{DEFAULT_SUBMIT_ARTIFACT_TOOL_NAME}`"
    return f"`{unique_list[0]}`"


def _default_commit_partials() -> dict[str, str]:
    root = packaged_template_root()
    return load_partial_templates((root, root / "shared"))


def _commit_payload_variables(
    diff_content: str,
    *,
    payload_config: CommitPromptPayloadConfig | None,
) -> dict[str, str]:
    if payload_config is None or payload_config.output_dir is None:
        return {"DIFF": diff_content, "DIFF_PATH": ""}

    output_dir = payload_config.output_dir

    return build_prompt_payload_variables(
        {"DIFF": diff_content},
        prompt_name_prefix=payload_config.name_prefix,
        write_payload=lambda relative_path, content: write_payload_to_directory(
            output_dir,
            relative_path,
            content,
        ),
    )
