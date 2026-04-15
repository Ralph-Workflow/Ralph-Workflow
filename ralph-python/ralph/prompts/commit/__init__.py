"""Commit prompt generation utilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..payload_refs import build_prompt_payload_variables, write_payload_to_directory
from ..template_engine import render_template
from ..template_registry import (
    TemplateNotFoundError,
    TemplateRegistry,
    load_partial_templates,
    packaged_template_root,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path


@dataclass(frozen=True)
class CommitPromptPayloadConfig:
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
    variables = {
        "SUBMIT_ARTIFACT_TOOL_INSTRUCTIONS": _format_submit_artifact_tool_instructions(
            submit_artifact_tool_names
        ),
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
    diff_content = diff.strip()
    if not diff_content:
        raise ValueError("empty diff provided; cannot build commit prompt")

    template = (packaged_template_root() / "commit_simplified.jinja").read_text(encoding="utf-8")
    variables = {
        "SUBMIT_ARTIFACT_TOOL_NAME": submit_artifact_tool_name,
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
    return (packaged_template_root() / "commit_message.jinja").read_text(encoding="utf-8")


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
