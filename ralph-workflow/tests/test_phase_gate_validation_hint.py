"""Behavioral coverage for validation-phase retry hints."""

from __future__ import annotations

from ralph.phases.required_artifacts import build_proof_failure_hint, build_retry_hint


def test_validation_retry_hint_demands_repair_before_resubmission() -> None:
    hint = build_retry_hint("development", "SPEC008 missing evidence", validation=True)

    assert hint.startswith("VALIDATION FAILURE")
    assert "fix the underlying issue" in hint.lower()
    assert "do not resubmit unchanged work" in hint.lower()


def test_validation_proof_hint_uses_the_canonical_banner() -> None:
    hint = build_proof_failure_hint("development", "S-2 is unproven", validation=True)

    assert hint.startswith("VALIDATION FAILURE")
    assert "S-2 is unproven" in hint
    assert "do not resubmit unchanged work" in hint.lower()
