
from __future__ import annotations

import re
import threading
from collections import OrderedDict
from typing import TYPE_CHECKING, cast

from jinja2 import (
    DictLoader,
    Environment,
    StrictUndefined,
    Template,
    TemplateError,
    TemplateNotFound,
    Undefined,
)

from ralph.prompts.template_rendering_error import TemplateRenderingError

if TYPE_CHECKING:
    from collections.abc import Mapping


_EXCESS_STATIC_BLANK_LINES = re.compile(r"\n{3,}")
_FENCE_LINE = re.compile(r"^[ \t]*(?P<fence>`{3,}|~{3,})")
_IMPORT_TAG = re.compile(
    r"(?P<body>{%\s*(?:from|import)\b.*?)(?P<end>\s*%})",
    re.DOTALL,
)
_IMPORT_CONTEXT_SUFFIX = re.compile(r"\b(?:with|without)\s+context\s*$")
_VARIABLE_BLANK_LINE = re.compile(r"(?m)^(?=\r?$)")
_MAX_COMPILED_TEMPLATES = 64
_MAX_CACHED_RENDERERS = 16
_TemplateCache = OrderedDict[str, Template]
_RendererCache = OrderedDict[tuple[tuple[str, str], ...], "TemplateRenderer"]
_TOOL_VARIABLE_REFERENCE = re.compile(r"\b[A-Z][A-Z0-9_]*_TOOL_(?:NAME|REFERENCE)\b")


def _tool_variable_names(text: str) -> set[str]:
    return {match.group() for match in _TOOL_VARIABLE_REFERENCE.finditer(text)}


def _raise_template_error(message: str) -> str:
    raise TemplateRenderingError(message)


class TemplateRenderer:

    def __init__(self, partials: Mapping[str, str]) -> None:
        templates = {
            f"{name}.j2": _normalize_static_spacing(content) for name, content in partials.items()
        }
        strict_undefined = cast(
            "type[Undefined]", StrictUndefined
        )
        self._environment = Environment(
            loader=DictLoader(templates),
            autoescape=False,
            undefined=strict_undefined,
            keep_trailing_newline=True,
            cache_size=_MAX_COMPILED_TEMPLATES,
        )
        filters = cast("dict[str, object]", self._environment.filters)
        filters["split_items"] = self._split_items
        self._environment.globals["raise_error"] = _raise_template_error
        self._compiled_templates: _TemplateCache = OrderedDict()
        self._render_lock = threading.RLock()
        self._active_blank_line_token = ""
        self._partial_tool_variables: frozenset[str] = frozenset(
            name for content in templates.values() for name in _tool_variable_names(content)
        )

    def render(self, template_text: str, variables: Mapping[str, str]) -> str:
        with self._render_lock:
            return self._render_locked(template_text, variables)

    def _render_locked(self, template_text: str, variables: Mapping[str, str]) -> str:
        try:
            protected_variables, blank_line_token = _protect_variable_blank_lines(
                template_text,
                self._with_absent_tool_variables(template_text, variables),
            )
            self._active_blank_line_token = blank_line_token

            template = self._compiled_template(template_text)
            normalized = _normalize_rendered_spacing(
                template.render(**protected_variables),
            )
            return _restore_variable_blank_lines(normalized, blank_line_token)
        except TemplateNotFound as exc:
            raise TemplateRenderingError(str(exc)) from exc
        except TemplateRenderingError:
            raise
        except TemplateError as exc:
            raise TemplateRenderingError(str(exc)) from exc
        except TimeoutError:
            raise
        except Exception as exc:
            raise TemplateRenderingError(str(exc)) from exc

    def _with_absent_tool_variables(
        self,
        template_text: str,
        variables: Mapping[str, str],
    ) -> Mapping[str, str]:
        referenced = self._partial_tool_variables | _tool_variable_names(template_text)
        missing = referenced - set(variables)
        if not missing:
            return variables
        return {**dict.fromkeys(missing, ""), **variables}

    def _split_items(self, values: str) -> list[str]:
        clean_values = values.replace(self._active_blank_line_token, "")
        return _split_loop_items(clean_values)

    def _compiled_template(self, template_text: str) -> Template:
        normalized = _normalize_static_spacing(template_text)
        cached = self._compiled_templates.get(normalized)
        if cached is not None:
            self._compiled_templates.move_to_end(normalized)
            return cached

        template = self._environment.from_string(normalized)
        self._compiled_templates[normalized] = template
        if len(self._compiled_templates) > _MAX_COMPILED_TEMPLATES:
            self._compiled_templates.popitem(last=False)
        return template


_RENDERERS: _RendererCache = OrderedDict()
_RENDERERS_LOCK = threading.Lock()


def _renderer_for(partials: Mapping[str, str]) -> TemplateRenderer:
    key = tuple(sorted(partials.items()))
    with _RENDERERS_LOCK:
        cached = _RENDERERS.get(key)
        if cached is not None:
            _RENDERERS.move_to_end(key)
            return cached
        renderer = TemplateRenderer(partials)
        _RENDERERS[key] = renderer
        if len(_RENDERERS) > _MAX_CACHED_RENDERERS:
            _RENDERERS.popitem(last=False)
        return renderer


def render_template(
    template_text: str,
    variables: Mapping[str, str],
    partials: Mapping[str, str],
) -> str:
    return _renderer_for(partials).render(template_text, variables)


def _split_loop_items(values: str) -> list[str]:
    if "," in values:
        return [item.strip() for item in values.split(",")]
    return [line.strip() for line in values.splitlines() if line.strip()]


def _normalize_static_spacing(template_source: str) -> str:
    return _collapse_unfenced_blank_runs(_imports_with_context(template_source))


def _imports_with_context(template_source: str) -> str:

    def add_context(match: re.Match[str]) -> str:
        body = match.group("body")
        if _IMPORT_CONTEXT_SUFFIX.search(body):
            return match.group(0)
        return f"{body} with context{match.group('end')}"

    return _IMPORT_TAG.sub(add_context, template_source)


def _protect_variable_blank_lines(
    template_text: str,
    variables: Mapping[str, str],
) -> tuple[dict[str, str], str]:
    token_number = 917340286
    token = f"\ue000{token_number}\ue001"
    values = tuple(variables.values())
    while token in template_text or any(token in value for value in values):
        token_number += 1
        token = f"\ue000{token_number}\ue001"
    protected = {
        name: (_VARIABLE_BLANK_LINE.sub(token, value) if "\n" in value else value)
        for name, value in variables.items()
    }
    return protected, token


def _restore_variable_blank_lines(rendered: str, token: str) -> str:
    token_line = re.compile(rf"(?m)^[ \t]*{re.escape(token)}[ \t]*(?=\r?$)")
    return token_line.sub("", rendered)


def _collapse_unfenced_blank_runs(text: str) -> str:
    output: list[str] = []
    outside: list[str] = []
    fence_character = ""
    fence_length = 0

    def flush_outside() -> None:
        if outside:
            chunk = "".join(outside)
            if output and output[-1].endswith("\n"):
                chunk = _EXCESS_STATIC_BLANK_LINES.sub("\n\n", f"\n{chunk}")[1:]
            else:
                chunk = _EXCESS_STATIC_BLANK_LINES.sub("\n\n", chunk)
            output.append(chunk)
            outside.clear()

    for line in text.splitlines(keepends=True):
        match = _FENCE_LINE.match(line)
        if not fence_character:
            if match is None:
                outside.append(line)
                continue
            flush_outside()
            fence = cast(
                "str", match.group("fence")
            )
            fence_character = fence[0]
            fence_length = len(fence)
            output.append(line)
            continue

        output.append(line)
        if match is None:
            continue
        fence = cast(
            "str", match.group("fence")
        )
        if fence[0] == fence_character and len(fence) >= fence_length:
            fence_character = ""
            fence_length = 0

    flush_outside()
    return "".join(output)


def _normalize_rendered_spacing(rendered: str) -> str:
    return _collapse_unfenced_blank_runs(rendered)
