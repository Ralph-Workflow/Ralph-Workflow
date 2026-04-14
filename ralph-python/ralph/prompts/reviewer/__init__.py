"""Utility functions for rendering reviewer prompts."""

from __future__ import annotations

from ralph.prompts.template_engine import TemplateRenderingError, render_template
from ralph.prompts.template_registry import TemplateNotFoundError, TemplateRegistry

from .templates import DEFAULT_REVIEW_TEMPLATE

__all__ = [
    "CHANGES_PLACEHOLDER",
    "DEFAULT_REVIEW_TEMPLATE",
    "PLAN_PLACEHOLDER",
    "prompt_review",
    "render_review_prompt",
]

PLAN_PLACEHOLDER = "(no plan available)"
"""Fallback text when reviewer plan content is empty."""

CHANGES_PLACEHOLDER = "(no diff available)"
"""Fallback text when reviewer change description is empty."""


def _normalize_content(value: str, placeholder: str) -> str:
    stripped = value.strip()
    return stripped if stripped else placeholder


def render_review_prompt(
    plan: str,
    changes: str,
    *,
    template_registry: TemplateRegistry | None = None,
    template_name: str = "review",
) -> str:
    """Render the reviewer prompt using the requested template.

    If a template registry is provided, the named template is used. Missing
    placeholders or templates fall back to the built-in default template.
    """

    plan_value = _normalize_content(plan, PLAN_PLACEHOLDER)
    changes_value = _normalize_content(changes, CHANGES_PLACEHOLDER)

    template = DEFAULT_REVIEW_TEMPLATE
    if template_registry is not None:
        try:
            template = template_registry.get_template(template_name)
        except TemplateNotFoundError:
            template = DEFAULT_REVIEW_TEMPLATE

    try:
        return render_template(template, {"PLAN": plan_value, "CHANGES": changes_value}, {})
    except TemplateRenderingError as err:
        raise ValueError("Unable to render review prompt; invalid Jinja template") from err


prompt_review = render_review_prompt
"""Backward-compatible alias matching the original reviewer prompt name."""
