"""Direct tests for the runtime template engine helpers."""

from __future__ import annotations

import re

import pytest
from jinja2 import Environment, Template

from ralph.prompts.template_context import TemplateContext
from ralph.prompts.template_engine import (
    TemplateRenderer,
    render_template,
)
from ralph.prompts.template_registry import (
    TemplateNotFoundError,
    TemplateRegistry,
)
from ralph.prompts.template_rendering_error import TemplateRenderingError


def test_render_template_supports_variables_partials_loops_and_conditionals() -> None:
    template = (
        "Hello {{NAME}}!\n"
        "{% include 'footer.j2' %}\n"
        "{% if HAS_ITEMS %}Items: "
        "{% for ITEM in ITEMS|split_items %}[{{ITEM}}]{% endfor %}{% endif %}"
        "{% if HAS_FALLBACK %}unused{% else %} done{% endif %}"
    )

    rendered = render_template(
        template,
        {
            "NAME": "Ralph",
            "HAS_ITEMS": "true",
            "ITEMS": "one,two",
            "HAS_FALLBACK": "",
        },
        {"footer": "Footer {{NAME}}"},
    )

    assert rendered == "Hello Ralph!\nFooter Ralph\nItems: [one][two] done"


def test_render_template_uses_default_and_reports_missing_partial_or_variable() -> None:
    assert render_template("{{ MISSING|default('fallback') }}", {}, {}) == "fallback"

    with pytest.raises(TemplateRenderingError, match="'MISSING' is undefined"):
        render_template("{{MISSING}}", {}, {})

    with pytest.raises(TemplateRenderingError, match=re.escape("footer.txt")):
        render_template("{% include 'footer.txt' %}", {}, {})


def test_template_registries_cover_success_and_error_paths() -> None:
    registry = TemplateRegistry()
    registry.register_template("review", "Review template")
    assert registry.get_template("review") == "Review template"

    with pytest.raises(TemplateNotFoundError, match="template 'missing' not found"):
        registry.get_template("missing")

    context_registry = TemplateRegistry()
    context_registry.register_template("planning", "Plan")
    assert context_registry.get_template("planning") == "Plan"

    default_context = TemplateContext.default()
    assert default_context.registry.get_template("planning")
    assert default_context.registry.get_template("developer_iteration")


def test_reusable_renderer_does_not_leak_macro_globals_between_renders() -> None:
    renderer = TemplateRenderer(
        {"shared/value": ("{% macro render_value() -%}{{ VALUE }}{%- endmacro %}")}
    )
    source = "{% from 'shared/value.j2' import render_value %}{{ render_value() }}"

    assert renderer.render(source, {"VALUE": "first"}) == "first"
    assert renderer.render(source, {"VALUE": "second"}) == "second"

    with pytest.raises(TemplateRenderingError, match="'VALUE' is undefined"):
        renderer.render(source, {})


def test_pre_contextualized_macro_import_preserves_current_render_variables() -> None:
    rendered = render_template(
        "{% from 'shared/value.j2' import render_value with context %}{{ render_value() }}",
        {"VALUE": "current"},
        {"shared/value": "{% macro render_value() -%}{{ VALUE }}{%- endmacro %}"},
    )

    assert rendered == "current"


def test_template_macro_can_report_an_explicit_rendering_error() -> None:
    with pytest.raises(TemplateRenderingError, match="payload is required"):
        render_template("{{ raise_error('payload is required') }}", {}, {})


def test_render_template_preserves_a_fence_without_leading_outside_text() -> None:
    template = "```\nfirst\n\n\n\nsecond\n```\n"

    assert render_template(template, {}, {}) == template


def test_short_or_mismatched_fences_do_not_close_the_open_fenced_payload() -> None:
    template = "````markdown\nfirst\n```\n~~~\n\n\n\nsecond\n"

    assert render_template(template, {}, {}) == template


def test_render_template_preserves_blank_runs_in_filtered_fenced_payload() -> None:
    payload = "first\n\n\n\nsecond"
    rendered = render_template(
        "Before\n\n\n\n```markdown\n{{ PAYLOAD|trim|indent(3, true) }}\n```\n\n\n\nAfter",
        {"PAYLOAD": payload},
        {},
    )

    assert rendered == ("Before\n\n```markdown\n   first\n\n\n\n   second\n```\n\nAfter")


def test_render_template_preserves_blank_runs_in_filtered_unfenced_payload() -> None:
    payload = "first\n\n\n\nsecond"
    rendered = render_template(
        "Before\n\n\n\n{{ PAYLOAD|trim|indent(3, true) }}\n\n\n\nAfter",
        {"PAYLOAD": payload},
        {},
    )

    assert rendered == ("Before\n\n   first\n\n\n\n   second\n\nAfter")


def test_render_template_preserves_payload_that_contains_the_initial_blank_line_marker() -> None:
    marker = "\ue000917340286\ue001"
    payload = f"first {marker}\n\n\n\nsecond"

    rendered = render_template(
        "{{ PAYLOAD|trim }}",
        {"PAYLOAD": payload},
        {},
    )

    assert rendered == payload


def test_split_items_ignores_payload_blank_line_sentinels() -> None:
    rendered = render_template(
        "{% for ITEM in ITEMS|split_items %}[{{ ITEM }}]{% endfor %}",
        {"ITEMS": "first\n\n\n\nsecond"},
        {},
    )

    assert rendered == "[first][second]"


def test_reusable_renderer_compiles_once_and_evicts_the_oldest_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compile_calls: list[str] = []
    original = Environment.from_string

    def counting_from_string(
        environment: Environment,
        source: str,
        globals: dict[str, object] | None = None,
        template_class: type[Template] | None = None,
    ) -> Template:
        compile_calls.append(source)
        return original(environment, source, globals, template_class)

    monkeypatch.setattr(Environment, "from_string", counting_from_string)
    renderer = TemplateRenderer({})

    renderer.render("source-0", {})
    renderer.render("source-0", {})
    assert compile_calls == ["source-0"]

    for index in range(1, 65):
        renderer.render(f"source-{index}", {})
    renderer.render("source-0", {})

    assert len(compile_calls) == 66
