"""Behavior tests for the closed markdown artifact grammar."""

from ralph.mcp.artifacts.markdown import parse_markdown_document


def test_parser_keeps_line_anchored_frontmatter_sections_and_items() -> None:
    document, diagnostics = parse_markdown_document(
        "---\ntype: example\nschema_version: 1\n---\n## Steps\n- [S1] Build it\n- [x] [S2] Verify it\n"
    )

    assert diagnostics == []
    assert document.frontmatter == {"type": "example", "schema_version": "1"}
    steps = document.section("Steps")
    assert steps is not None
    assert steps.items[1].checked is True
    assert steps.items[1].line == 7


def test_parser_returns_diagnostics_for_unterminated_and_arbitrary_markdown() -> None:
    _, diagnostics = parse_markdown_document("---\ntype: plan\n# Not allowed\n")

    assert [(diagnostic.line, diagnostic.rule_id) for diagnostic in diagnostics] == [
        (3, "MD005"),
        (1, "MD007"),
    ]
