"""PartialNode — a {{> partial_name }} include directive."""

from __future__ import annotations

from dataclasses import dataclass

from ralph.prompts._template_node import TemplateNode


@dataclass
class PartialNode(TemplateNode):
    """A `{{> partial_name }}` include directive."""

    name: str


__all__ = ["PartialNode"]
