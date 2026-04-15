"""Commit prompt generation utilities."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..template_engine import render_template
from ..template_registry import (
    TemplateNotFoundError,
    TemplateRegistry,
    load_partial_templates,
    packaged_template_root,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

DEFAULT_COMMIT_TEMPLATE_NAME = "commit_message"
DEFAULT_SUBMIT_ARTIFACT_TOOL_NAME = "ralph_submit_artifact"


def prompt_commit_message(
    diff: str,
    *,
    template_registry: TemplateRegistry | None = None,
    partials: Mapping[str, str] | None = None,
    submit_artifact_tool_names: Sequence[str] = (DEFAULT_SUBMIT_ARTIFACT_TOOL_NAME,),
) -> str:
    """Return the commit message prompt for the provided diff."""

    diff_content = diff.strip()
    if not diff_content:
        raise ValueError("empty diff provided; cannot build commit prompt")

    template = _select_template(template_registry)
    return render_template(
        template,
        {
            "DIFF": diff_content,
            "SUBMIT_ARTIFACT_TOOL_INSTRUCTIONS": _format_submit_artifact_tool_instructions(
                submit_artifact_tool_names
            ),
        },
        dict(partials or _default_commit_partials()),
    )


def prompt_commit_message_for_opencode(diff: str, *, submit_artifact_tool_name: str) -> str:
    diff_content = diff.strip()
    if not diff_content:
        raise ValueError("empty diff provided; cannot build commit prompt")

    template = (packaged_template_root() / "commit_simplified.jinja").read_text(encoding="utf-8")
    return render_template(
        template,
        {
            "DIFF": diff_content,
            "SUBMIT_ARTIFACT_TOOL_NAME": submit_artifact_tool_name,
        },
        _default_commit_partials(),
    )


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
