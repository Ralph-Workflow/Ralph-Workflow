"""Pure behavior tests for the commit-cleanup markdown specification."""

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec, registered_specs
from ralph.mcp.artifacts.markdown.specs import COMMIT_CLEANUP_SPEC

_VALID_DOCUMENT = """---
type: commit_cleanup
analysis_complete: true
---
## Reason
- [R1] Temporary build output must not be committed

## Actions
- [A1] delete_file | tmp/scratch.log
- [A2] add_to_gitignore | *.tmp
"""


def _error_ids(text: str) -> set[str]:
    _, diagnostics = parse_and_validate(text, get_spec("commit_cleanup"))
    return {diagnostic.rule_id for diagnostic in diagnostics if diagnostic.severity == "error"}


def test_commit_cleanup_spec_is_registered() -> None:
    assert get_spec("commit_cleanup") is COMMIT_CLEANUP_SPEC
    assert "commit_cleanup" in {spec.artifact_type for spec in registered_specs()}


def test_valid_commit_cleanup_document_produces_typed_content() -> None:
    content, diagnostics = parse_and_validate(_VALID_DOCUMENT, get_spec("commit_cleanup"))

    assert diagnostics == []
    assert content["analysis_complete"] is True
    assert content["actions"] == [
        {"action": "delete_file", "path": "tmp/scratch.log"},
        {"action": "add_to_gitignore", "pattern": "*.tmp"},
    ]


def test_commit_cleanup_comment_injection_guard_rejects_hash_prefix() -> None:
    # Path *traversal* is enforced at execution time (ralph/git/commit_cleanup.py);
    # the validation-layer security guard rejects gitignore comment-line injection.
    assert "SPEC010" in _error_ids(
        """---
type: commit_cleanup
analysis_complete: true
---
## Actions
- [A1] add_to_gitignore | #disable-real-rule
"""
    )


def test_commit_cleanup_malformed_action_entry_is_an_error() -> None:
    assert "SPEC010" in _error_ids(
        """---
type: commit_cleanup
analysis_complete: true
---
## Actions
- [A1] delete_file tmp/scratch.log
"""
    )
