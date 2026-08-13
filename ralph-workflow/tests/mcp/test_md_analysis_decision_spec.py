"""Focused tests for development-analysis-decision request_changes contract."""

from __future__ import annotations

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec

_FINDING = (
    "Criterion: tests pass. Expected observation: focused test passes. "
    "Verdict: not met. Evidence: `pytest -q` fails. Location: tests/test_foo.py. "
    "Remaining work: add the missing edge-case test to tests/test_foo.py."
)


def _dev_request_changes(finding: str = _FINDING) -> str:
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


def _dev_completed() -> str:
    return """---
type: development_analysis_decision
status: completed
---

## Summary
- [SUM-1] All criteria met.

## Criterion Verdicts
- [DA-001] Criterion: tests pass. Expected observation: focused test passes. Verdict: met. Evidence: `pytest -q` passes. Location: tests/test_foo.py.
"""


def _dev_failed() -> str:
    return """---
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


def test_valid_request_changes_with_remaining_work_is_accepted() -> None:
    content, diagnostics = parse_and_validate(
        _dev_request_changes(), get_spec("development_analysis_decision")
    )
    assert diagnostics == []
    assert content["status"] == "request_changes"


def test_completed_decision_is_accepted() -> None:
    content, diagnostics = parse_and_validate(
        _dev_completed(), get_spec("development_analysis_decision")
    )
    assert diagnostics == []
    assert content["status"] == "completed"


def test_failed_decision_is_accepted() -> None:
    _content, diagnostics = parse_and_validate(
        _dev_failed(), get_spec("development_analysis_decision")
    )
    assert diagnostics == []


def test_request_changes_missing_remaining_work_rejected() -> None:
    finding = (
        "Criterion: tests pass. Expected observation: focused test passes. "
        "Verdict: not met. Evidence: `pytest -q` fails. Location: tests/test_foo.py."
    )
    content, diagnostics = parse_and_validate(
        _dev_request_changes(finding), get_spec("development_analysis_decision")
    )
    rule_ids = {d.rule_id for d in diagnostics}
    assert "ANALYSIS015" in rule_ids
    assert content == {}


def test_request_changes_placeholder_location_rejected() -> None:
    finding = (
        "Criterion: tests pass. Expected observation: focused test passes. "
        "Verdict: not met. Evidence: `pytest -q` fails. Location: unknown. "
        "Remaining work: add the missing test."
    )
    _content, diagnostics = parse_and_validate(
        _dev_request_changes(finding), get_spec("development_analysis_decision")
    )
    rule_ids = {d.rule_id for d in diagnostics}
    assert "ANALYSIS016" in rule_ids
    assert _content == {}


def test_request_changes_without_criterion_or_plan_ref_rejected() -> None:
    finding = (
        "Expected observation: focused test passes. "
        "Verdict: not met. Evidence: `pytest -q` fails. Location: tests/test_foo.py. "
        "Remaining work: add the missing test."
    )
    _content, diagnostics = parse_and_validate(
        _dev_request_changes(finding), get_spec("development_analysis_decision")
    )
    rule_ids = {d.rule_id for d in diagnostics}
    assert "ANALYSIS017" in rule_ids


def test_request_changes_with_plan_reference_accepted() -> None:
    finding = (
        "Criterion: plan step S-1 is complete. Expected observation: tests pass. "
        "Verdict: not met. Evidence: `pytest -q` fails. Location: tests/test_foo.py. "
        "Remaining work: add the missing test. Plan reference: [S-1]"
    )
    _content, diagnostics = parse_and_validate(
        _dev_request_changes(finding), get_spec("development_analysis_decision")
    )
    assert diagnostics == []


def test_request_changes_per_finding_criterion_required() -> None:
    """S-4: every finding must independently carry Criterion: or Plan reference:."""
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
    doc = (
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
    _content, diagnostics = parse_and_validate(
        doc, get_spec("development_analysis_decision")
    )
    rule_ids = {d.rule_id for d in diagnostics}
    assert "ANALYSIS017" in rule_ids


def test_request_changes_missing_location_rejected() -> None:
    """S-4: every finding must include a concrete Location:."""
    finding_no_loc = (
        "Criterion: tests pass. Expected observation: focused test passes. "
        "Verdict: not met. Evidence: `pytest -q` fails. "
        "Remaining work: add the missing edge-case test."
    )
    finding_with_loc = (
        "Criterion: tests pass. Expected observation: focused test passes. "
        "Verdict: not met. Evidence: `pytest -q` fails. Location: tests/test_foo.py."
    )
    doc = (
        "---\n"
        "type: development_analysis_decision\n"
        "status: request_changes\n"
        "---\n\n"
        "## Summary\n"
        "- [SUM-1] One criterion is not met.\n\n"
        "## What Came Up Short\n"
        f"- [DA-001] {finding_no_loc}\n\n"
        "## Criterion Verdicts\n"
        f"- [DA-001] {finding_with_loc}\n"
    )
    _content, diagnostics = parse_and_validate(
        doc, get_spec("development_analysis_decision")
    )
    rule_ids = {d.rule_id for d in diagnostics}
    assert "ANALYSIS016" in rule_ids


def test_request_changes_all_findings_complete_accepted() -> None:
    """S-4: multi-finding request_changes passes when every finding is complete."""
    finding_a = (
        "Criterion: tests pass. Expected observation: focused test passes. "
        "Verdict: not met. Evidence: `pytest -q` fails. Location: tests/test_a.py. "
        "Remaining work: add the missing edge-case test."
    )
    finding_b = (
        "Criterion: lint is clean. Expected observation: ruff passes. "
        "Verdict: not met. Evidence: `ruff check` fails. Location: src/b.py. "
        "Remaining work: fix the lint error."
    )
    doc = (
        "---\n"
        "type: development_analysis_decision\n"
        "status: request_changes\n"
        "---\n\n"
        "## Summary\n"
        "- [SUM-1] Two criteria are not met.\n\n"
        "## What Came Up Short\n"
        f"- [DA-001] {finding_a}\n"
        f"- [DA-002] {finding_b}\n\n"
        "## Criterion Verdicts\n"
        f"- [DA-001] {finding_a}\n"
        f"- [DA-002] {finding_b}\n"
    )
    _content, diagnostics = parse_and_validate(
        doc, get_spec("development_analysis_decision")
    )
    assert diagnostics == []


def test_request_changes_mismatched_mirrored_verdict_rejected() -> None:
    """DA-008: a What Came Up Short 'Verdict: met' must not mirror a 'not met' verdict."""
    criterion = (
        "Criterion: tests pass. Expected observation: focused test passes. "
        "Verdict: not met. Evidence: `pytest -q` fails. Location: tests/test_foo.py."
    )
    # Same ID, but the shortfall says 'met' while the criterion verdict says 'not met'.
    finding_with_wrong_verdict = (
        "Criterion: tests pass. Expected observation: focused test passes. "
        "Verdict: met. Evidence: `pytest -q` fails. Location: tests/test_foo.py. "
        "Remaining work: add the missing edge-case test."
    )
    doc = (
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
    _content, diagnostics = parse_and_validate(
        doc, get_spec("development_analysis_decision")
    )
    rule_ids = {d.rule_id for d in diagnostics}
    assert "ANALYSIS018" in rule_ids
