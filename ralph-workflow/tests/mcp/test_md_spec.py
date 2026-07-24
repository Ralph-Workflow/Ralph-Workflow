"""Behavior tests for the shared markdown artifact validation gate."""

from ralph.mcp.artifacts.markdown import (
    LenientEnum,
    MdArtifactSpec,
    SectionRule,
    parse_and_validate,
)
from ralph.mcp.artifacts.markdown._diagnostic import Diagnostic
from ralph.mcp.artifacts.markdown._document import ParsedDocument


def _to_content(document: ParsedDocument) -> dict[str, object]:
    steps = document.section("Steps")
    assert steps is not None
    return {"status": document.frontmatter["status"], "steps": [item.text for item in steps.items]}


def _normalize(content: dict[str, object]) -> dict[str, object]:
    if not content["steps"]:
        raise ValueError("Steps must be non-empty")
    return content


def test_shared_validator_coerces_declared_lenient_vocabulary_then_normalizes() -> None:
    spec = MdArtifactSpec(
        artifact_type="example",
        required_frontmatter=frozenset({"status"}),
        sections={"Steps": SectionRule(require_items=True)},
        to_content=_to_content,
        normalize_content=_normalize,
        lenient_enums={"status": LenientEnum(frozenset({"ready"}), "ready")},
    )

    content, diagnostics = parse_and_validate("---\nstatus: typo\n---\n## Steps\n- [S1] Build\n", spec)

    assert content == {"status": "ready", "steps": ["Build"]}
    assert [(diagnostic.rule_id, diagnostic.severity) for diagnostic in diagnostics] == [
        ("SPEC009", "warning")
    ]


def test_shared_validator_rejects_missing_required_document_structure() -> None:
    spec = MdArtifactSpec(
        artifact_type="example",
        required_frontmatter=frozenset({"status"}),
        sections={"Steps": SectionRule(require_items=True)},
        to_content=_to_content,
        normalize_content=_normalize,
    )

    content, diagnostics = parse_and_validate("---\nstatus: ready\n---\n## Other\n- [S1] Build\n", spec)

    assert content == {}
    assert {(diagnostic.section, diagnostic.rule_id) for diagnostic in diagnostics} == {
        ("Other", "SPEC004"),
        ("Steps", "SPEC008"),
    }


def test_document_validator_runs_before_mapper_without_expanding_spec_constructor() -> None:
    mapper_called = False

    def mapper(document: ParsedDocument) -> dict[str, object]:
        nonlocal mapper_called
        mapper_called = True
        return {"status": document.frontmatter["status"]}

    def reject(document: ParsedDocument) -> list[Diagnostic]:
        return [Diagnostic(document.frontmatter_lines["status"], None, "TEST001", "rejected")]

    spec = MdArtifactSpec(
        artifact_type="example",
        required_frontmatter=frozenset({"status"}),
        sections={},
        to_content=mapper,
        normalize_content=lambda content: content,
        validate_document=reject,
    )

    content, diagnostics = parse_and_validate("---\nstatus: wrong\n---\n", spec)

    assert content == {}
    assert mapper_called is False
    assert [diagnostic.rule_id for diagnostic in diagnostics] == ["TEST001"]
    assert "validate_frontmatter" not in MdArtifactSpec.__annotations__


def test_shared_validator_rejects_body_and_blocks_where_a_section_forbids_them() -> None:
    spec = MdArtifactSpec(
        artifact_type="example",
        required_frontmatter=frozenset({"status"}),
        sections={"Steps": SectionRule(require_items=True)},
        to_content=_to_content,
        normalize_content=_normalize,
    )

    content, diagnostics = parse_and_validate(
        "---\nstatus: ready\n---\n"
        "## Steps\n"
        "- [S1] Build\n"
        "  Extra: field line\n"
        "stray prose\n"
        "- bare bullet\n"
        "### [B1] Block\n",
        spec,
    )

    assert content == {}
    assert {(diagnostic.line, diagnostic.rule_id) for diagnostic in diagnostics} == {
        (6, "MD004"),
        (7, "MD004"),
        (8, "MD003"),
        (9, "MD001"),
    }
