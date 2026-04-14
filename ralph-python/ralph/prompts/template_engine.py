"""Minimal rendering engine for RFC-009 prompt templates."""

from __future__ import annotations

import re
from typing import Mapping

from jinja2 import DictLoader, Environment, StrictUndefined, TemplateError

PARTIAL_INCLUDE_PATTERN = re.compile(r"\{\{\s*>\s*([^}\s]+)\s*\}\}")
DEFAULT_FILTER_PATTERN = re.compile(r"\{\{\s*([A-Z0-9_]+)\s*\|\s*default=\"([^\"]*)\"\s*\}\}")


class TemplateRenderingError(Exception):
    """Raised when a template cannot be rendered."""


def render_template(
    template_text: str,
    variables: Mapping[str, str],
    partials: Mapping[str, str],
) -> str:
    """Render the provided template text with partials and variables."""

    processed = _apply_partial_includes(template_text)
    processed = _rewrite_default_filters(processed)
    loader = DictLoader({f"{name}.txt": content for name, content in partials.items()})
    env = Environment(loader=loader, undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True)
    try:
        template = env.from_string(processed)
        return template.render(**variables)
    except TemplateError as exc:
        raise TemplateRenderingError(str(exc)) from exc


def _apply_partial_includes(text: str) -> str:
    return PARTIAL_INCLUDE_PATTERN.sub(lambda match: f"{{% include '{match.group(1)}.txt' %}}", text)


def _rewrite_default_filters(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        variable, default_value = match.group(1), match.group(2)
        escaped = default_value.replace('"', '\\"')
        return f"{{{{ {variable}|default(\"{escaped}\") }}}}"

    return DEFAULT_FILTER_PATTERN.sub(replace, text)
