"""Structured product_spec artifact validation helpers and PROMPT.md rendering."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

from pydantic import ConfigDict, Field, ValidationError, model_validator

from ralph.mcp.artifacts._product_spec_errors import ProductSpecValidationError
from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec
from ralph.pydantic_validation_errors import format_validation_error_messages
from ralph.workspace import FsWorkspace

if TYPE_CHECKING:
    from pathlib import Path

from ralph.pydantic_compat import RalphBaseModel

PRODUCT_SPEC_ARTIFACT_TYPE = "product_spec"
_PRODUCT_SPEC_MARKDOWN_PATH = ".agent/artifacts/product_spec.md"


class ProductSpec(RalphBaseModel):
    """Validated schema for a product_spec artifact."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1)
    scope: str = Field(..., min_length=1)
    goals: list[str] = Field(..., min_length=1)
    users: list[str] = Field(..., min_length=1)
    constraints: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(..., min_length=1)
    product_behavior: list[str] = Field(default_factory=list)
    ux_ui_requirements: list[str] = Field(default_factory=list)
    scope_boundaries: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_required_lists(self) -> ProductSpec:
        if not self.goals:
            raise ValueError("goals must be a non-empty list")
        if not self.users:
            raise ValueError("users must be a non-empty list")
        if not self.success_criteria:
            raise ValueError("success_criteria must be a non-empty list")
        return self


def normalize_product_spec_content(content: dict[str, object]) -> dict[str, object]:
    """Validate and normalize a raw product_spec content dict."""
    try:
        validated = ProductSpec.model_validate(content)
        return validated.model_dump(mode="python", exclude_none=True)
    except ValidationError as exc:
        msgs = format_validation_error_messages(exc)
        raise ProductSpecValidationError(
            msgs[0] if len(msgs) == 1 else "\n".join(msgs) if msgs else str(exc)
        ) from exc


def _bullet_lines(items: list[str]) -> list[str]:
    """Convert a list of strings to bullet lines."""
    return [f"- {item}" for item in items]


def render_product_spec_as_prompt(spec: dict[str, object]) -> str:
    """Render a product_spec dict as a PROMPT.md-formatted string.

    The output follows the canonical PROMPT.md structure:
    - # Goal: title (bold) + scope paragraph
    - ## Context: goals, users, constraints, product_behavior, ux_ui_requirements
    - ## Acceptance criteria: success_criteria bullets
    - ## Notes: scope_boundaries + open_questions (only if non-empty)
    """
    lines: list[str] = []

    # # Goal — H1 heading
    lines.append("# Goal")
    title = str(spec.get("title", ""))
    scope = str(spec.get("scope", ""))
    lines.append(f"**{title}**")
    if scope:
        lines.append(scope)
    lines.append("")

    # ## Context — H2 heading
    lines.append("## Context")
    _emit_context_group(lines, "**Goals:**", _as_string_list(spec.get("goals", [])))
    _emit_context_group(lines, "**Users:**", _as_string_list(spec.get("users", [])))
    _emit_context_group(lines, "**Constraints:**", _as_string_list(spec.get("constraints", [])))
    _emit_context_group(
        lines, "**Product behavior:**", _as_string_list(spec.get("product_behavior", []))
    )
    _emit_context_group(
        lines, "**UX/UI requirements:**", _as_string_list(spec.get("ux_ui_requirements", []))
    )
    lines.append("")

    # ## Acceptance criteria — H2 heading
    lines.append("## Acceptance criteria")
    lines.extend(_bullet_lines(_as_string_list(spec.get("success_criteria", []))))
    lines.append("")

    # ## Notes — H2 heading (only if scope_boundaries or open_questions are non-empty)
    scope_boundaries = _as_string_list(spec.get("scope_boundaries", []))
    open_questions = _as_string_list(spec.get("open_questions", []))
    if scope_boundaries or open_questions:
        lines.append("## Notes")
        lines.extend(_bullet_lines(scope_boundaries))
        lines.extend(_bullet_lines(open_questions))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _as_string_list(value: object) -> list[str]:
    """Convert a value to a list of strings."""
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _emit_context_group(lines: list[str], label: str, items: list[str]) -> None:
    """Append a labeled bullet group to lines if items is non-empty."""
    if not items:
        return
    lines.append(label)
    lines.extend(_bullet_lines(items))
    lines.append("")


def read_product_spec_artifact(repo_root: Path) -> dict[str, object] | None:
    """Read and validate the canonical Markdown product specification."""
    workspace = FsWorkspace(repo_root)
    try:
        markdown = workspace.read(_PRODUCT_SPEC_MARKDOWN_PATH)
    except (FileNotFoundError, OSError):
        return None

    import_module("ralph.mcp.artifacts.markdown.specs")
    content, diagnostics = parse_and_validate(markdown, get_spec(PRODUCT_SPEC_ARTIFACT_TYPE))
    if any(diagnostic.severity == "error" for diagnostic in diagnostics):
        return None
    return content


__all__ = [
    "PRODUCT_SPEC_ARTIFACT_TYPE",
    "ProductSpec",
    "ProductSpecValidationError",
    "normalize_product_spec_content",
    "read_product_spec_artifact",
    "render_product_spec_as_prompt",
]
