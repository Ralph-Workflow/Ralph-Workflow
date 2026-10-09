"""Focused tests for the free-form development-analysis-decision contract.

The development analysis decision is free-form below the frontmatter: the
only mechanically validated field is the closed ``status`` enum. The
shared ``What Came Up Short`` / ``Criterion Verdicts`` shape, the per-field
labels, and the per-rule identifiers (ANALYSIS002-019) belong to the
prior structured contract and no longer apply.
"""

from __future__ import annotations

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec


def _dev_request_changes(finding: str) -> str:
    return f"""---
type: development_analysis_decision
status: request_changes
---

## Summary
- [SUM-1] One criterion is not met.

## What Came Up Short
- [DA-001] {finding}

## Criterion Verdicts
- [DA-001] {finding}
"""


def test_valid_request_changes_with_remaining_work_is_accepted() -> None:
    finding = (
        "Criterion: tests pass. Expected observation: focused test passes. "
        "Verdict: not met. Evidence: `pytest -q` fails. Location: tests/test_foo.py. "
        "Remaining work: add the missing edge-case test to tests/test_foo.py."
    )
    content, diagnostics = parse_and_validate(
        _dev_request_changes(finding), get_spec("development_analysis_decision")
    )
    assert diagnostics == []
    assert content["status"] == "request_changes"


def test_completed_decision_is_accepted() -> None:
    document = """---
type: development_analysis_decision
status: completed
---

## Summary
- [SUM-1] All criteria met.

## Criterion Verdicts
- [DA-001] Criterion: tests pass. Expected observation: focused test passes. Verdict: met. Evidence: `pytest -q` passes. Location: tests/test_foo.py.
"""
    content, diagnostics = parse_and_validate(document, get_spec("development_analysis_decision"))
    assert diagnostics == []
    assert content["status"] == "completed"


def test_failed_decision_is_accepted() -> None:
    document = """---
type: development_analysis_decision
status: failed
---

## Summary
- [SUM-1] A criterion is not evaluable.

## What Came Up Short
- [DA-001] Criterion: tests pass. Expected observation: focused test passes. Verdict: not evaluable. Evidence: cannot determine. Location: tests/test_foo.py.

## Criterion Verdicts
- [DA-001] Criterion: tests pass. Expected observation: focused test passes. Verdict: not evaluable. Evidence: cannot determine. Location: tests/test_foo.py.
"""
    _content, diagnostics = parse_and_validate(document, get_spec("development_analysis_decision"))
    assert diagnostics == []


def test_request_changes_missing_remaining_work_is_accepted() -> None:
    """Free-form: the body is the next agent's reading matter. The validator
    does not require ``Remaining work:`` or any other per-field label.
    """
    finding = (
        "Criterion: tests pass. Expected observation: focused test passes. "
        "Verdict: not met. Evidence: `pytest -q` fails. Location: tests/test_foo.py."
    )
    content, diagnostics = parse_and_validate(
        _dev_request_changes(finding), get_spec("development_analysis_decision")
    )
    assert diagnostics == []
    assert content["status"] == "request_changes"


def test_request_changes_with_placeholder_location_is_accepted() -> None:
    """Free-form: no per-finding location predicate. ``Location: unknown`` is prose."""
    finding = (
        "Criterion: tests pass. Expected observation: focused test passes. "
        "Verdict: not met. Evidence: `pytest -q` fails. Location: unknown. "
        "Remaining work: add the missing test."
    )
    content, diagnostics = parse_and_validate(
        _dev_request_changes(finding), get_spec("development_analysis_decision")
    )
    assert diagnostics == []
    assert content["status"] == "request_changes"


def test_request_changes_without_criterion_or_plan_ref_is_accepted() -> None:
    """Free-form: the body can take any shape; no Criterion / Plan reference predicate."""
    finding = (
        "Expected observation: focused test passes. "
        "Verdict: not met. Evidence: `pytest -q` fails. Location: tests/test_foo.py. "
        "Remaining work: add the missing test."
    )
    content, diagnostics = parse_and_validate(
        _dev_request_changes(finding), get_spec("development_analysis_decision")
    )
    assert diagnostics == []
    assert content["status"] == "request_changes"


def test_request_changes_with_plan_reference_accepted() -> None:
    finding = (
        "Criterion: plan step S-1 is complete. Expected observation: tests pass. "
        "Verdict: not met. Evidence: `pytest -q` fails. Location: tests/test_foo.py. "
        "Remaining work: add the missing test. Plan reference: [S-1]"
    )
    content, diagnostics = parse_and_validate(
        _dev_request_changes(finding), get_spec("development_analysis_decision")
    )
    assert diagnostics == []
    assert content["status"] == "request_changes"


def test_request_changes_per_finding_criterion_unconstrained() -> None:
    """Free-form: a developer analysis decision is whatever prose the agent writes."""
    good_finding = (
        "Criterion: tests pass. Expected observation: focused test passes. "
        "Verdict: not met. Evidence: `pytest -q` fails. Location: tests/test_foo.py. "
        "Remaining work: add the missing edge-case test."
    )
    bad_finding = (
        "Expected observation: lint is clean. "
        "Verdict: not met. Evidence: `ruff check` fails. Location: src/bar.py. "
        "Remaining work: fix the lint error."
    )
    document = (
        "---\n"
        "type: development_analysis_decision\n"
        "status: request_changes\n"
        "---\n\n"
        "## Summary\n"
        "- [SUM-1] Two criteria are not met.\n\n"
        "## What Came Up Short\n"
        f"- [DA-001] {good_finding}\n"
        f"- [DA-002] {bad_finding}\n\n"
        "## Criterion Verdicts\n"
        f"- [DA-001] {good_finding}\n"
        f"- [DA-002] Criterion: lint is clean. Expected observation: lint is clean. "
        "Verdict: not met. Evidence: `ruff check` fails. Location: src/bar.py.\n"
    )
    content, diagnostics = parse_and_validate(document, get_spec("development_analysis_decision"))
    assert diagnostics == []
    assert content["status"] == "request_changes"


def test_request_changes_missing_location_is_accepted() -> None:
    """Free-form: no per-finding Location predicate."""
    document = """---
type: development_analysis_decision
status: request_changes
---

## Summary
- [SUM-1] One criterion is not met.

## What Came Up Short
- [DA-001] Criterion: tests pass. Expected observation: focused test passes. Verdict: not met. Evidence: `pytest -q` fails. Remaining work: add the missing edge-case test.

## Criterion Verdicts
- [DA-001] Criterion: tests pass. Expected observation: focused test passes. Verdict: not met. Evidence: `pytest -q` fails.
"""
    content, diagnostics = parse_and_validate(document, get_spec("development_analysis_decision"))
    assert diagnostics == []
    assert content["status"] == "request_changes"


def test_request_changes_all_findings_complete_accepted() -> None:
    """Free-form: multi-finding request_changes passes regardless of body shape."""
    document = """---
type: development_analysis_decision
status: request_changes
---

## Summary
- [SUM-1] Two criteria are not met.

## What Came Up Short
- [DA-001] Criterion: tests pass. Expected observation: focused test passes. Verdict: not met. Evidence: `pytest -q` fails. Location: tests/test_a.py. Remaining work: add the missing edge-case test.
- [DA-002] Criterion: lint is clean. Expected observation: ruff passes. Verdict: not met. Evidence: `ruff check` fails. Location: src/b.py. Remaining work: fix the lint error.

## Criterion Verdicts
- [DA-001] Criterion: tests pass. Expected observation: focused test passes. Verdict: not met. Evidence: `pytest -q` fails. Location: tests/test_a.py.
- [DA-002] Criterion: lint is clean. Expected observation: ruff passes. Verdict: not met. Evidence: `ruff check` fails. Location: src/b.py.
"""
    content, diagnostics = parse_and_validate(document, get_spec("development_analysis_decision"))
    assert diagnostics == []
    assert content["status"] == "request_changes"


def test_mismatched_mirrored_verdict_is_accepted() -> None:
    """Free-form: no mirror predicate, so a "met"/"not met" mismatch is fine."""
    criterion = (
        "Criterion: tests pass. Expected observation: focused test passes. "
        "Verdict: not met. Evidence: `pytest -q` fails. Location: tests/test_foo.py."
    )
    finding_with_wrong_verdict = (
        "Criterion: tests pass. Expected observation: focused test passes. "
        "Verdict: met. Evidence: `pytest -q` fails. Location: tests/test_foo.py. "
        "Remaining work: add the missing edge-case test."
    )
    document = (
        "---\n"
        "type: development_analysis_decision\n"
        "status: request_changes\n"
        "---\n\n"
        "## Summary\n"
        "- [SUM-1] One criterion is not met.\n\n"
        "## What Came Up Short\n"
        f"- [DA-001] {finding_with_wrong_verdict}\n\n"
        "## Criterion Verdicts\n"
        f"- [DA-001] {criterion}\n"
    )
    content, diagnostics = parse_and_validate(document, get_spec("development_analysis_decision"))
    assert diagnostics == []
    assert content["status"] == "request_changes"


def test_free_form_plan_reference_only_finding_is_accepted() -> None:
    """Free-form: any prose is fine, including Plan-reference-only findings."""
    finding_plan_ref_only = (
        "Expected observation: focused test passes. "
        "Verdict: not met. Evidence: `pytest -q` fails. Location: tests/test_foo.py. "
        "Remaining work: the focused regression test is still missing. "
        "Plan reference: [WU-1]"
    )
    criterion_with_plan_ref = (
        "Criterion: focused regression test exists. "
        "Expected observation: focused test passes. "
        "Verdict: not met. Evidence: `pytest -q` fails. Location: tests/test_foo.py. "
        "Plan reference: [WU-1]"
    )
    document = (
        "---\n"
        "type: development_analysis_decision\n"
        "status: request_changes\n"
        "---\n\n"
        "## Summary\n"
        "- [SUM-1] One criterion is not met.\n\n"
        "## What Came Up Short\n"
        f"- [DA-001] {finding_plan_ref_only}\n\n"
        "## Criterion Verdicts\n"
        f"- [DA-001] {criterion_with_plan_ref}\n"
    )
    content, diagnostics = parse_and_validate(document, get_spec("development_analysis_decision"))
    assert diagnostics == []
    assert content["status"] == "request_changes"


def test_orphan_plan_reference_only_finding_is_accepted() -> None:
    """Free-form: no mirror predicate, so an orphan Plan-reference-only finding is fine."""
    document = """---
type: development_analysis_decision
status: request_changes
---

## Summary
- [SUM-1] One criterion is not met (orphan mirror).

## What Came Up Short
- [DA-001] Expected observation: focused regression test passes. Verdict: not met. Evidence: `pytest -q` fails. Location: tests/test_orphan.py. Remaining work: the focused regression test is still missing. Plan reference: [WU-2]

## Criterion Verdicts
- [DA-002] Criterion: focused regression test passes. Expected observation: focused regression test passes. Verdict: met. Evidence: `pytest -q` passes. Location: tests/test_foo.py.
"""
    content, diagnostics = parse_and_validate(document, get_spec("development_analysis_decision"))
    assert diagnostics == []
    assert content["status"] == "request_changes"
