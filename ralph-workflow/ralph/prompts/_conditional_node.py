"""ConditionalNode — an {% if condition %} block in a parsed template."""

from __future__ import annotations

from dataclasses import dataclass

from ralph.prompts._template_node import TemplateNode


@dataclass
class ConditionalNode(TemplateNode):
    """An `{% if condition %}` block with truthy and falsy branches."""

    condition: str
    truthy: list[TemplateNode]
    falsy: list[TemplateNode]


__all__ = ["ConditionalNode"]
