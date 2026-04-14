"""Commit prompt generation utilities."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..template_engine import render_template
from ..template_registry import TemplateNotFoundError, TemplateRegistry

if TYPE_CHECKING:
    from collections.abc import Sequence

DEFAULT_COMMIT_TEMPLATE_NAME = "commit_message"
DEFAULT_SUBMIT_ARTIFACT_TOOL_NAME = "ralph_submit_artifact"

DEFAULT_COMMIT_MESSAGE_TEMPLATE = (
    "Task: produce a single-line conventional commit subject for the diff below.\n"
    "Read the diff silently. Do not write an analysis.\n\n"
    "---\n"
    "## COMMIT MESSAGE FORMAT\n"
    "<type>[optional scope]: subject\n\n"
    "Use the types feat/fix/docs/refactor/test/style/perf/build/ci/chore. If the change\n"
    "does not fit, default to chore and describe the intent. Keep the subject <= 50 chars\n"
    "and write it in lowercase imperative mood. Generate a single-line conventional\n"
    "commit subject only.\n\n"
    "STRICT OUTPUT RULES:\n"
    "Do not explain the diff. Do not output bullets, markdown, sections, rationale,\n"
    "or file lists.\n"
    "Do not produce a commit body. Do not wrap the subject in code fences.\n"
    "Your job is to decide on the single-line subject and submit it by calling the tool directly.\n"
    "Call the tool before emitting any final text. If you skip the tool call, the\n"
    "task has failed.\n\n"
    "MCP REQUIREMENT:\n"
    "You MUST submit the final result by calling {{ SUBMIT_ARTIFACT_TOOL_INSTRUCTIONS }} with\n"
    'artifact_type="commit_message" and JSON content {"message": "<subject>"}.\n'
    'If no commit should be created, submit {"message": "SKIP: <reason>"}.\n\n'
    "REQUIRED PROCEDURE:\n"
    "1. Read the diff and decide on the best single-line conventional commit subject.\n"
    '2. Immediately call the tool with JSON exactly like {"message": "feat(scope): subject"}.\n'
    "3. After the MCP call succeeds, optionally echo that same single line once\n"
    "   and nothing else.\n\n"
    "DIFF:\n"
    "{{ DIFF }}\n\n"
    "After the MCP submission, you may optionally echo the same single-line subject "
    "as plain text,\n"
    "but the MCP artifact is the authoritative output. Do not emit markdown, bullets, "
    "or explanations\n"
    "as the final answer."
)


def prompt_commit_message(
    diff: str,
    *,
    template_registry: TemplateRegistry | None = None,
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
        {},
    )


def prompt_commit_message_for_opencode(diff: str, *, submit_artifact_tool_name: str) -> str:
    diff_content = diff.strip()
    if not diff_content:
        raise ValueError("empty diff provided; cannot build commit prompt")

    return (
        "Do not analyze anything. Read the diff silently. "
        f"The only tool you may call is `{submit_artifact_tool_name}`. "
        "Do not call bash or any other tool. "
        f'Immediately call `{submit_artifact_tool_name}` with artifact_type="commit_message" '
        'and content {"message":"<subject>"}. '
        'If no commit should be created, use content {"message":"SKIP: <reason>"}. '
        "After the tool call succeeds, output only the single line <subject> and nothing else.\n\n"
        "DIFF:\n"
        f"{diff_content}\n"
    )


def _select_template(template_registry: TemplateRegistry | None) -> str:
    """Choose the commit_message template, falling back to the default."""

    if template_registry is not None:
        try:
            return template_registry.get_template(DEFAULT_COMMIT_TEMPLATE_NAME)
        except TemplateNotFoundError:
            pass
    return DEFAULT_COMMIT_MESSAGE_TEMPLATE


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
