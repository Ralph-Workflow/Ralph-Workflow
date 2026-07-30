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


def test_parser_captures_blocks_item_fields_and_body_lines() -> None:
    document, diagnostics = parse_markdown_document(
        "---\ntype: example\n---\n"
        "## Summary\n"
        "Some prose context.\n"
        "Intent: one line\n"
        "## Risks\n"
        "- [R-1] A risk\n"
        "  Severity: medium\n"
        "## Steps\n"
        "\n"
        "### [S-1] First step\n"
        "Step prose.\n"
        "\n"
        "Files:\n"
        "- modify a.py\n"
    )

    assert diagnostics == []
    summary = document.section("Summary")
    assert summary is not None
    assert [line.text for line in summary.lines] == ["Some prose context.", "Intent: one line"]
    risks = document.section("Risks")
    assert risks is not None
    assert risks.items[0].fields[0].text == "Severity: medium"
    assert risks.items[0].fields[0].line == 9
    steps = document.section("Steps")
    assert steps is not None
    assert steps.items == ()
    block = steps.blocks[0]
    assert (block.identifier, block.title, block.line) == ("S-1", "First step", 12)
    assert [line.text for line in block.lines] == ["Step prose.", "Files:", "- modify a.py"]


def test_parser_flags_malformed_headings_and_preamble_content() -> None:
    _, diagnostics = parse_markdown_document(
        "---\ntype: example\n---\n"
        "stray preamble\n"
        "## Steps\n"
        "### no stable id\n"
    )

    assert [(diagnostic.line, diagnostic.rule_id) for diagnostic in diagnostics] == [
        (4, "MD002"),
        (6, "MD001"),
    ]
