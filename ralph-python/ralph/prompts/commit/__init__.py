"""Commit prompt generation utilities."""

from __future__ import annotations

from ..template_registry import TemplateNotFound, TemplateRegistry

DEFAULT_COMMIT_TEMPLATE_NAME = "commit_message"

DEFAULT_COMMIT_MESSAGE_TEMPLATE = (
    "You are a commit message generation expert. Analyze the git diff below and "
    "produce a conventional commit message that clearly states what changed and why.\n\n"
    "DIFF:\n"
    "{DIFF}\n\n"
    "---\n"
    "## COMMIT MESSAGE FORMAT\n"
    "<type>[optional scope]: subject\n\n"
    "Use the types feat/fix/docs/refactor/test/style/perf/build/ci/chore. If the change\n"
    "does not fit, default to chore and describe the intent. Keep the subject <= 50 chars\n"
    "and write it in lowercase imperative mood.\n\n"
    "Provide a body only if explaining the motivation or why the change was required.\n\n"
    "Output format: <ralph-commit><ralph-subject>type: description</ralph-subject></ralph-commit>"
)


def prompt_commit_message(
    diff: str, *, template_registry: TemplateRegistry | None = None
) -> str:
    """Return the commit message prompt for the provided diff."""

    diff_content = diff.strip()
    if not diff_content:
        raise ValueError("empty diff provided; cannot build commit prompt")

    template = _select_template(template_registry)
    return template.replace("{DIFF}", diff_content)


def _select_template(template_registry: TemplateRegistry | None) -> str:
    """Choose the commit_message template, falling back to the default."""

    if template_registry is not None:
        try:
            return template_registry.get_template(DEFAULT_COMMIT_TEMPLATE_NAME)
        except TemplateNotFound:
            pass
    return DEFAULT_COMMIT_MESSAGE_TEMPLATE
