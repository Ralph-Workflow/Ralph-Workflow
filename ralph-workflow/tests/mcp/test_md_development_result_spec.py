"""Pure behavior tests for the free-form development-result markdown spec.

Status-only validation: the body is the next agent's reading matter and is
never checked for structure, evidence, dispositions, or the prior
timebox-warned ``Incomplete Work`` CLOSED grammar.
"""

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec
from ralph.mcp.artifacts.markdown.specs import DEVELOPMENT_RESULT_SPEC


def test_development_result_spec_accepts_free_form_completed_body() -> None:
    content, diagnostics = parse_and_validate(
        """---
type: development_result
status: completed
---
## Summary
- [SUM-1] Implemented the markdown artifact spec.

Free-form prose the next agent reads. The validator does not inspect the
body beyond the frontmatter ``status`` enum.
""",
        DEVELOPMENT_RESULT_SPEC,
    )

    assert diagnostics == []
    assert content["status"] == "completed"
    assert get_spec("development_result") is DEVELOPMENT_RESULT_SPEC


def test_development_result_rejects_unknown_status() -> None:
    content, diagnostics = parse_and_validate(
        """---
type: development_result
status: uncertain
---
## Summary
- [SUM-1] Completed the work.
## Files Changed
- [F-1] src/example.py
""",
        DEVELOPMENT_RESULT_SPEC,
    )

    assert content == {}
    assert any(
        diagnostic.severity == "error"
        and "completed" in diagnostic.message
        and "partial" in diagnostic.message
        for diagnostic in diagnostics
    )


def test_development_result_accepts_partial_with_minimal_body() -> None:
    content, diagnostics = parse_and_validate(
        """---
type: development_result
status: partial
---
This is a free-form body. The validator only checks the status enum.
""",
        DEVELOPMENT_RESULT_SPEC,
    )

    assert diagnostics == []
    assert content["status"] == "partial"


def test_development_result_accepts_failed_with_minimal_body() -> None:
    content, diagnostics = parse_and_validate(
        """---
type: development_result
status: failed
---
Work stopped; the next agent reads whatever was written.
""",
        DEVELOPMENT_RESULT_SPEC,
    )

    assert diagnostics == []
    assert content["status"] == "failed"


def test_development_result_accepts_arbitrary_section_headings() -> None:
    """Free-form body: any heading is allowed without shape validation."""
    content, diagnostics = parse_and_validate(
        """---
type: development_result
status: completed
---
## What I Did
- Changed the spec to validate only the status.

## What I Verified
- Ran the focused tests; they pass.

## Why I Chose This Route
- The structured proof machinery was over-engineered for the next agent.
""",
        DEVELOPMENT_RESULT_SPEC,
    )

    assert diagnostics == []
    assert content["status"] == "completed"


def test_development_result_does_not_require_proof_sections() -> None:
    """No `## Plan Items Proven` / `## Analysis Items Addressed` sections required."""
    content, diagnostics = parse_and_validate(
        """---
type: development_result
status: completed
---
Wrote what I did, what changed, and what was verified. No structured proof.
""",
        DEVELOPMENT_RESULT_SPEC,
    )

    assert diagnostics == []
    assert content["status"] == "completed"


def test_development_result_does_not_require_disposition() -> None:
    """`Disposition:` was a structured proof field; free-form results omit it."""
    _content, diagnostics = parse_and_validate(
        """---
type: development_result
status: completed
---
Done. The plan's S-1 step shipped in src/main.py and pytest passes.
""",
        DEVELOPMENT_RESULT_SPEC,
    )

    assert diagnostics == []


def test_development_result_ignores_incomplete_work_field() -> None:
    """The prior CLOSED-grammar `Incomplete Work` gate is removed; any prose is fine."""
    content, diagnostics = parse_and_validate(
        """---
type: development_result
status: failed
---
## Incomplete Work
- Free-form prose about what is unfinished.
""",
        DEVELOPMENT_RESULT_SPEC,
    )

    assert diagnostics == []
    assert content["status"] == "failed"
