"""Pure behavior tests for the commit-review markdown specification."""

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec, registered_specs
from ralph.mcp.artifacts.markdown.specs import COMMIT_REVIEW_SPEC

_VALID_TREE_SHA = "0123456789abcdef0123456789abcdef01234567"

_VALID_DOCUMENT = f"""---
type: commit_review
verdict: approve
tree_sha: {_VALID_TREE_SHA}
---
## Reason
- [R1] Temporary build output must not be committed

## Actions
- [A1] delete_file | tmp/scratch.log
- [A2] add_to_gitignore | *.tmp
"""


def _error_ids(text: str) -> set[str]:
    _, diagnostics = parse_and_validate(text, get_spec("commit_review"))
    return {diagnostic.rule_id for diagnostic in diagnostics if diagnostic.severity == "error"}


def test_commit_review_spec_is_registered() -> None:
    assert get_spec("commit_review") is COMMIT_REVIEW_SPEC
    assert "commit_review" in {spec.artifact_type for spec in registered_specs()}


def test_valid_commit_review_document_produces_typed_content() -> None:
    content, diagnostics = parse_and_validate(_VALID_DOCUMENT, get_spec("commit_review"))

    assert diagnostics == []
    assert content["verdict"] == "approve"
    assert content["tree_sha"] == _VALID_TREE_SHA
    assert content["actions"] == [
        {"action": "delete_file", "path": "tmp/scratch.log"},
        {"action": "add_to_gitignore", "pattern": "*.tmp"},
    ]
    assert content["reason"] == "Temporary build output must not be committed"


def test_valid_commit_review_approve_without_reason() -> None:
    doc = f"""---
type: commit_review
verdict: approve
tree_sha: {_VALID_TREE_SHA}
---
## Actions
"""
    content, diagnostics = parse_and_validate(doc, get_spec("commit_review"))
    assert diagnostics == []
    assert content["verdict"] == "approve"
    assert content["tree_sha"] == _VALID_TREE_SHA
    assert content["actions"] == []
    assert "reason" not in content


def test_valid_commit_review_reject_with_reason() -> None:
    doc = f"""---
type: commit_review
verdict: reject
tree_sha: {_VALID_TREE_SHA}
---
## Reason
- [R1] Accidental deletion of src/main.py detected

## Actions
"""
    content, diagnostics = parse_and_validate(doc, get_spec("commit_review"))
    assert diagnostics == []
    assert content["verdict"] == "reject"
    assert content["tree_sha"] == _VALID_TREE_SHA
    assert content["reason"] == "Accidental deletion of src/main.py detected"


def test_reject_without_reason_section_is_error() -> None:
    doc = f"""---
type: commit_review
verdict: reject
tree_sha: {_VALID_TREE_SHA}
---
## Actions
"""
    assert "SPEC008" in _error_ids(doc)


def test_reject_with_empty_reason_section_is_error() -> None:
    doc = f"""---
type: commit_review
verdict: reject
tree_sha: {_VALID_TREE_SHA}
---
## Reason

## Actions
"""
    assert "SPEC006" in _error_ids(doc)


def test_invalid_verdict_is_rejected() -> None:
    doc = f"""---
type: commit_review
verdict: maybe
tree_sha: {_VALID_TREE_SHA}
---
## Actions
"""
    assert "SPEC010" in _error_ids(doc)


def test_missing_verdict_is_rejected() -> None:
    doc = f"""---
type: commit_review
tree_sha: {_VALID_TREE_SHA}
---
## Actions
"""
    assert "SPEC002" in _error_ids(doc)


def test_missing_tree_sha_is_rejected() -> None:
    doc = """---
type: commit_review
verdict: approve
---
## Actions
"""
    assert "SPEC002" in _error_ids(doc)


def test_invalid_tree_sha_not_40_hex_is_rejected() -> None:
    doc = """---
type: commit_review
verdict: approve
tree_sha: shortsha
---
## Actions
"""
    assert "SPEC010" in _error_ids(doc)


def test_commit_review_spec_regression_wrong_type_diagnostic_uses_shared_grammar_code() -> None:
    """A wrong artifact type is diagnosed with the shared markdown grammar code.

    Plan step: WU-3 — the commit-review artifact must not carry the retired
    cleanup family's rule code. The artifact contract (PROMPT.md, "Artifact
    contract") requires validation diagnostics to follow the existing
    markdown artifact grammar (``MD``/``SPEC``/``REF`` codes), so a wrong
    ``type`` frontmatter value is reported with the shared closed-grammar
    code ``SPEC010`` — the code this spec already uses for its other
    closed-grammar rejections.
    """
    doc = f"""---
type: wrong
verdict: approve
tree_sha: {_VALID_TREE_SHA}
---
## Actions
"""
    content, diagnostics = parse_and_validate(doc, get_spec("commit_review"))

    assert content == {}
    assert {diagnostic.rule_id for diagnostic in diagnostics if diagnostic.severity == "error"} == {
        "SPEC010"
    }


def test_legacy_analysis_complete_not_accepted_as_verdict() -> None:
    doc = """---
type: commit_review
analysis_complete: true
---
## Actions
"""
    # Missing required verdict and tree_sha
    error_ids = _error_ids(doc)
    assert "SPEC002" in error_ids


def test_dual_field_commit_review_document_is_rejected() -> None:
    """A document carrying both ``verdict`` and the retired field is rejected.

    The artifact contract has a single verdict field: ``analysis_complete``
    was replaced by ``verdict`` and there is no dual-field support. The
    markdown grammar is a closed contract, so a document that still carries
    the retired field next to a valid ``verdict`` must fail closed with an
    unknown-frontmatter diagnostic instead of being silently accepted.
    """
    doc = f"""---
type: commit_review
verdict: approve
tree_sha: {_VALID_TREE_SHA}
analysis_complete: false
---
## Actions
"""
    content, diagnostics = parse_and_validate(doc, get_spec("commit_review"))

    # RALPH-TEST-REASON: the commit_review contract declares exactly one verdict
    # field, so a document that also carries `analysis_complete` has no valid
    # parse; the assertions below encode that contract.
    assert content == {}, "a dual-field document must not yield an accepted verdict"
    assert "analysis_complete" not in content
    errors = [diagnostic for diagnostic in diagnostics if diagnostic.severity == "error"]
    assert errors, "a dual-field document must be rejected with an error diagnostic"
    assert "SPEC003" in {diagnostic.rule_id for diagnostic in errors}
    assert any("analysis_complete" in diagnostic.message for diagnostic in errors)


def test_commit_review_comment_injection_guard_rejects_hash_prefix() -> None:
    assert "SPEC010" in _error_ids(
        f"""---
type: commit_review
verdict: approve
tree_sha: {_VALID_TREE_SHA}
---
## Actions
- [A1] add_to_gitignore | #disable-real-rule
"""
    )


def test_commit_review_malformed_action_entry_is_an_error() -> None:
    assert "SPEC010" in _error_ids(
        f"""---
type: commit_review
verdict: approve
tree_sha: {_VALID_TREE_SHA}
---
## Actions
- [A1] delete_file tmp/scratch.log
"""
    )


def test_commit_review_uppercase_verdict_is_rejected() -> None:
    doc = f"""---
type: commit_review
verdict: APPROVE
tree_sha: {_VALID_TREE_SHA}
---
## Actions
"""
    assert "SPEC010" in _error_ids(doc)


def test_reject_with_frontmatter_reason_without_reason_section_is_error() -> None:
    doc = f"""---
type: commit_review
verdict: reject
tree_sha: {_VALID_TREE_SHA}
reason: frontmatter reason text
---
## Actions
"""
    assert "SPEC008" in _error_ids(doc)
