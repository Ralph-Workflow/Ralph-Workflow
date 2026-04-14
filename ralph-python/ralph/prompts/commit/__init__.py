"""Commit prompt generation utilities."""

from __future__ import annotations

from ..template_engine import render_template
from ..template_registry import TemplateNotFoundError, TemplateRegistry

DEFAULT_COMMIT_TEMPLATE_NAME = "commit_message"

DEFAULT_COMMIT_MESSAGE_TEMPLATE = (
    "You are a commit message generation expert. Analyze the git diff below and "
    "produce a conventional commit message that clearly states what changed and why.\n\n"
    "DIFF:\n"
    "{{ DIFF }}\n\n"
    "---\n"
    "## COMMIT MESSAGE FORMAT\n"
    "<type>[optional scope]: subject\n\n"
    "Use the types feat/fix/docs/refactor/test/style/perf/build/ci/chore. If the change\n"
    "does not fit, default to chore and describe the intent. Keep the subject <= 50 chars\n"
    "and write it in lowercase imperative mood.\n\n"
    "Output only plain-text commit subject (single line, no XML, no markdown, no quotes).\n"
    "If no commit should be created, output plain text: SKIP: <reason>."
)


def prompt_commit_message(diff: str, *, template_registry: TemplateRegistry | None = None) -> str:
    """Return the commit message prompt for the provided diff."""

    diff_content = diff.strip()
    if not diff_content:
        raise ValueError("empty diff provided; cannot build commit prompt")

    template = _select_template(template_registry)
    return render_template(template, {"DIFF": diff_content}, {})


def _select_template(template_registry: TemplateRegistry | None) -> str:
    """Choose the commit_message template, falling back to the default."""

    if template_registry is not None:
        try:
            return template_registry.get_template(DEFAULT_COMMIT_TEMPLATE_NAME)
        except TemplateNotFoundError:
            pass
    return DEFAULT_COMMIT_MESSAGE_TEMPLATE
