"""TextNode — a literal text segment in a parsed template."""

from __future__ import annotations

from dataclasses import dataclass

from ralph.prompts._template_node import TemplateNode


@dataclass
class TextNode(TemplateNode):
    """A literal text segment in a parsed template."""

    text: str


__all__ = ["TextNode"]
