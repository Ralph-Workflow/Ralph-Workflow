"""VariableNode — a {{ VARIABLE }} substitution in a parsed template."""

from __future__ import annotations

from dataclasses import dataclass

from ralph.prompts._template_node import TemplateNode


@dataclass
class VariableNode(TemplateNode):
    """A `{{ VARIABLE }}` substitution with an optional default value."""

    name: str
    default: str | None
    placeholder: str


__all__ = ["VariableNode"]
