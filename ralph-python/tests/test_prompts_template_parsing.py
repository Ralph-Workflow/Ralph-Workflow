"""Tests for the template parsing helpers."""

import importlib

template_parsing = importlib.import_module("ralph.prompts.template_parsing")

ConditionalNode = template_parsing.ConditionalNode
LoopNode = template_parsing.LoopNode
TextNode = template_parsing.TextNode
VariableNode = template_parsing.VariableNode
parse_metadata_line = template_parsing.parse_metadata_line
parse_template = template_parsing.parse_template


def test_parse_template_returns_expected_ast() -> None:
    template = (
        'Hello {{NAME|default="Guest"}} '
        '{% if FLAG %}Flagged{% else %}Clear{% endif %} '
        '{% for item in items %}{{item}}{% endfor %}'
    )
    expected = [
        TextNode("Hello "),
        VariableNode(name="NAME", default="Guest", placeholder='NAME|default="Guest"'),
        TextNode(" "),
        ConditionalNode(
            condition="FLAG",
            truthy=[TextNode("Flagged")],
            falsy=[TextNode("Clear")],
        ),
        TextNode(" "),
        LoopNode(
            variable="item",
            iterable="items",
            body=[VariableNode(name="item", default=None, placeholder="item")],
        ),
    ]
    assert parse_template(template) == expected


def test_parse_metadata_line_extracts_version_and_purpose() -> None:
    version_line = "{# Version: 1.0 #}"
    purpose_line = "{# PURPOSE: Demo #}"

    assert parse_metadata_line(version_line) == ("1.0", None)
    assert parse_metadata_line(purpose_line) == (None, "Demo")
