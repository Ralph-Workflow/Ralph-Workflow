"""LoopNode — a {% for x in iterable %} loop node in a parsed template."""

from __future__ import annotations

from dataclasses import dataclass

from ralph.prompts._template_node import TemplateNode


@dataclass
class LoopNode(TemplateNode):
    """A `{% for x in iterable %}` loop with a body."""

    variable: str
    iterable: str
    body: list[TemplateNode]


__all__ = ["LoopNode"]
