"""Behavior tests for the shared markdown artifact validation gate."""

from ralph.mcp.artifacts.markdown import (
    LenientEnum,
    MdArtifactSpec,
    SectionRule,
    parse_and_validate,
)
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
