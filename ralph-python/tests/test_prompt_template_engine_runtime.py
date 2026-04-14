"""Direct tests for the runtime template engine helpers."""

from __future__ import annotations

import pytest

from ralph.prompts.template_context import TemplateContext, TemplateRegistry as ContextTemplateRegistry
from ralph.prompts.template_engine import TemplateRenderingError, render_template
from ralph.prompts.template_parsing import TemplateNode
from ralph.prompts.template_registry import TemplateNotFoundError, TemplateRegistry


def test_render_template_supports_variables_partials_loops_and_conditionals() -> None:
    template = (
        "Hello {{NAME}}!\n"
        "{{> footer}}\n"
        "{% if HAS_ITEMS %}Items: {% for ITEM in ITEMS %}[{{ITEM}}]{% endfor %}{% endif %}"
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
    assert render_template('{{MISSING|default="fallback"}}', {}, {}) == "fallback"

    with pytest.raises(TemplateRenderingError, match="'MISSING' is undefined"):
        render_template("{{MISSING}}", {}, {})

    with pytest.raises(TemplateRenderingError, match="footer.txt"):
        render_template("{{> footer}}", {}, {})


def test_template_registries_cover_success_and_error_paths() -> None:
    registry = TemplateRegistry()
    registry.register_template("review", "Review template")
    assert registry.get_template("review") == "Review template"

    with pytest.raises(TemplateNotFoundError, match="template 'missing' not found"):
        registry.get_template("missing")

    context_registry = ContextTemplateRegistry()
    context_registry.register_template("planning", "Plan")
    assert context_registry.get_template("planning") == "Plan"

    default_context = TemplateContext.default()
    assert default_context.registry.get_template("planning")
    assert default_context.registry.get_template("developer_iteration")


def test_render_template_rejects_unsupported_node_types() -> None:
    class UnknownNode(TemplateNode):
        pass

    with pytest.raises(TemplateRenderingError, match="unsupported template node: UnknownNode"):
        from ralph.prompts import template_engine

        template_engine._render_nodes([UnknownNode()], {}, {})
