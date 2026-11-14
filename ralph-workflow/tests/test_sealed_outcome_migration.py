from __future__ import annotations

import inspect

from ralph.pipeline.conflict_resolution import _driver_events, attempt_fault


def test_sealed_outcome_migration_regression_attempt_fault_rejects_text_evidence() -> None:
    signature = inspect.signature(attempt_fault.classify_attempt_fault)

    assert signature.parameters["evidence"].annotation == (
        "AttemptFault | ResolutionTerminationReason | None"
    )
    assert "_classify_text_attempt_fault" not in inspect.getsource(attempt_fault)


def test_sealed_outcome_migration_regression_declared_decisions_requires_status() -> None:
    signature = inspect.signature(_driver_events.announce_declared_decisions)

    assert signature.parameters["status"].annotation == "IntegrationStatus | None"
