"""Regression contracts for conflict-resolution relay infrastructure failure (S-4)."""

from __future__ import annotations

import pytest

from ralph.agents.invoke import SupervisionInfrastructureError, raise_on_relay_health_error


def test_conflict_resolution_relay_failure_precedes_inactivity_classification() -> None:
    """S-4: a relay fault is typed infrastructure failure, never conflict inactivity."""
    reader = type("Reader", (), {"_relay_health_error": lambda self: "relay acknowledgement timed out"})()

    with pytest.raises(SupervisionInfrastructureError, match="SUPERVISION_INFRASTRUCTURE_FAILURE") as exc_info:
        raise_on_relay_health_error(reader, "resolver")

    assert "CONFLICT_INACTIVITY" not in str(exc_info.value)
    assert exc_info.value.detail == "relay acknowledgement timed out"
