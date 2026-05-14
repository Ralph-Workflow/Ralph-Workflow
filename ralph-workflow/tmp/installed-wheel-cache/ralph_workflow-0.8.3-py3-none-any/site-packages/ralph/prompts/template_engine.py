"""Minimal rendering engine for RFC-009 prompt templates."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from jinja2 import (
    DictLoader,
    Environment,
    StrictUndefined,
    TemplateError,
    TemplateNotFound,
    Undefined,
)

from ralph.prompts.template_parsing import (
    ConditionalNode,
    LoopNode,
    PartialNode,
    TemplateNode,
    TextNode,
    VariableNode,
    eval_conditional,
    split_loop_items,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


class TemplateRenderingError(Exception):
    """Raised when a template cannot be rendered."""


def _raise_template_error(message: str) -> str:
    """Global callable for Jinja2 macros to raise a rendering error explicitly."""
    raise TemplateRenderingError(message)


def render_template(
    template_text: str,
    variables: Mapping[str, str],
    partials: Mapping[str, str],
) -> str:
    """Render the provided template text with partials and variables."""

    try:
        templates = {"__main__.j2": template_text}
        templates.update({f"{name}.j2": content for name, content in partials.items()})

        strict_undefined = cast("type[Undefined]", StrictUndefined)

        environment = Environment(
            loader=DictLoader(templates),
            autoescape=False,
            undefined=strict_undefined,
            keep_trailing_newline=True,
        )
        filters = cast("dict[str, object]", environment.filters)
        filters["split_items"] = split_loop_items
        globals_dict = environment.globals
        globals_dict["raise_error"] = _raise_template_error

        template = environment.get_template("__main__.j2")
        return template.render(**dict(variables))
    except TemplateNotFound as exc:
        raise TemplateRenderingError(str(exc)) from exc
    except TemplateRenderingError:
        raise
    except TemplateError as exc:
        raise TemplateRenderingError(str(exc)) from exc
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
