"""Utility functions for rendering reviewer prompts."""

from __future__ import annotations

from ralph.mcp.tools.names import DECLARE_COMPLETE_TOOL, SUBMIT_ARTIFACT_TOOL
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.template_engine import TemplateRenderingError, render_template
from ralph.prompts.template_registry import (
    TemplateNotFoundError,
    TemplateRegistry,
    packaged_template_root,
)

_SUBMIT_ARTIFACT_TOOL_REFERENCE = f"`{SUBMIT_ARTIFACT_TOOL}`"
_DECLARE_COMPLETE_TOOL_REFERENCE = DECLARE_COMPLETE_TOOL

__all__ = [
    "CHANGES_PLACEHOLDER",
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

    template = _load_packaged_review_template()
    partials = TemplateContext.default().partials
    if template_registry is not None:
        try:
            template = template_registry.get_template(template_name)
        except TemplateNotFoundError:
            template = _load_packaged_review_template()

    try:
        return render_template(
            template,
            {
                "PLAN": plan_value,
                "PLAN_PATH": "",
                "CHANGES": changes_value,
                "CHANGES_PATH": "",
                "FIX_RESULT": "(no fix result available)",
                "FIX_RESULT_PATH": "",
                "LAST_RETRY_ERROR": "",
                "SUBMIT_ARTIFACT_TOOL_REFERENCE": _SUBMIT_ARTIFACT_TOOL_REFERENCE,
                "SUBMIT_ARTIFACT_TOOL_INSTRUCTIONS": f"the tool named {SUBMIT_ARTIFACT_TOOL}",
                "DECLARE_COMPLETE_TOOL_REFERENCE": _DECLARE_COMPLETE_TOOL_REFERENCE,
            },
            partials,
        )
    except TemplateRenderingError as err:
        raise ValueError("Unable to render review prompt; invalid Jinja template") from err


prompt_review = render_review_prompt
"""Backward-compatible alias matching the original reviewer prompt name."""


def _load_packaged_review_template() -> str:
    return (packaged_template_root() / "review.jinja").read_text(encoding="utf-8")
