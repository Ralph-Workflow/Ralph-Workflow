"""Regression coverage for execution consumers of permissive plan receipts."""

from ralph.pipeline.work_units import parse_work_units_from_artifact


def test_plan_execution_regression_prose_without_ids_has_no_worker_gate() -> None:
    """S-4: accepted prose remains executable without inventing worker scopes."""
    accepted_plan_content = {
        "summary": "This accepted prose plan has no canonical implementation identifiers."
    }

    assert parse_work_units_from_artifact(accepted_plan_content) is None
