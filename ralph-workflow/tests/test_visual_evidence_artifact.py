from __future__ import annotations

from importlib import import_module

import pytest

from ralph.mcp.artifacts.design_verdict import (
    DESIGN_VERDICT_ARTIFACT_TYPE,
    DesignVerdict,
    normalize_design_verdict_content,
)
from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec


def _ensure_spec_registered() -> None:
    import_module("ralph.mcp.artifacts.markdown.specs")


def _build_document(
    *,
    cell_ids: str = "cap-001,cap-002",
    intent: str = "The header should align its three action buttons in a single row.",
    verdict_status: str = "pass",
    verdict_summary: str = "Buttons align on desktop and stack on mobile.",
    findings: tuple[str, ...] = (
        "cap-001 | 0,0,320,40 | alignment | minor | Off-grid by 2px at the smallest breakpoint.",
        "cap-002 | 0,0,320,160 | stacking | info | Stack order is icon-then-label.",
    ),
    run_id: str = "2026-08-10-001",
    judgement_tier: str = "deterministic",
    target: str = "src/components/header.tsx",
    before_id: str = "manifest-before-001",
    after_id: str = "manifest-after-001",
) -> str:
    findings_block = "\n".join(f"- [F-{index + 1}] {entry}" for index, entry in enumerate(findings))
    return (
        "---\n"
        "type: design_verdict\n"
        f"judgement_tier: {judgement_tier}\n"
        "---\n"
        "\n"
        "## Capture Provenance\n"
        f"\nrun_id: {run_id}\n"
        f"target: {target}\n"
        f"before_id: {before_id}\n"
        f"after_id: {after_id}\n"
        f"cell_ids: {cell_ids}\n"
        "\n"
        "## Design Intent\n"
        "\n"
        f"- [I-1] {intent}\n"
        "\n"
        "## Verdict\n"
        "\n"
        f"- [V-1] {verdict_status} | {verdict_summary}\n"
        "\n"
        "## Findings\n"
        "\n"
        f"{findings_block}\n"
    )


def _errors(text: str) -> list[str]:
    _ensure_spec_registered()
    spec = get_spec(DESIGN_VERDICT_ARTIFACT_TYPE)
    _, diagnostics = parse_and_validate(text, spec)
    return [diagnostic.rule_id for diagnostic in diagnostics if diagnostic.severity == "error"]


def _all_diagnostics(text: str) -> list[str]:
    _ensure_spec_registered()
    spec = get_spec(DESIGN_VERDICT_ARTIFACT_TYPE)
    _, diagnostics = parse_and_validate(text, spec)
    return [diagnostic.rule_id for diagnostic in diagnostics]


def test_spec_is_registered() -> None:
    _ensure_spec_registered()
    spec = get_spec(DESIGN_VERDICT_ARTIFACT_TYPE)
    assert spec.artifact_type == DESIGN_VERDICT_ARTIFACT_TYPE


def test_valid_document_passes_with_no_errors() -> None:
    text = _build_document()
    assert _errors(text) == []


def test_canonical_content_is_extracted_from_a_valid_document() -> None:
    text = _build_document()
    _ensure_spec_registered()
    spec = get_spec(DESIGN_VERDICT_ARTIFACT_TYPE)
    content, diagnostics = parse_and_validate(text, spec)
    assert [d for d in diagnostics if d.severity == "error"] == []
    content_dict = content
    assert content_dict["run_id"] == "2026-08-10-001"
    assert content_dict["judgement_tier"] == "deterministic"
    assert content_dict["target"] == "src/components/header.tsx"
    assert content_dict["before_id"] == "manifest-before-001"
    assert content_dict["after_id"] == "manifest-after-001"
    assert content_dict["cell_ids"] == ["cap-001", "cap-002"]
    intent_value = content_dict["intent"]
    assert isinstance(intent_value, str)
    assert intent_value.startswith("The header should align")
    assert content_dict["status"] == "pass"
    summary_value = content_dict["summary"]
    assert isinstance(summary_value, str)
    assert "Buttons align" in summary_value
    findings_value = content_dict["findings"]
    assert isinstance(findings_value, list)
    assert len(findings_value) == 2
    first_finding, second_finding = findings_value
    assert isinstance(first_finding, dict)
    assert isinstance(second_finding, dict)
    assert first_finding["capture_id"] == "cap-001"
    assert first_finding["region"] == "0,0,320,40"
    assert first_finding["dimension"] == "alignment"
    assert first_finding["severity"] == "minor"
    assert second_finding["capture_id"] == "cap-002"


def test_finding_with_unknown_capture_id_is_rejected() -> None:
    text = _build_document(
        findings=("cap-ghost | 0,0,100,100 | alignment | minor | Not in cell_ids.",),
    )
    assert "DV003" in _errors(text)


def test_finding_with_malformed_region_is_rejected() -> None:
    text = _build_document(
        findings=("cap-001 | not-a-region | alignment | minor | Bad region.",),
    )
    assert "DV004" in _errors(text)


def test_finding_with_negative_region_is_rejected() -> None:
    text = _build_document(
        findings=("cap-001 | -1,0,100,100 | alignment | minor | Negative coordinate.",),
    )
    assert "DV004" in _errors(text)


def test_finding_with_empty_severity_is_rejected() -> None:
    text = _build_document(
        findings=("cap-001 | 0,0,100,100 | alignment |  | Missing severity.",),
    )
    assert "DV005" in _errors(text)


def test_finding_with_malformed_shape_is_rejected() -> None:
    text = _build_document(
        findings=("cap-001 | 0,0,100,100 | alignment | minor",),
    )
    assert "DV003" in _errors(text)


def test_pass_verdict_with_blocker_finding_is_rejected() -> None:
    text = _build_document(
        verdict_status="pass",
        findings=("cap-001 | 0,0,100,100 | layout | blocker | Breaks the entire header.",),
    )
    assert "DV006" in _errors(text)


def test_pass_verdict_with_major_finding_is_rejected() -> None:
    text = _build_document(
        verdict_status="pass",
        findings=("cap-001 | 0,0,100,100 | layout | major | Whole region is offset.",),
    )
    assert "DV006" in _errors(text)


def test_fail_verdict_with_no_blocking_finding_is_rejected() -> None:
    text = _build_document(
        verdict_status="fail",
        findings=("cap-001 | 0,0,100,100 | alignment | minor | Off-grid by 2px.",),
    )
    assert "DV007" in _errors(text)


def test_fail_verdict_with_blocker_finding_passes() -> None:
    text = _build_document(
        verdict_status="fail",
        findings=("cap-001 | 0,0,100,100 | layout | blocker | Breaks the entire header.",),
    )
    assert _errors(text) == []


def test_blocked_verdict_is_independent_of_finding_severity() -> None:
    text = _build_document(
        verdict_status="blocked",
        findings=("cap-001 | 0,0,100,100 | alignment | minor | Off-grid by 2px.",),
    )
    assert _errors(text) == []


def test_intent_with_source_smuggle_phrase_is_rejected() -> None:
    text = _build_document(intent="Inspect the source of the header component.")
    assert "DV008" in _errors(text)


def test_intent_with_diff_smuggle_phrase_is_rejected() -> None:
    text = _build_document(intent="Show me the diff between before and after.")
    assert "DV008" in _errors(text)


def test_intent_with_dom_smuggle_phrase_is_rejected() -> None:
    text = _build_document(intent="Walk the DOM tree of the rendered header.")
    assert "DV008" in _errors(text)


def test_intent_with_stylesheet_smuggle_phrase_is_rejected() -> None:
    text = _build_document(intent="Compare this against the production stylesheet.")
    assert "DV008" in _errors(text)


@pytest.mark.parametrize(
    "intent",
    [
        "Walk the dom tree of the rendered header.",
        "Walk the Dom Tree of the rendered header.",
        "Read the Source Code of the header component.",
        "Show me the Diff between before and after.",
        "Compare this against the production Stylesheet.",
    ],
)
def test_intent_smuggle_detection_is_case_insensitive(intent: str) -> None:
    assert "DV008" in _errors(_build_document(intent=intent))


@pytest.mark.parametrize(
    "intent",
    [
        "Match the css rules that shipped with the design system.",
        "Confirm the CSS custom properties resolve for the banner.",
        "Check the class name applied to the primary action.",
        "Verify the classNames emitted for the primary action.",
        "Confirm the inline styles on the banner container.",
        "Confirm the style attribute on the banner container.",
        "Confirm the computed style of the banner container.",
    ],
)
def test_intent_rejects_css_class_and_style_smuggles(intent: str) -> None:
    assert "DV008" in _errors(_build_document(intent=intent))


@pytest.mark.parametrize(
    "intent",
    [
        "Fix the source of the spacing inconsistency in the card grid.",
        "Resolve a class of layout bugs that appear only when stacked.",
        "The header must read differently at the two smallest breakpoints.",
        "Keep the visual style of the header consistent across themes.",
        "Random kingdom banners must not clip at the narrow viewport.",
        "The difference between the two themes must stay legible.",
    ],
)
def test_intent_accepts_ordinary_design_prose(intent: str) -> None:
    assert _errors(_build_document(intent=intent)) == []


@pytest.mark.parametrize(
    "intent",
    [
        "Consult the source before judging the header spacing.",
        "View the source for the header component.",
        "Audit the source that renders the header.",
        "Study the source that produces the banner.",
        "Cat the source for the card grid component.",
        "Pull up the source for the card grid component.",
        "I looked at the source while drafting this intent.",
        "Compare the source-code against the rendered header.",
        "Judge the header against the source's contents.",
    ],
)
def test_intent_rejects_source_reading_however_it_is_worded(intent: str) -> None:
    assert "DV008" in _errors(_build_document(intent=intent))


def test_intent_rejects_a_code_reading_directive_before_the_source_of_idiom() -> None:
    intent = "Read the source of the header component before judging it."
    assert "DV008" in _errors(_build_document(intent=intent))


@pytest.mark.parametrize(
    "intent",
    [
        "Check the DOMs emitted for each breakpoint.",
        "Report on diffing the builds before and after the change.",
        "Confirm the diffed output matches the rendered header.",
    ],
)
def test_intent_rejects_plural_and_inflected_code_artifact_terms(intent: str) -> None:
    assert "DV008" in _errors(_build_document(intent=intent))


@pytest.mark.parametrize(
    "intent",
    [
        "Keep the resource card grid aligned across breakpoints.",
        "The spacing scale stays the source of truth for spacing.",
        "Fix the source of the crowding in the sidebar.",
    ],
)
def test_intent_accepts_the_root_cause_and_resource_senses(intent: str) -> None:
    assert _errors(_build_document(intent=intent)) == []


def test_missing_capture_provenance_field_is_rejected() -> None:
    text = _build_document(target="")
    rule_ids = _all_diagnostics(text)
    assert any(rule_id.startswith("DV") or rule_id == "SPEC010" for rule_id in rule_ids)


def test_missing_cell_ids_is_rejected() -> None:
    text = _build_document(cell_ids="")
    rule_ids = _all_diagnostics(text)
    assert any(rule_id.startswith("DV") or rule_id == "SPEC010" for rule_id in rule_ids)


def test_unknown_type_frontmatter_is_rejected() -> None:
    text = "---\ntype: bogus_verdict\n---\n\n## Capture Provenance\n\n"
    assert "DV001" in _errors(text)


def test_unknown_frontmatter_status_is_rejected() -> None:
    text = (
        "---\ntype: design_verdict\nstatus: maybe\n---\n\n## Capture Provenance\n"
        "run_id: r\ntarget: t\nbefore_id: b\nafter_id: a\ncell_ids: c-1\n\n"
        "## Design Intent\n\n- [I-1] Align buttons.\n\n"
        "## Verdict\n\n- [V-1] pass | Looks fine.\n\n"
        "## Findings\n\n- [F-1] c-1 | 0,0,1,1 | d | info | n\n"
    )
    assert "DV002" in _errors(text)


def test_normalize_design_verdict_accepts_canonical_content() -> None:
    text = _build_document()
    _ensure_spec_registered()
    spec = get_spec(DESIGN_VERDICT_ARTIFACT_TYPE)
    content, diagnostics = parse_and_validate(text, spec)
    assert [d for d in diagnostics if d.severity == "error"] == []
    normalized = normalize_design_verdict_content(content)
    assert normalized["status"] == "pass"
    assert normalized["run_id"] == "2026-08-10-001"
    assert normalized["cell_ids"] == ["cap-001", "cap-002"]


def test_normalize_design_verdict_rejects_unknown_status() -> None:
    import pydantic

    with pytest.raises((pydantic.ValidationError, ValueError)):
        normalize_design_verdict_content(
            {
                "run_id": "r",
                "target": "t",
                "before_id": "b",
                "after_id": "a",
                "cell_ids": ["c-1"],
                "intent": "Align buttons.",
                "status": "maybe",
                "summary": "s",
                "findings": [],
            }
        )


def test_pydantic_schema_rejects_missing_required_fields() -> None:
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        DesignVerdict(status="pass")


def test_pydantic_schema_rejects_extra_fields() -> None:
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        DesignVerdict.model_validate(
            {
                "run_id": "r",
                "target": "t",
                "before_id": "b",
                "after_id": "a",
                "cell_ids": ["c-1"],
                "intent": "Align buttons.",
                "status": "pass",
                "summary": "s",
                "findings": [],
                "smuggled": "value",
            }
        )
