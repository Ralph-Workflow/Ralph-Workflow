"""Minimal rendering engine for RFC-009 prompt templates."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.prompts.template_parsing import (
    ConditionalNode,
    LoopNode,
    PartialNode,
    TemplateNode,
    TextNode,
    VariableNode,
    eval_conditional,
    parse_template,
    split_loop_items,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


class TemplateRenderingError(Exception):
    """Raised when a template cannot be rendered."""


def render_template(
    template_text: str,
    variables: Mapping[str, str],
    partials: Mapping[str, str],
) -> str:
    """Render the provided template text with partials and variables."""

    try:
        return _render_nodes(parse_template(template_text), variables, partials)
    except TemplateRenderingError:
        raise
    except Exception as exc:  # pragma: no cover - defensive wrapper
        raise TemplateRenderingError(str(exc)) from exc


def _render_nodes(
    nodes: list[TemplateNode],
    variables: Mapping[str, str],
    partials: Mapping[str, str],
) -> str:
    rendered_parts: list[str] = []

    for node in nodes:
        if isinstance(node, TextNode):
            rendered_parts.append(node.text)
            continue

        if isinstance(node, VariableNode):
            rendered_parts.append(_render_variable(node, variables))
            continue

        if isinstance(node, PartialNode):
            try:
                partial_text = partials[node.name]
            except KeyError as exc:
                raise TemplateRenderingError(f"{node.name}.txt") from exc
            rendered_parts.append(render_template(partial_text, variables, partials))
            continue

        if isinstance(node, LoopNode):
            iterable_value = variables.get(node.iterable, "")
            for item in split_loop_items(iterable_value):
                loop_variables = dict(variables)
                loop_variables[node.variable] = item
                rendered_parts.append(_render_nodes(node.body, loop_variables, partials))
            continue

        if isinstance(node, ConditionalNode):
            branch = node.truthy if eval_conditional(node.condition, variables) else node.falsy
            rendered_parts.append(_render_nodes(branch, variables, partials))
            continue

        raise TemplateRenderingError(f"unsupported template node: {type(node).__name__}")

    return "".join(rendered_parts)


def _render_variable(node: VariableNode, variables: Mapping[str, str]) -> str:
    if node.name in variables:
        return variables[node.name]
    if node.default is not None:
        return node.default
    raise TemplateRenderingError(f"'{node.name}' is undefined")
