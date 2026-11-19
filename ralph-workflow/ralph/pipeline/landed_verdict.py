from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from ralph.pipeline._landed_predicate import expected_landed_facts, operation_landed

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.pipeline.auto_integrate_record import IntegrationRecord

__all__ = ("LandedVerdict", "expected_landed_facts", "landed_verdict")


class LandedVerdict(StrEnum):
    LANDED = "landed"
    NOT_LANDED = "not_landed"
    UNKNOWN = "unknown"


def landed_verdict(root: Path, record: IntegrationRecord) -> LandedVerdict:
    decision = operation_landed(root, record)
    if decision.landed:
        return LandedVerdict.LANDED
    if decision.observed_head is None:
        return LandedVerdict.UNKNOWN
    return LandedVerdict.NOT_LANDED
