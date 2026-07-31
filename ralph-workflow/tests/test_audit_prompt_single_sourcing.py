"""Regression coverage for canonical cross-surface prompt statements."""

from ralph.testing.audit_prompt_single_sourcing import collect_violations


def test_cross_surface_statements_are_single_sourced_verbatim() -> None:
    assert collect_violations() == []
