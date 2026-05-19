"""_AstFrame — internal AST stack frame for template parsing."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, TypedDict

if TYPE_CHECKING:
    from ralph.prompts._conditional_node import ConditionalNode
    from ralph.prompts._loop_node import LoopNode
    from ralph.prompts._template_node import TemplateNode


class _AstFrame(TypedDict):
    type: Literal["root", "loop", "if_truthy", "if_falsy"]
    nodes: list[TemplateNode]
    node: LoopNode | ConditionalNode | None


__all__ = ["_AstFrame"]
